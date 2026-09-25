"""The public demo: a sample week run through the real engines.

The landing page and FAQ promise "start with the demo account, see how it
sorts a sample week". This is that week. Nothing on the demo page is
hand-written output: the assignments are fixed sample data, but the ranking
comes from :mod:`intelliplan.intelligence.priority` and the week from
:class:`SchedulingService`, the same code a signed-in student gets.

Dates are relative to today so the demo never shows a stale week.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any

from intelliplan.domain.assignment import Assignment, AssignmentKind, AssignmentStatus
from intelliplan.intelligence import priority as prio
from intelliplan.services.scheduling import (
    SchedulingService,
    StudentContext,
    plan_to_schedule_data,
)

#: A believable high-school week. (id, title, course, kind, days until due,
#: points, estimated minutes)
SAMPLE: tuple[tuple[str, str, str, AssignmentKind, int, float, int], ...] = (
    ("calc-rr", "Related rates problem set", "AP Calculus BC", AssignmentKind.HOMEWORK, 1, 20, 60),
    ("hist-read", "Ch. 12 reading + notes", "US History", AssignmentKind.HOMEWORK, 2, 10, 40),
    ("bio-quiz", "Ch. 7 quiz: cellular respiration", "AP Biology", AssignmentKind.TEST, 3, 50, 45),
    ("phys-ps4", "Problem set #4: kinematics", "Physics", AssignmentKind.HOMEWORK, 4, 25, 75),
    ("span-vocab", "Unit 3 vocab quiz", "Spanish III", AssignmentKind.TEST, 5, 30, 30),
    ("eng-essay", "Argumentative essay", "English 11", AssignmentKind.PROJECT, 6, 100, 240),
    ("bio-lab", "Enzyme lab report", "AP Biology", AssignmentKind.LAB, 8, 40, 120),
)

#: After school on weekdays, late morning to afternoon on weekends.
_WEEKDAY = {"start": "15:30", "end": "21:30"}
_WEEKEND = {"start": "10:00", "end": "16:00"}
AVAILABILITY = {
    **{d: dict(_WEEKDAY) for d in ("Mon", "Tue", "Wed", "Thu", "Fri")},
    **{d: dict(_WEEKEND) for d in ("Sat", "Sun")},
}

COURSE_COLORS = {
    "AP Calculus BC": "#6f8cff", "US History": "#c7894a", "AP Biology": "#4fbf85",
    "Physics": "#9b7ff0", "Spanish III": "#e0a23a", "English 11": "#e5677d",
}

TIER_LABEL = {"critical": "High", "focus": "High", "steady": "Medium", "light": "Low"}


def _assignments(today: date) -> list[Assignment]:
    return [
        Assignment(
            id=i, title=title, course=course, due_date=today + timedelta(days=days),
            kind=kind, status=AssignmentStatus.NOT_STARTED, points_possible=points,
            est_minutes=minutes, source="manual",
        )
        for i, title, course, kind, days, points, minutes in SAMPLE
    ]


def _due_label(due: date, today: date) -> str:
    days = (due - today).days
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due {due:%a}" if days < 7 else f"due {due:%b} {due.day}"


@lru_cache(maxsize=4)
def build(today: date) -> dict[str, Any]:
    """Rank and schedule the sample week as of ``today``. Cached per day."""
    items = _assignments(today)
    ranked = prio.rank(items, prio.PriorityContext.from_assignments(today, items))

    priorities = [{
        "title": a.title,
        "course": a.course,
        "color": COURSE_COLORS.get(a.course, "#888"),
        "score": score.score,
        "label": TIER_LABEL.get(str(getattr(score.tier, "value", score.tier)).lower(), "Medium"),
        "due": _due_label(a.due_date, today),
        "minutes": a.est_minutes,
        "reasons": [c.reason for c in score.rationale if c.weight > 0][:2],
    } for a, score in ranked]

    rows = [{
        "id": a.id, "title": a.title, "course": a.course, "kind": a.kind.value,
        "due_date": a.due_date.isoformat(), "est_minutes": a.est_minutes,
        "priority": score.score, "points_possible": a.points_possible,
    } for a, score in ranked]

    ctx = StudentContext(availability=AVAILABILITY, preferred_time="evening")
    svc = SchedulingService(ctx)
    # Plan from 7 AM so the whole of "today" is available to the demo.
    now = datetime.combine(today, time(7, 0))
    plan = svc.plan(rows, today=today, now=now)
    data = plan_to_schedule_data(plan, availability=AVAILABILITY, preferred_time="evening")

    days = []
    for day in data.get("schedule") or []:
        blocks = [b for b in day.get("blocks") or [] if not b.get("is_break")]
        if not blocks:
            continue
        for b in blocks:
            b["color"] = COURSE_COLORS.get(b.get("course"), "#888")
        days.append({
            "date": day.get("date"),
            "label": datetime.fromisoformat(str(day.get("date"))).strftime("%A"),
            "minutes": sum(int(b.get("duration_minutes") or 0) for b in blocks),
            "blocks": blocks,
        })
    return {
        "priorities": priorities,
        "days": days,
        "total_minutes": sum(d["minutes"] for d in days),
    }
