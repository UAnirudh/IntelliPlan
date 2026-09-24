"""Rescheduling by intent — the scenarios a real week throws at a student.

The property that matters most is the one students notice first: a replan
repairs what broke and leaves everything else where they last saw it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from intelliplan.intelligence.planner import PlannerTask, build_plan, capacities_from_minutes
from intelliplan.intelligence.rescheduling import (
    Disruption,
    parse_disruption,
    replan,
    snapshot_from_schedule,
)
from intelliplan.intelligence.risk import simulate
from intelliplan.services.scheduling import plan_to_schedule_data

TODAY = date(2026, 3, 2)  # Monday
CAPS = capacities_from_minutes(TODAY, [150] * 14)


def week():
    return [
        PlannerTask(id=f"a{i}", title=f"Assignment {i}", course=["Chem", "Math", "History"][i % 3],
                    kind="homework", due_date=TODAY + timedelta(days=4 + i),
                    est_minutes=[60, 90, 120][i % 3], priority=50 + 5 * i)
        for i in range(6)
    ]


@pytest.fixture
def saved():
    plan = build_plan(week(), CAPS, today=TODAY)
    data = plan_to_schedule_data(plan)
    for di, day in enumerate(data["schedule"]):
        for bi, block in enumerate(day["blocks"]):
            block["block_id"] = f"d{di}-b{bi}"
    return plan, data


def assess(plan, tasks):
    return simulate(plan, tasks, today=TODAY, seed=11)


def run(data, payload, progress=None):
    snap = snapshot_from_schedule(data, progress or {}, TODAY)
    return replan(snap, CAPS, parse_disruption(payload, TODAY), assess=assess)


def pairs(plan):
    return {(s.task_id, s.day) for s in plan.sessions}


# ── reading the saved plan ───────────────────────────────────────────


def test_the_snapshot_carries_remaining_work_already_calibrated(saved):
    plan, data = saved
    snap = snapshot_from_schedule(data, {}, TODAY)
    assert {t.id for t in snap.tasks} == {f"a{i}" for i in range(6)}
    assert all(t.calibrated for t in snap.tasks)
    total = {t.id: t.est_minutes for t in snap.tasks}
    for s in plan.sessions:
        total[s.task_id] -= s.minutes
    assert all(v == 0 for v in total.values())


def test_ticked_blocks_are_credited_not_rescheduled(saved):
    plan, data = saved
    first = next(b for d in data["schedule"] for b in d["blocks"] if not b.get("is_break"))
    task_id, minutes = first["task_id"], first["duration_minutes"]
    planned = sum(s.minutes for s in plan.sessions if s.task_id == task_id)

    snap = snapshot_from_schedule(data, {first["block_id"]: {"done": True}}, TODAY)
    assert snap.done[task_id] == minutes
    remaining = next((t.est_minutes for t in snap.tasks if t.id == task_id), 0)
    assert remaining == planned - minutes

    result = replan(snap, CAPS, parse_disruption({"action": "catch_up"}, TODAY), assess=assess)
    assert sum(s.minutes for s in result.plan.sessions if s.task_id == task_id) == planned - minutes


def test_work_left_on_a_past_day_is_missed_and_needs_a_new_day(saved):
    _, data = saved
    snap = snapshot_from_schedule(data, {}, TODAY + timedelta(days=2))
    assert snap.missed
    for task_id, minutes in snap.missed.items():
        assert minutes > 0
        assert all(d >= TODAY + timedelta(days=2) for d, _ in snap.sittings.get(task_id, ()))


def test_the_numeric_priority_survives_enrichment_labels():
    data = {"schedule": [{"date": TODAY.isoformat(), "blocks": [
        {"task_id": "x", "assignment": "X", "duration_minutes": 30,
         "priority": "High", "priority_score": 91},
    ]}]}
    assert snapshot_from_schedule(data, {}, TODAY).tasks[0].priority == 91


def test_stages_come_back_as_a_chain():
    data = {"schedule": [{"date": (TODAY + timedelta(days=i)).isoformat(), "blocks": [
        {"task_id": f"essay::{k}", "assignment": f"Essay — {k}", "parent_title": "Essay",
         "stage_title": f"Essay — {k}", "stage_index": i + 1, "duration_minutes": 40}
    ]} for i, k in enumerate(["research", "draft", "revise"])]}
    snap = snapshot_from_schedule(data, {}, TODAY)
    deps = {t.id: t.depends_on for t in snap.tasks}
    assert deps == {"essay::research": (), "essay::draft": ("essay::research",),
                    "essay::revise": ("essay::draft",)}


# ── the property students notice ─────────────────────────────────────


def test_an_untouched_plan_reproduces_itself_exactly(saved):
    plan, data = saved
    result = run(data, {"action": "catch_up"})
    assert result.moved_sittings == 0
    assert pairs(result.plan) == pairs(plan)


def test_clearing_a_day_moves_only_what_was_on_it(saved):
    plan, data = saved
    lost = TODAY + timedelta(days=1)
    on_lost = sum(1 for s in plan.sessions if s.day == lost)
    assert on_lost
    result = run(data, {"action": "skip_day", "day": lost.isoformat()})
    assert not [s for s in result.plan.sessions if s.day == lost]
    assert result.moved_sittings <= on_lost + 1


def test_less_time_keeps_the_day_inside_the_limit(saved):
    _, data = saved
    result = run(data, {"action": "limit_day", "minutes": 30})
    assert sum(s.minutes for s in result.plan.sessions if s.day == TODAY) <= 30


def test_a_pushed_task_does_not_appear_before_its_day(saved):
    plan, data = saved
    task = next(s.task_id for s in plan.sessions if s.day == TODAY)
    until = TODAY + timedelta(days=2)
    result = run(data, {"action": "push", "task_id": task, "day": until.isoformat()})
    assert all(s.day >= until for s in result.plan.sessions if s.task_id == task)


def test_a_pinned_sitting_stays_where_it_was_put(saved):
    plan, data = saved
    source = plan.sessions[0]
    target = TODAY + timedelta(days=5)
    result = run(data, {"action": "pin", "task_id": source.task_id, "day": target.isoformat(),
                        "from_day": source.day.isoformat(), "minutes": source.minutes})
    assert (source.task_id, target) in pairs(result.plan)


def test_done_work_leaves_and_unblocks_what_waited_on_it():
    data = {"schedule": [{"date": (TODAY + timedelta(days=i)).isoformat(), "blocks": [
        {"task_id": f"essay::{k}", "assignment": f"Essay — {k}", "parent_title": "Essay",
         "stage_index": i + 1, "duration_minutes": 40,
         "due_date": (TODAY + timedelta(days=8)).isoformat()}
    ]} for i, k in enumerate(["research", "draft"])]}
    result = run(data, {"action": "done", "task_id": "essay::research"})
    assert {s.task_id for s in result.plan.sessions} == {"essay::draft"}
    assert result.tasks[0].depends_on == ()


def test_partial_progress_shrinks_the_remaining_work(saved):
    plan, data = saved
    task = plan.sessions[0].task_id
    before = sum(s.minutes for s in plan.sessions if s.task_id == task)
    result = run(data, {"action": "progress", "task_id": task, "minutes": 20})
    after = sum(s.minutes for s in result.plan.sessions if s.task_id == task)
    assert after == before - 20


def test_extra_time_never_makes_the_week_riskier(saved):
    _, data = saved
    result = run(data, {"action": "add_time", "day": (TODAY + timedelta(days=2)).isoformat(),
                        "minutes": 120})
    assert result.report_after.weighted_on_time >= result.report_literal.weighted_on_time - 0.01


# ── consequences are explained ───────────────────────────────────────


def test_every_replan_explains_itself(saved):
    _, data = saved
    result = run(data, {"action": "skip_day"})
    assert result.headline.startswith("Cleared Mon Mar 2.")
    assert result.detail
    summary = result.summary()
    assert set(summary["risk"]) == {"before", "if_you_do_nothing", "after"}
    assert summary["strategy"] in ("minimal", "rebalanced")


# ── validation ───────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    {"action": "teleport"},
    {"action": "skip_day", "day": (TODAY - timedelta(days=1)).isoformat()},
    {"action": "limit_day"},
    {"action": "add_time", "minutes": 0},
    {"action": "push"},
    {"action": "pin", "task_id": "x"},
    {"action": "skip_day", "day": (TODAY + timedelta(days=400)).isoformat()},
])
def test_malformed_intents_are_refused_with_a_reason(payload):
    with pytest.raises(ValueError) as err:
        parse_disruption(payload, TODAY)
    assert str(err.value)


def test_defaults_are_the_obvious_ones():
    assert parse_disruption({"action": "skip_day"}, TODAY).day == TODAY
    assert parse_disruption({"action": "push", "task_id": "x"}, TODAY).day == TODAY + timedelta(days=1)


def test_add_time_can_be_told_the_capacity_is_already_counted(saved):
    _, data = saved
    snap = snapshot_from_schedule(data, {}, TODAY)
    d = Disruption(kind="add_time", day=TODAY, minutes=500, capacity_applied=True)
    from intelliplan.intelligence.rescheduling import apply_disruption

    adjusted = apply_disruption(snap, CAPS, d)
    assert [c.minutes for c in adjusted.capacities] == [c.minutes for c in CAPS]
