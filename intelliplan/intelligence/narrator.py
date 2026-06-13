"""Narrator — the ONLY place in the engine that talks to an LLM.

``brief(facts, ai_chat_json=None, student_name="") → BriefingText``

The Narrator consumes a fully-resolved facts dict (built by
``TodayService`` from the deterministic engines) and produces the 2–4
sentence briefing. It never computes anything — dates, scores, and
hours arrive pre-calculated.

Dependency-injected AI: the ``ai_chat_json`` callable defaults to
:func:`ai_provider.chat_json` (imported lazily so unit tests never load
the genai/groq SDKs). When AI is unavailable or fails, the Narrator
falls back to a templated briefing built from the same facts — the
Command Center degrades gracefully, never blank.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from intelliplan.domain.plan import BriefingText

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are IntelliPlan's daily briefing writer — a calm, sharp academic \
chief of staff for a student. You receive a JSON object of pre-computed \
facts about the student's day. Write a short personal briefing.

Rules:
- 2 to 4 sentences. No bullet points, no markdown, no emoji.
- Present tense, second person ("you"), address the student by first name once.
- Lead with the single most important thing (the top task or the health change).
- Reference at most ONE number from the facts — the most decision-relevant one.
- Never invent facts. Only use what is in the JSON.
- Do not mention priority scores or point values directly; translate them
  into plain language ("your highest priority", "worth a big chunk of your grade").
- Tone: steady and encouraging, never alarmist, never saccharine.

Respond with a JSON object:
{"headline": "<one short sentence — the single most important takeaway>",
 "body": "<1-3 sentences expanding on it>",
 "tone": "<one of: calm-focused | encouraging | heads-up>"}
"""


def _default_ai(messages: list[dict], **kwargs: Any) -> dict:
    from ai_provider import chat_json  # lazy — keeps SDK imports out of tests

    return chat_json(messages, **kwargs)


def _greeting_name(student_name: str) -> str:
    first = (student_name or "").strip().split(" ")[0]
    return first or "there"


def fallback_brief(facts: dict[str, Any], student_name: str = "") -> BriefingText:
    """Templated briefing from the same facts — used when AI is down.

    Deliberately plain but useful: names the #1 task and the heaviest
    day. The UI shows this identically to an AI briefing (with a subtle
    "offline" label driven by ``generated_by``).
    """
    name = _greeting_name(student_name)
    top = (facts.get("top_tasks") or [{}])[0]
    top_title = top.get("title") or ""
    top_course = top.get("course") or ""
    heaviest = facts.get("heaviest_day_name") or ""
    health_delta = int(facts.get("health_delta") or 0)

    if not top_title:
        headline = f"You're all caught up, {name}."
        body = "No urgent work on your plate today. A good day to review or get ahead."
        tone = "encouraging"
    else:
        course_part = f" for {top_course}" if top_course else ""
        headline = f"Start with {top_title}{course_part}."
        bits: list[str] = []
        why = top.get("top_reason") or ""
        if why:
            bits.append(f"It's your highest priority — {why.lower()}.")
        if heaviest:
            bits.append(f"{heaviest} looks like your heaviest day, so getting this done early keeps the week manageable.")
        if health_delta < 0:
            bits.append("Your academic health dipped — clearing this helps it recover.")
        body = " ".join(bits) or "It's the highest-impact item on your list today."
        tone = "heads-up" if health_delta < 0 else "calm-focused"

    return BriefingText(
        headline=headline,
        body=body,
        tone=tone,
        generated_by="fallback-template",
        cached=False,
    )


def brief(
    facts: dict[str, Any],
    *,
    student_name: str = "",
    ai_chat_json: Callable[..., dict] | None = None,
) -> BriefingText:
    """Produce the daily briefing from pre-computed facts.

    ``facts`` is the JSON-safe dict built by ``TodayService._facts``.
    Falls back to :func:`fallback_brief` on any AI failure.
    """
    ai = ai_chat_json or _default_ai
    payload = dict(facts)
    payload["student_first_name"] = _greeting_name(student_name)
    try:
        raw = ai(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            tier="fast",
            temperature=0.4,
            max_tokens=400,
        )
        headline = str(raw.get("headline") or "").strip()
        body = str(raw.get("body") or "").strip()
        tone = str(raw.get("tone") or "calm-focused").strip()
        if not headline or not body:
            raise ValueError("AI briefing missing headline/body")
        if tone not in ("calm-focused", "encouraging", "heads-up"):
            tone = "calm-focused"
        return BriefingText(
            headline=headline,
            body=body,
            tone=tone,
            generated_by=str(facts.get("model_hint") or "ai"),
            cached=False,
        )
    except Exception as exc:  # any AI failure → graceful template
        logger.warning("Narrator AI brief failed (%s); using fallback template", exc)
        return fallback_brief(facts, student_name=student_name)
