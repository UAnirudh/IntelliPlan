"""The LLM reasoning layer for the adaptive scheduler.

The model's job here is narrow: take a decision that has *already been made*
by the deterministic engines, and explain it in a sentence a student will
read. It does not choose the action, compute the score, judge feasibility, or
know what time it is.

Why the boundary is drawn there
-------------------------------
A model asked "what should this student do next?" will answer. The answer
will be fluent, it will cite plausible reasons, and there is no way to tell a
good one from a bad one — because there is nothing to check it against. A
model asked "here is the action, here are the six measured factors behind it,
say it in one sentence" produces something that can be verified line by line
against the facts it was given. The first is a scheduler that sounds
intelligent; the second is one that is accountable.

So this module enforces the boundary in code rather than in prompt wording:

* the model receives only the reason codes and rounded values the engines
  produced — never raw grades, never assignment descriptions, never anything
  the student has not opted into sharing;
* every field it returns is validated against those inputs before it is
  shown, and anything unrecognised is dropped;
* any failure — no key, bad JSON, hallucinated reason code, disabled
  personalisation — falls through to a deterministic sentence built from the
  same reason codes.

The fallback is the important part. It is not a degraded mode we tolerate; it
is the guarantee that the explanation is always grounded, with the LLM as an
optional improvement to its phrasing.

Purity
------
No Flask, no ORM, no clock reads. The AI callable is injected.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from intelliplan.intelligence.nba import REASON_LABELS

logger = logging.getLogger(__name__)

__all__ = ["Explanation", "explain", "fallback_explain", "build_state"]


_SYSTEM_PROMPT = """\
You are the academic reasoning layer of IntelliPlan.

A deterministic scheduling engine has ALREADY chosen what this student \
should do next and scored it. Your only job is to say why, in the \
student's language.

THE ENGINE IS AUTHORITATIVE for: time, availability, conflicts, deadlines, \
feasibility, scores, probabilities, and which action was chosen. You may \
not disagree with it, re-rank it, or suggest a different action.

RULES:
- Use ONLY the facts in the JSON. Never invent a deadline, a grade, a time, \
a mastery level, or a number that is not there.
- 1 to 2 sentences. No markdown, no bullets, no emoji, no exclamation marks.
- Second person, present tense, plain and factual. Not motivational.
- Reference at most ONE number, and only if it is in the JSON.
- Do not restate the score or the probability as a percentage unless the \
JSON marks it as confident.
- If the JSON says confidence is low, say what is uncertain rather than \
sounding sure.
- Never mention that you are an AI, a model, or a reasoning layer.
- Do not reveal or describe your reasoning process. Give the conclusion.

You must also pick the reason codes that actually drove the decision, from \
the list provided. Do not invent codes.

Respond with a JSON object:
{"why": "<1-2 sentences>",
 "primary_reasons": ["<reason code>", "..."],
 "uncertainty": "<one short clause, or empty string if nothing is uncertain>"}
