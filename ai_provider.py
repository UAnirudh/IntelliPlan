"""Unified AI provider — Google Gemini primary, Groq fallback.

All IntelliPlan AI features route through this module so we can swap
providers without touching every endpoint.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import Any, Literal

logger = logging.getLogger(__name__)

Tier = Literal["standard", "fast", "vision"]

# ── Model config ──────────────────────────────────────────────────
GEMINI_STANDARD = os.getenv("GEMINI_STANDARD_MODEL", "gemini-2.5-flash")
# gemini-2.0-flash-lite has been retired. The API's own 404 names the
# replacement: "This model models/gemini-2.0-flash-lite is no longer
# available. Please update your code to use models/gemini-3.5-flash-lite".
#
# This mattered more than a model bump: the fast tier is what generates
# the daily briefing, so every 404 fell through to the Groq fallback,
# and with no Groq key configured it fell through again to the static
# template. The Command Center was quietly serving canned text while
# reporting itself healthy — the only trace was one log line per call.
GEMINI_FAST = os.getenv("GEMINI_FAST_MODEL", "gemini-3.5-flash-lite")
GEMINI_VISION = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")

# Groq decommissioned the Llama 3.3 70B and Llama 3.1 8B models on
# 2026-08-16. Keep the environment variables so deployments can override
# these defaults, but make the supported replacements the out-of-box path.
GROQ_STANDARD = os.getenv("GROQ_STANDARD_MODEL", "openai/gpt-oss-120b")
GROQ_FAST = os.getenv("GROQ_FAST_MODEL", "openai/gpt-oss-20b")
# Qwen 3.6 27B is the recommended multimodal replacement for deprecated
# Llama vision models on Groq.
GROQ_VISION = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
GROQ_WHISPER = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

_TIER_GEMINI = {"standard": GEMINI_STANDARD, "fast": GEMINI_FAST, "vision": GEMINI_VISION}
_TIER_GROQ = {"standard": GROQ_STANDARD, "fast": GROQ_FAST, "vision": GROQ_VISION}

# Claude is the paid-plan model. It is never reachable on a free account, and
# an unset ANTHROPIC_API_KEY simply drops it out of the chain.
CLAUDE_STANDARD = os.getenv("CLAUDE_STANDARD_MODEL", "claude-sonnet-5")
CLAUDE_FAST = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_VISION = os.getenv("CLAUDE_VISION_MODEL", "claude-sonnet-5")
_TIER_CLAUDE = {"standard": CLAUDE_STANDARD, "fast": CLAUDE_FAST, "vision": CLAUDE_VISION}


def anthropic_api_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY")


#: Ordered model chain per tier. Gemini's free-tier quota is counted *per
#: model* -- a 429 on gemini-2.5-flash says nothing about the lite model's
#: allowance -- so the standard tier drops to the lite model before it leaves
#: Google at all. That single step is what kept the tutor answering after the
#: 20-requests-a-day cap on 2.5-flash was spent; before it, one exhausted
#: model took every AI feature in the product down with it.
_CHAINS: dict[str, list[tuple[str, str]]] = {
    "standard": [
        ("gemini", GEMINI_STANDARD),
        ("gemini", GEMINI_FAST),
        ("groq", GROQ_STANDARD),
        ("groq", GROQ_FAST),
    ],
    "fast": [
        ("gemini", GEMINI_FAST),
        ("gemini", GEMINI_STANDARD),
        ("groq", GROQ_FAST),
        ("groq", GROQ_STANDARD),
    ],
    "vision": [
        ("gemini", GEMINI_VISION),
        ("gemini", GEMINI_FAST),
        ("groq", GROQ_VISION),
    ],
}


def model_chain(tier: Tier = "standard", plan: str = "free") -> list[tuple[str, str]]:
    """The ordered (provider, model) list this request may try.

    Paid plans lead with Claude; everyone shares the free ladder underneath it,
    so a paid student whose Claude call fails still gets an answer rather than
    an error. Providers with no key are dropped here rather than attempted.
    """
    chain: list[tuple[str, str]] = []
    if plan == "paid" and anthropic_api_key():
        chain.append(("claude", _TIER_CLAUDE[tier]))
    chain.extend(_CHAINS.get(tier, _CHAINS["standard"]))
    have = {"gemini": bool(gemini_api_key()), "groq": bool(groq_api_key()),
            "claude": bool(anthropic_api_key())}
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for step in chain:
        if have.get(step[0]) and step not in seen:
            seen.add(step)
            out.append(step)
    return out

_gemini_client_cache = None
_groq_client_cache = None

# Gemini 2.5+ are *thinking* models: internal reasoning tokens are billed
# against max_output_tokens. Left unbounded, a hard prompt can spend the whole
# budget thinking and emit truncated JSON — measured at 7541 thinking / 444
# output tokens on an 8000 budget for a 12-assignment schedule. Capping
# thinking keeps the reasoning that makes plans good while guaranteeing room
# to actually write the answer (1024 thinking left 4540 output on that same
# request). Override with GEMINI_THINKING_BUDGET; -1 disables the cap.
DEFAULT_THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "1024"))

#: Largest share of max_output_tokens that internal reasoning may consume.
#: The remainder is reserved for the response itself, so a small max_tokens
#: can never produce an empty body — see _gemini_chat.
THINKING_SHARE = 0.4
#: Below this many tokens, reasoning is not worth the room it costs.
MIN_USEFUL_THINKING = 128


class AIQuotaExhausted(RuntimeError):
    """Every configured model refused the call for quota or rate-limit reasons.

    Distinct from AIUnavailable: the keys are fine and the request was well
    formed, the allowance is simply spent. Callers surface this to the student
    as "the AI is at today's limit", never as a generic failure.
    """

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AIUnavailable(RuntimeError):
    """No AI backend is configured at all, or every one failed for other reasons."""


class AITruncatedError(RuntimeError):
    """The model hit its token ceiling and returned a partial response.

    Raised rather than returned so the provider chain treats it as a failure
    and tries the next backend. A truncated body is non-empty, so without
    this it would sail back to the caller and blow up as a JSON parse error
    with the fallback never consulted.
    """


def _supports_thinking(model: str) -> bool:
    """Thinking config is valid on the Gemini 2.5+ families.

    Parsed from the version number rather than matched against a list of
    known strings. The list version silently stopped working the moment
    the fast tier moved to gemini-3.5-flash-lite: "3.5" was in neither
    "2.5" nor "3.0", so the model was treated as non-thinking, the
    thinking cap was never applied, and reasoning tokens were free to eat
    the entire output budget — the exact failure DEFAULT_THINKING_BUDGET
    exists to prevent. Every future 3.x/4.x release would have landed the
    same way.
    """
    m = (model or "").lower()
    if "-thinking" in m:
        return True
    match = re.search(r"gemini-(\d+)(?:\.(\d+))?", m)
    if not match:
        return False
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    return (major, minor) >= (2, 5)


def gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def groq_api_key() -> str | None:
    return os.getenv("GROQ_API_KEY")


def ai_available() -> bool:
    return bool(gemini_api_key() or groq_api_key())


def _gemini_client():
    global _gemini_client_cache
    if _gemini_client_cache is None:
        from google import genai

        key = gemini_api_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        # API key auth (Google AI Studio) — project ID is metadata only, not
        # passed to the client (vertexai + api_key are mutually exclusive).
        _gemini_client_cache = genai.Client(api_key=key)
    return _gemini_client_cache


def _groq_client():
    global _groq_client_cache
    if _groq_client_cache is None:
        from groq import Groq

        key = groq_api_key()
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set.")
        _groq_client_cache = Groq(api_key=key)
    return _groq_client_cache


def _split_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Pull system prompts out; return (system_instruction, chat_messages)."""
    system_parts: list[str] = []
    chat: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content.strip())
        else:
            chat.append(msg)
    system = "\n\n".join(system_parts) if system_parts else None
    return system, chat


