"""FSRS scheduling for IntelliPlan flashcards.

The study page already had spaced repetition: SM-2, in localStorage, keyed to
one browser. SM-2 dates from 1987 and schedules on an ease factor alone, so a
card you keep forgetting and a card you always get right drift apart slowly
and every review costs about the same. FSRS models memory directly -- each
card carries a *stability* (how long it survives in memory) and a
*difficulty* (how much a review moves that stability) -- and schedules the
next review for the day retention is predicted to fall to a target the
student sets. In practice that is the difference Anki users describe as
"same retention, a third fewer reviews".

The weights are the FSRS-4.5 defaults. They are trainable per student from
their own review log, which is what the paid tier will do with the reviews
this module writes; the defaults are what everyone gets until then.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Grades, in the order the buttons appear.
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
GRADES = (AGAIN, HARD, GOOD, EASY)

#: FSRS-4.5 default weights.
DEFAULT_W = (
    0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031,
    1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272,
    2.8755, 0.4143, 0.5768,
)

#: The forgetting curve FSRS fits. R(t) = (1 + FACTOR * t / S) ** DECAY.
DECAY = -0.5
FACTOR = 19.0 / 81.0

#: Difficulty is clamped to this range throughout.
MIN_DIFFICULTY, MAX_DIFFICULTY = 1.0, 10.0
#: A card is never scheduled further out than this. Ten years is already past
#: the end of school for every student here.
MAX_INTERVAL_DAYS = 3650
#: Below a day, scheduling happens in minutes inside the session instead.
LEARNING_STEPS_MINUTES = (1, 10)
RELEARNING_STEPS_MINUTES = (10,)

STATE_NEW = "new"
STATE_LEARNING = "learning"
STATE_REVIEW = "review"
STATE_RELEARNING = "relearning"


@dataclass(frozen=True)
class CardState:
    """Everything the scheduler needs to know about one card."""

    state: str = STATE_NEW
    stability: float = 0.0
    difficulty: float = 0.0
    reps: int = 0
    lapses: int = 0
    step: int = 0
    last_review: datetime | None = None


@dataclass(frozen=True)
class Scheduled:
    """The result of grading a card."""

    state: str
    stability: float
    difficulty: float
    reps: int
    lapses: int
    step: int
    due: datetime
    interval_days: float
    last_review: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recall after ``elapsed_days``, given stability.

    This is the number the whole system exists to control: the next review is
    placed where this figure falls to the student's target retention.
    """
    if stability <= 0:
        return 0.0
    return float((1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY)


def interval_for_retention(stability: float, target: float) -> float:
    """Days until retrievability decays to ``target``."""
    target = min(max(target, 0.7), 0.99)
    if stability <= 0:
        return 0.0
    return float(stability / FACTOR * (target ** (1 / DECAY) - 1))


def _initial_stability(grade: int, w=DEFAULT_W) -> float:
    return max(0.1, w[grade - 1])


def _initial_difficulty(grade: int, w=DEFAULT_W) -> float:
    """FSRS-4.5 sets initial difficulty linearly in the grade.

    The exponential form belongs to FSRS-5 and pairs with its own weights;
    applied to the 4.5 vector it returns about -5.5 for a Good answer, which
    clamps to the floor and leaves every new card at minimum difficulty.
    """
    return _clamp_difficulty(w[4] - (grade - 3) * w[5])


def _clamp_difficulty(d: float) -> float:
    return min(max(d, MIN_DIFFICULTY), MAX_DIFFICULTY)


def _next_difficulty(difficulty: float, grade: int, w=DEFAULT_W) -> float:
    """Difficulty drifts on every review and is pulled back toward the value
    an Easy answer would have set. Without that mean reversion difficulty
    ratchets upward forever and mature cards never lengthen again."""
    delta = difficulty - w[6] * (grade - 3)
    return _clamp_difficulty(w[7] * _initial_difficulty(EASY, w) + (1 - w[7]) * delta)


def _stability_after_recall(d: float, s: float, r: float, grade: int, w=DEFAULT_W) -> float:
    hard_penalty = w[15] if grade == HARD else 1.0
    easy_bonus = w[16] if grade == EASY else 1.0
    growth = (
        math.exp(w[8])
        * (11 - d)
        * (s ** -w[9])
        * (math.exp((1 - r) * w[10]) - 1)
        * hard_penalty
        * easy_bonus
    )
    return max(0.1, s * (1 + growth))


def _stability_after_lapse(d: float, s: float, r: float, w=DEFAULT_W) -> float:
    return max(
        0.1,
        min(
            w[11] * (d ** -w[12]) * (((s + 1) ** w[13]) - 1) * math.exp((1 - r) * w[14]),
            s,
        ),
    )


def review(card: CardState, grade: int, *, target_retention: float = 0.9,
           now: datetime | None = None, w=DEFAULT_W) -> Scheduled:
    """Grade a card and return its next state and due date.

    Learning and relearning cards move through minute-scale steps first: a
    card seen for the first time and answered Good is not stable for four
    days, and pretending otherwise is how a student ends up with a deck full
    of half-learned material coming back a week later.
    """
    if grade not in GRADES:
        raise ValueError(f"grade must be one of {GRADES}")
    now = now or _now()
    elapsed = 0.0
    if card.last_review:
        elapsed = max(0.0, (now - card.last_review).total_seconds() / 86400.0)

    if card.state == STATE_NEW or card.stability <= 0:
        stability = _initial_stability(grade, w)
        difficulty = _initial_difficulty(grade, w)
    else:
        r = retrievability(elapsed, card.stability)
        difficulty = _next_difficulty(card.difficulty, grade, w)
        if grade == AGAIN:
            stability = _stability_after_lapse(difficulty, card.stability, r, w)
        else:
            stability = _stability_after_recall(difficulty, card.stability, r, grade, w)

    reps = card.reps + 1
    lapses = card.lapses + (1 if grade == AGAIN and card.state == STATE_REVIEW else 0)

    # ── Short steps, before the card is trusted to a real interval ──
    if card.state in (STATE_NEW, STATE_LEARNING):
        steps, next_state, graduated = LEARNING_STEPS_MINUTES, STATE_LEARNING, STATE_REVIEW
    elif card.state == STATE_RELEARNING:
        steps, next_state, graduated = RELEARNING_STEPS_MINUTES, STATE_RELEARNING, STATE_REVIEW
    else:
        steps, next_state, graduated = (), STATE_REVIEW, STATE_REVIEW

    if steps:
        if grade == AGAIN:
            step = 0
        elif grade == EASY:
            step = len(steps)          # Easy skips the remaining steps
        elif grade == HARD:
            step = card.step           # Hard repeats the current one
        else:
            step = card.step + 1
        if step < len(steps):
            due = now + timedelta(minutes=steps[step])
            return Scheduled(next_state, stability, difficulty, reps, lapses, step,
                             due, (due - now).total_seconds() / 86400.0, now)
        # Graduating: fall through to a real interval.
        state = graduated
        step = 0
    else:
        if grade == AGAIN:
            # A forgotten mature card goes back through relearning rather than
            # straight to a multi-day interval it has just proved it cannot hold.
            due = now + timedelta(minutes=RELEARNING_STEPS_MINUTES[0])
            return Scheduled(STATE_RELEARNING, stability, difficulty, reps, lapses, 0,
                             due, (due - now).total_seconds() / 86400.0, now)
        state, step = STATE_REVIEW, 0

    days = interval_for_retention(stability, target_retention)
    if grade == HARD:
        days *= 0.9
    days = min(max(days, 1.0), MAX_INTERVAL_DAYS)
    return Scheduled(state, stability, difficulty, reps, lapses, step,
                     now + timedelta(days=days), days, now)


def preview(card: CardState, *, target_retention: float = 0.9,
            now: datetime | None = None) -> dict[int, float]:
    """Interval in days each button would produce, for the button labels.

    Anki shows this and students rely on it: seeing "Good → 6d" next to
    "Easy → 15d" is what stops them pressing Easy on everything.
    """
    now = now or _now()
    return {g: review(card, g, target_retention=target_retention, now=now).interval_days
            for g in GRADES}
