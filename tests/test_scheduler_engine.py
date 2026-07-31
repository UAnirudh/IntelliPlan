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
    strip_auto_breaks,
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


def test_a_later_block_backfills_room_left_in_an_earlier_window():
    # Placement used to walk windows one way only: once a block was too big for
    # the morning and moved to the evening, every block after it was stuck in
    # the evening too — even a short one that fit the gap the big block left.
    # Here 90 minutes cannot fit the 70 left after the first block, but the
    # 30-minute block that follows it can.
    placed, overflow = place_day_blocks(
        [_block("Long essay", minutes=100), _block("Problem set", minutes=90),
         _block("Flashcards", minutes=30)],
        [_window(7, 10), _window(17, 22)],
    )
    assert not overflow
    cards = next(b for b in placed if b["assignment"] == "Flashcards")
    assert datetime.fromisoformat(cards["start_iso"]).hour < 12, (
        "the short block should use the morning gap, not pile into the evening"
    )


def test_backfilling_never_overlaps_what_is_already_in_that_window():
    placed, _ = place_day_blocks(
        [_block("A", minutes=100), _block("B", minutes=90), _block("C", minutes=30),
         _block("D", minutes=25)],
        [_window(7, 10), _window(17, 22)],
    )
    by_window = {}
    for b in placed:
        by_window.setdefault(datetime.fromisoformat(b["start_iso"]).hour < 12, []).append(b)
    for group in by_window.values():
        group.sort(key=lambda b: b["start_iso"])
        for prev, cur in zip(group, group[1:]):
            assert datetime.fromisoformat(prev["end_iso"]) <= datetime.fromisoformat(cur["start_iso"])


def test_placed_blocks_come_back_in_clock_order():
    # The scheduler renders this list top to bottom, so a block that backfilled
    # an earlier window must not appear below the evening work it was placed
    # after.
    placed, _ = place_day_blocks(
        [_block("A", minutes=100), _block("B", minutes=90), _block("C", minutes=30)],
        [_window(7, 10), _window(17, 22)],
    )
    starts = [b["start_iso"] for b in placed]
    assert starts == sorted(starts)


def test_reflow_never_backfills_a_hand_arranged_plan():
    # Backfilling is right when the engine chose the order and wrong when the
    # student did. Dragging three blocks into place and getting the third one
    # back above the second is the plan rearranging itself under their hands.
    placed, _ = place_day_blocks(
        [_block("First", minutes=100), _block("Second", minutes=90),
         _block("Third", minutes=30)],
        [_window(7, 10), _window(17, 22)],
        preserve_order=True,
    )
    titles = [b["assignment"] for b in placed if not b.get("is_break")]
    assert titles == ["First", "Second", "Third"]


def test_backfill_cannot_push_more_urgent_work_into_overflow():
    dna = StudyDNA(sample_size=10, best_slot="morning")
    urgent = _block("Due tomorrow", minutes=90)
    urgent["due_date"] = "2026-07-28"
    later = _block("Due next month", minutes=30)
    later["due_date"] = "2026-08-28"
    placed, overflow = place_day_blocks([later, urgent], [_window(7, 9), _window(17, 18)], dna)
    assert [b["assignment"] for b in overflow] == []
    assert next(b for b in placed if b["assignment"] == "Due tomorrow")


def test_no_windows_means_everything_overflows():
    placed, overflow = place_day_blocks([_block()], [])
    assert placed == []
    assert len(overflow) == 1


def test_a_long_block_is_split_into_sittings_not_trimmed():
    # This used to assert the 180-minute block came back as a single 45-minute
    # one. That threw away 135 minutes of required work: the student was shown
    # a plan that could not possibly finish the assignment, and the only record
    # of it was a `split_note` key nothing rendered.
    dna = StudyDNA(sample_size=10, stamina_minutes=30)
    placed, overflow = place_day_blocks([_block(minutes=180)], [_window(17, 22)], dna)
    work = [b for b in placed if not b.get("is_break")]
    assert len(work) == 4                                   # 180 / (30 * 1.5)
    assert all(b["duration_minutes"] <= 45 for b in work)
    total = sum(b["duration_minutes"] for b in work) + sum(
        b["duration_minutes"] for b in overflow
    )
    assert total == 180, "every minute of the assignment must survive the split"


