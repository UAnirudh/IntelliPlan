"""Recovery tests — reading a week the student did not follow.

The behaviour under test is mostly about restraint: crediting work that was
done, not scolding the student for the future, and not declaring a task
finished on partial progress.
"""

from __future__ import annotations

from datetime import date, timedelta

from intelliplan.services.recovery import (
    build_reality,
    summarise_changes,
)

TODAY = date(2026, 3, 4)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def block(bid, task_id, minutes=60, title="History research", **over):
    base = {
        "block_id": bid,
        "id": bid,
        "task_id": task_id,
        "parent_title": title,
        "assignment": title,
        "duration_minutes": minutes,
        "is_break": False,
    }
    base.update(over)
    return base


def plan(*days):
    return {"schedule": [{"date": d.isoformat(), "blocks": list(bs)} for d, bs in days]}


# ── What got done ─────────────────────────────────────────────────────


def test_a_checked_block_counts_as_work_done():
    facts = build_reality(
        plan((YESTERDAY, [block("b1", "hist", 60)])), {"b1": {"done": True}}, TODAY
    )
    assert facts.reality.completed["hist"] == 60
    assert not facts.missed
    assert not facts.needs_replan


def test_the_older_boolean_progress_shape_still_reads():
    facts = build_reality(
        plan((YESTERDAY, [block("b1", "hist", 60)])), {"b1": True}, TODAY
    )
    assert facts.reality.completed["hist"] == 60


def test_an_unchecked_past_block_is_missed():
    facts = build_reality(plan((YESTERDAY, [block("b1", "hist", 60)])), {}, TODAY)
    assert facts.missed_minutes == 60
    assert facts.reality.missed_task_ids == frozenset({"hist"})
    assert facts.needs_replan


def test_today_is_not_a_failure_yet():
    """An unchecked block at 9am is not a miss. A planner that scolds the
    student for not having done the future is a planner they close."""
    facts = build_reality(plan((TODAY, [block("b1", "hist")])), {}, TODAY)
    assert not facts.missed
    assert not facts.needs_replan


def test_the_future_is_not_a_failure_either():
    facts = build_reality(plan((TOMORROW, [block("b1", "hist")])), {}, TODAY)
    assert not facts.missed


def test_breaks_are_not_work_to_miss():
    facts = build_reality(
        plan((YESTERDAY, [block("b1", "x", is_break=True, title="Long break")])),
        {},
        TODAY,
    )
    assert not facts.missed


# ── Finished vs partly done ───────────────────────────────────────────


def test_a_task_is_finished_only_when_every_sitting_is_checked():
    """Marking it finished on partial progress makes work vanish from the plan
    while the assignment is still outstanding."""
    data = plan(
        (YESTERDAY, [block("b1", "paper", 60), block("b2", "paper", 60)]),
    )
    partial = build_reality(data, {"b1": {"done": True}}, TODAY)
    assert "paper" not in partial.reality.finished_task_ids

    whole = build_reality(data, {"b1": {"done": True}, "b2": {"done": True}}, TODAY)
    assert "paper" in whole.reality.finished_task_ids


def test_a_future_sitting_keeps_a_task_unfinished():
    data = plan(
        (YESTERDAY, [block("b1", "paper", 60)]),
        (TOMORROW, [block("b2", "paper", 60)]),
    )
    facts = build_reality(data, {"b1": {"done": True}}, TODAY)
    assert "paper" not in facts.reality.finished_task_ids


# ── Abandoned sittings ────────────────────────────────────────────────


def test_abandoned_minutes_are_credited_not_discarded():
    """Twenty minutes of real work is the difference between "you did nothing"
    and "you started". Re-scheduling time the student already spent is how a
    plan starts feeling like it is not listening."""
    facts = build_reality(
        plan((YESTERDAY, [block("b1", "hist", 60)])),
        {},
        TODAY,
        abandoned_minutes={"hist": 20},
    )
    assert facts.reality.abandoned["hist"] == 20
    assert "hist" in facts.reality.missed_task_ids


