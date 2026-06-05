"""Unified AI provider — Google Gemini primary, Groq fallback.

All IntelliPlan AI features route through this module so we can swap
providers without touching every endpoint.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Literal

logger = logging.getLogger(__name__)

Tier = Literal["standard", "fast", "vision"]

# ── Model config ──────────────────────────────────────────────────
GEMINI_STANDARD = os.getenv("GEMINI_STANDARD_MODEL", "gemini-2.5-flash")
GEMINI_FAST = os.getenv("GEMINI_FAST_MODEL", "gemini-2.0-flash-lite")
GEMINI_VISION = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")

GROQ_STANDARD = os.getenv("GROQ_STANDARD_MODEL", "llama-3.3-70b-versatile")
GROQ_FAST = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
GROQ_VISION = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_WHISPER = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

_TIER_GEMINI = {"standard": GEMINI_STANDARD, "fast": GEMINI_FAST, "vision": GEMINI_VISION}
_TIER_GROQ = {"standard": GROQ_STANDARD, "fast": GROQ_FAST, "vision": GROQ_VISION}

_gemini_client_cache = None
_groq_client_cache = None


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


def _gemini_chat(
    messages: list[dict],
    tier: Tier,
    temperature: float,
    max_tokens: int,
    response_format: dict | None,
) -> str:
    from google.genai import types

    system, chat_messages = _split_messages(messages)
    if not chat_messages:
        chat_messages = messages

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if system:
        config_kwargs["system_instruction"] = system
    if response_format and response_format.get("type") == "json_object":
        config_kwargs["response_mime_type"] = "application/json"

    client = _gemini_client()
    resp = client.models.generate_content(
        model=_TIER_GEMINI[tier],
        contents=_gemini_contents(chat_messages),
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = (resp.text or "").strip()
    if not text and getattr(resp, "candidates", None):
        parts = []
        for cand in resp.candidates:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", []) or []:
                if getattr(part, "text", None):
                    parts.append(part.text)
        text = "".join(parts).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def _groq_chat(
    messages: list[dict],
    tier: Tier,
    temperature: float,
    max_tokens: int,
    response_format: dict | None,
) -> str:
    client = _groq_client()
    kwargs: dict[str, Any] = {
        "model": _TIER_GROQ[tier],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def chat(
    messages: list[dict],
    *,
    tier: Tier = "standard",
    temperature: float = 0.7,
    max_tokens: int = 512,
    response_format: dict | None = None,
) -> str:
    """Chat completion — Gemini first, Groq on failure."""
    errors: list[str] = []

    if gemini_api_key():
        try:
            return _gemini_chat(messages, tier, temperature, max_tokens, response_format)
        except Exception as exc:
            errors.append(f"Gemini: {exc}")
            logger.warning("Gemini chat failed (%s), trying Groq fallback", exc)

    if groq_api_key():
        try:
            return _groq_chat(messages, tier, temperature, max_tokens, response_format)
        except Exception as exc:
            errors.append(f"Groq: {exc}")
            logger.error("Groq chat fallback failed: %s", exc)

    raise RuntimeError(
        "No AI backend available. Set GEMINI_API_KEY (primary) or GROQ_API_KEY (fallback). "
        + ("; ".join(errors) if errors else "")
    )


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
