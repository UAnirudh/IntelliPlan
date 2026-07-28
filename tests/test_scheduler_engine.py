"""Unit tests for the personalized scheduling engine.

The engine is deliberately free of Flask/DB imports, so these run standalone:
    pytest tests/test_scheduler_engine.py
"""

from datetime import date, datetime

import pytest

from scheduler_engine import (
    DEFAULT_STAMINA_MINUTES,
    MIN_SAMPLES_FOR_SIGNAL,
    SLOT_WINDOWS,
    StudyDNA,
    Window,
    build_study_dna,
    describe_week,
    parse_commitments,
    place_day_blocks,
    summarize_progress,
    windows_for_date,
)

MONDAY = date(2026, 7, 27)  # a Monday
SATURDAY = date(2026, 8, 1)


# ── parse_commitments ─────────────────────────────────────────────


def test_parses_the_placeholder_example_from_settings():
    busy = parse_commitments("soccer Mon/Wed 4–6 pm, piano Fri 5 pm")
    assert busy["Mon"] == [(16 * 60, 18 * 60)]
    assert busy["Wed"] == [(16 * 60, 18 * 60)]
    assert busy["Fri"] == [(17 * 60, 18 * 60)]  # single time → 1 hour


def test_trailing_meridiem_applies_to_both_ends_of_a_range():
    assert parse_commitments("band Tue 4-6 pm")["Tue"] == [(16 * 60, 18 * 60)]


def test_meridiem_does_not_invert_a_range_that_crosses_noon():
    # "11–1 pm" must be 11 AM to 1 PM, not 11 PM to 1 PM.
    assert parse_commitments("lab Thu 11–1 pm")["Thu"] == [(11 * 60, 13 * 60)]


def test_bare_hours_are_read_as_afternoon_for_a_student():
    assert parse_commitments("practice Mon 4-6")["Mon"] == [(16 * 60, 18 * 60)]


def test_full_day_names_and_and_separator():
    busy = parse_commitments("debate Monday and Thursday 3-5 pm")
    assert busy["Mon"] == busy["Thu"] == [(15 * 60, 17 * 60)]


@pytest.mark.parametrize("text", ["", None, "just some free text", "12345"])
def test_unparseable_commitments_yield_empty_dict(text):
    assert parse_commitments(text) == {}


# ── windows_for_date ──────────────────────────────────────────────


def test_windows_follow_recorded_availability_not_preferred_time():
    avail = {"Mon": ["morning"]}
    windows = windows_for_date(
        MONDAY, avail, preferred_time="evening", now=datetime(2026, 7, 20, 8, 0)
    )
    assert len(windows) == 1
    assert windows[0].start.hour == SLOT_WINDOWS["morning"][0]
    assert windows[0].end.hour == SLOT_WINDOWS["morning"][1]


def test_falls_back_to_preferred_time_when_day_has_no_availability():
    windows = windows_for_date(
        SATURDAY, {"Mon": ["morning"]}, preferred_time="afternoon",
        now=datetime(2026, 7, 20, 8, 0),
    )
    assert windows[0].start.hour == SLOT_WINDOWS["afternoon"][0]


def test_full_day_name_keys_are_accepted():
    windows = windows_for_date(
        MONDAY, {"Monday": ["morning"]}, now=datetime(2026, 7, 20, 8, 0)
    )
    assert windows[0].start.hour == 6


def test_commitments_carve_a_hole_in_the_free_window():
    windows = windows_for_date(
        MONDAY, {"Mon": ["evening"]}, commitments="soccer Mon 6-7 pm",
        now=datetime(2026, 7, 20, 8, 0),
    )
    # Evening is 17–22; soccer 18–19 splits it into 17–18 and 19–22.
    assert [(w.start.hour, w.end.hour) for w in windows] == [(17, 18), (19, 22)]


def test_adjacent_slots_merge_into_one_window():
    windows = windows_for_date(
        MONDAY, {"Mon": ["afternoon", "evening"]}, now=datetime(2026, 7, 20, 8, 0)
    )
    assert len(windows) == 1
    assert (windows[0].start.hour, windows[0].end.hour) == (12, 22)