def test_junk_abandoned_values_are_dropped():
    facts = build_reality(
        plan((YESTERDAY, [block("b1", "hist")])), {}, TODAY,
        abandoned_minutes={"hist": 0, "": 30},
    )
    assert facts.reality.abandoned == {}


# ── Lost days ─────────────────────────────────────────────────────────


def test_a_day_with_work_and_no_progress_is_a_lost_day():
    facts = build_reality(
        plan((YESTERDAY, [block("b1", "hist"), block("b2", "math", title="Math")])),
        {},
        TODAY,
    )
    assert facts.lost_days == (YESTERDAY,)


def test_a_day_with_partial_progress_is_not_lost():
    facts = build_reality(
        plan((YESTERDAY, [block("b1", "hist"), block("b2", "math", title="Math")])),
        {"b1": {"done": True}},
        TODAY,
    )
    assert facts.lost_days == ()


# ── Robustness ────────────────────────────────────────────────────────


def test_an_empty_plan_recovers_to_nothing():
    facts = build_reality({}, {}, TODAY)
    assert not facts.needs_replan
    assert facts.completed_minutes == 0


def test_garbage_shapes_do_not_raise():
    facts = build_reality(
        {"schedule": [None, {"date": "not-a-date", "blocks": [None, 7]}]},
        {"b1": "yes"},
        TODAY,
    )
    assert not facts.missed


# ── Explaining the diff ───────────────────────────────────────────────


def test_a_moved_assignment_is_reported_with_both_dates():
    before = plan((YESTERDAY, [block("b1", "hist")]))
    after = plan((TOMORROW, [block("b9", "hist")]))
    facts = build_reality(before, {}, TODAY)
    changes = summarise_changes(before, after, facts)
    assert changes
    moved = changes[0]
    assert moved.kind == "moved"
    assert moved.from_day == YESTERDAY and moved.to_day == TOMORROW
    assert "didn't get to this" in moved.reason


def test_work_that_vanished_is_reported_first():
    before = plan(
        (TOMORROW, [block("b1", "hist"), block("b2", "math", title="Math")])
    )
    after = plan((TOMORROW, [block("b3", "hist")]))
    kinds = [c.kind for c in summarise_changes(before, after, None)]
    assert kinds[0] == "dropped"


def test_new_work_is_reported_as_added():
    before = plan((TOMORROW, [block("b1", "hist")]))
    after = plan(
        (TOMORROW, [block("b1", "hist"), block("b2", "essay", title="Essay")])
    )
    added = [c for c in summarise_changes(before, after, None) if c.kind == "added"]
    assert [c.title for c in added] == ["Essay"]


def test_an_unchanged_plan_reports_no_changes():
    same = plan((TOMORROW, [block("b1", "hist")]))
    assert summarise_changes(same, same, None) == []


def test_regenerated_block_ids_are_not_reported_as_changes():
    """Block ids are recreated on every plan. Diffing them would report that
    the entire week changed every single time."""
    before = plan((TOMORROW, [block("b1", "hist")]))
    after = plan((TOMORROW, [block("totally-different-id", "hist")]))
    assert summarise_changes(before, after, None) == []


def test_the_change_list_is_capped():
    before = plan(
        (TOMORROW, [block(f"b{i}", f"t{i}", title=f"Task {i}") for i in range(20)])
    )
    after = plan((TOMORROW, []))
    assert len(summarise_changes(before, after, None, limit=5)) == 5


def test_changes_serialise_for_the_client():
    before = plan((YESTERDAY, [block("b1", "hist")]))
    after = plan((TOMORROW, [block("b2", "hist")]))
    payload = summarise_changes(before, after, None)[0].to_dict()
    assert payload["from"] == YESTERDAY.isoformat()
    assert payload["to"] == TOMORROW.isoformat()
    assert payload["title"] == "History research"
