"""Ephemeral, deterministic signals from the learner's current message.

The durable learner model deliberately changes slowly and only from evidence.
This module fills the complementary role: it recognizes an explicit request in
*this* message and chooses a safe instructional response for this turn.  It
never stores message text, preferences, diagnoses, or mastery claims.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TurnSignal:
    """A bounded teaching intent, with no raw learner content."""

    kind: str
    guidance: str


_CHECK_WORK = re.compile(
    r"\b(?:check|grade|mark|review|look over|verify)\b.{0,48}\b(?:work|answer|solution|steps?)\b"
    r"|\b(?:is|are|was)\b.{0,36}\b(?:right|correct)\b",
    re.IGNORECASE,
)
_STUCK = re.compile(
    r"\b(?:stuck|lost|confused|don't understand|do not understand|no idea|can'?t figure|help me)\b",
    re.IGNORECASE,
)
_HINT = re.compile(r"\b(?:hint|nudge|clue|don'?t tell me|without (?:the )?answer)\b", re.IGNORECASE)
_PRACTICE = re.compile(
    r"\b(?:quiz|test|practice|drill|flash ?cards?|ask me questions?|retrieval)\b",
    re.IGNORECASE,
)
_ATTEMPT = re.compile(
    r"\b(?:i think|i tried|i got|i used|i calculated|i solved|my answer|because|therefore|so i)\b"
    r"|(?:=|\d)\s*(?:[+\-*/^=]|\d)",
    re.IGNORECASE,
)
_EXPLAIN = re.compile(r"\b(?:explain|teach|what (?:is|does)|why (?:does|is)|how (?:do|does|can))\b", re.IGNORECASE)


_GUIDANCE = {
    'check_work': (
        'The learner explicitly asked to check work. Start from the work they supplied, '
        'confirm any sound reasoning, identify only the first consequential mismatch, and '
        'ask them to repair that step. Do not replace their work with a full solution.'
    ),
    'stuck': (
        'The learner signaled that they are stuck. Acknowledge it briefly, reduce the task '
        'to the smallest useful next action or offer two concrete choices, then ask them to try. '
        'Do not simply repeat the same explanation.'
    ),
    'hint': (
        'The learner asked for a hint. Give one progressively revealing hint for the next step, '
        'not the answer, and wait for their attempt before adding another hint.'
    ),
    'practice': (
        'The learner asked to practice. Begin with a short retrieval question before explaining, '
        'give specific feedback after the response, and adjust the next item from that response. '
        'A generated question is practice, not proof of mastery.'
    ),
    'attempt': (
        'The learner supplied an attempt or reasoning. Engage with that exact reasoning first: '
        'ask one question that checks the pivotal step or lets them extend it. Do not restart '
        'with a generic explanation or infer durable mastery from this one attempt.'
    ),
    'explanation': (
        'The learner requested an explanation. Give only the minimum factual clarification needed, '
        'then ask one short recall or application question before moving on.'
    ),
    'diagnose': (
        'No explicit learning request was detected. Ask one answerable diagnostic question before '
        'assuming background knowledge, pace, or confidence.'
    ),
}


def classify_turn(message: str) -> TurnSignal:
    """Classify an explicit immediate need without retaining the message.

    Priority matters: checking supplied work should not be converted into a
    generic quiz, and a direct statement of being stuck needs more scaffolding
    than a standard explanation request.
    """
    text = str(message or '').strip()
    if _CHECK_WORK.search(text):
        kind = 'check_work'
    elif _STUCK.search(text):
        kind = 'stuck'
    elif _HINT.search(text):
        kind = 'hint'
    elif _PRACTICE.search(text):
        kind = 'practice'
    elif _ATTEMPT.search(text):
        kind = 'attempt'
    elif _EXPLAIN.search(text):
        kind = 'explanation'
    else:
        kind = 'diagnose'
    return TurnSignal(kind=kind, guidance=_GUIDANCE[kind])
