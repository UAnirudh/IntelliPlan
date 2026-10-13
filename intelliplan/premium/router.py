"""Pick the Claude model for one tutor question, or ask for clarification.

A fast, cheap model reads the question first. It says what is being asked,
how hard it is, and whether it is too vague to answer well. That decides
between three tiers:

``quick``     Claude Haiku. Definitions, quick checks, one-step questions.
``balanced``  Claude Sonnet. Most homework help: worked examples, feedback.
``deep``      Claude Opus. Proofs, multi-step problems, long essay review.

Routing is advisory and never the only line of defence for cost: the budget
check in ``decide`` downgrades a tier the student cannot afford, and the
classifier's answer is validated rather than trusted.

The classifier is injected (``classify(question, context, classifier=...)``)
so this module never imports a provider and can be tested without a network.
Without a classifier, or when it fails, a keyword heuristic decides.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from intelliplan.premium import pricing

logger = logging.getLogger(__name__)

QUICK = "quick"
BALANCED = "balanced"
DEEP = "deep"
TIERS = (QUICK, BALANCED, DEEP)
AUTO = "auto"
PREFERENCES = (AUTO, QUICK, BALANCED, DEEP)

INTENTS = ("explain", "solve", "check_work", "essay_feedback", "study_plan",
           "practice", "chat", "other")

MAX_CLARIFYING_QUESTIONS = 3

#: How a clarification reply starts. Stored conversations keep only role and
#: content, so this is also how a later turn recognises one.
CLARIFY_PREFIX = "Before I answer"


@dataclass(frozen=True)
class TierSpec:
    model: str
    effort: str
    max_tokens: int


def tier_spec(tier: str) -> TierSpec:
    """Model, effort and output ceiling per tier. Each is overridable."""
    specs = {
        QUICK: TierSpec(os.getenv("PREMIUM_MODEL_QUICK", "claude-haiku-5-5"),
                        os.getenv("PREMIUM_EFFORT_QUICK", "low"), 1500),
        BALANCED: TierSpec(os.getenv("PREMIUM_MODEL_BALANCED", "claude-sonnet-5-5"),
                           os.getenv("PREMIUM_EFFORT_BALANCED", "medium"), 4000),
        DEEP: TierSpec(os.getenv("PREMIUM_MODEL_DEEP", "claude-opus-5-5"),
                       os.getenv("PREMIUM_EFFORT_DEEP", "high"), 8000),
    }
    return specs.get(tier, specs[BALANCED])


@dataclass
class Classification:
    intent: str = "other"
    subject: str = "General"
    difficulty: int = 3
    tier: str = BALANCED
    needs_clarification: bool = False
    clarifying_questions: list[str] = field(default_factory=list)
    summary: str = ""
    source: str = "heuristic"


@dataclass
class Route:
    tier: str
    model: str
    effort: str
    max_tokens: int
    classification: Classification
    downgraded: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        c = self.classification
        return {
            "tier": self.tier, "model": self.model, "effort": self.effort,
            "intent": c.intent, "subject": c.subject, "difficulty": c.difficulty,
            "source": c.source, "downgraded": self.downgraded, "reason": self.reason,
        }


# ── Classification ──────────────────────────────────────────────────────

CLASSIFIER_PROMPT = """You route questions for a student tutoring app. Read the student's latest \
message (and the short context before it) and return only a JSON object with these keys:

"intent": one of explain, solve, check_work, essay_feedback, study_plan, practice, chat, other
"subject": the school subject in one or two words, e.g. "Algebra", "Biology", "US History"
"difficulty": integer 1-5. 1 = a definition or one-line fact. 3 = a typical homework problem \
with a few steps. 5 = a proof, a long multi-part problem, or detailed feedback on a long essay.
"tier": "quick" for difficulty 1-2, "balanced" for 3, "deep" for 4-5
"needs_clarification": true only if a good tutor could not start without more information, \
for example "help me with my homework" with nothing attached, or a question that names no \
topic. Short but clear questions do not need clarification.
"clarifying_questions": if needs_clarification, up to 3 short questions to ask the student; \
otherwise an empty list
"summary": what the student is asking, in under 15 words

