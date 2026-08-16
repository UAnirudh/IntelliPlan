"""Base effort sizing from assignment metadata.

This module answers the question the estimation model *cannot*: how big is
this specific piece of work, before we know anything about the student?

The distinction matters. :mod:`intelliplan.intelligence.estimation` learns a
multiplicative bias — "this student takes 40% longer than planned on Chemistry
labs" — and applies it to whatever number arrives. That correction is
per-course and per-kind, so it cannot tell two assignments inside the same
course apart. If the number arriving is ``points_possible × 1.5`` for both,
then::

    "Complete problems 12–18"                    (7 problems)
    "Write a 2,000-word researched essay"

get the same estimate whenever they are worth the same points, and no amount
of learning fixes it. Bias correction cannot recover information the base
estimate never had.

So this module reads the metadata the LMS actually gives us — the description,
the submission type, the rubric, the quiz question count — and derives minutes
from the work described. Points are the *fallback*, not the signal.

How the numbers combine
-----------------------
Signals that describe **different work** add up: "read pages 120–145 and
answer questions 1–10" is reading plus problems. Signals that describe **the
same work** take the maximum: a rubric with six criteria and a 1,500-word
requirement are two descriptions of one essay, and adding them double-counts.

Every rate below is a starting prior with a stated derivation. They are not
meant to be right for a particular student — that is exactly what the
estimation model is for. They are meant to be right about *relative size*,
which is the part that has to come from the assignment itself.

Purity
------
No Flask, no ORM, no clock, no network. Text in, minutes out.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from intelliplan.intelligence.estimation import baseline_minutes

__all__ = [
    "SizingSignal",
    "Sizing",
    "size_assignment",
    "size_from_metadata",
    "strip_markup",
]


# ── Rates ─────────────────────────────────────────────────────────────
#
# Each of these is a prior with a reason. Change them here, not at call sites.

#: Minutes per word of *graded* writing, counting the whole job — reading the
#: prompt, research, outlining, drafting, and revision — not typing speed.
#: 0.3 puts a 2,000-word researched essay at ~10 hours, which is the right
#: order of magnitude for secondary-school work.
MINUTES_PER_WORD_WRITING = 0.30
#: Short written answers carry no research or revision overhead, so the same
#: word count is much less work.
MINUTES_PER_WORD_SHORT_ANSWER = 0.12
#: A double-spaced page is ~275 words.
WORDS_PER_PAGE = 275

#: Minutes to read one page of assigned text, without note-taking.
MINUTES_PER_PAGE_READING = 3.5
#: Note-taking roughly doubles reading time, and the instructions usually say
#: so ("read chapter 4 and take notes").
NOTE_TAKING_MULTIPLIER = 2.0
#: Chapters, when no page range is given. A textbook chapter is ~25 pages.
PAGES_PER_CHAPTER = 25

#: Minutes per problem on a problem set. Deliberately mid-range: a vocabulary
#: item is one minute, a multi-step physics problem is ten, and the estimation
#: model is what resolves that per course.
MINUTES_PER_PROBLEM = 4.0
#: Minutes per question when *taking* a quiz (as opposed to studying for it).
MINUTES_PER_QUIZ_QUESTION = 1.5

#: Minutes each rubric criterion implies. A rubric row is a thing the student
#: has to deliberately do, so six rows is a multi-part deliverable.
MINUTES_PER_RUBRIC_ROW = 8.0
#: Rubrics get long for administrative reasons too, so cap their contribution.
MAX_RUBRIC_MINUTES = 60.0

#: Long instructions mean a task with parts. Not a strong signal on its own —
#: it only ever raises a floor, never overrides a real measurement.
INSTRUCTION_WORDS_PER_MINUTE = 12.0
MAX_INSTRUCTION_MINUTES = 45.0

#: Absolute bounds. The upper bound is total work on a task, not a sitting —
#: a term project genuinely is twenty hours, and the planner splits it.
MIN_SIZED_MINUTES = 5
MAX_SIZED_MINUTES = 1200

#: Below this, a page range is a length requirement ("3–5 pages"); above it,
#: it is a range of page numbers ("read pages 120–145").
PAGE_NUMBER_THRESHOLD = 20

#: Signals that measure the work itself, and so may size a task *below* the
#: points/kind prior. Everything else can only raise it.
_STRONG_SIGNALS = frozenset({"word_count", "reading", "problems", "quiz", "time_limit"})

_WRITING_KINDS = frozenset({"project", "essay", "paper"})
_WRITING_WORDS = (
    "essay", "paper", "report", "write", "writing", "reflection", "narrative",
    "analysis", "argument", "thesis", "journal", "review", "summary",
)


# ── Output ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SizingSignal:
    """One piece of evidence about how big the work is.

    ``detail`` is student-facing: "2,000 words", "7 problems". It exists so
    the UI can say *why* an assignment was sized the way it was without
    exposing the arithmetic.
    """

    name: str
    detail: str
    minutes: int


@dataclass(frozen=True, slots=True)
class Sizing:
    """A base estimate with the evidence behind it."""

    minutes: int
    #: ``"metadata"`` when the description or the LMS told us something real,
    #: ``"points"`` when only the grade weight was available, ``"kind"`` when
    #: not even that.
    basis: str
    signals: tuple[SizingSignal, ...] = ()
    #: Natural work boundaries found in the description — problems, rubric
    #: rows. The planner splits at these in preference to splitting at
    #: minute 34.
    subtask_count: int = 0

    @property
    def is_measured(self) -> bool:
        return self.basis == "metadata"

    @property
    def explanation(self) -> str:
        if not self.signals:
            return f"Estimated at {self.minutes} min."
        parts = ", ".join(s.detail for s in self.signals)
        return f"{self.minutes} min, sized from {parts}."


# ── Text handling ─────────────────────────────────────────────────────


def strip_markup(raw: Any) -> str:
    """Canvas descriptions are HTML. Reduce to plain text, collapse space.

    Entities are unescaped *before* tags are stripped, so ``&lt;br&gt;`` in
    a description does not become a tag we then delete along with the text
    around it.
    """
    if not raw:
        return ""
    text = str(raw)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _to_int(text: str) -> int:
    try:
        return int(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _word_count_required(text: str) -> int:
    """Words the student has to produce, from the instructions.

    Ranges ("500–750 words") take the midpoint; a student writing to a range
    lands in the middle of it, and sizing for the ceiling teaches them the
    planner is padded.
    """
    lower = text.lower()
    span = re.search(
        r"(\d[\d,]*)\s*(?:-|–|—|to)\s*(\d[\d,]*)\s*(?:\+\s*)?words?\b", lower
    )
    if span:
        lo, hi = _to_int(span.group(1)), _to_int(span.group(2))
        if lo and hi and hi >= lo:
            return (lo + hi) // 2
    single = re.search(r"(\d[\d,]*)\s*(?:\+|or more)?[\s-]*words?\b", lower)
    if single:
        return _to_int(single.group(1))
    return 0


def _page_signals(text: str) -> tuple[int, int]:
    """``(pages_to_write, pages_to_read)`` from the instructions.

    The same phrase shape means opposite things depending on magnitude and
    context: "3–5 pages" is how much to produce, "pages 120–145" is how much
    to consume. Page *numbers* are large and prefixed by the word; page
    *counts* are small and followed by it.
    """
    lower = text.lower()
    to_read = 0
    to_write = 0

    for m in re.finditer(
        r"(?:pages?|pp?\.)\s*(\d+)\s*(?:-|–|—|to|through)\s*(\d+)", lower
    ):
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if hi > lo and (lo > PAGE_NUMBER_THRESHOLD or hi > PAGE_NUMBER_THRESHOLD):
            to_read = max(to_read, hi - lo + 1)

    for m in re.finditer(r"(\d+)\s*(?:-|–|—|to)\s*(\d+)\s*pages?\b", lower):
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if lo and hi >= lo and hi <= PAGE_NUMBER_THRESHOLD:
            to_write = max(to_write, (lo + hi) // 2)

    if not to_write:
        single = re.search(r"(\d+)[\s-]*pages?\b", lower)
        if single:
            count = _to_int(single.group(1))
            # "page 7" is a location, not a length. A length requirement is
            # phrased as a count and stays small.
            if 0 < count <= PAGE_NUMBER_THRESHOLD and not re.search(
                r"(?:pages?|pp?\.)\s*\d+\s*(?:-|–|—|to|through)", lower
            ):
                to_write = count

    if not to_read:
        chapters = re.findall(r"chapters?\s*(\d+)", lower)
        if chapters and _is_reading(lower):
            to_read = PAGES_PER_CHAPTER * max(1, len(set(chapters)))

    return to_write, to_read


def _is_reading(lower: str) -> bool:
    return bool(re.search(r"\bread(?:ing|s)?\b", lower))


def _wants_notes(lower: str) -> bool:
    return bool(re.search(r"\b(notes?|annotate|annotations?|summar)\w*\b", lower))


def _problem_count(text: str) -> int:
    """How many discrete problems or questions the work contains.

    Ranges are inclusive — "problems 12–18" is seven problems, not six, and
    the off-by-one is the difference between a plan and an argument.
    """
    lower = text.lower()
    best = 0

    for m in re.finditer(
        r"(?:problems?|questions?|exercises?|items?|#)\s*(\d+)\s*"
        r"(?:-|–|—|to|through|thru)\s*(\d+)",
        lower,
    ):
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if hi >= lo and (hi - lo) < 200:
            best = max(best, hi - lo + 1)

    for m in re.finditer(
        r"(\d+)\s*(?:short[\s-]answer\s*)?(?:problems?|questions?|exercises?)\b", lower
    ):
        best = max(best, _to_int(m.group(1)))

    # "#1-15" without the word "problems" in front.
    for m in re.finditer(r"#\s*(\d+)\s*(?:-|–|—|to)\s*(\d+)", lower):
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if hi >= lo and (hi - lo) < 200:
            best = max(best, hi - lo + 1)

    return best if best < 500 else 0


def _looks_like_writing(title: str, kind: str, text: str) -> bool:
    if kind in _WRITING_KINDS:
        return True
    haystack = f"{title} {text[:400]}".lower()
    return any(w in haystack for w in _WRITING_WORDS)


# ── Sizing ────────────────────────────────────────────────────────────


def size_from_metadata(
    title: str = "",
    kind: str = "",
    description: str = "",
    points_possible: float | None = None,
    submission_types: Sequence[str] | str | None = None,
    rubric_rows: int = 0,
    quiz_question_count: int = 0,
    time_limit_minutes: int | None = None,
) -> Sizing:
    """Derive a base estimate from whatever the LMS told us.

    Falls back to :func:`~intelliplan.intelligence.estimation.baseline_minutes`
    when nothing in the metadata describes the work, so this is always safe to
    call — an assignment with an empty description is sized exactly as it was
    before this module existed.
    """
    text = strip_markup(description)
    lower = text.lower()
    kind = (kind or "").strip().lower()
    types = _normalise_types(submission_types)
    signals: list[SizingSignal] = []
    subtasks = 0

    # ── A timed quiz is not an estimate, it is a fact ──────────────────
    if time_limit_minutes and int(time_limit_minutes) > 0:
        minutes = _clip(int(time_limit_minutes))
        return Sizing(
            minutes=minutes,
            basis="metadata",
            signals=(SizingSignal("time_limit", f"a {minutes}-minute time limit", minutes),),
        )

    # ── Work the student produces ─────────────────────────────────────
    produce = 0.0
    words = _word_count_required(text)
    pages_to_write, pages_to_read = _page_signals(text)
    writing = _looks_like_writing(title, kind, text)

    if not words and pages_to_write:
        words = pages_to_write * WORDS_PER_PAGE

    if words:
        rate = MINUTES_PER_WORD_WRITING if writing else MINUTES_PER_WORD_SHORT_ANSWER
        produce = words * rate
        signals.append(
            SizingSignal("word_count", f"{words:,} words", int(round(produce)))
        )

    if rubric_rows > 0:
        rubric_minutes = min(MAX_RUBRIC_MINUTES, rubric_rows * MINUTES_PER_RUBRIC_ROW)
        subtasks = max(subtasks, rubric_rows)
        # Same deliverable as the word count — take the larger view of it, do
        # not stack two descriptions of one essay.
        if rubric_minutes > produce:
            produce = rubric_minutes
            signals = [s for s in signals if s.name != "word_count"]
            signals.append(
                SizingSignal(
                    "rubric",
                    f"a {rubric_rows}-criterion rubric",
                    int(round(rubric_minutes)),
                )
            )

    # ── Work the student consumes or answers ──────────────────────────
    consume = 0.0
    if pages_to_read:
        rate = MINUTES_PER_PAGE_READING
        detail = f"{pages_to_read} pages of reading"
        if _wants_notes(lower):
            rate *= NOTE_TAKING_MULTIPLIER
            detail += " with notes"
        consume += pages_to_read * rate
        signals.append(
            SizingSignal("reading", detail, int(round(pages_to_read * rate)))
        )

    problems = _problem_count(text)
    if problems:
        is_quiz = "online_quiz" in types or kind in ("quiz",)
        rate = MINUTES_PER_QUIZ_QUESTION if is_quiz else MINUTES_PER_PROBLEM
        consume += problems * rate
        subtasks = max(subtasks, problems)
        signals.append(
            SizingSignal("problems", f"{problems} problems", int(round(problems * rate)))
        )
    elif quiz_question_count:
        minutes = quiz_question_count * MINUTES_PER_QUIZ_QUESTION
        consume += minutes
        signals.append(
            SizingSignal(
                "quiz", f"{quiz_question_count} quiz questions", int(round(minutes))
            )
        )

    total = produce + consume

    # ── Instruction complexity: a floor, never an override ─────────────
    instruction_words = len(text.split())
    if instruction_words >= 60:
        floor = min(
            MAX_INSTRUCTION_MINUTES, instruction_words / INSTRUCTION_WORDS_PER_MINUTE
        )
        if floor > total:
            total = floor
            signals.append(
                SizingSignal(
                    "instructions",
                    "multi-part instructions",
                    int(round(floor)),
                )
            )

    if total > 0:
        # Weak evidence may only ever raise the prior, never lower it. A
        # six-row rubric says "this has parts"; it does not say the parts are
        # small, and letting it size a project on its own turned a two-hour
        # presentation into forty minutes. Word counts, page ranges, problem
        # counts and time limits are different — they describe the work
        # directly, and they are allowed to come out below the prior, because
        # "problems 1–4" really is fifteen minutes however many points it
        # carries.
        if not any(s.name in _STRONG_SIGNALS for s in signals):
            prior = float(baseline_minutes(kind, points_possible))
            total = max(total, prior * _weak_uplift(rubric_rows, signals))
        return Sizing(
            minutes=_clip(total),
            basis="metadata",
            signals=tuple(signals),
            subtask_count=min(subtasks, 50),
        )

    # ── Nothing measurable. Fall back to the existing priors. ──────────
    fallback = baseline_minutes(kind, points_possible)
    return Sizing(
        minutes=fallback,
        basis="points" if points_possible else "kind",
        signals=(),
    )


def size_assignment(row: Mapping[str, Any]) -> Sizing:
    """Size a loose assignment dict, whatever spelling of keys it arrived with.

    This is the adapter every call site should use, so that adding a signal
    means editing one function rather than eight.
    """
    return size_from_metadata(
        title=str(row.get("title") or row.get("name") or ""),
        kind=str(row.get("kind") or row.get("type") or ""),
        description=(
            row.get("description")
            or row.get("instructions")
            or row.get("notes")
            or ""
        ),
        points_possible=_float_or_none(row.get("points_possible")),
        submission_types=row.get("submission_types"),
        rubric_rows=_rubric_rows(row.get("rubric")),
        quiz_question_count=_int_or_zero(row.get("question_count")),
        time_limit_minutes=_int_or_zero(row.get("time_limit")) or None,
    )


def _weak_uplift(rubric_rows: int, signals: Sequence[SizingSignal]) -> float:
    """How much weak evidence raises the prior for a task of this kind.

    A rubric does not measure the work, but it does say where in the range
    for its kind this piece sits: a six-criterion presentation is a bigger
    presentation than a bare one. Small per-row, hard-capped, so a
    twenty-row administrative rubric cannot triple an estimate.
    """
    uplift = 1.0
    if rubric_rows > 0:
        uplift = max(uplift, min(1.4, 1.0 + 0.06 * rubric_rows))
    if any(s.name == "instructions" for s in signals):
        uplift = max(uplift, 1.1)
    return uplift


def _normalise_types(value: Sequence[str] | str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    if isinstance(value, str):
        parts = [p.strip().lower() for p in value.replace(",", " ").split()]
    else:
        parts = [str(p).strip().lower() for p in value]
    return frozenset(p for p in parts if p)


def _rubric_rows(rubric: Any) -> int:
    if isinstance(rubric, (list, tuple)):
        return len(rubric)
    if isinstance(rubric, int):
        return max(0, rubric)
    return 0


def _int_or_zero(value: Any) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, out)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(minutes: float) -> int:
    return int(max(MIN_SIZED_MINUTES, min(MAX_SIZED_MINUTES, round(minutes))))