def test_today_is_trimmed_to_start_after_now():
    now = datetime(2026, 7, 27, 19, 12)
    windows = windows_for_date(MONDAY, {"Mon": ["evening"]}, now=now)
    assert windows[0].start >= now
    assert windows[0].start.hour == 19


def test_slivers_below_the_minimum_are_dropped():
    now = datetime(2026, 7, 27, 21, 50)  # 10 minutes left of the evening slot
    assert windows_for_date(MONDAY, {"Mon": ["evening"]}, now=now) == []


# ── build_study_dna ───────────────────────────────────────────────


def _row(est=60, act=60, course="Math", day="Mon", slot="evening"):
    return {
        "estimated_time": est, "actual_time": act, "course": course,
        "day_of_week": day, "time_of_day": slot,
    }


def test_no_history_produces_no_signal():
    dna = build_study_dna([])
    assert not dna.has_signal
    assert dna.to_prompt() == ""
    assert dna.stamina_minutes == DEFAULT_STAMINA_MINUTES


def test_a_single_row_is_not_enough_to_act_on():
    assert not build_study_dna([_row()]).has_signal


def test_detects_a_student_who_underestimates():
    dna = build_study_dna([_row(est=60, act=90) for _ in range(6)])
    assert dna.estimation_ratio == pytest.approx(1.5)
    assert dna.adjust_estimate(60) == 90
    assert "50% longer than" in dna.to_prompt()


def test_per_course_bias_overrides_the_global_ratio():
    rows = [_row(est=60, act=60, course="Art") for _ in range(6)]
    rows += [_row(est=60, act=120, course="Physics") for _ in range(3)]
    dna = build_study_dna(rows)
    assert dna.adjust_estimate(60, "Physics") == 120
    assert dna.adjust_estimate(60, "Art") == 60


def test_absurd_ratios_are_discarded_as_bad_data():
    # A 60-minute estimate "actually" taking 1 minute is a mis-click.
    rows = [_row(est=60, act=1) for _ in range(6)]
    assert build_study_dna(rows).estimation_ratio is None


def test_best_slot_is_where_the_student_actually_finishes_work():
    rows = [_row(slot="morning") for _ in range(8)] + [_row(slot="evening")]
    assert build_study_dna(rows).best_slot == "morning"


def test_stamina_tracks_the_median_finished_block():
    dna = build_study_dna([_row(est=60, act=30) for _ in range(6)])
    assert dna.stamina_minutes == 30


def test_stamina_is_clamped_to_a_sane_range():
    dna = build_study_dna([_row(est=200, act=400) for _ in range(6)])
    assert dna.stamina_minutes == 90


def test_low_adherence_asks_for_a_lighter_plan():
    dna = build_study_dna(
        [_row() for _ in range(6)], progress_records=[{"total": 20, "done": 4}]
    )
    assert dna.adherence == 0.2
    assert "plan fewer, shorter blocks" in dna.to_prompt()


def test_high_adherence_unlocks_an_ambitious_plan():
    dna = build_study_dna(
        [_row() for _ in range(6)], progress_records=[{"total": 20, "done": 19}]
    )
    assert "full, ambitious plan" in dna.to_prompt()


def test_adjust_estimate_is_identity_without_signal():
    assert StudyDNA().adjust_estimate(75) == 75


# ── summarize_progress ────────────────────────────────────────────


def test_summarize_progress_handles_the_current_client_shape():
    assert summarize_progress({"d1-b1": {"done": True}, "d1-b2": {"done": False}}) == {
        "total": 2, "done": 1,
    }


def test_summarize_progress_handles_legacy_bare_booleans():
    assert summarize_progress({"a": True, "b": False}) == {"total": 2, "done": 1}


def test_summarize_progress_accepts_a_json_string():
    assert summarize_progress('{"a": {"done": true}}') == {"total": 1, "done": 1}


