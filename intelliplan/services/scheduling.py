"""Scheduling service — the seam between the planner and the application.

The intelligence layer is deliberately ignorant of Flask, the ORM, and the
block dictionaries the front end renders. This module is where that
ignorance is paid for: it reads the student's real availability, fits the
estimation model on their real history, runs the planner, and converts the
result into the ``schedule_data`` shape ``App.py`` and ``scheduler.html``
already speak.

Two-stage scheduling
--------------------
The old pipeline asked an LLM for both *what to do on which day* and *at
what time*, then re-timed the result. That conflated two different
problems. Here they are separate:

1. :mod:`intelliplan.intelligence.planner` decides day allocation — how
   much work exists, how it splits, which day each sitting lands on, how
   much buffer sits before each deadline.
2. ``scheduler_engine.place_day_blocks`` decides clock placement — where
   inside the student's actual free windows each sitting sits, with breaks.

Stage 2 was already good and is reused unchanged. Stage 1 is the part this
project replaced.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

import scheduler_engine
from intelliplan.intelligence.decomposition import decompose
from intelliplan.intelligence.estimation import EstimationModel, build_estimation_model
from intelliplan.intelligence.planner import (
    DayCapacity,
    Plan,
    PlannerConfig,
    PlannerTask,
    Reality,
    build_plan,
    reschedule,
    task_from_mapping,
)

logger = logging.getLogger(__name__)

__all__ = [
    "StudentContext",
    "SchedulingService",
    "plan_to_schedule_data",
]

DEFAULT_HORIZON_DAYS = 14

#: Typical minutes lost between two consecutive blocks. ``place_day_blocks``
#: leaves 5 minutes after a short block and 10 after a long one.
_TRANSITION_MINUTES = 8.0
#: Length of the reset ``place_day_blocks`` inserts after a long work run.
_LONG_BREAK_MINUTES = 15.0


def usable_minutes(window_minutes: int, stamina_minutes: int, long_break_after: int) -> int:
    """Discount raw free time by the breaks that time will actually contain.

    A three-hour evening does not hold three hours of study. The clock
    placer puts a short gap between blocks and a real break after a long
    run, and those minutes come out of the same window. A planner that
    allocates against the raw figure fills the day to the brim, placement
    then runs out of clock, and the last sitting is quietly dropped — which
    is exactly how a student ends up with "part 2 of 7" and no part 1.

    Solving ``work + work/S·gap + work/B·break ≤ window`` for work gives the
    amount of *study* a window can really hold.
    """
    window = max(0, int(window_minutes))
    if window <= 0:
        return 0
    stamina = max(10, int(stamina_minutes or 45))
    long_break_after = max(20, int(long_break_after or 90))
    overhead = 1.0 + (_TRANSITION_MINUTES / stamina) + (_LONG_BREAK_MINUTES / long_break_after)
    return int(window / overhead)


@dataclass(frozen=True)
class StudentContext:
    """Everything about one student the scheduler needs, already loaded.

    Assembled by the caller (``App.py``) so this service performs no I/O of
    its own and stays testable without an app context.
    """

    availability: Mapping[str, Any] | None = None
    commitments: str | None = None
    preferred_time: str = "evening"
    #: ``TaskFeedback``-shaped rows.
    feedback_rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    #: Active-study session observations.
    session_rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    #: Weekday abbreviations the student historically under-delivers on.
    weak_days: Sequence[str] = field(default_factory=tuple)
    #: ``{concept: mastery 0..1}`` from the concept-mastery repository.
    concept_mastery: Mapping[str, float] = field(default_factory=dict)
    timezone_offset_minutes: int = 0
    #: What the student said they want to study per day, in minutes. A
    #: comfort ceiling, never a hard one — their availability windows are the
    #: hard limit. ``None`` means they never said, so the planner's default
    #: target utilisation applies.
    daily_target_minutes: int | None = None
    #: Dated committed time from the student's real calendar,
    #: ``{date: [(start_minute, end_minute)]}``. Weekly commitments recur; a
    #: dentist appointment does not, and study time booked on top of one is a
    #: plan the student cannot follow.
    busy_by_date: Mapping[date, Sequence[tuple[int, int]]] = field(default_factory=dict)


class SchedulingService:
    """Builds plans for one student. Stateless between calls."""

    def __init__(
        self,
        context: StudentContext,
        config: PlannerConfig | None = None,
        decompose_stages: bool = True,
    ) -> None:
        self._ctx = context
        self._config = config or PlannerConfig()
        self._model: EstimationModel | None = None
        self._decompose = decompose_stages

    # ── Model ─────────────────────────────────────────────────────────

    @property
    def model(self) -> EstimationModel:
        """Fit lazily and once — fitting walks every history row."""
        if self._model is None:
            self._model = build_estimation_model(
                feedback_rows=self._ctx.feedback_rows,
                session_rows=self._ctx.session_rows,
            )
        return self._model

    # ── Capacity ──────────────────────────────────────────────────────

    def capacities(
        self,
        today: date,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        now: datetime | None = None,
    ) -> list[DayCapacity]:
        """The student's real free minutes per day over the horizon.

        Derived from the same window logic that places blocks on the clock,
        so the planner can never allocate a minute that placement will then
        refuse to find room for.
        """
        weak = {str(d)[:3].title() for d in self._ctx.weak_days}
        stamina = self.model.stamina_minutes
        long_break_after = scheduler_engine.long_break_after_for(
            scheduler_engine.StudyDNA(stamina_minutes=stamina)
        )
        out: list[DayCapacity] = []
        for offset in range(max(1, horizon_days)):
            day = today + timedelta(days=offset)
            try:
                windows = scheduler_engine.windows_for_date(
                    day,
                    self._ctx.availability,
                    self._ctx.preferred_time,
                    self._ctx.commitments,
                    now=now,
                    busy_by_date=self._ctx.busy_by_date,
                )
            except Exception as exc:
                # A malformed availability blob must not cost the student a
                # whole plan; treat that day as unknown-but-usable and move on.
                logger.warning("window computation failed for %s: %s", day, exc)
                windows = []
            # Each window is discounted on its own. Two separate 60-minute
            # windows are not the same as one 120-minute window: each pays
            # its own break overhead, and summing first would overstate the
            # day by exactly the amount that makes blocks fall off the end.
            minutes = sum(usable_minutes(w.minutes, stamina, long_break_after) for w in windows)
            label = scheduler_engine.DAY_ABBR[day.weekday()]
            out.append(
                DayCapacity(
                    day=day,
                    minutes=minutes,
                    # A historically weak day is not unavailable — it is
                    # expensive. Pricing it lets a deadline still use it.
                    quality=0.75 if label in weak else 1.0,
                    comfort_minutes=self._ctx.daily_target_minutes,
                )
            )
        return out

    # ── Tasks ─────────────────────────────────────────────────────────

    def tasks_from(self, rows: Sequence[Mapping[str, Any]]) -> list[PlannerTask]:
        """Normalise assignment dicts and attach concept-difficulty signal.

        Large work of a recognisable shape is expanded into ordered stages
        here rather than in the planner, because the planner's job is to place
        work, not to have opinions about what writing an essay involves.
        """
        tasks: list[PlannerTask] = []
        for row in rows:
            try:
                task = task_from_mapping(row)
            except Exception as exc:
                logger.warning("skipping unparseable task %r: %s", row, exc)
                continue
            concepts = task.concepts or self._concepts_in(task, row)
            if concepts != task.concepts:
                task = replace(task, concepts=concepts)
            penalty = self._weak_concept_penalty(concepts)
            if penalty > 0:
                task = replace(task, weak_concept_penalty=penalty)
            tasks.extend(self._staged(task, row))
        return tasks

    def _staged(
        self, task: PlannerTask, row: Mapping[str, Any]
    ) -> list[PlannerTask]:
        """One task, or its stages when decomposition earns its place.

        Each stage becomes a first-class :class:`PlannerTask` with its own
        minutes and a real ``depends_on`` edge, which the planner already
        knows how to respect — it has simply never been given any. That is
        what stops the plan from scheduling "revise the draft" on Tuesday and
        "write the draft" on Thursday.
        """
        if not self._decompose:
            return [task]
        try:
            result = decompose(
                minutes=int(task.est_minutes or 0),
                title=task.title,
                kind=task.kind,
                description=str(row.get("description") or ""),
                problem_count=int(task.subtask_count or 0),
            )
        except Exception as exc:
            logger.warning("decomposition failed for %r: %s", task.title, exc)
            return [task]
        if not result.applied:
            return [task]

        out: list[PlannerTask] = []
        for index, stage in enumerate(result.stages, start=1):
            out.append(
                replace(
                    task,
                    id=f"{task.id}::{stage.key}",
                    title=f"{task.title} — {stage.label}",
                    parent_title=task.title,
                    stage_index=index,
                    est_minutes=stage.minutes,
                    # The stage minutes were carved out of an estimate that
                    # already accounted for work done, so re-subtracting it
                    # per stage would shrink the task once per stage.
                    done_minutes=0,
                    depends_on=tuple(f"{task.id}::{d}" for d in stage.depends_on),
                    # A stage that wants one unbroken sitting says so by
                    # declaring no internal subtasks to split at.
                    subtask_count=0,
                )
            )
        return out

    def _concepts_in(self, task: PlannerTask, row: Mapping[str, Any]) -> tuple[str, ...]:
        """Concepts this task touches, matched against what the student studies.

        The planner has priced ``concept_stack`` — the cost of piling several
        shaky ideas into one day — since it was written, and nothing ever
        populated ``PlannerTask.concepts``, so the weight has been dead code
        and the concept-mastery table was loaded and then ignored.

        The vocabulary is strictly the student's own tracked concepts, matched
        on word boundaries in the title and description. That is deliberately
        conservative: inventing concepts from an assignment title is how a
        planner starts asserting that "Chapter 4" is about integrals.
        """
        mastery = self._ctx.concept_mastery
        if not mastery:
            return ()
        haystack = f"{task.title} {row.get('description') or ''}".lower()
        if not haystack.strip():
            return ()
        found = [
            name for name in mastery
            if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", haystack)
        ]
        return tuple(sorted(set(found))[:8])

    def _weak_concept_penalty(self, concepts: Sequence[str]) -> float:
        """How shaky this task's concepts are for this student, 0..1."""
        mastery = self._ctx.concept_mastery
        if not mastery or not concepts:
            return 0.0
        gaps = [1.0 - float(mastery[c]) for c in concepts if c in mastery]
        if not gaps:
            return 0.0
        return round(max(0.0, min(1.0, max(gaps))), 3)

    # ── Planning ──────────────────────────────────────────────────────

    def plan(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        today: date | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        now: datetime | None = None,
    ) -> Plan:
        now = now or datetime.now()
        today = today or now.date()
        return build_plan(
            self.tasks_from(task_rows),
            self.capacities(today, horizon_days, now=now),
            model=self.model,
            today=today,
            config=self._config,
        )

    def replan(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        reality: Reality,
        today: date | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        now: datetime | None = None,
    ) -> Plan:
        now = now or datetime.now()
        today = today or now.date()
        return reschedule(
            self.tasks_from(task_rows),
            self.capacities(today, horizon_days, now=now),
            reality,
            model=self.model,
            today=today,
            config=self._config,
        )

    # ── Rendering ─────────────────────────────────────────────────────

    def to_schedule_data(self, plan: Plan, now: datetime | None = None) -> dict[str, Any]:
        """Convert a :class:`Plan` into the app's ``schedule_data`` shape.

        Runs each day's sittings through the existing clock placer so the
        output is identical in structure to what the LLM path produced —
        every consumer (the interactive view, saved schedules, the calendar
        export, the reminder job) keeps working untouched.
        """
        return plan_to_schedule_data(
            plan,
            availability=self._ctx.availability,
            preferred_time=self._ctx.preferred_time,
            commitments=self._ctx.commitments,
            stamina_minutes=self.model.stamina_minutes,
            now=now,
            busy_by_date=self._ctx.busy_by_date,
        )


