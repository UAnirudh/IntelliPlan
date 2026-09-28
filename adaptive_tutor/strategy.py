"""Deterministic teaching decisions over the tutor's stored learning evidence.

These are prompts for an instructional move, not a claim that an AI score
proves mastery. A model may explain the move, but cannot silently change the
learner's score. Unrelated history must not redirect a student's current ask.
"""

from __future__ import annotations

import re
from typing import Any


def learning_stage(grade: Any) -> str:
    value = str(grade or "").strip().lower()
    if value in {"k", "kg", "kindergarten"}:
        return "early"
    if "college" in value or "university" in value or "undergraduate" in value:
        return "college"
    match = re.search(r"\b(1[0-2]|[1-9])(?:st|nd|rd|th)?\b", value)
    if not match:
        return "unspecified"
    number = int(match.group(1))
    if number <= 2:
        return "early"
    if number <= 8:
        return "middle"
    return "high_school"


STAGE_GUIDANCE = {
    "early": "Use short spoken-friendly sentences, concrete objects, and one small reading, writing, or number task at a time. Invite the learner to try before revealing a solution.",
    "middle": "Use a concrete example, then ask the learner to explain a step in their own words. Increase complexity only after an independent check.",
    "high_school": "Connect methods to evidence, algebraic structure, and counterexamples. Ask the learner to justify a claim, then test it in a new case.",
    "college": "Ask for assumptions, evidence quality, and a competing interpretation. Give discipline-appropriate rigor and check transfer to a novel problem.",
    "unspecified": "Begin with one brief diagnostic question before choosing difficulty; do not infer ability from age or grade alone.",
}


def teaching_move(context: dict[str, Any], subject: str = "General",
                  focus_text: str = "") -> dict[str, str | None]:
    """Choose a move using evidence from the subject the learner actually asked about."""
    requested = (subject or "General").strip().casefold()
    if requested == "general":
        return {"kind": "diagnose", "topic": None}
    rows = [
        row for row in context.get("mastery") or []
        if row.get("source") == "scored_check"
        and str(row.get("subject") or "").strip().casefold() == requested
    ]
    if focus_text:
        body = focus_text.casefold()
        body_words = set(re.findall(r"[a-z0-9]+", body))
        rows = [row for row in rows if (
            str(row.get('topic') or '').casefold() in body
            or len(body_words & set(re.findall(r"[a-z0-9]+", str(row.get('topic') or '').casefold()))) >= 2
        )]
    if not rows:
        return {"kind": "diagnose", "topic": None}
    row = min(rows, key=lambda item: (
        float(item.get("mastery_score") or 0),
        -int(item.get("total_attempts") or 0),
    ))
    topic = re.sub(r"[\r\n]+", " ", str(row.get("topic") or "")).strip()[:100] or None
    attempts = int(row.get("total_attempts") or 0)
    confidence = float(row.get("confidence_level") or 0)
    score = float(row.get("mastery_score") or 0)
    if attempts < 3 or confidence < 50:
        return {"kind": "diagnose", "topic": topic}
    if score < 80:
        return {"kind": "repair", "topic": topic}
    return {"kind": "transfer", "topic": topic}


MOVE_GUIDANCE = {
    "diagnose": "Evidence is thin. Ask one answerable question that reveals the learner's current reasoning; do not label them weak or claim mastery.",
    "repair": "Use a smaller example or another representation for the relevant concept. Let the learner perform one independent step, then respond to that step.",
    "transfer": "Offer one unfamiliar example or counterexample that tests whether the idea transfers beyond the practiced form.",
}
