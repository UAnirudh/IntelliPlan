"""Overrides — the student moves work, and is told what it costs."""

from __future__ import annotations

from datetime import date, timedelta

from intelliplan.intelligence import overrides
from intelliplan.intelligence.planner import DayCapacity, PlannerTask, build_plan

TODAY = date(2026, 8, 21)


def task(tid, *, days=3, minutes=120, priority=70, course="Physics"):
    return PlannerTask(
        id=tid, title=f"Task {tid}", course=course, kind="homework",
        due_date=TODAY + timedelta(days=days), est_minutes=minutes,
        priority=priority, points_possible=50,
    )


def caps(days=7, minutes=150):
    return [DayCapacity(day=TODAY + timedelta(days=i), minutes=minutes)
            for i in range(days)]


def a_plan(tasks=None, day_count=7):
    tasks = tasks or [task("t1"), task("t2", days=6, minutes=200)]
    return build_plan(tasks, caps(days=day_count), today=TODAY), tasks


def minutes_on(plan, day):
    for d in plan.days:
        if d.day == day:
            return d.scheduled_minutes
    return 0


def keys(consequences):
    return {c.key for c in consequences}


# ── moving ───────────────────────────────────────────────────────────


def test_moving_work_takes_it_off_today_and_puts_it_on_tomorrow():
    before, _ = a_plan()
    after, moved, target = overrides.move_task(before, task_id="t1", from_day=TODAY)
    assert moved > 0
    assert target == TODAY + timedelta(days=1)
    assert minutes_on(after, TODAY) == minutes_on(before, TODAY) - moved
    assert minutes_on(after, target) == minutes_on(before, target) + moved


def test_moving_something_that_is_not_there_changes_nothing():
    before, _ = a_plan()
    after, moved, target = overrides.move_task(before, task_id="ghost", from_day=TODAY)
    assert (after, moved, target) == (before, 0, None)


def test_moving_off_the_last_day_becomes_a_stated_deferral_not_a_deletion():
    tasks = [task("t1", days=1, minutes=90)]
    before = build_plan(tasks, caps(days=1), today=TODAY)
    after, moved, target = overrides.move_task(before, task_id="t1", from_day=TODAY)
    assert target is None
    assert moved > 0
    assert [d.task_id for d in after.deferred] == ["t1"]
    assert after.overloaded


def test_a_moved_plan_recomputes_its_own_totals():
    before, _ = a_plan()
    after, moved, _ = overrides.move_task(before, task_id="t1", from_day=TODAY)
    assert after.total_minutes == sum(d.scheduled_minutes for d in after.days)
    assert after.total_minutes == before.total_minutes


# ── skipping and shortening ──────────────────────────────────────────


def test_skipping_records_the_work_as_deferred_rather_than_absorbing_it():
    before, _ = a_plan()
    after, dropped = overrides.skip_task(before, task_id="t1", day=TODAY)
    assert dropped > 0
    assert after.total_minutes == before.total_minutes - dropped
    assert any(d.task_id == "t1" for d in after.deferred)
    assert "skip" in after.deferred[0].reason.lower()


def test_shortening_keeps_part_of_the_sitting_and_defers_the_rest():
    before, _ = a_plan()
    original = sum(s.minutes for s in before.days[0].sessions if s.task_id == "t1")
    after, cut = overrides.shorten_task(
        before, task_id="t1", day=TODAY, minutes=original - 20
    )
    assert cut == 20
    kept = sum(s.minutes for s in after.days[0].sessions if s.task_id == "t1")
    assert kept == original - 20
    assert sum(d.minutes for d in after.deferred) == 20


def test_shortening_to_a_longer_length_is_a_no_op():
    before, _ = a_plan()
    after, cut = overrides.shorten_task(before, task_id="t1", day=TODAY, minutes=9999)
    assert (after, cut) == (before, 0)


def test_shortening_to_zero_is_refused_rather_than_silently_deleting():
    before, _ = a_plan()
    after, cut = overrides.shorten_task(before, task_id="t1", day=TODAY, minutes=0)
    assert (after, cut) == (before, 0)


# ── consequences ─────────────────────────────────────────────────────


def test_a_move_reports_the_minutes_freed_today_and_added_tomorrow():
    """The example from the brief, verbatim: frees time tonight, costs it
    tomorrow, and says both."""
    before, tasks = a_plan()
    after, moved, target = overrides.move_task(before, task_id="t1", from_day=TODAY)
    report = overrides.evaluate(before, after, tasks, TODAY,
                                affected_days=(TODAY, target))

    freed = next(c for c in report.gains if c.key == f"minutes:{TODAY.isoformat()}")
    added = next(c for c in report.costs if c.key == f"minutes:{target.isoformat()}")
    assert freed.delta == -moved
    assert added.delta == moved


def test_pushing_work_out_of_the_plan_is_reported_as_a_cost():
    tasks = [task("t1", days=1, minutes=90)]
    before = build_plan(tasks, caps(days=1), today=TODAY)
    after, _, _ = overrides.move_task(before, task_id="t1", from_day=TODAY)
    report = overrides.evaluate(before, after, tasks, TODAY)
    assert "deferred" in keys(report.costs)
    assert report.infeasible_reason