def test_split_parts_say_which_sitting_they_are():
    dna = StudyDNA(sample_size=10, stamina_minutes=30)
    placed, _ = place_day_blocks([_block("Lab report", minutes=120)], [_window(15, 22)], dna)
    work = [b for b in placed if not b.get("is_break")]
    assert [b["part_index"] for b in work] == [1, 2, 3]
    assert all(b["part_total"] == 3 for b in work)
    assert "part 1 of 3" in work[0]["assignment"].lower()


def test_a_split_leaves_no_useless_stub_sitting():
    # 100 minutes with a 45-minute cap is 3 sittings. Filling greedily gives
    # 45 + 45 + 10, and a 10-minute sitting is not worth opening the book for.
    dna = StudyDNA(sample_size=10, stamina_minutes=30)
    placed, _ = place_day_blocks([_block(minutes=100)], [_window(15, 22)], dna)
    work = [b for b in placed if not b.get("is_break")]
    assert len(work) == 3
    assert min(b["duration_minutes"] for b in work) >= 30
    assert sum(b["duration_minutes"] for b in work) == 100


def test_split_parts_that_do_not_fit_today_overflow_to_be_carried():
    # The caller moves overflow to the next available day, so a split must not
    # cram every sitting into one evening.
    dna = StudyDNA(sample_size=10, stamina_minutes=30)
    placed, overflow = place_day_blocks([_block(minutes=180)], [_window(19, 20)], dna)
    assert len(placed) == 1
    assert len(overflow) == 3
    assert sum(b["duration_minutes"] for b in placed + overflow) == 180


def test_reflow_does_not_re_split_what_the_student_arranged():
    # preserve_order is the drag-and-drop path. Splitting there would multiply
    # the blocks the student just positioned by hand.
    dna = StudyDNA(sample_size=10, stamina_minutes=30)
    placed, _ = place_day_blocks(
        [_block(minutes=180)], [_window(15, 22)], dna, preserve_order=True
    )
    assert len(placed) == 1
    assert placed[0]["duration_minutes"] == 180


def test_an_already_split_block_is_not_split_again():
    dna = StudyDNA(sample_size=10, stamina_minutes=30)
    once, _ = place_day_blocks([_block(minutes=120)], [_window(15, 22)], dna)
    twice, _ = place_day_blocks(strip_auto_breaks(once), [_window(15, 22)], dna)
    assert len([b for b in twice if not b.get("is_break")]) == 3


def test_a_block_barely_over_the_cap_is_left_whole():
    # 46 minutes against a 45-minute cap is one minute of overshoot. Splitting
    # it produces two 23-minute sittings — half the student's actual capacity,
    # for no benefit. A minute over is not two sittings' worth of work.
    dna = StudyDNA(sample_size=10, stamina_minutes=30)   # cap = 45
    placed, _ = place_day_blocks([_block(minutes=46)], [_window(15, 22)], dna)
    work = [b for b in placed if not b.get("is_break")]
    assert len(work) == 1
    assert work[0]["duration_minutes"] == 46
    assert "part_total" not in work[0]


def test_breaks_are_never_split():
    dna = StudyDNA(sample_size=10, stamina_minutes=20)
    placed, _ = place_day_blocks(
        [_block("Lunch", minutes=60, is_break=True)], [_window(12, 14)], dna
    )
    assert len(placed) == 1
    assert placed[0]["duration_minutes"] == 60


def test_hard_work_is_steered_into_the_measured_best_slot():
    dna = StudyDNA(sample_size=10, best_slot="morning")
    placed, _ = place_day_blocks(
        [_block("Easy reading", difficulty="Easy"), _block("Hard proof", difficulty="Hard")],
        [_window(17, 19), _window(7, 9)],
        dna,
    )
    hard = next(b for b in placed if b["assignment"] == "Hard proof")
    assert hard["window_slot"] == "morning"


