"""Choosing *how* to study, not just what.

Forty-five minutes on Newton's Laws is not one activity. For a student who
has not understood the material yet it means working through the concept with
worked examples; for one who understands it but cannot retrieve it under time
pressure it means closed-book recall and error review. Prescribing the same
session to both is the difference between a planner and a tutor.

The mapping here is deliberately narrow and rule-shaped, because the evidence
it encodes is narrow and rule-shaped: distributed practice, retrieval
practice, interleaving, and error analysis have well-established conditions
under which they help. What makes this adaptive is not the rules — it is that
the inputs (mastery, its confidence, assessment proximity, minutes available)
are all measured rather than assumed, and that a low-confidence mastery
estimate deliberately falls back to a safer ladder instead of committing to
the aggressive one.

Purity
------
No Flask, no ORM, no clock reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "StudyMethod",
    "MethodPlan",
    "recommend",
    "MASTERY_BANDS",
]


#: Mastery band boundaries. A band is a claim about what a student can
#: currently do, so the boundaries are stated once and shared.
MASTERY_BANDS = {
    "low": 0.45,       # below this: they do not have the concept yet
    "moderate": 0.75,  # below this: they have it but cannot retrieve it reliably
}

#: Below this confidence in the mastery estimate we will not commit to the
#: high-mastery ladder. Recommending timed exam practice to someone we only
#: *think* has the material is the expensive direction to be wrong in.
MIN_CONFIDENCE_FOR_ADVANCED = 0.45

#: A session shorter than this cannot hold a full learn→practice ladder.
SHORT_SESSION_MINUTES = 25


@dataclass(frozen=True, slots=True)
class StudyMethod:
    """One step in a session."""

    key: str
    label: str
    minutes: int


@dataclass(frozen=True, slots=True)
class MethodPlan:
    """How to spend a session, and why that shape.

    ``band`` and ``rationale`` exist so the UI can explain the choice without
    the LLM being asked to invent a justification for it after the fact.
    """

    band: str
    steps: tuple[StudyMethod, ...]
    rationale: str
    reason_codes: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return " → ".join(s.label for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "summary": self.summary,
            "rationale": self.rationale,
            "reason_codes": list(self.reason_codes),
            "steps": [
                {"key": s.key, "label": s.label, "minutes": s.minutes}
                for s in self.steps
            ],
        }


def _split(minutes: int, shares: tuple[float, ...]) -> tuple[int, ...]:
    """Divide a session into steps, giving the remainder to the first step.

    The remainder goes to the *first* step on purpose: it is the step that
    everything after it depends on, and a two-minute rounding difference
    matters more at the start of a session than at the end.
    """
    minutes = max(1, int(minutes))
    parts = [max(1, int(minutes * s)) for s in shares]
    drift = minutes - sum(parts)
    parts[0] = max(1, parts[0] + drift)
    return tuple(parts)


def recommend(
    *,
    mastery: float | None,
    confidence: float = 0.0,
    minutes: int = 45,
    kind: str = "homework",
    assessment_days_away: int | None = None,
    days_since_review: int | None = None,
    recent_error_count: int = 0,
) -> MethodPlan:
    """Pick the shape of one study session.

    ``mastery`` of ``None`` means we have never measured this material. That
    is not the same as low mastery, and it does not get the remedial ladder —
    it gets a diagnostic first, because guessing wrong in either direction
    wastes the session.
    """
    minutes = max(5, int(minutes))
    reasons: list[str] = []

    # ── work that is not concept learning at all ────────────────────
    if kind in ("administrative", "admin"):
        return MethodPlan(
            band="n/a",
            steps=(StudyMethod("do", "Just get it done", minutes),),
            rationale="Administrative work does not need a study method.",
            reason_codes=("administrative",),
        )

    if kind in ("essay", "project"):
        a, b, c = _split(minutes, (0.2, 0.6, 0.2))
        return MethodPlan(
            band="production",
            steps=(
                StudyMethod("plan", "Outline what this sitting produces", a),
                StudyMethod("draft", "Write / build", b),
                StudyMethod("review", "Read back and fix", c),
            ),
            rationale=(
                "Extended production work benefits from a stated target per "
                "sitting; without one the session drifts."
            ),
            reason_codes=("production_work",),
        )

    # ── routine work we have no mastery reading on ──────────────────
    # Homework is not primarily a study-method decision, and dressing it up
    # as one ("diagnose, then fill the gaps") reads as the app padding a
    # to-do item. Say the useful thing instead: do it, then check it.
    if mastery is None and kind in ("homework", "classwork", "reading", "practice", ""):
        a, b = _split(minutes, (0.8, 0.2))
        return MethodPlan(
            band="routine",
            steps=(
                StudyMethod("work", "Work through it", a),
                StudyMethod("check", "Check what you got wrong", b),
            ),
            rationale=(
                "Routine work. Leaving a few minutes to check your own answers "
                "is the part most people skip and the part that carries."
            ),
            reason_codes=("routine_work",),
        )

    # ── too short for a ladder ──────────────────────────────────────
    if minutes < SHORT_SESSION_MINUTES:
        reasons.append("short_session")
        if mastery is not None and mastery < MASTERY_BANDS["moderate"]:
            return MethodPlan(
                band="short",
                steps=(StudyMethod("retrieval", "Quick recall practice", minutes),),
                rationale=(
                    "Too short to learn something new, long enough for recall "
                    "practice — which is where a short session pays best."
                ),
                reason_codes=tuple(reasons),
            )
        return MethodPlan(
            band="short",
            steps=(StudyMethod("review", "Skim and self-quiz", minutes),),
            rationale="A short sitting is worth a review pass, not a new topic.",
            reason_codes=tuple(reasons),
        )

    # ── never measured ──────────────────────────────────────────────
    if mastery is None or confidence <= 0.0:
        a, b, c = _split(minutes, (0.25, 0.5, 0.25))
        return MethodPlan(
            band="unknown",
            steps=(
                StudyMethod("diagnose", "Try a few problems cold", a),
                StudyMethod("study", "Fill the gaps that exposed", b),
                StudyMethod("check", "Re-try what you missed", c),
            ),
            rationale=(
                "We have not seen you work on this yet, so the session starts "
                "by finding out what you already know."
            ),
            reason_codes=("no_mastery_evidence",),
        )

    exam_near = assessment_days_away is not None and assessment_days_away <= 3
    if exam_near:
        reasons.append("assessment_near")
    if days_since_review is not None and days_since_review >= 7:
        reasons.append("stale_material")
    if recent_error_count >= 3:
        reasons.append("repeated_mistakes")

    # ── low mastery: build it before drilling it ───────────────────
    if mastery < MASTERY_BANDS["low"]:
        reasons.append("low_mastery")
        a, b, c = _split(minutes, (0.4, 0.35, 0.25))
        return MethodPlan(
            band="low",
            steps=(
                StudyMethod("learn", "Work through the concept", a),
                StudyMethod("guided", "Guided practice with worked examples", b),
                StudyMethod("independent", "Try problems unaided", c),
            ),
            rationale=(
                "Retrieval practice only works on material you have. At this "
                "level the session has to build the concept first."
            ),
            reason_codes=tuple(reasons),
        )

    # ── moderate mastery: retrieval and error review ───────────────
    if mastery < MASTERY_BANDS["moderate"] or confidence < MIN_CONFIDENCE_FOR_ADVANCED:
        if confidence < MIN_CONFIDENCE_FOR_ADVANCED:
            reasons.append("low_confidence_estimate")
        else:
            reasons.append("moderate_mastery")
        a, b, c = _split(minutes, (0.3, 0.45, 0.25))
        return MethodPlan(
            band="moderate",
            steps=(
                StudyMethod("retrieval", "Recall it closed-book first", a),
                StudyMethod("targeted", "Targeted problems on the shaky parts", b),
                StudyMethod("errors", "Review every mistake", c),
            ),
            rationale=(
                "You have the material; what is missing is retrieving it "
                "without prompts. Recall first, then work the gaps it exposes."
            ),
            reason_codes=tuple(reasons),
        )

    # ── high mastery ───────────────────────────────────────────────
    reasons.append("high_mastery")
    if exam_near:
        a, b, c = _split(minutes, (0.25, 0.5, 0.25))
        return MethodPlan(
            band="high",
            steps=(
                StudyMethod("mixed", "Mixed retrieval across topics", a),
                StudyMethod("timed", "Timed practice under exam conditions", b),
                StudyMethod("errors", "Error analysis", c),
            ),
            rationale=(
                "You know this. What is left to train is doing it accurately "
                "under time pressure, and interleaving topics is what an exam "
                "actually asks of you."
            ),
            reason_codes=tuple(reasons),
        )

    a, b = _split(minutes, (0.45, 0.55))
    return MethodPlan(
        band="high",
        steps=(
            StudyMethod("spaced", "Spaced review", a),
            StudyMethod("interleave", "Interleave with related topics", b),
        ),
        rationale=(
            "Strong material with no assessment close by needs maintenance, "
            "not re-teaching — a short spaced pass keeps it from decaying."
        ),
        reason_codes=tuple(reasons),
    )
