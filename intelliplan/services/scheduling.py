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

The Follow-Through engine
-------------------------
With ``follow_through=True`` the service also fits the student's
Follow-Through model (:mod:`intelliplan.intelligence.followthrough`), feeds
its predictions into the planner's cost, sizes deadline buffers by simulation
(:mod:`intelliplan.intelligence.robust`), and exposes intent-level
rescheduling (:meth:`SchedulingService.adjust`) and an on-time forecast
(:meth:`SchedulingService.forecast`). Off, the service is exactly what it was.
"""

from __future__ import annotations

import logging
import math
import re
import zlib
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import scheduler_engine
from intelliplan.intelligence.decomposition import decompose
from intelliplan.intelligence.estimation import EstimationModel, build_estimation_model
from intelliplan.intelligence.followthrough import (
    FollowThroughModel,
    evaluate_holdout,
    fit_followthrough,
    observations_from_outcomes,
    observations_from_sessions,
    slot_mix_for_windows,
)
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
from intelliplan.intelligence.rescheduling import (
    Replan,
    parse_disruption,
    plan_from_sittings,
    replan as replan_intent,
    snapshot_from_schedule,
)
from intelliplan.intelligence.risk import RiskReport, simulate
from intelliplan.intelligence.robust import plan_with_risk_control

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for annotations only
    from intelliplan.intelligence import counterfactual

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
    #: Past plan blocks labelled done / not done
    #: (:func:`intelliplan.intelligence.followthrough.harvest_plan_outcomes`).
    outcome_rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    #: Population prior for the Follow-Through model; ``None`` → built-in.
    followthrough_prior: Any = None


class SchedulingService:
    """Builds plans for one student. Stateless between calls."""

    def __init__(
        self,
        context: StudentContext,
        config: PlannerConfig | None = None,
        decompose_stages: bool = True,
        follow_through: bool = False,
    ) -> None:
        self._ctx = context
        self._config = config or PlannerConfig()
        self._model: EstimationModel | None = None
        self._decompose = decompose_stages
        self._follow_through = follow_through
        self._ft: FollowThroughModel | None = None
        #: ``{day: {slot: share}}`` — where each day's free time falls,
        #: recorded by :meth:`capacities`.
        self._slot_mix: dict[date, dict[str, float]] = {}
        #: ``{day: {"set": minutes} | {"add": minutes}}`` — what the student
        #: told us about specific days ("can't study today", "extra time
        #: Saturday"). Stored on the plan so later adjustments remember them.
        self._overrides: dict[date, dict[str, int]] = {}
        #: The on-time forecast of the last plan :meth:`plan` built.
        self.last_risk: RiskReport | None = None
        self.last_hardened: tuple[str, ...] = ()

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

    @property
    def follow_through_enabled(self) -> bool:
        return self._follow_through

    def _observations(self) -> list:
        return observations_from_sessions(self._ctx.session_rows) + observations_from_outcomes(
            self._ctx.outcome_rows
        )

    @property
    def followthrough(self) -> FollowThroughModel:
        """The student's fitted Follow-Through model, fitted once per service."""
        if self._ft is None:
            self._ft = fit_followthrough(
                self._observations(),
                now=datetime.now(),
                prior=self._ctx.followthrough_prior,
                stamina_minutes=self.model.stamina_minutes,
            )
        return self._ft

    def completion_fn(self):
        """P(done) for the planner's placement cost, or ``None`` when off.

        Deliberately leaves out the model's deadline-proximity effect. Many
        students really are likelier to do work the night before it is due,
        and the model learns that so the *forecast* can be honest. Letting
        the planner act on it would mean scheduling everything for the
        night before — we model procrastination to predict it, not to plan
        for it.
        """
        if not self._follow_through:
            return None
        ft = self.followthrough
        mixes = dict(self._slot_mix)
        item_cache: dict[tuple, float] = {}
        day_cache: dict[date, float] = {}

        def completion(task: PlannerTask, minutes: int, day: date, load: int) -> float:
            key = (task.course, task.kind, task.difficulty, int(minutes))
            zi = item_cache.get(key)
            if zi is None:
                zi = item_cache[key] = ft.item_logit(
                    course=task.course, kind=task.kind,
                    difficulty=task.difficulty, minutes=int(minutes),
                )
            zd = day_cache.get(day)
            if zd is None:
                zd = day_cache[day] = ft.day_logit(day, mixes.get(day) or "evening")
            z = zi + zd + ft.load_logit(load)
            return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

        return completion

    def session_follow_through(self):
        """P(done) for a planned :class:`Session`, for the risk simulator."""
        ft = self.followthrough
        mixes = dict(self._slot_mix)

        def probability(session, load: int) -> float:
            due = session.due_date
            return ft.probability(
                day=session.day,
                course=session.course,
                kind=session.kind,
                difficulty=session.difficulty,
                minutes=session.minutes,
                slot_mix=mixes.get(session.day) or "evening",
                prior_load_minutes=load,
                days_to_due=(due - session.day).days if due else None,
            )

        return probability

    def sigma_for(self, task: PlannerTask) -> float:
        """Log-space duration spread the estimation model measured."""
        try:
            est = self.model.predict(100, task.course, task.kind)
        except Exception:
            return 0.35
        if est.minutes <= 0:
            return 0.35
        return max(0.1, math.log(max(est.high_minutes, est.minutes) / est.minutes))

    def assessor(self, today: date, seed: int | None = None):
        """A deterministic ``assess(plan, tasks) -> RiskReport``."""
        follow = self.session_follow_through()
        # How reliably free time gets used for catching up. Deliberately
        # *below* the student's follow-through on planned blocks: work with a
        # time and a place gets done more than work that is merely owed —
        # which is the entire premise of planning, and a simulator that
        # scored them equally would call every plan pointless.
        recovery = max(0.25, min(0.70, 0.75 * self.followthrough.baseline_probability))

        def assess(plan: Plan, tasks: Sequence[PlannerTask]) -> RiskReport:
            return simulate(
                plan, tasks, today=today, follow_through=follow,
                sigma_for=self.sigma_for, recovery_rate=recovery, seed=seed,
            )

        return assess

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
            override = self._overrides.get(day) or {}
            if override.get("add"):
                windows = scheduler_engine.extend_windows(
                    windows, day, override["add"], self._ctx.preferred_time, now=now,
                )
            # Each window is discounted on its own. Two separate 60-minute
            # windows are not the same as one 120-minute window: each pays
            # its own break overhead, and summing first would overstate the
            # day by exactly the amount that makes blocks fall off the end.
            minutes = sum(usable_minutes(w.minutes, stamina, long_break_after) for w in windows)
            if override.get("set") is not None:
                minutes = min(minutes, max(0, int(override["set"])))
            self._slot_mix[day] = slot_mix_for_windows(windows)
            label = scheduler_engine.DAY_ABBR[day.weekday()]
            quality = 0.75 if label in weak else 1.0
            if self._follow_through and self.followthrough.has_signal:
                # The Follow-Through model has measured this student's
                # weekdays directly; pricing a weak day here as well would
                # count the same evidence twice.
                quality = 1.0
            out.append(
                DayCapacity(
                    day=day,
                    minutes=minutes,
                    # A historically weak day is not unavailable — it is
                    # expensive. Pricing it lets a deadline still use it.
                    quality=quality,
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
        tasks = self.tasks_from(task_rows)
        capacities = self.capacities(today, horizon_days, now=now)
        if not self._follow_through:
            return build_plan(
                tasks, capacities, model=self.model, today=today, config=self._config,
            )
        result = plan_with_risk_control(
            tasks, capacities, assess=self.assessor(today), model=self.model,
            today=today, config=self._config, completion=self.completion_fn(),
        )
        self.last_risk = result.report
        self.last_hardened = result.hardened
        return result.plan

    def replan(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        reality: Reality,
        today: date | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        now: datetime | None = None,
        anchors: Mapping[str, Sequence[date]] | None = None,
    ) -> Plan:
        """Recover from missed work. ``anchors`` keeps untouched work in place."""
        now = now or datetime.now()
        today = today or now.date()
        capacities = self.capacities(today, horizon_days, now=now)
        return reschedule(
            self.tasks_from(task_rows),
            capacities,
            reality,
            model=self.model,
            today=today,
            config=self._config,
            completion=self.completion_fn(),
            anchors=anchors,
        )

    # ── Intent-level rescheduling ─────────────────────────────────────

    def adjust(
        self,
        schedule_data: Mapping[str, Any] | None,
        progress: Mapping[str, Any] | None,
        payload: Mapping[str, Any],
        *,
        today: date | None = None,
        now: datetime | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        credit: Mapping[str, int] | None = None,
        finished: Sequence[str] = (),
    ) -> tuple[Replan, dict[str, Any]]:
        """Apply one intent ("can't study today", "push this", …) to the saved plan.

        Raises ``ValueError`` with a student-readable message when the
        intent is malformed. Returns the replan and the rendered
        ``schedule_data`` — the caller decides whether to save it (preview).
        """
        now = now or datetime.now()
        today = today or now.date()
        disruption = parse_disruption(payload, today)
        snapshot = snapshot_from_schedule(
            schedule_data, progress, today, credit=credit, finished=finished,
        )
        # Remember what the student has said about specific days. Without
        # this, "can't study today" followed by "push this one" put work
        # straight back onto the day they had just cleared.
        overrides = overrides_from_json((schedule_data or {}).get("capacity_overrides"), today)
        if disruption.kind == "skip_day" and disruption.day is not None:
            overrides[disruption.day] = {"set": 0}
        elif disruption.kind == "limit_day" and disruption.day is not None:
            overrides[disruption.day] = {"set": int(disruption.minutes or 0)}
        elif disruption.kind == "add_time" and disruption.day is not None:
            previous = overrides.get(disruption.day) or {}
            overrides[disruption.day] = {
                "add": int(previous.get("add") or 0) + int(disruption.minutes or 0)
            }
            # The extra time is already in the capacities computed below
            # (through real clock windows, with breaks accounted for); the
            # engine must not add it a second time.
            disruption = replace(disruption, capacity_applied=True)
        self._overrides = overrides
        capacities = self.capacities(today, horizon_days, now=now)
        # One seed for before / literal / after, so all three face the same
        # simulated futures and their differences are the plans', not noise.
        seed = zlib.crc32(
            ("|".join(sorted(t.id for t in snapshot.tasks)) + today.isoformat()).encode()
        ) & 0x7FFFFFFF
        assess = self.assessor(today, seed) if self._follow_through else None
        result = replan_intent(
            snapshot, capacities, disruption, model=self.model, config=self._config,
            completion=self.completion_fn(), assess=assess,
        )
        data = self.to_schedule_data(result.plan, now=now)
        if result.report_after is not None:
            data["forecast"] = result.report_after.to_dict()
        return result, data

    def autopilot(
        self,
        schedule_data: Mapping[str, Any] | None,
        progress: Mapping[str, Any] | None,
        task_rows: Sequence[Mapping[str, Any]] = (),
        *,
        today: date | None = None,
        now: datetime | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        credit: Mapping[str, int] | None = None,
        finished: Sequence[str] = (),
        finished_titles: Sequence[str] = (),
        force: bool = False,
    ) -> "AutopilotRun | None":
        """Re-plan without being asked, when the facts call for it.

        Returns ``None`` when there is nothing to do — no missed work, no new
        work, no deadline a rebalance would protect — or when autopilot is
        off or standing down after an undo. Otherwise returns the run, whose
        ``data`` the caller saves (with an undo copy of the old plan).
        """
        from intelliplan.intelligence import autopilot as ap
        from intelliplan.intelligence.rescheduling import Disruption

        now = now or datetime.now()
        today = today or now.date()
        state = ap.AutopilotState.from_json((schedule_data or {}).get("autopilot"))
        if not state.enabled:
            return None

        snapshot = snapshot_from_schedule(
            schedule_data, progress, today, credit=credit, finished=finished,
        )
        self._overrides = overrides_from_json(
            (schedule_data or {}).get("capacity_overrides"), today
        )
        capacities = self.capacities(today, horizon_days, now=now)

        new_tasks = ap.detect_new_work(
            schedule_data, self.tasks_from(task_rows) if task_rows else (), today,
            finished_titles=finished_titles,
        )
        seed = zlib.crc32(
            ("|".join(sorted(t.id for t in snapshot.tasks)) + today.isoformat()).encode()
        ) & 0x7FFFFFFF
        assess = self.assessor(today, seed)
        current = plan_from_sittings(snapshot.tasks, snapshot.sittings, capacities, today)
        try:
            report = assess(current, snapshot.tasks) if snapshot.tasks else None
        except Exception:
            report = None

        missed_titles = [snapshot.title_of(k) for k in snapshot.missed]
        triggers = ap.triggers_for(
            missed_minutes=sum(snapshot.missed.values()),
            missed_titles=missed_titles,
            new_tasks=new_tasks,
            report=report,
        )
        if not force and not ap.should_run(state, triggers, today, now):
            return None
        if not triggers:
            return None

        result = replan_intent(
            snapshot, capacities,
            Disruption(kind="autopilot", new_tasks=tuple(new_tasks)),
            model=self.model, config=self._config,
            completion=self.completion_fn(), assess=assess,
        )
        placed_new = {
            s.task_id for s in result.plan.sessions
            if s.task_id in {t.id for t in new_tasks}
        }
        facts = {t.key for t in triggers} & {"missed", "new_work"}
        if not facts and result.moved_sittings == 0:
            # A risk trigger that the do-no-harm rule declined: the minimal
            # plan won, so there is nothing worth telling anyone about.
            return None
        if facts == {"new_work"} and not placed_new and result.moved_sittings == 0:
            return None

        data = self.to_schedule_data(result.plan, now=now)
        if result.report_after is not None:
            data["forecast"] = result.report_after.to_dict()
        headline, reasons = ap.explain(triggers, result.moved_sittings)
        entry = {
            "at": now.isoformat(timespec="seconds"),
            "headline": headline,
            "reasons": reasons,
            "moved": result.moved_sittings,
            "triggers": [t.key for t in triggers],
        }
        return AutopilotRun(
            replan=result,
            data=data,
            headline=headline,
            reasons=tuple(reasons),
            triggers=tuple(t.key for t in triggers),
            state=state.with_entry(entry, now=now),
            new_task_ids=tuple(sorted(placed_new)),
        )

    def forecast(
        self,
        schedule_data: Mapping[str, Any] | None,
        progress: Mapping[str, Any] | None,
        *,
        today: date | None = None,
        now: datetime | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        evaluate: bool = True,
    ) -> dict[str, Any]:
        """On-time odds for the plan as it stands, and what the model has learned."""
        now = now or datetime.now()
        today = today or now.date()
        snapshot = snapshot_from_schedule(schedule_data, progress, today)
        self._overrides = overrides_from_json(
            (schedule_data or {}).get("capacity_overrides"), today
        )
        capacities = self.capacities(today, horizon_days, now=now)
        current = plan_from_sittings(snapshot.tasks, snapshot.sittings, capacities, today)
        report = self.assessor(today)(current, snapshot.tasks) if snapshot.tasks else None
        ft = self.followthrough
        evaluation = None
        if evaluate:
            try:
                evaluation = evaluate_holdout(
                    self._observations(), now=now, prior=self._ctx.followthrough_prior,
                    stamina_minutes=ft.stamina_minutes,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("follow-through evaluation failed: %s", exc)
        summary = ft.summary()
        summary.pop("coefficients", None)
        return {
            "risk": report.to_dict() if report is not None else None,
            "missed_minutes": sum(snapshot.missed.values()),
            "insights": [i.to_dict() for i in ft.insights()],
            "model": summary,
            "evaluation": evaluation.to_dict() if evaluation else None,
        }

    # ── Counterfactuals ───────────────────────────────────────────────

    def plan_candidates(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        today: date | None = None,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        now: datetime | None = None,
        objective: "counterfactual.ObjectiveWeights | None" = None,
        completion_probability: Any = None,
    ) -> list["counterfactual.Candidate"]:
        """Build one plan per objective profile and score them all.

        This is the same work :meth:`plan` does, four times, under different
        weights — which is affordable precisely because planning is
        deterministic and local. The point is not to find a better optimum
        than :meth:`plan` finds; it is that "front-load and be safe" and
        "spread it out and remember it" are different plans for different
        weeks, and picking between them needs both on the table.

        Returns candidates ranked best-first. An empty list means every
        profile failed, which the caller must treat as "fall back to
        :meth:`plan`" rather than as "no work to do".
        """
        from intelliplan.intelligence import counterfactual

        now = now or datetime.now()
        today = today or now.date()
        tasks = self.tasks_from(task_rows)
        if not tasks:
            return []
        capacities = self.capacities(today, horizon_days, now=now)
        if completion_probability is None and self._follow_through:
            # Without this, ``completion_odds`` in every candidate's metrics
            # silently degrades to a coverage measure.
            ft = self.followthrough
            mixes = dict(self._slot_mix)

            def completion_probability(course: str, minutes: int, day: date) -> float:
                return ft.probability(
                    day=day, course=course, minutes=minutes,
                    slot_mix=mixes.get(day) or "evening",
                )

        return counterfactual.generate_candidates(
            tasks,
            capacities,
            model=self.model,
            today=today,
            base_config=self._config,
            objective=objective,
            completion_probability=completion_probability,
            completion=self.completion_fn(),
        )

    # ── Rendering ─────────────────────────────────────────────────────

    def to_schedule_data(self, plan: Plan, now: datetime | None = None) -> dict[str, Any]:
        """Convert a :class:`Plan` into the app's ``schedule_data`` shape.

        Runs each day's sittings through the existing clock placer so the
        output is identical in structure to what the LLM path produced —
        every consumer (the interactive view, saved schedules, the calendar
        export, the reminder job) keeps working untouched.
        """
        data = plan_to_schedule_data(
            plan,
            availability=self._ctx.availability,
            preferred_time=self._ctx.preferred_time,
            commitments=self._ctx.commitments,
            stamina_minutes=self.model.stamina_minutes,
            now=now,
            busy_by_date=self._ctx.busy_by_date,
            extra_minutes_by_date={
                d: int(o["add"]) for d, o in self._overrides.items() if o.get("add")
            },
        )
        if self._overrides:
            data["capacity_overrides"] = overrides_to_json(self._overrides)
        if self._follow_through:
            data["engine"] = "followthrough-v1"
            if self.last_risk is not None and plan is not None:
                data["forecast"] = self.last_risk.to_dict()
        return data


    def set_capacity_overrides(
        self, raw: Mapping[str, Any] | None, today: date | None = None
    ) -> None:
        """Honour day-level overrides stored on a saved plan."""
        self._overrides = overrides_from_json(raw, today or date.today())


@dataclass(frozen=True)
class AutopilotRun:
    """One autonomous re-plan, ready to save and explain."""

    replan: Replan
    data: dict[str, Any]
    headline: str
    reasons: tuple[str, ...]
    triggers: tuple[str, ...]
    #: The plan's autopilot state *after* this run (log entry appended).
    state: Any
    new_task_ids: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        body = self.replan.summary()
        body.update({
            "headline": self.headline,
            "reasons": list(self.reasons),
            "triggers": list(self.triggers),
            "new_task_ids": list(self.new_task_ids),
        })
        return body


def overrides_from_json(raw: Any, today: date) -> dict[date, dict[str, int]]:
    """Parse stored overrides, dropping past days and anything malformed."""
    out: dict[date, dict[str, int]] = {}
    if not isinstance(raw, Mapping):
        return out
    for key, value in raw.items():
        try:
            day = date.fromisoformat(str(key)[:10])
        except ValueError:
            continue
        if day < today or not isinstance(value, Mapping):
            continue
        entry: dict[str, int] = {}
        for field_name in ("set", "add"):
            if value.get(field_name) is None:
                continue
            try:
                entry[field_name] = max(0, min(24 * 60, int(value[field_name])))
            except (TypeError, ValueError):
                continue
        if entry:
            out[day] = entry
    return out


def overrides_to_json(overrides: Mapping[date, Mapping[str, int]]) -> dict[str, dict[str, int]]:
    return {d.isoformat(): dict(v) for d, v in sorted(overrides.items())}


# ── Rendering (free function so tests need no service) ────────────────


def plan_to_schedule_data(
    plan: Plan,
    availability: Mapping[str, Any] | None = None,
    preferred_time: str = "evening",
    commitments: str | None = None,
    stamina_minutes: int = 45,
    now: datetime | None = None,
    busy_by_date: Mapping[date, Sequence[tuple[int, int]]] | None = None,
    extra_minutes_by_date: Mapping[date, int] | None = None,
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
            extra = (extra_minutes_by_date or {}).get(day_plan.day)
            if extra:
                windows = scheduler_engine.extend_windows(
                    windows, day_plan.day, extra, preferred_time, now=now,
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