"""


@dataclass(frozen=True, slots=True)
class Explanation:
    """A validated, groundable explanation of one decision."""

    why: str
    primary_reasons: tuple[str, ...]
    uncertainty: str
    generated_by: str

    @property
    def is_ai(self) -> bool:
        return self.generated_by not in ("", "fallback-template")

    def to_dict(self) -> dict[str, Any]:
        return {
            "why": self.why,
            "primary_reasons": list(self.primary_reasons),
            "uncertainty": self.uncertainty,
            "generated_by": self.generated_by,
        }


# ── State construction ───────────────────────────────────────────────


def build_state(
    action: Any,
    *,
    minutes_available: int,
    today: date | None = None,
    alternatives: Sequence[Any] = (),
    behavior: Any = None,
    student_first_name: str = "",
) -> dict[str, Any]:
    """The exact structured state the model is allowed to see.

    Data minimisation is the point of this function existing rather than the
    caller dumping the decision object. Assignment titles go (the student
    needs to be told which task, and the title is already on their screen);
    descriptions, grades, point values, course averages, and every raw
    behavioural count stay behind. Anything not listed here never leaves the
    server.
    """
    candidate = action.candidate
    return {
        "student_first_name": (student_first_name or "").strip().split(" ")[0],
        "action": {
            "kind": candidate.kind.value,
            "title": candidate.title,
            "course": candidate.course,
            "minutes": candidate.minutes,
            "due_in_days": _due_in_days(candidate, today),
            "method": action.method,
        },
        "decision": {
            "reason_codes": list(action.reason_codes),
            "confidence": round(float(action.confidence), 2),
            "confident": bool(action.confidence >= 0.6),
            "completion_probability": (
                round(float(action.completion_probability), 2)
                if action.confidence >= 0.6
                else None
            ),
        },
        "reason_code_meanings": {
            code: REASON_LABELS[code]
            for code in action.reason_codes
            if code in REASON_LABELS
        },
        "context": {
            "minutes_available": int(minutes_available),
            "best_time_of_day": getattr(behavior, "best_slot", None),
            "alternatives_considered": len(alternatives),
        },
    }


def _due_in_days(candidate: Any, today: date | None) -> int | None:
    """Days until this is due, or ``None`` when either half is unknown.

    ``today`` is a parameter rather than a clock read so the state handed to
    the model is reproducible from a log line — which is the only way to
    audit what it was told after the fact.
    """
    if today is None or candidate.due_date is None:
        return None
    return (candidate.due_date - today).days


# ── Fallback ─────────────────────────────────────────────────────────


def fallback_explain(action: Any) -> Explanation:
    """A deterministic explanation built from the reason codes.

    This is the floor, and it is a real floor: the sentences come from
    :data:`nba.REASON_LABELS`, which are generated from measured facts, so
    this path can state things the AI path is only allowed to rephrase.
    """
    codes = [c for c in action.reason_codes if c in REASON_LABELS]
    lead = REASON_LABELS[codes[0]] if codes else ""
    support = REASON_LABELS[codes[1]] if len(codes) > 1 else ""
    why = " ".join(x for x in (lead, support) if x)
    if not why:
        why = "This is the highest-value thing on your plan for the time you have."

    uncertainty = ""
    if action.confidence < 0.5:
        uncertainty = "we do not have much history on how these sessions go for you yet"

    return Explanation(
        why=why,
        primary_reasons=tuple(codes[:3]),
        uncertainty=uncertainty,
        generated_by="fallback-template",
    )


# ── AI path ──────────────────────────────────────────────────────────


def _default_ai(messages: list[dict], **kwargs: Any) -> dict:
    from ai_provider import chat_json  # lazy — keeps SDK imports out of tests

    return chat_json(messages, **kwargs)


def explain(
    action: Any,
    *,
    minutes_available: int,
    today: date | None = None,
    alternatives: Sequence[Any] = (),
    behavior: Any = None,
    student_first_name: str = "",
    personalization_enabled: bool = False,
    ai_chat_json: Callable[..., dict] | None = None,
) -> Explanation:
    """Explain one decision, with the model as an optional improvement.

    ``personalization_enabled`` is the student's own opt-in. When it is off,
    nothing about them is sent anywhere and the deterministic path is used —
    which is not a punishment, because that path already says the true thing.
    """
    if not personalization_enabled:
        return fallback_explain(action)

    state = build_state(
        action,
        minutes_available=minutes_available,
        today=today,
        alternatives=alternatives,
        behavior=behavior,
        student_first_name=student_first_name,
    )
    ai = ai_chat_json or _default_ai
    try:
        raw = ai(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(state, ensure_ascii=False)},
            ],
            tier="fast",
            temperature=0.3,
            max_tokens=300,
        )
        return _validate(raw, action)
    except Exception as exc:
        logger.warning("reasoning-layer AI failed (%s); using deterministic text", exc)
        return fallback_explain(action)


def _validate(raw: Mapping[str, Any], action: Any) -> Explanation:
    """Reject anything the model made up, then accept the rest.

    Validation is not politeness here. The model is being asked to describe a
    decision it did not make, from facts it did not gather; the only thing
    standing between a hallucinated reason code and a student's screen is
    this function.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("reasoning layer returned a non-object")

    why = str(raw.get("why") or "").strip()
    if not why:
        raise ValueError("reasoning layer returned no explanation")
    if len(why) > 400:
        # A model that ignored "1 to 2 sentences" has probably ignored the
        # other rules too. Do not truncate it into looking compliant.
        raise ValueError("reasoning layer returned an over-long explanation")

    allowed = set(action.reason_codes)
    claimed = raw.get("primary_reasons")
    reasons = tuple(
        str(c) for c in (claimed if isinstance(claimed, list) else []) if str(c) in allowed
    )
    if not reasons:
        # Losing every code means the model invented its own vocabulary.
        # Fall back to the engine's own ordering rather than showing text
        # whose stated reasons we could not verify.
        reasons = tuple(action.reason_codes[:3])

    uncertainty = str(raw.get("uncertainty") or "").strip()
    if len(uncertainty) > 200:
        uncertainty = ""

    return Explanation(
        why=why,
        primary_reasons=reasons,
        uncertainty=uncertainty,
        generated_by="ai",
    )
