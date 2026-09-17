"""Activation funnel and retention cohorts, computed from our own rows.

No vendor and no API key. PostHog (``analytics.py``) no-ops when it is not
configured, and even configured it only ever saw streak events, so it could
not answer the one question every growth decision depends on: of the
students who signed up in a given week, how many were still here a week
later?

Everything below is pure. The glue module reads the database into
:class:`UserFacts` rows; this module turns those rows into numbers, which
keeps every definition testable without a database and reviewable in one
file.

Definitions
-----------
*Active on a day* means the student did something that shows intent to use
the planner on that local date: a streak-qualifying action, a study
session, a saved plan, or an Active session. Merely having a session cookie
does not count.

*Dn retention* is the share of an eligible cohort that was active in a
window starting n days after signup:

* D1  — active on day 1 exactly.
* D7  — active on any of days 7-13.
* D30 — active on any of days 30-59.

Windows rather than single days for D7 and D30 because a student who studies
every Tuesday is retained, and a single-day definition would call them lost
six weeks out of seven. A student is only *eligible* for Dn once their
window has fully closed; counting someone who signed up yesterday as "not
retained at D7" would drag every recent cohort toward zero.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Sequence

__all__ = [
    "UserFacts",
    "RETENTION_WINDOWS",
    "FUNNEL_STEPS",
    "activation_funnel",
    "biggest_leak",
    "cohort_retention",
    "week_start",
]

#: name -> (first day offset, last day offset), inclusive.
RETENTION_WINDOWS: "OrderedDict[str, tuple[int, int]]" = OrderedDict(
    (("d1", (1, 1)), ("d7", (7, 13)), ("d30", (30, 59)))
)

#: The activation path, in the order a student walks it.
FUNNEL_STEPS: tuple[tuple[str, str], ...] = (
    ("signed_up", "Signed up"),
    ("connected", "Connected a school or calendar"),
    ("planned", "Planned something"),
    ("came_back", "Came back on a later day"),
)


@dataclass(frozen=True, slots=True)
class UserFacts:
    """Everything the metrics need to know about one account."""

    user_id: int
    signed_up: date
    connected: bool = False
    planned: bool = False
    active_dates: frozenset[date] = field(default_factory=frozenset)

    def came_back(self) -> bool:
        return any(d > self.signed_up for d in self.active_dates)

    def active_between(self, first: int, last: int) -> bool:
        lo = self.signed_up + timedelta(days=first)
        hi = self.signed_up + timedelta(days=last)
        return any(lo <= d <= hi for d in self.active_dates)


def week_start(day: date) -> date:
    """Monday of the ISO week containing ``day``."""
    return day - timedelta(days=day.weekday())


def activation_funnel(users: Iterable[UserFacts]) -> list[dict]:
    """Counts per funnel step, each step a subset of the one before it.

    Strictly nested on purpose. A student who "came back" without ever
    planning anything is not evidence that planning is optional, it is a
    student whose visit taught us nothing about activation, and letting them
    into a later step makes that step's conversion rate look better than
    the path actually performs.
    """
    rows = list(users)
    passed = {
        "signed_up": rows,
    }
    passed["connected"] = [u for u in passed["signed_up"] if u.connected]
    passed["planned"] = [u for u in passed["connected"] if u.planned]
    passed["came_back"] = [u for u in passed["planned"] if u.came_back()]

    out: list[dict] = []
    top = len(rows)
    previous = top
    for key, label in FUNNEL_STEPS:
        count = len(passed[key])
        out.append({
            "step": key,
            "label": label,
            "count": count,
            "of_signups": _ratio(count, top),
            "of_previous": _ratio(count, previous),
        })
        previous = count
    return out


def biggest_leak(funnel: Sequence[dict]) -> dict | None:
    """The step that loses the largest share of the people who reached it.

    Share rather than absolute count: the first step always loses the most
    people in raw numbers simply because it has the most people, which would
    point every fix at the top of the funnel forever.
    """
    worst: dict | None = None
    for before, after in zip(funnel, funnel[1:]):
        if before["count"] == 0:
            continue
        lost = before["count"] - after["count"]
        share = lost / before["count"]
        if worst is None or share > worst["lost_share"]:
            worst = {
                "from_step": before["step"],
                "to_step": after["step"],
                "label": after["label"],
                "lost": lost,
                "lost_share": round(share, 4),
            }
    return worst


def cohort_retention(users: Iterable[UserFacts], today: date) -> list[dict]:
    """Weekly signup cohorts with D1/D7/D30 retention, newest first.

    A rate is ``None`` when nobody in the cohort is old enough for that
    window yet — "not measurable" and "zero" are different answers and the
    dashboard must be able to tell them apart.
    """
    cohorts: dict[date, list[UserFacts]] = {}
    for u in users:
        cohorts.setdefault(week_start(u.signed_up), []).append(u)

    out: list[dict] = []
    for start in sorted(cohorts, reverse=True):
        members = cohorts[start]
        row: dict = {"cohort": start.isoformat(), "size": len(members)}
        for name, (first, last) in RETENTION_WINDOWS.items():
            eligible = [u for u in members if u.signed_up + timedelta(days=last) < today]
            retained = sum(1 for u in eligible if u.active_between(first, last))
            row[name] = {
                "eligible": len(eligible),
                "retained": retained,
                "rate": _ratio(retained, len(eligible)) if eligible else None,
            }
        out.append(row)
    return out


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0