def test_the_student_is_never_blocked_from_overriding():
    """Even the worst override is allowed. The product tells them the price;
    it does not refuse."""
    tasks = [task("t1", days=0, minutes=90)]
    before = build_plan(tasks, caps(days=1), today=TODAY)
    after, _, _ = overrides.move_task(before, task_id="t1", from_day=TODAY)
    assert overrides.evaluate(before, after, tasks, TODAY).accepted_possible


def test_an_override_that_changes_nothing_says_so_plainly():
    before, tasks = a_plan()
    report = overrides.evaluate(before, before, tasks, TODAY)
    assert report.gains == ()
    assert report.costs == ()
    assert "almost nothing" in report.summary


def test_tiny_deltas_are_not_reported_as_consequences():
    """A consequence list padded with rounding noise is one nobody reads."""
    before, tasks = a_plan()
    after, _, _ = overrides.move_task(before, task_id="t1", from_day=TODAY)
    for c in list(report_all(before, after, tasks)):
        if c.key.startswith("minutes:"):
            assert abs(c.delta) >= overrides.MINUTE_NOISE_FLOOR
        else:
            assert abs(c.delta) >= overrides.METRIC_NOISE_FLOOR


def report_all(before, after, tasks):
    report = overrides.evaluate(before, after, tasks, TODAY)
    return list(report.gains) + list(report.costs)


def test_the_summary_always_names_both_sides_when_both_exist():
    before, tasks = a_plan()
    after, _, _ = overrides.move_task(before, task_id="t1", from_day=TODAY)
    report = overrides.evaluate(before, after, tasks, TODAY)
    if report.gains and report.costs:
        assert report.gains[0].label in report.summary
        assert report.costs[0].label in report.summary


def test_the_outcome_serialises_for_the_api():
    before, tasks = a_plan()
    after, moved, target = overrides.move_task(before, task_id="t1", from_day=TODAY)
    outcome = overrides.OverrideOutcome(
        plan=after,
        report=overrides.evaluate(before, after, tasks, TODAY),
        moved_minutes=moved,
        target_day=target,
    )
    payload = outcome.to_dict()
    assert payload["moved_minutes"] == moved
    assert payload["target_day"] == target.isoformat()
    assert payload["accepted_possible"] is True
    assert isinstance(payload["gains"], list)


def test_consequences_use_the_behavioural_odds_when_they_are_supplied():
    before, tasks = a_plan()
    after, _, _ = overrides.move_task(before, task_id="t1", from_day=TODAY)

    calls: list[str] = []

    def odds(course, minutes, day):
        calls.append(course)
        return 0.5

    overrides.evaluate(before, after, tasks, TODAY, completion_probability=odds)
    assert calls


# ── rebalance offer ──────────────────────────────────────────────────


def test_a_harmless_move_does_not_prompt_a_rebalance():
    """Prompting after every move trains people to dismiss the prompt.

    "Harmless" has to mean genuinely harmless: a roomy week where the
    receiving day absorbs the work without going over. The default fixture
    is *not* that — moving t1 there pushes tomorrow past its capacity, which
    is precisely when the student should be told.
    """
    tasks = [task("t1", days=5, minutes=60)]
    before = build_plan(tasks, caps(days=7, minutes=400), today=TODAY)
    after, moved, target = overrides.move_task(before, task_id="t1", from_day=TODAY)
    assert moved > 0
    worth, message = overrides.rebalance_worth_offering(after, touched=(TODAY, target))
    assert worth is False
    assert message == ""


def test_a_move_that_overloads_the_receiving_day_does_prompt():
    """The default fixture: 80 minutes moved onto a day that then holds 168
    against a 150-minute capacity."""
    before, _ = a_plan()
    after, moved, target = overrides.move_task(before, task_id="t1", from_day=TODAY)
    assert moved > 0
    worth, message = overrides.rebalance_worth_offering(after, touched=(TODAY, target))
    assert worth is True
    assert "over what you have free" in message


def test_work_with_nowhere_to_go_prompts_a_rebalance():
    tasks = [task("t1", days=1, minutes=90)]
    before = build_plan(tasks, caps(days=1), today=TODAY)
    after, _, _ = overrides.move_task(before, task_id="t1", from_day=TODAY)
    worth, message = overrides.rebalance_worth_offering(after)
    assert worth is True
    assert "no longer has a place" in message


def test_an_empty_plan_is_never_offered_a_rebalance():
    from intelliplan.intelligence.planner import Plan

    worth, message = overrides.rebalance_worth_offering(Plan(days=()))
    assert worth is False
    assert message == ""


def test_the_offer_rides_along_on_the_outcome_payload():
    tasks = [task("t1", days=1, minutes=90)]
    before = build_plan(tasks, caps(days=1), today=TODAY)
    after, moved, target = overrides.move_task(before, task_id="t1", from_day=TODAY)
    _, offer = overrides.rebalance_worth_offering(after)
    outcome = overrides.OverrideOutcome(
        plan=after,
        report=overrides.evaluate(before, after, tasks, TODAY),
        moved_minutes=moved,
        target_day=target,
        rebalance_offer=offer,
    )
    assert outcome.to_dict()["rebalance_offer"] == offer
    assert offer
