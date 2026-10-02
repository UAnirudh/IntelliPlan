"""Grade Pulse: turn an LMS sync into alerts that say what to do next.

Every StudentVUE what-if tool and every LMS app already tells a student
their score. None of them tell the student what the score *means* for the
course, which is the question they actually have the moment they see a 78:
"what do I need on the final now?" This module answers it, and answers the
sibling question for new work: "where does this fit in my week?"

Pure, like the rest of ``intelliplan/services``. It takes the gradebook and
assignment lists the LMS helpers already return, a previous snapshot, and a
clock; it returns events and a new snapshot. ``grade_pulse_glue.py`` owns
the database and the hooks.

Why a snapshot diff, not LMS webhooks
-------------------------------------
Of the sources IntelliPlan reads (Canvas, StudentVUE, Schoology, Classroom,
Blackboard, Moodle, the extension scraper, CSV), only Canvas can push, and
only with an admin-registered developer key. A diff of "what we saw last
time" against "what we see now" works identically for every source,
including the ones that are just a scraped HTML table.

The snapshot holds hashes, not titles
-------------------------------------
To recognise a grade or an assignment as *new* we only need to know whether
we have seen it before -- not what it was called. Keys are SHA-256 prefixes of course + assignment identity, and the only plaintext
values kept are percentages and due dates. That is not anonymisation -- a
course name is guessable, so its hash is too -- but it keeps titles and
teacher-written text out of one more table, which costs nothing.

Never alert on first sight
--------------------------
The first time a source is observed for a student, everything in it is
"new". Alerting on that would greet every student who connects Canvas with
forty notifications about grades they already know. First sight of a source
-- and first sight of a course within a known source -- sets a baseline
silently.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "LETTER_BANDS",
    "letter_for",
    "final_needed",
    "FinalOutlook",
    "final_outlook",
    "GradeAlert",
    "diff_gradebook",
    "diff_course_grades",
    "grade_event_contexts",
    "NewAssignment",
    "diff_assignments",
    "Placement",
    "slot_assignment",
    "assignment_event_context",
]

#: Snapshot format version. Bumped if the shape changes, so an old row is
#: treated as "no baseline" rather than misread.
SNAPSHOT_VERSION = 1

#: Letter floors. Identical to ``canvas_helper._letter_from_pct``, which is
#: what the /grades page shows -- an alert that disagreed with the page
#: about which letter 89.9 is would be its own bug report.
LETTER_BANDS: tuple[tuple[float, str], ...] = (
    (93.0, "A"), (90.0, "A-"), (87.0, "B+"), (83.0, "B"), (80.0, "B-"),
    (77.0, "C+"), (73.0, "C"), (70.0, "C-"), (67.0, "D+"), (63.0, "D"),
    (60.0, "D-"), (0.0, "F"),
)

#: A course drop at least this large (in percentage points) is worth an
#: interruption. Smaller wobbles are what a gradebook does all term, and
#: buzzing for each one is how notifications get muted.
DROP_THRESHOLD = 1.0

#: With no course percentage to compare against (some scrapers only carry
#: the assignment), a score below this is the signal instead.
LOW_SCORE = 75.0

#: The weight assumed for a final when the gradebook does not say. Twenty
#: percent is the figure the public final-grade calculator uses in its own
#: worked example, and the alert says out loud that it is an assumption.
DEFAULT_FINAL_WEIGHT = 0.20

#: More changes than this in one sync become one digest message.
DIGEST_AFTER = 3

_FINAL_WORDS = re.compile(r"\b(final|finals|final exam|semester exam|exam)\b", re.I)


# ── Letters and the final-grade formula ───────────────────────────────


def letter_for(pct: float | None) -> str:
    if pct is None:
        return ""
    for floor, letter in LETTER_BANDS:
        if pct >= floor:
            return letter
    return "F"


def floor_for(letter: str) -> float | None:
    for floor, name in LETTER_BANDS:
        if name == letter:
            return floor
    return None


def final_needed(current: float, target: float, final_weight: float) -> float:
    """Score needed on the final, in percent.

    The same formula ``tools/final-grade-calculator`` shows students:
    ``required = (target - current * (1 - w)) / w`` with ``w`` as a fraction.
    Kept in one Python place so the alert and the calculator cannot drift.
    """
    if final_weight <= 0:
        raise ValueError("final weight must be positive")
    return (target - current * (1.0 - final_weight)) / final_weight


@dataclass(frozen=True, slots=True)
class FinalOutlook:
    """What the final has to be for the student to hold their letter."""

    outlook: str                   # need | locked | unreachable
    target_letter: str
    target_verb: str               # keep | get back to
    need_pct: float | None = None
    fallback_letter: str = ""
    fallback_need_pct: float | None = None
    final_weight: float = DEFAULT_FINAL_WEIGHT
    #: How the weight was found: categories | points | assumed.
    method: str = "assumed"

    def as_context(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "outlook": self.outlook,
            "target_letter": self.target_letter,
            "target_verb": self.target_verb,
        }
        if self.need_pct is not None:
            ctx["need_pct"] = self.need_pct
        if self.fallback_letter:
            ctx["fallback_letter"] = self.fallback_letter
            ctx["fallback_need_pct"] = self.fallback_need_pct
        if self.method == "assumed":
            ctx["assumed_final_weight"] = round(self.final_weight * 100)
        return ctx


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round_up(value: float) -> float:
    """Needed scores round *up*: telling someone 90.6 rounds to 91 is honest;
    telling them 90 is a promise the math does not keep."""
    return float(math.ceil(value - 1e-9))


def _graded_pct(assignment: Mapping[str, Any]) -> float | None:
    """Percent on one assignment, or None when it does not count (yet)."""
    if assignment.get("excused") or assignment.get("omit_from_final_grade"):
        return None
    if "graded" in assignment and not assignment.get("graded"):
        return None
    earned = _num(assignment.get("points_earned"))
    possible = _num(assignment.get("points_possible"))
    if earned is None or not possible or possible <= 0:
        return None
    return earned / possible * 100.0


def _final_weight(course: Mapping[str, Any]) -> tuple[float, str] | None:
    """The final's share of the course, as a fraction, and how it was found.

    Returns None when the final has already been graded -- then there is no
    final left to plan around and the alert should not invent one.
    """
    assignments = [a for a in (course.get("assignments") or []) if isinstance(a, Mapping)]

    # 1. Weighted categories with a category of its own for the final. The
    #    current grade only averages categories that have graded work, so the
    #    final's fraction is its weight over (graded weights + its weight).
    if str(course.get("weighting") or "groups") != "points":
        weights: dict[str, float] = {}
        for group in course.get("groups") or []:
            if isinstance(group, Mapping) and _num(group.get("weight")):
                weights[str(group.get("name") or "")] = float(group["weight"])
        graded_weight = 0.0
        graded_names: set[str] = set()
        for cat in course.get("categories") or []:
            if not isinstance(cat, Mapping):
                continue
            name = str(cat.get("type") or cat.get("name") or "")
            weight = _num(cat.get("weight")) or 0.0
            weights.setdefault(name, weight)
            if weight > 0 and (_num(cat.get("points_possible")) or 0) > 0:
                graded_weight += weight
                graded_names.add(name)
        final_cats = [n for n, w in weights.items() if w > 0 and _FINAL_WORDS.search(n)]
        if final_cats and graded_weight > 0:
            name = final_cats[0]
            if name in graded_names:
                return None  # the final category already has a score in it
            w = weights[name]
            return w / (graded_weight + w), "categories"

    # 2. Points-based: an ungraded assignment that is plainly the final.
    finals = [a for a in assignments if _FINAL_WORDS.search(str(a.get("title") or ""))]
    if finals:
        if any(_graded_pct(a) is not None for a in finals):
            return None
        possible_left = _num(finals[0].get("points_possible")) or 0.0
        graded_possible = sum(
            _num(a.get("points_possible")) or 0.0
            for a in assignments if _graded_pct(a) is not None
        )
        if possible_left > 0 and graded_possible > 0:
            return possible_left / (graded_possible + possible_left), "points"

    # 3. Nothing says. Assume, and say so.
    return DEFAULT_FINAL_WEIGHT, "assumed"


def final_outlook(
    current: float | None,
    *,
    held_letter: str = "",
    course: Mapping[str, Any] | None = None,
) -> FinalOutlook | None:
    """What the final has to be to hold ``held_letter`` (the letter the
    student had before this grade), given a ``current`` course percentage.

    Falls back a letter at a time when the held one is out of reach, so the
    alert always ends on something the student can still do.
    """
    if current is None:
        return None
    found = _final_weight(course or {})
    if found is None:
        return None
    weight, method = found
    target_letter = held_letter or letter_for(current)
    target = floor_for(target_letter)
    if target is None or target_letter == "F":
        return None
    verb = "keep" if current >= target else "get back to"
    need = final_needed(current, target, weight)
    if need <= 0:
        return FinalOutlook("locked", target_letter, verb, None,
                            final_weight=weight, method=method)
    if need <= 100:
        return FinalOutlook("need", target_letter, verb, _round_up(need),
                            final_weight=weight, method=method)
    # Out of reach: find the best letter that is not.
    fallback, fallback_need = "", None
    seen = False
    for floor, letter in LETTER_BANDS:
        if letter == target_letter:
            seen = True
            continue
        if not seen or letter == "F":
            continue
        n = final_needed(current, floor, weight)
        if n <= 100:
            fallback, fallback_need = letter, max(0.0, _round_up(n))
            break
    return FinalOutlook("unreachable", target_letter, verb, None,
                        fallback_letter=fallback, fallback_need_pct=fallback_need,
                        final_weight=weight, method=method)


# ── Snapshot keys ─────────────────────────────────────────────────────


def _h(*parts: Any) -> str:
    raw = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def course_key(name: str) -> str:
    return _h("course", name)


def assignment_key(course: str, assignment: Mapping[str, Any]) -> str:
    """Stable identity for one gradebook row. The LMS id when there is one;
    title + due date otherwise (StudentVUE has no ids)."""
    ident = assignment.get("id")
    if ident not in (None, ""):
        return _h("a", course, "id", ident)
    return _h("a", course, assignment.get("title"), assignment.get("due_date"))


def _course_name(course: Mapping[str, Any]) -> str:
    return str(course.get("course") or course.get("class_name") or course.get("name") or "").strip()


def _course_pct(course: Mapping[str, Any]) -> float | None:
    return _num(course.get("percentage"))


def is_baseline(snapshot: Mapping[str, Any] | None) -> bool:
    return bool(snapshot) and snapshot.get("v") == SNAPSHOT_VERSION


# ── Grades ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GradeAlert:
    """One grade worth telling the student about."""

    course: str
    item: str                       # "" for a course-level change
    score_pct: float | None
    old_course_pct: float | None
    new_course_pct: float | None
    letter: str
    dedupe_key: str
    outlook: FinalOutlook | None = None
    #: How much the course moved. Used to order a digest.
    drop: float = 0.0

    def context(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "course": self.course,
            "item": self.item,
            "score_pct": None if self.score_pct is None else round(self.score_pct, 1),
            "old_course_pct": None if self.old_course_pct is None else round(self.old_course_pct, 1),
            "new_course_pct": None if self.new_course_pct is None else round(self.new_course_pct, 1),
            "letter": self.letter,
        }
        if self.outlook is not None:
            ctx.update(self.outlook.as_context())
        return ctx


def _significant(old_course: float | None, new_course: float | None, score: float | None) -> bool:
    """The notification-fatigue guardrail. See DROP_THRESHOLD."""
    if old_course is not None and new_course is not None:
        if old_course - new_course >= DROP_THRESHOLD:
            return True
        return letter_for(old_course) != letter_for(new_course)
    return score is not None and score < LOW_SCORE


def diff_gradebook(
    previous: Mapping[str, Any] | None,
    courses: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> tuple[list[GradeAlert], dict[str, Any]]:
    """Compare a gradebook-detail payload with the last snapshot.

    ``courses`` is what ``canvas_helper.get_gradebook_detail`` and
    ``studentvue_helper.get_gradebook_detail`` return. Returns the alerts
    worth sending and the snapshot to store in place of ``previous``.
    """
    baseline = is_baseline(previous)
    prev_courses: Mapping[str, Any] = (previous or {}).get("courses") or {}
    snap_courses: dict[str, Any] = {}
    alerts: list[GradeAlert] = []

    for course in courses or []:
        if not isinstance(course, Mapping):
            continue
        name = _course_name(course)
        if not name:
            continue
        ckey = course_key(name)
        new_pct = _course_pct(course)
        graded: dict[str, float] = {}
        for a in course.get("assignments") or []:
            if not isinstance(a, Mapping):
                continue
            pct = _graded_pct(a)
            if pct is not None:
                graded[assignment_key(name, a)] = round(pct, 2)
        snap_courses[ckey] = {"pct": new_pct, "graded": graded}

        before = prev_courses.get(ckey) if baseline else None
        if not isinstance(before, Mapping):
            continue  # first sight of this course: baseline, no alerts
        old_pct = _num(before.get("pct"))
        seen: Mapping[str, Any] = before.get("graded") or {}
        fresh = []
        for a in course.get("assignments") or []:
            if not isinstance(a, Mapping):
                continue
            key = assignment_key(name, a)
            pct = graded.get(key)
            if pct is None:
                continue
            was = _num(seen.get(key))
            if was is not None and abs(was - pct) < 0.05:
                continue
            fresh.append((a, key, pct))
        if not fresh:
            continue
        if not _significant(old_pct, new_pct, min(p for _, _, p in fresh)):
            continue
        outlook = final_outlook(new_pct, held_letter=letter_for(old_pct) if old_pct is not None else "",
                                course=course)
        # Several scores in one course in one sync are one course movement;
        # name the lowest, which is the one that moved it.
        a, key, pct = min(fresh, key=lambda t: t[2])
        alerts.append(GradeAlert(
            course=name,
            item=str(a.get("title") or "").strip()[:120],
            score_pct=pct,
            old_course_pct=old_pct,
            new_course_pct=new_pct,
            letter=str(course.get("letter") or letter_for(new_pct)),
            dedupe_key=f"{source}:{ckey}:{key}:{pct:.1f}",
            outlook=outlook,
            drop=(old_pct - new_pct) if old_pct is not None and new_pct is not None else 0.0,
        ))

    return alerts, {"v": SNAPSHOT_VERSION, "courses": snap_courses}


def diff_course_grades(
    previous: Mapping[str, Any] | None,
    grades: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> tuple[list[GradeAlert], dict[str, Any]]:
    """The same diff for sources that only report a course percentage
    (Schoology, the CSV/paste/extension importers, /grades/data).

    There is no assignment to name, so the alert names the movement and the
    final is assumed -- and the message says it is assumed.
    """
    baseline = is_baseline(previous)
    prev_courses: Mapping[str, Any] = (previous or {}).get("courses") or {}
    snap_courses: dict[str, Any] = {}
    alerts: list[GradeAlert] = []
    for row in grades or []:
        if not isinstance(row, Mapping):
            continue
        name = _course_name(row)
        pct = _course_pct(row)
        if not name or pct is None:
            continue
        ckey = course_key(name)
        snap_courses[ckey] = {"pct": pct}
        before = prev_courses.get(ckey) if baseline else None
        if not isinstance(before, Mapping):
            continue
        old = _num(before.get("pct"))
        if old is None or abs(old - pct) < 0.05:
            continue
        if not _significant(old, pct, None):
            continue
        alerts.append(GradeAlert(
            course=name, item="", score_pct=None,
            old_course_pct=old, new_course_pct=pct,
            letter=str(row.get("letter") or row.get("grade") or letter_for(pct))[:4],
            dedupe_key=f"{source}:{ckey}:course:{pct:.1f}",
            outlook=final_outlook(pct, held_letter=letter_for(old)),
            drop=old - pct,
        ))
    return alerts, {"v": SNAPSHOT_VERSION, "courses": snap_courses}


def _digest_line(alert: GradeAlert) -> str:
    from intelliplan.notifications.events import _final_clause, _pct

    head = f"{alert.course} {alert.item} {_pct(alert.score_pct)}%." if alert.item else \
        f"{alert.course} {_pct(alert.old_course_pct)} → {_pct(alert.new_course_pct)}."
    clause = _final_clause(alert.outlook.as_context()) if alert.outlook else ""
    return f"{head} {clause}".strip()


def grade_event_contexts(alerts: Sequence[GradeAlert]) -> list[tuple[str, dict[str, Any]]]:
    """``(dedupe_key, context)`` pairs, collapsed to one digest when many.

    The digest's key is derived from every grade it covers, so a sync that
    is replayed produces the same key and the outbox drops the duplicate.
    """
    if len(alerts) <= DIGEST_AFTER:
        return [(a.dedupe_key, a.context()) for a in alerts]
    ordered = sorted(alerts, key=lambda a: -a.drop)
    key = "digest:" + _h(*sorted(a.dedupe_key for a in alerts))
    return [(key, {"count": len(alerts), "items": [_digest_line(a) for a in ordered[:2]]})]


# ── New assignments ───────────────────────────────────────────────────

#: Sources that are the student's own typing. A student who just added
#: "read ch. 4" does not need to be told about it.
SELF_AUTHORED_SOURCES = frozenset({"manual", "csv", "paste", "custom", "quick_add", "notion"})

#: Keys whose due date is this far past are forgotten, so the snapshot does
#: not grow by a term's worth of assignments every term.
FORGET_AFTER_DAYS = 45


@dataclass(frozen=True, slots=True)
class NewAssignment:
    key: str
    title: str
    course: str
    due: date
    minutes: int
    source: str
    priority: str = "Medium"


def _parse_due(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _source_of(task: Mapping[str, Any]) -> str:
    src = str(task.get("source") or "").strip().lower()
    # "studentvue_missing" is the same assignment list seen through a
    # different endpoint; one baseline covers both.
    return src.split("_missing")[0] or "lms"


def _minutes(task: Mapping[str, Any]) -> int:
    for key in ("estimated_time", "est_minutes", "estimated_minutes", "duration_minutes"):
        value = _num(task.get(key))
        if value and value > 0:
            return int(max(15, min(240, value)))
    return 45


def diff_assignments(
    previous: Mapping[str, Any] | None,
    tasks: Iterable[Mapping[str, Any]],
    *,
    today: date,
) -> tuple[list[NewAssignment], dict[str, Any]]:
    """Find assignments not seen before, per source.

    Merges rather than replaces: two observers (the dashboard's list and the
    Command Center's) may each see a subset, and an assignment one of them
    saw must not look new to the other.
    """
    prev_sources: Mapping[str, Any] = (
        ((previous or {}).get("sources") or {}) if is_baseline(previous) else {}
    )
    sources: dict[str, dict[str, str]] = {
        s: dict(v) for s, v in prev_sources.items() if isinstance(v, Mapping)
    }
    fresh: list[NewAssignment] = []
    first_sight: set[str] = set()
    for task in tasks or []:
        if not isinstance(task, Mapping):
            continue
        src = _source_of(task)
        if src in SELF_AUTHORED_SOURCES:
            continue
        title = str(task.get("title") or "").strip()
        if not title:
            continue
        course = str(task.get("course") or "").strip()
        due = _parse_due(task.get("due_date"))
        key = _h("t", course, title, due.isoformat() if due else "")
        if src not in sources:
            sources[src] = {}
            first_sight.add(src)
        bucket = sources[src]
        if key in bucket:
            continue
        bucket[key] = due.isoformat() if due else ""
        if src in first_sight or due is None or due < today:
            continue  # baseline, undated, or already past: nothing to plan
        fresh.append(NewAssignment(
            key=key, title=title[:200], course=course[:120], due=due,
            minutes=_minutes(task), source=src,
            priority=str(task.get("priority") or "Medium"),
        ))
    cutoff = (today - timedelta(days=FORGET_AFTER_DAYS)).isoformat()
    for bucket in sources.values():
        for key in [k for k, d in bucket.items() if d and d < cutoff]:
            del bucket[key]
    return fresh, {"v": SNAPSHOT_VERSION, "sources": sources}


# ── Slotting new work into the saved plan ─────────────────────────────

#: Longest single sitting the slotter creates. Matches the scheduler's own
#: default focus block closely enough that an auto-added block does not
#: look out of place next to the ones the planner made.
SITTING_MAX = 60
MAX_SITTINGS = 4
#: Breathing room kept around existing blocks.
GAP_MINUTES = 5
#: How far ahead a new assignment may be planned.
HORIZON_DAYS = 14


@dataclass(frozen=True, slots=True)
class Placement:
    sittings: tuple[tuple[datetime, datetime], ...] = ()
    reason: str = ""                   # "" when placed, else why not

    @property
    def placed(self) -> bool:
        return bool(self.sittings)


def _clock(value: Any):
    try:
        return datetime.fromisoformat(str(value)).time()
    except (TypeError, ValueError):
        return None


def _busy(day: date, blocks: Iterable[Mapping[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Existing blocks on ``day`` as intervals.

    Only the clock part of start_iso/end_iso is trusted and re-anchored on
    the day: humanize_schedule() stamps every day's ISO times with the date
    the plan was generated (see App._schedule_to_ics).
    """
    out = []
    for b in blocks:
        if not isinstance(b, Mapping) or b.get("unplaced"):
            continue
        start_t, end_t = _clock(b.get("start_iso")), _clock(b.get("end_iso"))
        if start_t is None:
            continue
        start = datetime.combine(day, start_t)
        end = datetime.combine(day, end_t) if end_t else start + timedelta(
            minutes=int(_num(b.get("duration_minutes")) or 30))
        if end <= start:
            end += timedelta(days=1)
        out.append((start - timedelta(minutes=GAP_MINUTES), end + timedelta(minutes=GAP_MINUTES)))
    return sorted(out)


def _first_gap(windows, busy, minutes: int) -> tuple[datetime, datetime] | None:
    need = timedelta(minutes=minutes)
    for w in windows:
        cursor = w.start
        for b_start, b_end in busy:
            if b_end <= cursor or b_start >= w.end:
                continue
            if b_start - cursor >= need:
                return cursor, cursor + need
            cursor = max(cursor, b_end)
        if w.end - cursor >= need:
            return cursor, cursor + need
    return None


def _sittings(minutes: int) -> list[int]:
    count = min(MAX_SITTINGS, max(1, math.ceil(minutes / SITTING_MAX)))
    base, extra = divmod(minutes, count)
    return [base + (1 if i < extra else 0) for i in range(count)]


def _already_planned(schedule: Sequence[Mapping[str, Any]], title: str) -> bool:
    wanted = title.strip().lower()
    for day in schedule:
        for b in day.get("blocks") or []:
            for field_name in ("assignment", "parent_title"):
                if str(b.get(field_name) or "").strip().lower() == wanted:
                    return True
    return False


def _fmt(dt: datetime) -> str:
    return dt.strftime("%I:%M").lstrip("0")


def slot_assignment(
    schedule_data: dict[str, Any] | None,
    task: NewAssignment,
    *,
    windows_for: Callable[[date], Sequence[Any]],
    today: date,
    block_factory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Placement:
    """Put ``task`` into free time in the saved plan, in place.

    One sitting per day, earliest first, never on top of an existing block,
    and never after the due date (work due today may use today). Days the
    plan does not cover yet are added. ``windows_for(day)`` returns the
    student's free windows (objects with ``start``/``end`` datetimes) --
    ``scheduler_engine.windows_for_date`` in the app.
    """
    if not isinstance(schedule_data, dict) or not isinstance(schedule_data.get("schedule"), list):
        return Placement(reason="no_plan")
    schedule: list[dict[str, Any]] = schedule_data["schedule"]
    if _already_planned(schedule, task.title):
        return Placement(reason="already_planned")
    if task.due < today:
        return Placement(reason="past_due")

    last = min(task.due - timedelta(days=1), today + timedelta(days=HORIZON_DAYS))
    days = []
    cursor = today
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    if not days:
        days = [today]  # due today: today is the only day there is

    pending = _sittings(task.minutes)
    placed: list[tuple[datetime, datetime, dict[str, Any]]] = []
    by_date = {str(d.get("date") or "")[:10]: d for d in schedule if isinstance(d, dict)}
    for day in days:
        if not pending:
            break
        try:
            windows = list(windows_for(day) or [])
        except Exception:
            windows = []
        if not windows:
            continue
        entry = by_date.get(day.isoformat())
        gap = _first_gap(windows, _busy(day, (entry or {}).get("blocks") or []), pending[0])
        if gap is None:
            continue
        start, end = gap
        part = len(placed) + 1
        total = len(pending) + len(placed)
        block = {
            "assignment": task.title,
            "parent_title": task.title,
            "course": task.course,
            "duration_minutes": pending[0],
            "time_slot": f"{_fmt(start)} {start:%p} - {_fmt(end)} {end:%p}",
            "start_iso": start.isoformat(),
            "end_iso": end.isoformat(),
            "due_date": task.due.isoformat(),
            "priority": task.priority,
            # Marks the block as Grade Pulse's, so the UI and a later replan
            # can tell it was added for the student rather than by them.
            "auto_added": "new_assignment",
            "notes": "Added when this assignment appeared in your class.",
        }
        if total > 1:
            block["assignment"] = f"{task.title} (part {part} of {total})"
        if block_factory is not None:
            block = block_factory(block)
        if entry is None:
            entry = {"date": day.isoformat(), "day_name": day.strftime("%A"),
                     "blocks": [], "total_hours": 0}
            schedule.append(entry)
            by_date[day.isoformat()] = entry
        entry.setdefault("blocks", []).append(block)
        entry["blocks"].sort(key=lambda b: str(_clock(b.get("start_iso")) or "99"))
        try:
            entry["total_hours"] = round(float(entry.get("total_hours") or 0) + pending[0] / 60, 2)
        except (TypeError, ValueError):
            pass
        placed.append((start, end, block))
        pending.pop(0)

    if not placed:
        return Placement(reason="no_room")
    schedule.sort(key=lambda d: str(d.get("date") or ""))
    return Placement(sittings=tuple((s, e) for s, e, _ in placed))


def due_label(due: date, today: date) -> str:
    if due == today:
        return "today"
    if due == today + timedelta(days=1):
        return "tomorrow"
    if (due - today).days < 7:
        return due.strftime("%a")
    return f"{due:%b} {due.day}"


def slot_label(start: datetime, end: datetime, today: date) -> str:
    """"Wed 4:00–4:45 PM": the shape a student reads at a glance."""
    day = due_label(start.date(), today)
    day = day.capitalize() if day in ("today", "tomorrow") else day
    same_half = start.strftime("%p") == end.strftime("%p")
    left = _fmt(start) if same_half else f"{_fmt(start)} {start:%p}"
    return f"{day} {left}–{_fmt(end)} {end:%p}"


def assignment_event_context(task: NewAssignment, placement: Placement, today: date) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "title": task.title,
        "course": task.course,
        "due_label": due_label(task.due, today),
    }
    if placement.placed:
        start, end = placement.sittings[0]
        ctx["slot_label"] = slot_label(start, end, today)
        ctx["more_sittings"] = len(placement.sittings) - 1
    else:
        ctx["unscheduled_reason"] = placement.reason
    return ctx


def assignment_event_contexts(
    items: Sequence[tuple[NewAssignment, Placement]], today: date,
) -> list[tuple[str, dict[str, Any]]]:
    """Per-assignment events, or one digest when a teacher posts a unit."""
    if len(items) <= DIGEST_AFTER:
        return [(t.key, assignment_event_context(t, p, today)) for t, p in items]
    # Soonest-due first: that is the one the student has to act on.
    ordered = sorted(items, key=lambda tp: tp[0].due)
    lines = []
    for t, p in ordered[:2]:
        line = f"{t.title} due {due_label(t.due, today)}"
        if p.placed:
            line += f", scheduled {slot_label(p.sittings[0][0], p.sittings[0][1], today)}"
        lines.append(line + ".")
    key = "digest:" + _h(*sorted(t.key for t, _ in items))
    return [(key, {"count": len(items), "items": lines})]