# ── Rendering (free function so tests need no service) ────────────────


def plan_to_schedule_data(
    plan: Plan,
    availability: Mapping[str, Any] | None = None,
    preferred_time: str = "evening",
    commitments: str | None = None,
    stamina_minutes: int = 45,
    now: datetime | None = None,
    busy_by_date: Mapping[date, Sequence[tuple[int, int]]] | None = None,
) -> dict[str, Any]:
    """Render a plan as ``{"schedule": [{date, blocks: [...]}, ...], ...}``."""
    now = now or datetime.now()
    dna = scheduler_engine.StudyDNA(stamina_minutes=stamina_minutes)
    long_break_after = scheduler_engine.long_break_after_for(dna)

    days_out: list[dict[str, Any]] = []
    unplaced: list[dict[str, Any]] = []
    block_id = 1

    for day_plan in plan.days:
        if not day_plan.sessions:
            continue
        blocks: list[dict[str, Any]] = []
        for session in day_plan.sessions:
            blocks.append(
                {
                    "id": f"b{block_id}",
                    "assignment": session.label,
                    # The assignment this block belongs to, which for a stage
                    # is not its own title. Enrichment, the interactive view
                    # and the calendar export all match on this.
                    "parent_title": session.parent_title or session.title,
                    "stage_title": session.title if session.parent_title else "",
                    "task_id": session.task_id,
                    "course": session.course,
                    "duration_minutes": session.minutes,
                    "difficulty": _title_difficulty(session.difficulty),
                    "due_date": session.due_date.isoformat() if session.due_date else "",
                    "kind": session.kind,
                    "priority": session.priority,
                    "part_index": session.part_index,
                    "part_total": session.part_total,
                    "notes": " ".join(session.reasons),
                    "reasons": list(session.reasons),
                    "estimate_ratio": session.estimate_ratio,
                    "estimate_confidence": session.estimate_confidence,
                    "is_break": False,
                }
            )
            block_id += 1

        window_minutes = 0
        try:
            windows = scheduler_engine.windows_for_date(
                day_plan.day, availability, preferred_time, commitments, now=now,
                busy_by_date=busy_by_date,
            )
            window_minutes = sum(w.minutes for w in windows)
            placed, spilled = scheduler_engine.place_day_blocks(
                blocks,
                windows,
                dna,
                long_break_after=long_break_after,
                # The planner already decided order and split; re-sorting or
                # re-splitting here would silently overrule it.
                preserve_order=True,
            )
        except Exception as exc:
            logger.warning("clock placement failed for %s: %s", day_plan.day, exc)
            placed, spilled = blocks, []

        if spilled:
            unplaced.extend(spilled)

        days_out.append(
            {
                "date": day_plan.day.isoformat(),
                "day_name": day_plan.day.strftime("%A"),
                "blocks": placed,
                # Two different denominators, named so nothing compares the
                # wrong pair. Downstream enrichment recomputes total_minutes
                # across *all* blocks including breaks, so the figure it can
                # be honestly measured against is the whole window — not the
                # study-only capacity the optimizer allocates against.
                # Reporting "235 / 223" is how a plan starts looking broken
                # when it is in fact correct.
                "total_minutes": day_plan.scheduled_minutes,
                "capacity_minutes": window_minutes or day_plan.capacity_minutes,
                "study_minutes": day_plan.scheduled_minutes,
                "study_capacity_minutes": day_plan.capacity_minutes,
                "utilisation": day_plan.utilisation,
            }
        )

    notes = list(plan.notes)
    deferred = [
        {
            "task_id": d.task_id,
            "title": d.title,
            "minutes": d.minutes,
            "due_date": d.due_date.isoformat() if d.due_date else None,
            "reason": d.reason,
        }
        for d in plan.deferred
    ]

    # Anything the clock placer could not fit becomes a real deferral too.
    # A block that exists in the allocation but not on the calendar, and is
    # reported only as a footnote, is work the student has been told about
    # in a way they will never see — the plan looks complete and quietly
    # is not.
    if unplaced:
        for block in unplaced:
            deferred.append(
                {
                    "task_id": block.get("task_id"),
                    "title": block.get("parent_title") or block.get("assignment") or "Task",
                    "minutes": int(block.get("duration_minutes") or 0),
                    "due_date": block.get("due_date") or None,
                    "reason": "Ran past the end of your free hours that day.",
                }
            )
        notes.append(
            f"{len(unplaced)} block(s) ran past the end of your free hours. "
            f"They are listed as unscheduled rather than dropped."
        )

    return {
        "schedule": days_out,
        "notes": notes,
        "overloaded": plan.overloaded or bool(unplaced),
        "total_minutes": plan.total_minutes,
        "capacity_minutes": plan.capacity_minutes,
        "deferred": deferred,
        "generated_by": "planner-v2",
    }


def _title_difficulty(value: str) -> str:
    """The front end renders ``Hard``/``Medium``/``Easy``; the model uses
    lowercase. One conversion, in one place."""
    return {"easy": "Easy", "medium": "Medium", "hard": "Hard"}.get(
        (value or "medium").lower(), "Medium"
    )
