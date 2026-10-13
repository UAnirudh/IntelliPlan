"""Linked AI accounts: a student's own API key for Claude, OpenAI, Gemini or Groq.

What "linking an account" means
-------------------------------
None of these providers lets a third-party app spend a consumer subscription
(Claude Pro, ChatGPT Plus, Gemini Advanced) on a user's behalf; there is no
OAuth grant for that. What a student can link is an *API key* from the
provider's developer console, billed to them by the provider. That is what
this module handles.

A key is checked with a call that costs nothing (listing models) before it
is saved, stored encrypted, shown back only as its last four characters,
and never written to a log.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from intelliplan.premium import claude_tutor
from intelliplan.premium.pricing import Usage

logger = logging.getLogger(__name__)

ANTHROPIC = "anthropic"
OPENAI = "openai"
GEMINI = "gemini"
GROQ = "groq"

PROVIDERS: dict[str, dict[str, str]] = {
    ANTHROPIC: {"name": "Claude (Anthropic)", "console": "https://console.anthropic.com/settings/keys",
                "prefix": "sk-ant-"},
    OPENAI: {"name": "ChatGPT (OpenAI)", "console": "https://platform.openai.com/api-keys",
             "prefix": "sk-"},
    GEMINI: {"name": "Gemini (Google AI Studio)", "console": "https://aistudio.google.com/apikey",
             "prefix": ""},
    GROQ: {"name": "Groq", "console": "https://console.groq.com/keys", "prefix": "gsk_"},
}

#: The order linked accounts are tried in when a student has several.
PREFERENCE = (ANTHROPIC, OPENAI, GEMINI, GROQ)

_KEY_SHAPE = re.compile(r"^[A-Za-z0-9_\-.]{20,256}$")
_TIMEOUT = 15


def _model(provider: str, tier: str) -> str:
    defaults = {
        OPENAI: {"quick": "gpt-5-mini", "balanced": "gpt-5", "deep": "gpt-5"},
        GEMINI: {"quick": "gemini-2.5-flash", "balanced": "gemini-2.5-flash", "deep": "gemini-2.5-pro"},
        GROQ: {"quick": "openai/gpt-oss-20b", "balanced": "openai/gpt-oss-120b",
               "deep": "openai/gpt-oss-120b"},
    }
    env = f"BYOK_{provider.upper()}_MODEL_{tier.upper()}"
    return os.getenv(env) or defaults.get(provider, {}).get(tier, "")


def hint(key: str) -> str:
    """What the settings page shows: the last four characters only."""
    key = (key or "").strip()
    return f"…{key[-4:]}" if len(key) >= 8 else "…"


def looks_valid(provider: str, key: str) -> str | None:
    """A cheap shape check before any network call. Returns an error or None."""
    if provider not in PROVIDERS:
        return "Unknown provider."
    key = (key or "").strip()
    if not _KEY_SHAPE.match(key):
        return "That doesn't look like an API key. Copy it again from the provider's console."
    prefix = PROVIDERS[provider]["prefix"]
    if prefix and not key.startswith(prefix):
        return f"{PROVIDERS[provider]['name']} keys start with “{prefix}”."
    return None


@dataclass
class Check:
    ok: bool
    message: str
    key_invalid: bool = False


def verify(provider: str, key: str, *, session: Any = None) -> Check:
    """Confirm the key works with a call that spends no tokens."""
    shape = looks_valid(provider, key)
    if shape:
        return Check(False, shape, key_invalid=True)
    if provider == ANTHROPIC:
        return _verify_anthropic(key)
    http = session or requests
    try:
        if provider == OPENAI:
            r = http.get("https://api.openai.com/v1/models", timeout=_TIMEOUT,
                         headers={"Authorization": f"Bearer {key}"})
        elif provider == GEMINI:
            r = http.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                         timeout=_TIMEOUT, headers={"x-goog-api-key": key})
        else:
            r = http.get("https://api.groq.com/openai/v1/models", timeout=_TIMEOUT,
                         headers={"Authorization": f"Bearer {key}"})
    except requests.RequestException as exc:
        logger.info("byok verify for %s could not connect: %s", provider, type(exc).__name__)
        return Check(False, "Couldn't reach the provider. Try again in a minute.")
    if r.status_code in (401, 403):
        return Check(False, "The provider rejected that key.", key_invalid=True)
    if r.status_code == 429:
        # A rate-limited key is a real key.
        return Check(True, "Linked. The provider says this key is rate limited right now.")
    if r.status_code >= 400:
        return Check(False, f"The provider returned an error ({r.status_code}).")
    return Check(True, "Linked.")


def _verify_anthropic(key: str, client: Any = None) -> Check:
    import anthropic

    client = client or anthropic.Anthropic(api_key=key, max_retries=0, timeout=_TIMEOUT)
    try:
        client.models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        return Check(False, "Anthropic rejected that key.", key_invalid=True)
    except anthropic.RateLimitError:
        return Check(True, "Linked. Anthropic says this key is rate limited right now.")
    except anthropic.APIStatusError as exc:
        return Check(False, f"Anthropic returned an error ({exc.status_code}).")
    except anthropic.APIConnectionError:
        return Check(False, "Couldn't reach Anthropic. Try again in a minute.")
    return Check(True, "Linked.")


@dataclass
class ByokResult:
    text: str
    model: str
    usage: Usage


def chat(provider: str, key: str, *, tier: str, messages: list[dict],
         dynamic_system: list[str], max_tokens: int, effort: str,
         claude_model: str, stable_system: str = claude_tutor.TUTOR_SYSTEM,
         session: Any = None) -> ByokResult:
    """Answer a tutor turn on the student's own key. Raises TutorError."""
    if provider == ANTHROPIC:
        r = claude_tutor.answer(api_key=key, model=claude_model, effort=effort,
                                max_tokens=max_tokens, messages=messages,
                                dynamic_system=dynamic_system, stable_system=stable_system)
        return ByokResult(r.text, r.model, r.usage)

    system = "\n\n".join([stable_system] + [d for d in dynamic_system if d])
    chat_messages = [{"role": "system", "content": system}] + claude_tutor.to_claude_messages(messages)
    model = _model(provider, tier)
    http = session or requests
    try:
        if provider == GEMINI:
            contents = [{"role": "model" if m["role"] == "assistant" else "user",
                         "parts": [{"text": m["content"]}]} for m in chat_messages[1:]]
            r = http.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key}, timeout=120,
                json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                      "generationConfig": {"maxOutputTokens": max_tokens}})
        else:
            base = "https://api.openai.com/v1" if provider == OPENAI else "https://api.groq.com/openai/v1"
            body: dict[str, Any] = {"model": model, "messages": chat_messages}
            body["max_completion_tokens" if provider == OPENAI else "max_tokens"] = max_tokens
            r = http.post(f"{base}/chat/completions", timeout=120,
                          headers={"Authorization": f"Bearer {key}"}, json=body)
    except requests.RequestException as exc:
        raise claude_tutor.TutorError(f"could not reach {provider}") from exc

    if r.status_code in (401, 403):
        raise claude_tutor.TutorError(f"{provider} rejected the key", key_invalid=True)
    if r.status_code >= 400:
        raise claude_tutor.TutorError(f"{provider} returned {r.status_code}")
    data = r.json()
    if provider == GEMINI:
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "".join(p.get("text", "") for p in parts).strip()
        meta = data.get("usageMetadata") or {}
        usage = Usage(input_tokens=int(meta.get("promptTokenCount") or 0),
                      output_tokens=int(meta.get("candidatesTokenCount") or 0))
    else:
        text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        u = data.get("usage") or {}
        usage = Usage(input_tokens=int(u.get("prompt_tokens") or 0),
                      output_tokens=int(u.get("completion_tokens") or 0))
    if not text:
        raise claude_tutor.TutorError(f"{provider} returned an empty answer")
    return ByokResult(text, model, usage)
