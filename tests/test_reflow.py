"""Tests for reflow_schedule() — the pass that re-times a hand-dragged plan.

The invariant that matters: reflow must never reorder blocks. The student just
arranged them; our job is only to assign honest clock times and say plainly
when something no longer fits.
"""

from datetime import date, timedelta

import pytest

import App
import scheduler_engine as se
from App import reflow_schedule


def _day(d, *titles, minutes=50):
    return {
        "date": d.strftime("%Y-%m-%d"),
        "day_name": d.strftime("%A"),
        "blocks": [
            {"assignment": t, "course": "Math", "duration_minutes": minutes,
             "difficulty": "Medium", "is_break": False}
            for t in titles
        ],
    }


def _titles(day):
    """Work-block titles in order. Auto-inserted breaks are not the student's
    arrangement, so they're excluded."""
    return [b["assignment"] for b in day["blocks"] if not b.get("is_break")]


@pytest.fixture
def two_days():
    d0 = date.today() + timedelta(days=3)   # far enough out that "now" never trims it
    d1 = d0 + timedelta(days=1)
    return d0, d1


def _avail(*dates, slots=("evening",)):
    return {se.DAY_ABBR[d.weekday()]: list(slots) for d in dates}


def test_block_order_is_never_changed(two_days):
    d0, d1 = two_days
    data = {"schedule": [_day(d0, "C", "A", "B")]}
    out = reflow_schedule(data, availability=_avail(d0), dna=se.StudyDNA())
    assert _titles(out["schedule"][0]) == ["C", "A", "B"]


def test_hard_work_is_not_reshuffled_into_a_best_slot(two_days):
    # place_day_blocks() steers Hard work toward the measured best slot during
    # generation. During reflow that would undo a deliberate drag.
    d0, _ = two_days
    data = {"schedule": [_day(d0, "Easy one", "Hard one")]}
    data["schedule"][0]["blocks"][1]["difficulty"] = "Hard"
    dna = se.StudyDNA(sample_size=20, best_slot="morning")
    out = reflow_schedule(data, availability=_avail(d0, slots=("morning", "evening")), dna=dna)
    assert _titles(out["schedule"][0]) == ["Easy one", "Hard one"]


def test_times_land_inside_the_students_real_window(two_days):
    d0, _ = two_days
    out = reflow_schedule({"schedule": [_day(d0, "A", "B")]},
                          availability=_avail(d0), dna=se.StudyDNA())
    for b in out["schedule"][0]["blocks"]:
        hour = int(b["start_iso"][11:13])
        assert 17 <= hour < 22


def test_a_block_dragged_onto_a_commitment_is_flagged_and_moved(two_days):
    d0, d1 = two_days
    # Evening is 17-22. A commitment covering all of it leaves no room.
    busy = f"practice {se.DAY_ABBR[d0.weekday()]} 5-10 pm"
    data = {"schedule": [_day(d0, "A"), _day(d1, "B")]}
    out = reflow_schedule(data, availability=_avail(d0, d1),
                          commitments=busy, dna=se.StudyDNA())
    assert out["schedule"][0]["blocks"] == []
    assert _titles(out["schedule"][1]) == ["A", "B"]
    assert any("no free time" in n.lower() for n in out["placement_notes"])


def test_work_with_nowhere_to_go_is_marked_unplaced_not_dropped(two_days):
    d0, _ = two_days
    # One 45-minute-wide window, three 50-minute blocks.
    busy = f"practice {se.DAY_ABBR[d0.weekday()]} 6-10 pm"
    data = {"schedule": [_day(d0, "A", "B", "C")]}
    out = reflow_schedule(data, availability=_avail(d0), commitments=busy, dna=se.StudyDNA())
    blocks = out["schedule"][0]["blocks"]
    assert sorted(b["assignment"] for b in blocks) == ["A", "B", "C"]  # nothing lost
    unplaced = [b for b in blocks if b.get("unplaced")]
    assert len(unplaced) == 2
    assert all(b["conflict"] == "unplaceable" for b in unplaced)


def test_repeated_reflows_do_not_accumulate_breaks(two_days):
    # Every drag triggers a reflow. Re-running the break rule on a plan that
    # already has its breaks would grow the day by one block per drag.
    d0, _ = two_days
    data = {"schedule": [_day(d0, "A", "B", "C")]}
    counts = []
    for _ in range(4):
        data = reflow_schedule(data, availability=_avail(d0), dna=se.StudyDNA())
        counts.append(len(data["schedule"][0]["blocks"]))
    assert len(set(counts)) == 1, f"block count drifted across reflows: {counts}"


def test_a_break_the_plan_authored_survives_reflow(two_days):
    d0, _ = two_days
    data = {"schedule": [_day(d0, "A")]}
    data["schedule"][0]["blocks"].append(
        {"assignment": "Dinner", "duration_minutes": 30, "is_break": True}
    )
    out = reflow_schedule(data, availability=_avail(d0), dna=se.StudyDNA())
    assert any(b.get("assignment") == "Dinner" for b in out["schedule"][0]["blocks"])


def test_stale_conflict_flags_are_cleared_on_a_clean_reflow(two_days):
    d0, _ = two_days
    data = {"schedule": [_day(d0, "A")]}
    data["schedule"][0]["blocks"][0]["conflict"] = "no_room"
    data["schedule"][0]["blocks"][0]["unplaced"] = True
    out = reflow_schedule(data, availability=_avail(d0), dna=se.StudyDNA())
    b = out["schedule"][0]["blocks"][0]
    assert "conflict" not in b and "unplaced" not in b


def test_day_totals_are_refreshed_after_blocks_move(two_days):
    d0, d1 = two_days
    data = {"schedule": [_day(d0, "A", "B"), _day(d1)]}
    data["schedule"][0]["total_hours"] = 99  # stale value from before the drag
    out = reflow_schedule(data, availability=_avail(d0, d1), dna=se.StudyDNA())
    assert out["schedule"][0]["total_hours"] == pytest.approx(100 / 60, abs=0.1)
    assert out["schedule"][0]["study_minutes"] == 100


def test_guest_without_availability_is_passed_through_untouched(two_days):
    d0, _ = two_days
    data = {"schedule": [_day(d0, "A", "B")]}
    out = reflow_schedule(data)  # no availability, no dna
    assert _titles(out["schedule"][0]) == ["A", "B"]
    assert out["placement_notes"] == []


def test_empty_schedule_is_a_no_op():
    assert reflow_schedule({"schedule": []})["schedule"] == []


# ── endpoint ──────────────────────────────────────────────────────


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    with App.app.test_client() as c:
        yield c


def test_reflow_endpoint_rejects_a_missing_payload(client):
    assert client.post("/schedule/reflow", json={}).status_code == 400


def test_reflow_endpoint_rejects_an_empty_schedule(client):
    r = client.post("/schedule/reflow", json={"schedule_data": {"schedule": []}})
    assert r.status_code == 400


def test_reflow_endpoint_returns_the_retimed_plan(client, two_days):
    d0, _ = two_days
    r = client.post("/schedule/reflow", json={"schedule_data": {"schedule": [_day(d0, "A")]}})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert _titles(body["data"]["schedule"][0]) == ["A"]