def test_urgent_easy_work_is_not_displaced_by_hard_work_due_later():
    # The difficulty sort used to run on its own, so a Hard assignment due next
    # week was placed ahead of an Easy one due tomorrow — and when the evening
    # ran out, the one with the deadline was the one that overflowed.
    dna = StudyDNA(sample_size=10, best_slot="morning")
    due_soon = _block("Quiz corrections", difficulty="Easy")
    due_soon["due_date"] = "2026-07-28"
    due_later = _block("Research paper", difficulty="Hard")
    due_later["due_date"] = "2026-08-14"
    placed, overflow = place_day_blocks(
        [due_later, due_soon], [_window(7, 8), _window(17, 18)], dna
    )
    assert placed[0]["assignment"] == "Quiz corrections"
    assert not overflow


def test_the_deadline_that_overflows_is_the_furthest_away():
    dna = StudyDNA(sample_size=10, best_slot="morning")
    blocks = []
    for title, due in (("Due Friday", "2026-07-31"), ("Due Monday", "2026-08-03"),
                       ("Due tomorrow", "2026-07-28")):
        b = _block(title, minutes=45)
        b["due_date"] = due
        blocks.append(b)
    # Room for two 45-minute blocks, not three.
    placed, overflow = place_day_blocks(blocks, [_window(7, 8), _window(17, 18)], dna)
    assert [b["assignment"] for b in placed] == ["Due tomorrow", "Due Friday"]
    assert [b["assignment"] for b in overflow] == ["Due Monday"]


def test_difficulty_still_decides_between_work_due_the_same_day():
    dna = StudyDNA(sample_size=10, best_slot="morning")
    easy = _block("Easy reading", difficulty="Easy")
    hard = _block("Hard proof", difficulty="Hard")
    easy["due_date"] = hard["due_date"] = "2026-07-30"
    placed, _ = place_day_blocks([easy, hard], [_window(17, 19), _window(7, 9)], dna)
    hard_block = next(b for b in placed if b["assignment"] == "Hard proof")
    assert hard_block["window_slot"] == "morning"


def test_work_without_a_due_date_keeps_the_difficulty_ordering():
    # LMS items the model renamed never match the lookup, so they arrive with
    # no due date. They must not all jump the queue or all sink to the bottom
    # in a way that changes what happened before deadlines were considered.
    dna = StudyDNA(sample_size=10, best_slot="morning")
    placed, _ = place_day_blocks(
        [_block("Easy reading", difficulty="Easy"), _block("Hard proof", difficulty="Hard")],
        [_window(17, 19), _window(7, 9)],
        dna,
    )
    assert placed[0]["assignment"] == "Hard proof"


def test_a_long_run_of_work_gets_a_forced_break():
    placed, _ = place_day_blocks([_block(minutes=50) for _ in range(3)], [_window(15, 22)])
    assert any(b.get("is_break") and b["assignment"] == "Long break" for b in placed)


def test_the_day_never_ends_on_a_break():
    # A trailing "take a break" block is just a block that says stop working.
    placed, _ = place_day_blocks([_block(minutes=50) for _ in range(2)], [_window(15, 22)])
    assert not placed[-1].get("is_break")


def test_the_day_never_ends_on_a_break_across_several_windows():
    # The break rule asks "is there more work after this block?" in list order.
    # Once a later block can backfill an earlier window, the answer can be yes
    # while the work still lands hours *before* the break — leaving the evening
    # to close on "step away, eat, walk".
    placed, _ = place_day_blocks(
        [_block("Long essay", minutes=100), _block("Problem set", minutes=90),
         _block("Flashcards", minutes=30)],
        [_window(7, 10), _window(17, 22)],
    )
    assert not placed[-1].get("is_break")


def test_a_break_the_plan_authored_may_end_the_day():
    # Only engine-inserted breaks are trimmed. If the model or the student put
    # a break last on purpose, that is their call to make.
    placed, _ = place_day_blocks(
        [_block(minutes=45), _block("Dinner", minutes=30, is_break=True)],
        [_window(17, 22)],
    )
    assert placed[-1]["assignment"] == "Dinner"


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