@pytest.mark.parametrize("bad", ["not json", None, 42, []])
def test_summarize_progress_never_raises_on_garbage(bad):
    assert summarize_progress(bad) == {"total": 0, "done": 0}


# ── place_day_blocks ──────────────────────────────────────────────


def _window(h1, h2, day=MONDAY):
    return Window(
        start=datetime.combine(day, datetime.min.time()).replace(hour=h1),
        end=datetime.combine(day, datetime.min.time()).replace(hour=h2),
    )


def _block(title="Essay", minutes=45, difficulty="Medium", is_break=False):
    return {
        "assignment": title, "course": "English", "duration_minutes": minutes,
        "difficulty": difficulty, "is_break": is_break,
    }


def test_blocks_land_inside_the_given_window():
    placed, overflow = place_day_blocks([_block(), _block()], [_window(17, 22)])
    assert not overflow
    for b in placed:
        start = datetime.fromisoformat(b["start_iso"])
        end = datetime.fromisoformat(b["end_iso"])
        assert start.hour >= 17 and end.hour <= 22


def test_blocks_never_overlap_each_other():
    placed, _ = place_day_blocks([_block() for _ in range(3)], [_window(17, 22)])
    for prev, cur in zip(placed, placed[1:]):
        assert datetime.fromisoformat(prev["end_iso"]) <= datetime.fromisoformat(cur["start_iso"])


def test_work_that_does_not_fit_overflows_instead_of_running_past_midnight():
    # One 45-minute window cannot hold three 45-minute blocks.
    placed, overflow = place_day_blocks([_block() for _ in range(3)], [_window(21, 22)])
    assert len(placed) == 1
    assert len(overflow) == 2


def test_blocks_hop_to_the_next_window_when_the_first_fills_up():
    placed, overflow = place_day_blocks(
        [_block(minutes=50) for _ in range(2)], [_window(7, 8), _window(17, 22)]
    )
    assert not overflow
    assert datetime.fromisoformat(placed[0]["start_iso"]).hour == 7
    assert datetime.fromisoformat(placed[1]["start_iso"]).hour == 17


def test_no_windows_means_everything_overflows():
    placed, overflow = place_day_blocks([_block()], [])
    assert placed == []
    assert len(overflow) == 1


def test_a_long_block_is_trimmed_to_the_students_focus_length():
    dna = StudyDNA(sample_size=10, stamina_minutes=30)
    placed, _ = place_day_blocks([_block(minutes=180)], [_window(17, 22)], dna)
    assert placed[0]["duration_minutes"] == 45  # 30 * 1.5
    assert "split_note" in placed[0]


def test_hard_work_is_steered_into_the_measured_best_slot():
    dna = StudyDNA(sample_size=10, best_slot="morning")
    placed, _ = place_day_blocks(
        [_block("Easy reading", difficulty="Easy"), _block("Hard proof", difficulty="Hard")],
        [_window(17, 19), _window(7, 9)],
        dna,
    )
    hard = next(b for b in placed if b["assignment"] == "Hard proof")
    assert hard["window_slot"] == "morning"


def test_a_long_run_of_work_gets_a_forced_break():
    placed, _ = place_day_blocks([_block(minutes=50) for _ in range(3)], [_window(15, 22)])
    assert any(b.get("is_break") and b["assignment"] == "Long break" for b in placed)


def test_the_day_never_ends_on_a_break():
    # A trailing "take a break" block is just a block that says stop working.
    placed, _ = place_day_blocks([_block(minutes=50) for _ in range(2)], [_window(15, 22)])
    assert not placed[-1].get("is_break")


def test_empty_input_is_a_no_op():
    assert place_day_blocks([], [_window(17, 22)]) == ([], [])


# ── describe_week ─────────────────────────────────────────────────


def test_describe_week_is_empty_without_availability():
    assert describe_week(None) == ""


def test_describe_week_lists_real_hours_and_busy_periods():
    text = describe_week({"Mon": ["evening"], "Tue": []}, "soccer Mon 6-7 pm")
    assert "Mon: evening" in text
    assert "busy" in text
    assert "Tue: no study time available" in text
