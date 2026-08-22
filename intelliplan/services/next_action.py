"""NextActionService — the composition root for the adaptive scheduler.

The engines under :mod:`intelliplan.intelligence` are pure and know nothing
about students, databases, or requests. This is where that ignorance is paid
for: it gathers the student's real plan, real assignments, real mastery and
real history, hands them to the engines, and turns the answers back into
payloads the API can serialise.

Everything it needs arrives as an injected callable, so the whole service is
testable with four lambdas and no application context.

Timing
------
The brief's budget is under one second for a next-best-action and under two
for a re-plan. Nothing here calls an LLM, and the only I/O is what the
injected providers do. The engines themselves run in single-digit
milliseconds on a realistic week — the cost is entirely in loading the
student's data, which is why loading is behind providers the caller can
cache.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Callable, Mapping, Sequence

from intelliplan.domain.student import (
    BehaviorProfile,
    MasteryEstimate,
    NextAction,
)
from intelliplan.intelligence import constraints as constraints_engine
from intelliplan.intelligence import nba
from intelliplan.intelligence import overrides as overrides_engine
from intelliplan.intelligence.behavior import BehaviorModel, build_behavior_model
from intelliplan.intelligence.planner import (
    DayPlan,
    Plan,
    PlannerTask,
    Session,
)

logger = logging.getLogger(__name__)

__all__ = [
    "NextActionService",
    "StudentSnapshot",
    "TodayDecision",
    "decision_to_dict",
    "plan_from_schedule_data",
    "tasks_from_plan",
    "preview_override",
]


@dataclass(frozen=True, slots=True)
class StudentSnapshot:
    """Everything about one student at one moment, already loaded.

    Built once per request and passed to every engine, so a single page load
    cannot end up reasoning from two different versions of the same student.
    """

    user_id: int
    now: datetime
    available_minutes: int
    planned_today: tuple[Mapping[str, Any], ...] = ()
    assignments: tuple[Mapping[str, Any], ...] = ()
    mastery: tuple[MasteryEstimate, ...] = ()
    behavior: BehaviorModel | None = None
    worked_today_minutes: int = 0
    continuous_minutes: int = 0
    current_course: str = ""
    in_progress_task_ids: frozenset[str] = frozenset()

    @property
    def today(self) -> date:
        return self.now.date()


@dataclass(frozen=True, slots=True)
class TodayDecision:
    """What the Today surface renders.

    Deliberately shaped like the screen rather than like the engines: the
    headline, the reason, the runners-up, what is at risk, and how much time
    is left. Anything the screen does not show does not belong here.
    """

    generated_at: datetime
    headline: NextAction | None
    upcoming: tuple[NextAction, ...]
    behavior: BehaviorProfile | None
    minutes_remaining_today: int
    minutes_planned_today: int
    at_risk: tuple[Mapping[str, Any], ...] = ()
    feasibility: Mapping[str, Any] | None = None
    compute_ms: int = 0
    reason: str = ""

    @property
    def has_recommendation(self) -> bool:
        return self.headline is not None


def _action_to_dict(action: NextAction) -> dict[str, Any]:
    c = action.candidate
    return {
        "kind": c.kind.value,
        "title": c.title,
        "course": c.course,
        "subject": c.subject,
        "concept": c.concept,
        "task_id": c.task_id,
        "minutes": c.minutes,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "score": action.score,
        "confidence": action.confidence,
        "completion_probability": action.completion_probability,
        "reason_codes": list(action.reason_codes),
        "why": list(action.explanation),
        "method": action.method,
        "block_id": c.metadata.get("block_id", ""),
        # The component breakdown is what "Why was this scheduled?" shows to
        # a student who asks. It is not hidden reasoning — it is the actual
        # arithmetic, which is the only explanation worth trusting.
        "components": [{"key": k, "value": v} for k, v in action.components],
    }


def decision_to_dict(decision: TodayDecision) -> dict[str, Any]:
    return {
        "generated_at": decision.generated_at.isoformat(),
        "has_recommendation": decision.has_recommendation,
        "reason": decision.reason,
        "headline": _action_to_dict(decision.headline) if decision.headline else None,
        "upcoming": [_action_to_dict(a) for a in decision.upcoming],
        "minutes_remaining_today": decision.minutes_remaining_today,
        "minutes_planned_today": decision.minutes_planned_today,
        "at_risk": [dict(r) for r in decision.at_risk],
        "feasibility": dict(decision.feasibility) if decision.feasibility else None,
        "behavior": (
            {
                "completion_rate": decision.behavior.completion_rate,
                "best_slot": decision.behavior.best_slot,
                "stamina_minutes": decision.behavior.stamina_minutes,
                "evidence": decision.behavior.completion_evidence.source.value,
                "observations": decision.behavior.observations,
            }
            if decision.behavior
            else None
        ),
        "compute_ms": decision.compute_ms,
    }


@dataclass
class NextActionService:
    """Injected providers in, decisions out.

    Every provider is optional. A missing provider degrades the answer —
    without behaviour there are no personalised completion odds, without
    mastery there are no concept recommendations — but never breaks it, which
    is what makes this safe to turn on for a student whose data is half
    imported.
    """

    get_active_schedule: Callable[[int], Mapping[str, Any] | None]
    get_assignments: Callable[[int], Sequence[Mapping[str, Any]]] = (
        lambda uid: []
    )
    get_mastery: Callable[[int, date], Sequence[MasteryEstimate]] = (
        lambda uid, today: []
    )
    get_session_rows: Callable[[int], Sequence[Mapping[str, Any]]] = lambda uid: []
    get_feedback_rows: Callable[[int], Sequence[Mapping[str, Any]]] = lambda uid: []
    get_daily_minutes: Callable[[int], Mapping[Any, int]] = lambda uid: {}
    get_in_progress: Callable[[int], tuple[frozenset[str], str, int]] = (
        lambda uid: (frozenset(), "", 0)
    )
    get_remaining_minutes: Callable[[int, datetime], int] = lambda uid, now: 0
    #: How many overrides this student has made. Without it the behavioural
    #: model's reschedule rate is permanently zero — a number that looks
    #: measured and never is.
    get_reschedule_count: Callable[[int], int] = lambda uid: 0
    weights: nba.NBAWeights = field(default_factory=nba.NBAWeights)

    # ── snapshot ─────────────────────────────────────────────────────

    def snapshot(self, user_id: int, now: datetime) -> StudentSnapshot:
        """Load everything once.

        Each provider is wrapped: one failing source must not cost the
        student the whole recommendation, because the sources most likely to
        fail are the remote LMS ones and those fail all the time.
        """
        today = now.date()
        schedule = self._safe(self.get_active_schedule, user_id, default=None)
        planned = _blocks_for_day(schedule, today)

        in_progress, current_course, continuous = self._safe(
            self.get_in_progress, user_id, default=(frozenset(), "", 0)
        )
        session_rows = self._safe(self.get_session_rows, user_id, default=[])
        behavior: BehaviorModel | None
        try:
            behavior = build_behavior_model(
                session_rows,
                self._safe(self.get_feedback_rows, user_id, default=[]),
                daily_minutes=self._safe(self.get_daily_minutes, user_id, default={}),
                reschedule_events=int(
                    self._safe(self.get_reschedule_count, user_id, default=0)
                ),
                now=now,
            )
        except Exception as exc:
            logger.warning("behaviour model fit failed for user %s: %s", user_id, exc)
            behavior = None

        return StudentSnapshot(
            user_id=user_id,
            now=now,
            available_minutes=max(
                0, int(self._safe2(self.get_remaining_minutes, user_id, now, default=0))
            ),
            planned_today=tuple(planned),
            assignments=tuple(self._safe(self.get_assignments, user_id, default=[])),
            mastery=tuple(
                self._safe2(self.get_mastery, user_id, today, default=[])
            ),
            behavior=behavior,
            worked_today_minutes=_worked_today(session_rows, today),
            continuous_minutes=int(continuous or 0),
            current_course=str(current_course or ""),
            in_progress_task_ids=frozenset(in_progress or ()),
        )

    # ── the decision ─────────────────────────────────────────────────

    def decide(self, user_id: int, now: datetime, *, limit: int = 4) -> TodayDecision:
        """What should this student do next?"""
        started = time.perf_counter()
        snap = self.snapshot(user_id, now)

        ctx = nba.NBAContext(
            now=now,
            available_minutes=snap.available_minutes,
            behavior=snap.behavior,
            worked_today_minutes=snap.worked_today_minutes,
            continuous_minutes=snap.continuous_minutes,
            current_course=snap.current_course,
            in_progress_task_ids=snap.in_progress_task_ids,
            weights=self.weights,
        )
        actions = nba.decide(
            ctx,
            planned_today=snap.planned_today,
            assignments=snap.assignments,
            mastery=snap.mastery,
            limit=limit,
        )

        reason = ""
        if not actions:
            reason = (
                "Nothing is outstanding right now."
                if snap.available_minutes > 0
                else "You are outside the hours you set aside for study."
            )
        elif snap.available_minutes <= 0:
            # There is work, but no time today. Say so rather than
            # recommending a 45-minute block into a window that closed.
            reason = "Today's study window has closed — this is what tomorrow starts with."

        elapsed = int((time.perf_counter() - started) * 1000)
        return TodayDecision(
            generated_at=now,
            headline=actions[0] if actions else None,
            upcoming=tuple(actions[1:]),
            behavior=snap.behavior.profile() if snap.behavior else None,
            minutes_remaining_today=snap.available_minutes,
            minutes_planned_today=sum(
                int(b.get("duration_minutes") or 0)
                for b in snap.planned_today
                if not b.get("is_break")
            ),
            at_risk=tuple(_at_risk(snap)),
            compute_ms=elapsed,
            reason=reason,
        )

    # ── feasibility ──────────────────────────────────────────────────

    def check(
        self,
        user_id: int,
        *,
        now: datetime | None = None,
        windows_for: Any = None,
        busy_by_date: Mapping[date, Sequence[tuple[int, int]]] | None = None,
    ) -> constraints_engine.FeasibilityReport:
        """Re-assert the constraints over the plan the student currently has."""
        schedule = self._safe(self.get_active_schedule, user_id, default=None)
        return constraints_engine.check_schedule(
            schedule, windows_for=windows_for, busy_by_date=busy_by_date, now=now
        )

    # ── provider safety ──────────────────────────────────────────────

    @staticmethod
    def _safe(fn: Callable[..., Any], arg: Any, *, default: Any) -> Any:
        try:
            out = fn(arg)
        except Exception as exc:
            logger.warning("next-action provider %s failed: %s", getattr(fn, "__name__", fn), exc)
            return default
        return default if out is None else out

    @staticmethod
    def _safe2(fn: Callable[..., Any], a: Any, b: Any, *, default: Any) -> Any:
        try:
            out = fn(a, b)
        except Exception as exc:
            logger.warning("next-action provider %s failed: %s", getattr(fn, "__name__", fn), exc)
            return default
        return default if out is None else out


# ── helpers ──────────────────────────────────────────────────────────


def _blocks_for_day(
    schedule: Mapping[str, Any] | None, day: date
) -> list[Mapping[str, Any]]:
    for entry in (schedule or {}).get("schedule") or []:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("date") or "")[:10] == day.isoformat():
            return [b for b in (entry.get("blocks") or []) if isinstance(b, Mapping)]
    return []


def _worked_today(session_rows: Sequence[Mapping[str, Any]], today: date) -> int:
    total = 0
    for row in session_rows or []:
        if not isinstance(row, Mapping):
            continue
        at = row.get("started_at") or row.get("at") or row.get("ended_at")
        if isinstance(at, datetime) and at.date() == today:
            try:
                total += int(row.get("actual") or row.get("actual_minutes") or 0)
            except (TypeError, ValueError):
                continue
    return total


def _at_risk(snap: StudentSnapshot) -> list[dict[str, Any]]:
    """What is closest to going wrong, worst first.

    Two independent kinds of risk, kept separate because the fix is
    different: work that will not be finished in time, and concepts an
    assessment is about to test that the student does not have yet.
    """
    out: list[dict[str, Any]] = []
    today = snap.today
    for row in snap.assignments:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "").lower()
        if status in ("completed", "dismissed", "done"):
            continue
        due = row.get("due_date")
        due_date = _parse_date(due)
        if due_date is None:
            continue
        days = (due_date - today).days
        if days > 2:
            continue
        out.append(
            {
                "kind": "deadline",
                "title": str(row.get("title") or ""),
                "course": str(row.get("course") or ""),
                "due_date": due_date.isoformat(),
                "days_away": days,
            }
        )
    for est in snap.mastery:
        if est.assessment_days_away is None or est.assessment_days_away > 5:
            continue
        if not est.is_weak:
            continue
        out.append(
            {
                "kind": "mastery",
                "title": est.concept or est.topic,
                "course": est.subject,
                "mastery": round(est.mastery, 2),
                "confidence": round(est.confidence, 2),
                "days_away": est.assessment_days_away,
            }
        )
    out.sort(key=lambda r: (r.get("days_away", 99), -float(r.get("mastery", 1.0) or 0)))
    return out[:5]


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


# ── schedule_data ↔ Plan ─────────────────────────────────────────────


def plan_from_schedule_data(schedule_data: Mapping[str, Any] | None) -> Plan:
    """Rebuild a :class:`Plan` from the JSON blob the app stores.

    The override engine reasons over ``Plan`` objects, but what is actually
    persisted — and what the student has been looking at — is
    ``schedule_data``. Round-tripping through this adapter rather than
    re-planning from the assignments is deliberate: an override has to act on
    the plan the student can see, including any blocks they dragged by hand.
    Re-planning would answer a different question.

    Auto-inserted breaks are dropped. They are placement artefacts, not work,
    and carrying them into the planner's world would make every day look
    fuller than it is.
    """
    days: list[DayPlan] = []
    for entry in (schedule_data or {}).get("schedule") or []:
        if not isinstance(entry, Mapping):
            continue
        day = _parse_date(entry.get("date"))
        if day is None:
            continue
        sessions: list[Session] = []
        for block in entry.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("is_break"):
                continue
            try:
                minutes = int(block.get("duration_minutes") or 0)
            except (TypeError, ValueError):
                continue
            if minutes <= 0:
                continue
            sessions.append(
                Session(
                    task_id=str(block.get("task_id") or block.get("id") or ""),
                    title=str(block.get("assignment") or ""),
                    course=str(block.get("course") or ""),
                    kind=str(block.get("kind") or "homework"),
                    day=day,
                    minutes=minutes,
                    part_index=int(block.get("part_index") or 1),
                    part_total=int(block.get("part_total") or 1),
                    difficulty=str(block.get("difficulty") or "medium").lower(),
                    priority=int(block.get("priority") or 50),
                    due_date=_parse_date(block.get("due_date")),
                    reasons=tuple(block.get("reasons") or ()),
                    parent_title=str(block.get("parent_title") or ""),
                )
            )
        try:
            capacity = int(entry.get("capacity_minutes") or 0)
        except (TypeError, ValueError):
            capacity = 0
        days.append(
            DayPlan(
                day=day,
                sessions=tuple(sessions),
                capacity_minutes=capacity,
                scheduled_minutes=sum(s.minutes for s in sessions),
            )
        )
    days.sort(key=lambda d: d.day)
    scheduled = sum(d.scheduled_minutes for d in days)
    capacity = sum(d.capacity_minutes for d in days)
    return Plan(
        days=tuple(days),
        total_minutes=scheduled,
        capacity_minutes=capacity,
        overloaded=bool((schedule_data or {}).get("overloaded")),
    )


def tasks_from_plan(plan: Plan) -> list[PlannerTask]:
    """The tasks a plan implies, for scoring it.

    The measurement functions need to know what each session is *worth*, and
    the plan carries that per session. Collapsing back to one task per id
    keeps the "fraction of value covered" denominator meaning what it says
    instead of counting a three-part essay three times.
    """
    seen: dict[str, PlannerTask] = {}
    for s in plan.sessions:
        key = s.task_id or s.title
        existing = seen.get(key)
        if existing is None:
            seen[key] = PlannerTask(
                id=key,
                title=s.parent_title or s.title,
                course=s.course,
                kind=s.kind,
                due_date=s.due_date,
                est_minutes=s.minutes,
                difficulty=s.difficulty,
                priority=s.priority,
            )
        else:
            seen[key] = replace(
                existing, est_minutes=(existing.est_minutes or 0) + s.minutes
            )
    return list(seen.values())


def preview_override(
    schedule_data: Mapping[str, Any] | None,
    *,
    action: str,
    task_id: str,
    day: date,
    to_day: date | None = None,
    minutes: int = 0,
    today: date,
    behavior: BehaviorModel | None = None,
) -> overrides_engine.OverrideOutcome | None:
    """Apply an override to the student's saved plan and price it.

    Returns ``None`` when the override is a no-op — the task is not on that
    day, or shortening to a length it already is. A no-op is not an error and
    must not be reported as one; it is the student asking a question whose
    answer is "nothing would change".
    """
    before = plan_from_schedule_data(schedule_data)
    if not before.days:
        return None

    if action == "move":
        after, moved, target = overrides_engine.move_task(
            before, task_id=task_id, from_day=day, to_day=to_day
        )
    elif action == "skip":
        after, moved = overrides_engine.skip_task(before, task_id=task_id, day=day)
        target = None
    elif action == "shorten":
        after, moved = overrides_engine.shorten_task(
            before, task_id=task_id, day=day, minutes=minutes
        )
        target = None
    else:
        return None

    if moved <= 0:
        return None

    def odds(course: str, mins: int, when: date) -> float:
        # The slot is unknown at plan level — clock placement happens after
        # day allocation — so this asks the course/length question only.
        # Inventing a slot here would put a number on the screen that no
        # history supports.
        return behavior.completion_probability(course=course, minutes=mins).probability

    probability = odds if behavior is not None else None

    report = overrides_engine.evaluate(
        before,
        after,
        tasks_from_plan(before),
        today,
        completion_probability=probability,
        affected_days=tuple(d for d in (day, target) if d is not None),
    )
    _, offer = overrides_engine.rebalance_worth_offering(
        after, touched=tuple(d for d in (day, target) if d is not None)
    )
    return overrides_engine.OverrideOutcome(
        plan=after,
        report=report,
        moved_minutes=moved,
        target_day=target,
        rebalance_offer=offer,
    )