Return the JSON object only."""

_DEEP_WORDS = re.compile(
    r"\b(prove|proof|derive|derivation|step[- ]by[- ]step|explain why|essay|thesis|"
    r"analy[sz]e|compare and contrast|multi[- ]part|integral|differential|optimi[sz]e|"
    r"debug|algorithm|lab report|research paper)\b", re.I)
_QUICK_WORDS = re.compile(
    r"^\s*(what is|what's|define|definition of|who (was|is)|when (was|did)|"
    r"spell|translate|is it true)\b", re.I)
_VAGUE = re.compile(
    r"^\s*(help( me)?( with)?( my)?( this| it| homework| hw)?|i need help|can you help( me)?|"
    r"how do i do (this|it)|do (this|it|my homework)|i don'?t get (this|it)|explain (this|it))"
    r"[\s.!?]*$", re.I)


def heuristic(question: str, has_context: bool) -> Classification:
    text = (question or "").strip()
    words = len(text.split())
    if _VAGUE.match(text) and not has_context:
        return Classification(
            intent="other", difficulty=2, tier=QUICK, needs_clarification=True,
            clarifying_questions=[
                "Which class or subject is this for?",
                "Can you paste the question or describe the assignment?",
                "What have you tried so far, or where did you get stuck?",
            ],
            summary="Asked for help without saying what with",
        )
    if _DEEP_WORDS.search(text) or words > 180:
        return Classification(intent="explain", difficulty=4, tier=DEEP,
                              summary=text[:80])
    if _QUICK_WORDS.match(text) and words <= 20:
        return Classification(intent="explain", difficulty=1, tier=QUICK,
                              summary=text[:80])
    return Classification(intent="solve", difficulty=3, tier=BALANCED, summary=text[:80])


def _coerce(data: Any, fallback: Classification) -> Classification:
    """Validate a classifier's JSON. Anything malformed keeps the fallback's value."""
    if not isinstance(data, dict):
        return fallback
    intent = str(data.get("intent") or "").strip().lower()
    tier = str(data.get("tier") or "").strip().lower()
    try:
        difficulty = max(1, min(5, int(data.get("difficulty"))))
    except (TypeError, ValueError):
        difficulty = fallback.difficulty
    if tier not in TIERS:
        tier = QUICK if difficulty <= 2 else (BALANCED if difficulty == 3 else DEEP)
    questions = data.get("clarifying_questions") or []
    if not isinstance(questions, list):
        questions = []
    questions = [str(q).strip()[:200] for q in questions if str(q).strip()][:MAX_CLARIFYING_QUESTIONS]
    needs = bool(data.get("needs_clarification")) and bool(questions)
    return Classification(
        intent=intent if intent in INTENTS else fallback.intent,
        subject=str(data.get("subject") or fallback.subject).strip()[:40] or "General",
        difficulty=difficulty,
        tier=tier,
        needs_clarification=needs,
        clarifying_questions=questions if needs else [],
        summary=str(data.get("summary") or "").strip()[:160],
        source="classifier",
    )


def _parse_json(raw: str) -> Any:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in classifier output")
    return json.loads(raw[start:end + 1])


def classify(
    question: str,
    context: list[dict] | None = None,
    *,
    classifier: Callable[[list[dict]], str] | None = None,
) -> Classification:
    """Classify the latest question. Never raises.

    ``classifier`` takes chat messages and returns the model's raw text.
    ``context`` is the recent conversation, oldest first; only the last few
    turns are sent, trimmed, because the router is meant to be cheap.
    """
    context = context or []
    fallback = heuristic(question, has_context=any(m.get("role") == "assistant" for m in context))
    if classifier is None:
        return fallback
    recent = [
        f"{m.get('role')}: {str(m.get('content') or '')[:400]}"
        for m in context[-4:] if m.get("role") in ("user", "assistant")
    ]
    user_block = ("Context:\n" + "\n".join(recent) + "\n\n" if recent else "") + \
        f"Latest message:\n{(question or '')[:3000]}"
    try:
        raw = classifier([
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user", "content": user_block},
        ])
        return _coerce(_parse_json(raw), fallback)
    except Exception as exc:
        logger.info("router classifier failed, using heuristic: %s", exc)
        return fallback


# ── Decision ────────────────────────────────────────────────────────────


def decide(
    classification: Classification,
    *,
    preference: str = AUTO,
    remaining_micro: int | None,
    input_tokens: int,
) -> Route | None:
    """Turn a classification into a model, within what the student can afford.

    ``remaining_micro`` None means unmetered (the student's own key). Returns
    None when not even the cheapest tier fits in the remaining budget.
    """
    wanted = preference if preference in TIERS else classification.tier
    reason = "student preference" if preference in TIERS else f"{classification.source}: {classification.tier}"
    order = list(TIERS)
    start = order.index(wanted) if wanted in order else order.index(BALANCED)
    for idx in range(start, -1, -1):
        tier = order[idx]
        spec = tier_spec(tier)
        if remaining_micro is not None and \
                pricing.worst_case_micro(spec.model, input_tokens, spec.max_tokens) > remaining_micro:
            continue
        downgraded = idx != start
        return Route(
            tier=tier, model=spec.model, effort=spec.effort, max_tokens=spec.max_tokens,
            classification=classification, downgraded=downgraded,
            reason=reason + (" (downgraded to fit the remaining budget)" if downgraded else ""),
        )
    return None


def last_turn_was_clarification(context: list[dict]) -> bool:
    """Never ask twice in a row: the reply to a clarification gets an answer."""
    for m in reversed(context or []):
        if m.get("role") == "assistant":
            return bool(m.get("clarification")) or \
                str(m.get("content") or "").startswith(CLARIFY_PREFIX)
    return False


def clarification_text(questions: list[str]) -> str:
    """The reply a student sees instead of an answer."""
    noun = "a few quick questions" if len(questions) > 1 else "one quick question"
    lines = [f"{CLARIFY_PREFIX}, {noun} so I can help properly:"]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    lines.append("Reply with whatever you know, or say \"just answer\" and I'll do my best.")
    return "\n".join(lines)