def _gemini_contents(messages: list[dict]):
    from google.genai import types

    contents = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "assistant":
            role = "model"
        if isinstance(content, str):
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=content)])
            )
    return contents


def _finish_reason(resp) -> str:
    for cand in getattr(resp, "candidates", None) or []:
        fr = getattr(cand, "finish_reason", None)
        if fr is not None:
            return str(getattr(fr, "name", fr)).upper()
    return ""


def _gemini_chat(
    messages: list[dict],
    tier: Tier,
    temperature: float,
    max_tokens: int,
    response_format: dict | None,
    thinking_budget: int | None = None,
    model: str | None = None,
) -> str:
    from google.genai import types

    system, chat_messages = _split_messages(messages)
    if not chat_messages:
        chat_messages = messages

    model = model or _TIER_GEMINI[tier]
    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if system:
        config_kwargs["system_instruction"] = system
    if response_format and response_format.get("type") == "json_object":
        config_kwargs["response_mime_type"] = "application/json"

    budget = DEFAULT_THINKING_BUDGET if thinking_budget is None else thinking_budget
    if budget >= 0 and _supports_thinking(model):
        # Thinking tokens are drawn from max_output_tokens, so a caller who
        # asks for a short answer can end up with no answer at all: the
        # model spends the whole allowance reasoning, returns MAX_TOKENS
        # with an empty body, and the provider chain reads that as a dead
        # backend and falls through to the static template. A caller asking
        # for 16 tokens is not asking for a 1024-token deliberation.
        #
        # Scale the reasoning to fit inside what was actually granted,
        # always leaving the larger share for the response. Below the floor
        # there is no room to think usefully, so thinking is switched off
        # rather than squeezed — a direct answer beats a truncated one.
        usable = max(0, int(max_tokens * THINKING_SHARE))
        budget = min(budget, usable)
        if budget < MIN_USEFUL_THINKING:
            # Thinking cannot simply be switched off: gemini-3.5-flash-lite
            # rejects thinking_budget=0 with a bare INVALID_ARGUMENT. So
            # hold the floor and buy the room instead — raise the ceiling
            # so the reasoning overhead comes out of a bigger allowance
            # rather than out of the caller's answer. The caller asked for
            # N tokens of response and still gets N tokens of response.
            budget = MIN_USEFUL_THINKING
            config_kwargs["max_output_tokens"] = max_tokens + budget
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)

    client = _gemini_client()

    def _call(cfg: dict):
        return client.models.generate_content(
            model=model,
            contents=_gemini_contents(chat_messages),
            config=types.GenerateContentConfig(**cfg),
        )

    try:
        resp = _call(config_kwargs)
    except Exception as exc:
        # Some model families reject thinking_config outright. Losing the cap
        # is far better than losing the request, so retry once without it.
        # INVALID_ARGUMENT is included deliberately: a model that refuses a
        # thinking budget it does not support reports it as a bare 400 with
        # no mention of thinking anywhere in the message, so keying purely
        # on the word "thinking" left the request dead with a generic error.
        text = str(exc).lower()
        retryable = "thinking" in text or "invalid_argument" in text
        if not retryable or "thinking_config" not in config_kwargs:
            raise
        logger.info("Model %s rejected thinking_config, retrying without it", model)
        config_kwargs.pop("thinking_config", None)
        resp = _call(config_kwargs)

    text = (resp.text or "").strip()
    if not text and getattr(resp, "candidates", None):
        parts = []
        for cand in resp.candidates:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", []) or []:
                if getattr(part, "text", None):
                    parts.append(part.text)
        text = "".join(parts).strip()

    if _finish_reason(resp) == "MAX_TOKENS":
        um = getattr(resp, "usage_metadata", None)
        raise AITruncatedError(
            f"Gemini hit max_output_tokens ({max_tokens}); response is partial "
            f"(thinking={getattr(um, 'thoughts_token_count', '?')}, "
            f"output={getattr(um, 'candidates_token_count', '?')})."
        )
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def _groq_chat(
    messages: list[dict],
    tier: Tier,
    temperature: float,
    max_tokens: int,
    response_format: dict | None,
    model: str | None = None,
) -> str:
    client = _groq_client()
    kwargs: dict[str, Any] = {
        "model": model or _TIER_GROQ[tier],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    # Same trap as Gemini: a length-truncated body is non-empty and would
    # otherwise be returned as if it were a complete answer.
    if getattr(choice, "finish_reason", None) == "length":
        raise AITruncatedError(
            f"Groq hit max_tokens ({max_tokens}); response is partial."
        )
    if not content:
        raise RuntimeError("Groq returned an empty response.")
    return content


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "quota" in msg or "rate" in msg or "resource exhausted" in msg


def _is_transient_error(exc: Exception) -> bool:
    """A server-side blip worth one immediate retry on the same provider.

    Gemini returns 503 UNAVAILABLE under load spikes; these clear in seconds,
    and retrying is cheaper than degrading to the fallback model.
    """
    msg = str(exc).lower()
    return "503" in msg or "unavailable" in msg or "overloaded" in msg or "internal error" in msg


def _claude_chat(
    messages: list[dict],
    tier: Tier,
    temperature: float,
    max_tokens: int,
    response_format: dict | None,
    model: str | None = None,
) -> str:
    """Paid-plan path. The anthropic package is an optional dependency, so an
    install without it drops Claude from the chain instead of erroring."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package is not installed") from exc

    key = anthropic_api_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    system, chat_messages = _split_messages(messages)
    if not chat_messages:
        chat_messages = messages
    client = anthropic.Anthropic(api_key=key)
    kwargs: dict[str, Any] = {
        "model": model or _TIER_CLAUDE[tier],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": m["role"], "content": m["content"]} for m in chat_messages],
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    if resp.stop_reason == "max_tokens":
        raise AITruncatedError(f"Claude hit max_tokens ({max_tokens}); response is partial.")
    if not text:
        raise RuntimeError("Claude returned an empty response.")
    return text


#: Resolved by name at call time rather than bound here, so a test (or a
#: caller) that patches ai_provider._gemini_chat actually changes what runs.
_DISPATCH = {"gemini": "_gemini_chat", "groq": "_groq_chat", "claude": "_claude_chat"}


def chat(
    messages: list[dict],
    *,
    tier: Tier = "standard",
    temperature: float = 0.7,
    max_tokens: int = 512,
    response_format: dict | None = None,
    thinking_budget: int | None = None,
    plan: str = "free",
) -> str:
    """Chat completion, walking this tier's model chain until one answers.

    Gemini counts its free-tier quota per model, so a 429 on gemini-2.5-flash
    says nothing about the lite model's remaining allowance. The old code read
    any Gemini failure as "Gemini is down" and jumped straight to Groq, which
    on a deployment with no GROQ_API_KEY meant every AI feature in the product
    died the moment one model hit its daily cap. Walking the chain keeps the
    tutor answering on the next model instead.

    ``thinking_budget`` caps Gemini's internal reasoning tokens, which are
    billed against ``max_tokens``. ``None`` uses DEFAULT_THINKING_BUDGET;
    ``-1`` removes the cap; ``0`` disables thinking entirely.

    Raises AIQuotaExhausted when every model refused on quota, and
    AIUnavailable when nothing is configured or everything failed for some
    other reason. Callers can tell the student which of the two happened.
    """
    chain = model_chain(tier, plan)
    if not chain:
        raise AIUnavailable(
            "No AI backend available. Set GEMINI_API_KEY (primary) or GROQ_API_KEY (fallback)."
        )

    errors: list[str] = []
    quota_hits = 0
    attempted = 0
    retry_after: int | None = None
    #: Truncation is not a sick model, it is an answer that did not fit. The
    #: same provider's other models will cut it off in the same place, so the
    #: chain skips the rest of that provider rather than burning quota to be
    #: truncated again.
    skip_providers: set[str] = set()

    for provider, model in chain:
        if provider in skip_providers:
            continue
        attempted += 1
        fn = globals()[_DISPATCH[provider]]
        kwargs: dict[str, Any] = {"response_format": response_format, "model": model}
        if provider == "gemini":
            kwargs["thinking_budget"] = thinking_budget
        try:
            try:
                return fn(messages, tier, temperature, max_tokens, **kwargs)
            except Exception as exc:
                # A 503 under load clears in seconds. One quick retry on the
                # same model beats stepping down to a weaker one.
                if not _is_transient_error(exc) or isinstance(exc, AITruncatedError):
                    raise
                logger.info("%s/%s transient error (%s), retrying once", provider, model, exc)
                time.sleep(1.5)
                return fn(messages, tier, temperature, max_tokens, **kwargs)
        except Exception as exc:
            errors.append(f"{provider}/{model}: {exc}")
            if isinstance(exc, AITruncatedError):
                skip_providers.add(provider)
                logger.warning("%s/%s truncated, moving to the next provider: %s",
                               provider, model, exc)
            elif _is_quota_error(exc):
                quota_hits += 1
                retry_after = retry_after or _retry_after_seconds(exc)
                logger.info("%s/%s out of quota, trying next model", provider, model)
            else:
                logger.warning("%s/%s failed (%s), trying next model", provider, model, exc)

    if quota_hits and quota_hits == attempted:
        raise AIQuotaExhausted(
            "No AI backend available: every configured model is out of quota. "
            + "; ".join(errors),
            retry_after=retry_after,
        )
    raise AIUnavailable("No AI backend available. " + "; ".join(errors))


def _retry_after_seconds(exc: Exception) -> int | None:
    """Pull Gemini's retryDelay out of a 429 body so callers can say when."""
    m = re.search(r"['\"]retryDelay['\"]:\s*['\"](\d+)s", str(exc))
    if m:
        return int(m.group(1))
    m = re.search(r"retry in (\d+)", str(exc), re.I)
    return int(m.group(1)) if m else None


def vision(
    *,
    system_prompt: str,
    user_text: str,
    image_b64: str,
    image_mime: str = "image/jpeg",
    tier: Tier = "vision",
    temperature: float = 0.5,
    max_tokens: int = 1200,
) -> str:
    """Multimodal image analysis — Gemini first, Groq on failure."""
    errors: list[str] = []

    if gemini_api_key():
        try:
            from google.genai import types

            image_bytes = base64.b64decode(image_b64)
            client = _gemini_client()
            resp = client.models.generate_content(
                model=_TIER_GEMINI[tier],
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=image_bytes, mime_type=image_mime),
                            types.Part.from_text(text=user_text),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            text = (resp.text or "").strip()
            if text:
                return text
            raise RuntimeError("Gemini vision returned empty text.")
        except Exception as exc:
            errors.append(f"Gemini: {exc}")
            if _is_quota_error(exc):
                logger.info("Gemini quota/rate limit hit for vision, falling back to Groq")
            else:
                logger.warning("Gemini vision failed (%s), trying Groq fallback", exc)

    if groq_api_key():
        try:
            client = _groq_client()
            resp = client.chat.completions.create(
                model=_TIER_GROQ[tier],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
                            },
                        ],
                    },
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            errors.append(f"Groq: {exc}")
            logger.error("Groq vision fallback failed: %s", exc)

    raise RuntimeError(
        "Vision analysis unavailable. Set GEMINI_API_KEY or GROQ_API_KEY. "
        + ("; ".join(errors) if errors else "")
    )


