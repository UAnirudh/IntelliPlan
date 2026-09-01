"""Three things the day allocator could not see.

scheduler_engine already places work well: it orders by slack rather than by
due date, spreads sittings across the days a task has, respects a soft load
ceiling and steers hard work into the student's best measured slot. What it
could not see was three kinds of reality outside its inputs.

**Exams were treated as homework.** A test on Friday arrived as one task due
Friday, so it got one sitting placed a day early. Nobody revises for a unit
test in one sitting the night before, and the research on this is not
subtle: the same total minutes spread over several days produce far better
retention than the same minutes in one block. Exams now generate their own
spaced revision plan.

**Flashcard reviews were invisible.** FSRS knows exactly how many cards fall
due on each of the next seven days, and those reviews are real work. A
planner that budgets a student's Wednesday at 90 free minutes while 140
cards come due that day has already overbooked them.

**An impossible week failed quietly.** Work that would not fit came back as
an ``unplaced`` list, and a count of dropped blocks is not something a
student can act on. Now the shortfall is measured and described: how short
the week is, which days still have room, and what would have to give.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timedelta
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

# ── Exam detection and revision spacing ───────────────────────────

#: Words a student actually types into an assignment title when the thing is
#: an exam. "Review" alone is deliberately absent: "review chapter 4" is
#: homework, and treating it as an exam would triple it.
_EXAM_WORDS = re.compile(
    r"\b(exam|midterm|final|finals|test|quiz|assessment|ap\s+exam|sat|act|"
    r"benchmark|mock|paper\s*[123])\b", re.I)

#: Days before an exam to put revision sittings, longest lead first. Spacing
#: widens with the lead time available: a test a fortnight out gets four
#: passes, one in three days gets two.
_REVISION_LADDER = (10, 6, 3, 1)

#: A revision sitting is shorter than an assignment sitting on purpose --
#: active recall in 30 to 45 minutes beats a two-hour reread, and short
#: sessions are the ones a student actually starts.
_REVISION_MINUTES = 40
_MIN_REVISION_MINUTES = 25


@dataclass
class ExamPlan:
    exam_title: str
    exam_date: _date
    sittings: list[dict[str, Any]] = field(default_factory=list)


def looks_like_exam(task: Mapping[str, Any]) -> bool:
    """True when this task is an exam rather than something to hand in."""
    kind = str(task.get("type") or task.get("category") or "").lower()
    if kind in ("exam", "test", "quiz", "assessment"):
        return True
    title = str(task.get("title") or task.get("assignment") or "")
    return bool(_EXAM_WORDS.search(title))


def revision_plan(task: Mapping[str, Any], exam_date: _date, today: _date,
                  *, sitting_minutes: int = _REVISION_MINUTES) -> list[dict[str, Any]]:
    """Spaced revision sittings for one exam.

    The ladder is filtered to the lead time actually available, so a test
    three days out gets the two passes that fit rather than four crammed
    into one evening. Each sitting carries the day it is meant for as
    ``preferred_date``; the allocator still owns the final placement, but
    without the hint every sitting would compete for the same emptiest day
    and the spacing would be lost.
    """
    lead = (exam_date - today).days
    if lead <= 0:
        return []
    offsets = [d for d in _REVISION_LADDER if 0 < d <= lead]
    if not offsets:
        # Less than a day of lead: one focused pass is all there is room for.
        offsets = [1] if lead >= 1 else []
    if not offsets:
        return []

    title = str(task.get("title") or task.get("assignment") or "Exam")
    course = task.get("course") or task.get("class_name") or ""
    out: list[dict[str, Any]] = []
    total = len(offsets)
    for i, days_before in enumerate(offsets, start=1):
        when = exam_date - timedelta(days=days_before)
        # The last pass before the exam is the shortest: the night before is
        # for retrieval and gaps, not for meeting the material.
        minutes = sitting_minutes if days_before > 1 else max(
            _MIN_REVISION_MINUTES, int(sitting_minutes * 0.75))
        out.append({
            "title": f"Revise for {title} ({i} of {total})",
            "assignment": f"Revise for {title} ({i} of {total})",
            "course": course,
            "est_minutes": minutes,
            "duration_minutes": minutes,
            "difficulty": task.get("difficulty") or "medium",
            "due_date": when.isoformat(),
            "preferred_date": when.isoformat(),
            "is_revision": True,
            "exam_title": title,
            "exam_date": exam_date.isoformat(),
            "what_to_do": _revision_instruction(i, total, title),
        })
    return out


def _revision_instruction(index: int, total: int, title: str) -> str:
    """What the sitting is for. A revision block with no instruction becomes
    an hour of rereading, which is the least effective thing a student can
    do with the time."""
    if index == 1 and total > 1:
        return (f"First pass on {title}: skim the whole unit, then write down "
                "every topic you cannot explain out loud. That list drives the "
                "later sessions.")
    if index == total:
        return (f"Last pass before {title}: closed-book recall only. Work the "
                "problems you got wrong earlier and say the definitions aloud.")
    return (f"Practice pass on {title}: attempt problems and past questions "
            "without notes first, then check and correct.")


def expand_exams(tasks: Sequence[Mapping[str, Any]], today: _date,
                 *, sitting_minutes: int = _REVISION_MINUTES,
                 max_exams: int = 6) -> tuple[list[dict[str, Any]], list[ExamPlan]]:
    """Return ``(tasks + revision sittings, plans)``.

    The exam itself stays in the list: it is a real commitment on a real day
    and the week should show it. What changes is that the preparation now
    exists as scheduled work instead of being left to the student to
    remember at 10pm the night before.
    """
    out = [dict(t) for t in tasks]
    plans: list[ExamPlan] = []
    exams = 0
    for task in tasks:
        if exams >= max_exams or not looks_like_exam(task):
            continue
        when = _parse_date(task.get("due_date") or task.get("due") or task.get("date"))
        if not when:
            continue
        sittings = revision_plan(task, when, today, sitting_minutes=sitting_minutes)
        if not sittings:
            continue
        exams += 1
        out.extend(sittings)
        plans.append(ExamPlan(
            exam_title=str(task.get("title") or task.get("assignment") or "Exam"),
            exam_date=when, sittings=sittings))
    return out, plans


# ── Flashcard load ────────────────────────────────────────────────

#: Seconds a card takes, averaged over easy and hard ones. Anki's own
#: statistics put mature reviews around eight seconds and learning cards
#: nearer twenty; ten is a fair blended figure and errs toward reserving
#: slightly too little rather than eating the student's week.
SECONDS_PER_CARD = 10
#: Never hand more than this share of a day to card review. Beyond it the
#: reservation stops being a courtesy to the student and starts being the
#: reason their essay has nowhere to go.
MAX_REVIEW_SHARE = 0.35


def review_minutes_by_day(due_counts: Mapping[str, int]) -> dict[str, int]:
    """Convert per-day due-card counts into minutes of work."""
    return {day: max(0, int(round(count * SECONDS_PER_CARD / 60)))
            for day, count in (due_counts or {}).items() if count}


def reserve_review_time(capacity: Mapping[str, int],
                        due_counts: Mapping[str, int]) -> tuple[dict[str, int], dict[str, int]]:
    """Take the day's card reviews out of the capacity the allocator sees.

    Returns ``(remaining_capacity, reserved_minutes)``. FSRS already knows
    the due date of every card, so this is a real forecast rather than an
    estimate: the reviews waiting on Thursday are as much a claim on
    Thursday as a lesson is.
    """
    minutes = review_minutes_by_day(due_counts)
    remaining: dict[str, int] = {}
    reserved: dict[str, int] = {}
    for day, free in (capacity or {}).items():
        want = minutes.get(day, 0)
        take = min(want, int(free * MAX_REVIEW_SHARE)) if free > 0 else 0
        reserved[day] = take
        remaining[day] = max(0, free - take)
    return remaining, reserved


# ── Shortfall diagnosis ───────────────────────────────────────────

@dataclass
class Shortfall:
    """What a week that does not fit is actually short of."""

    fits: bool
    demand_minutes: int
    capacity_minutes: int
    missing_minutes: int
    unplaced_titles: list[str]
    roomiest_days: list[tuple[str, int]]
    message: str
    suggestions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fits": self.fits,
            "demand_minutes": self.demand_minutes,
            "capacity_minutes": self.capacity_minutes,
            "missing_minutes": self.missing_minutes,
            "unplaced": self.unplaced_titles,
            "roomiest_days": [{"date": d, "free_minutes": m} for d, m in self.roomiest_days],
            "message": self.message,
            "suggestions": self.suggestions,
        }


def diagnose(tasks: Sequence[Mapping[str, Any]], capacity: Mapping[str, int],
             placed: Mapping[str, Sequence[Mapping[str, Any]]],
             unplaced: Sequence[Mapping[str, Any]]) -> Shortfall:
    """Explain a week that did not fit, in terms a student can act on.

    "3 blocks could not be placed" tells them nothing. "Your week is 2h10m
    short; Sunday has 3h free and nothing on it" tells them what to do.
    """
    demand = sum(int(t.get("est_minutes") or t.get("duration_minutes") or 0) for t in tasks)
    total_capacity = sum(int(v or 0) for v in (capacity or {}).values())
    used = {day: sum(int(b.get("duration_minutes") or 0) for b in blocks)
            for day, blocks in (placed or {}).items()}
    missing = sum(int(b.get("duration_minutes") or b.get("est_minutes") or 0)
                  for b in unplaced or [])

    free_by_day = sorted(
        ((day, max(0, int(capacity.get(day, 0)) - used.get(day, 0)))
         for day in (capacity or {})),
        key=lambda p: -p[1])
    roomiest = [p for p in free_by_day if p[1] >= 30][:3]

    titles = []
    for block in unplaced or []:
        title = str(block.get("parent_title") or block.get("title")
                    or block.get("assignment") or "").strip()
        if title and title not in titles:
            titles.append(title)

    if not unplaced:
        return Shortfall(True, demand, total_capacity, 0, [], roomiest,
                         "Everything fits in the time you have.", [])

    suggestions: list[str] = []
    if roomiest:
        day, free = roomiest[0]
        suggestions.append(
            f"{_weekday(day)} still has {_hm(free)} free — move something there.")
    if total_capacity and missing > total_capacity * 0.25:
        # A quarter of the week missing is not a scheduling problem.
        suggestions.append(
            "This is more work than the week holds. Something has to move to "
            "next week or be cut, not rearranged.")
    else:
        suggestions.append(
            "Add an hour to one evening in Settings, or shorten a task's estimate.")
    if len(titles) == 1:
        suggestions.append(f"“{titles[0]}” is the piece with nowhere to go.")

    message = (f"Your week is {_hm(missing)} short. "
               f"{len(unplaced)} block{'s' if len(unplaced) != 1 else ''} "
               f"could not be placed in your free time.")
    return Shortfall(False, demand, total_capacity, missing, titles, roomiest,
                     message, suggestions)


def _hm(minutes: int) -> str:
    minutes = max(0, int(minutes))
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _weekday(iso: str) -> str:
    try:
        return _date.fromisoformat(iso).strftime("%A")
    except ValueError:
        return iso


def _parse_date(value: Any) -> "_date | None":
    if isinstance(value, _date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None
