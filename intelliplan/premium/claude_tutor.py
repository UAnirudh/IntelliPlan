"""One Claude tutor turn, with the usage that turn has to be billed for.

The stable tutor instructions go first with a cache breakpoint, so a
conversation's later turns read them from the prompt cache at a tenth of the
input price. Everything that changes per student or per turn (memory,
personalisation, the assignment) comes after the breakpoint, so it can change
without invalidating the cached prefix.

Opus and Sonnet calls opt into server-side refusal fallbacks: if a safety
classifier declines, Anthropic re-runs the request on a fallback model inside
the same call. ``response.model`` then names the model that actually served
the answer, and that is the model the turn is priced at.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from intelliplan.premium.pricing import Usage

logger = logging.getLogger(__name__)

#: Models that accept ``fallbacks: "default"`` on the Claude API.
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5",
                    "claude-fable-5-1")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

REFUSAL_REPLY = (
    "I can't help with that one. If this is for a class, tell me the subject and "
    "what the assignment asks, and I'll help you work through it."
)

TUTOR_SYSTEM = """You are Plani, the tutor inside IntelliPlan, a study planner used by middle \
school, high school and college students.

How to tutor:
- Teach, don't just answer. Lead with the idea the student needs, then work through it with them.
- For homework problems, show the method on the student's problem step by step and check \
their understanding. When they ask you to check their work, point to the first mistake and \
let them fix it before showing the rest.
- Match the student's level. Use plain words, short paragraphs, and an example before a definition.
- For essays and writing, give specific feedback on their draft. Do not write graded work for \
them to hand in as their own; offer an outline, a model paragraph on a different topic, or edits \
they can learn from.
- Use Markdown. Write math in LaTeX between $...$ or $$...$$.
- If you are not sure of a fact, say so. Tell the student to check deadlines and requirements \
with their teacher.
- Keep the student safe. If they mention self-harm, abuse or danger, respond with care and point \
them to a trusted adult and, in the US, the 988 Suicide & Crisis Lifeline."""


class TutorError(RuntimeError):
    """The call failed. ``retryable`` says whether another provider might succeed."""

    def __init__(self, message: str, *, retryable: bool = True, key_invalid: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.key_invalid = key_invalid


@dataclass
class TutorResult:
    text: str
    model: str
    usage: Usage
    stop_reason: str
    refused: bool = False
    truncated: bool = False


def to_claude_messages(messages: list[dict]) -> list[dict]:
    """Chat history in the shape the Messages API takes.

    System entries are dropped (they travel in ``system``), empty turns are
    dropped, and leading assistant turns are removed because the first
    message must be the user's.
    """
    out: list[dict] = []
    for m in messages or []:
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if not out and role == "assistant":
            continue
        out.append({"role": role, "content": content})
    return out


def build_system(stable: str, dynamic: list[str]) -> list[dict]:
    blocks: list[dict] = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}]
    blocks += [{"type": "text", "text": d} for d in dynamic if d and d.strip()]
    return blocks


def _text_of(response: Any) -> str:
    return "".join(getattr(b, "text", "") for b in (response.content or [])
                   if getattr(b, "type", "") == "text").strip()


def answer(
    *,
    api_key: str,
    model: str,
    effort: str,
    max_tokens: int,
    messages: list[dict],
    dynamic_system: list[str] | None = None,
    stable_system: str = TUTOR_SYSTEM,
    client: Any = None,
) -> TutorResult:
    """Run one tutor turn on Claude. Raises TutorError on failure."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - requirements pin it
        raise TutorError("the anthropic package is not installed") from exc

    chat = to_claude_messages(messages)
    if not chat or chat[-1]["role"] != "user":
        raise TutorError("no question to answer", retryable=False)

    client = client or anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": build_system(stable_system, dynamic_system or []),
        "messages": chat,
        "output_config": {"effort": effort},
    }
    try:
        if model in _FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=[_FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise TutorError("the Anthropic key was rejected", retryable=True, key_invalid=True) from exc
    except anthropic.PermissionDeniedError as exc:
        raise TutorError("the Anthropic key lacks permission for this model",
                         retryable=True, key_invalid=True) from exc
    except anthropic.BadRequestError as exc:
        raise TutorError(f"Claude rejected the request: {exc.message}", retryable=False) from exc
    except anthropic.RateLimitError as exc:
        raise TutorError("Claude is rate limited", retryable=True) from exc
    except anthropic.APIStatusError as exc:
        raise TutorError(f"Claude returned {exc.status_code}", retryable=True) from exc
    except anthropic.APIConnectionError as exc:
        raise TutorError("could not reach Claude", retryable=True) from exc

    served = str(getattr(response, "model", None) or model)
    usage = Usage.from_anthropic(response.usage)
    stop = str(getattr(response, "stop_reason", "") or "")
    if stop == "refusal":
        category = getattr(getattr(response, "stop_details", None), "category", None)
        logger.info("claude tutor refusal (category=%s, model=%s)", category, served)
        return TutorResult(REFUSAL_REPLY, served, usage, stop, refused=True)
    text = _text_of(response)
    if not text:
        raise TutorError("Claude returned an empty answer", retryable=True)
    truncated = stop == "max_tokens"
    if truncated:
        text += "\n\n_(I ran out of room here. Ask me to continue.)_"
    return TutorResult(text, served, usage, stop, truncated=truncated)