def _mime_for_audio(filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return {
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "webm": "audio/webm",
        "mp4": "audio/mp4",
        "flac": "audio/flac",
    }.get(ext, "audio/mpeg")


def transcribe_audio(filename: str, audio_bytes: bytes) -> str:
    """Speech-to-text — Gemini first, Groq Whisper on failure."""
    errors: list[str] = []
    mime = _mime_for_audio(filename)

    if gemini_api_key():
        try:
            from google.genai import types

            client = _gemini_client()
            resp = client.models.generate_content(
                model=GEMINI_FAST,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=audio_bytes, mime_type=mime),
                            types.Part.from_text(
                                text=(
                                    "Transcribe all spoken words in this audio verbatim. "
                                    "Output only the transcript text with no preamble."
                                )
                            ),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=8000),
            )
            text = (resp.text or "").strip()
            if text:
                return text
            raise RuntimeError("Gemini transcription returned empty text.")
        except Exception as exc:
            errors.append(f"Gemini: {exc}")
            if _is_quota_error(exc):
                logger.info("Gemini quota/rate limit hit for transcription, falling back to Groq Whisper")
            else:
                logger.warning("Gemini transcription failed (%s), trying Groq Whisper", exc)

    if groq_api_key():
        try:
            client = _groq_client()
            resp = client.audio.transcriptions.create(
                model=GROQ_WHISPER,
                file=(filename, audio_bytes),
                response_format="text",
            )
            text = resp if isinstance(resp, str) else getattr(resp, "text", "") or ""
            text = text.strip()
            if text:
                return text
            raise RuntimeError("Groq Whisper returned empty text.")
        except Exception as exc:
            errors.append(f"Groq: {exc}")
            logger.error("Groq Whisper fallback failed: %s", exc)

    raise RuntimeError(
        "Transcription unavailable. Set GEMINI_API_KEY or GROQ_API_KEY. "
        + ("; ".join(errors) if errors else "")
    )


def chat_json(
    messages: list[dict],
    *,
    tier: Tier = "standard",
    temperature: float = 0.3,
    max_tokens: int = 1200,
) -> dict:
    """Convenience wrapper that parses a JSON object response."""
    raw = chat(
        messages,
        tier=tier,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    return json.loads(raw)
