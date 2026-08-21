"""Behavioural model — completion probability, shrinkage, and evidence."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from intelliplan.domain.student import EvidenceSource
from intelliplan.intelligence.behavior import (
    POPULATION_COMPLETION_RATE,
    build_behavior_model,
    slot_for_hour,
)

NOW = datetime(2026, 8, 21, 12, 0)


def sitting(days_ago: int, *, completed: bool, minutes: int = 45,
            course: str = "Physics", hour: int = 19) -> dict:
    at = (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0)
    return {
        "actual": minutes,
        "course": course,
        "started_at": at,
        "completed": completed,
    }


# ── the cold-start contract ──────────────────────────────────────────


def test_a_brand_new_student_gets_the_population_rate_not_a_guess():
    model = build_behavior_model(now=NOW)
    prediction = model.completion_probability(course="Physics", slot="evening", minutes=45)
    assert prediction.probability == pytest.approx(POPULATION_COMPLETION_RATE, abs=0.001)
    assert prediction.evidence.source is EvidenceSource.POPULATION


def test_a_brand_new_student_is_never_penalised_for_a_long_sitting():
    """No evidence about long sittings must mean no length penalty.

    Applying one anyway would be inventing a fact about someone we have
    never observed.
    """
    model = build_behavior_model(now=NOW)
    short = model.completion_probability(minutes=20)
    long = model.completion_probability(minutes=180)
    assert short.length_factor == 1.0
    assert long.length_factor == 1.0
    assert short.probability == long.probability


def test_the_model_refuses_to_name_a_best_slot_on_thin_evidence():
    rows = [sitting(1, completed=True, hour=8), sitting(2, completed=True, hour=8)]
    assert build_behavior_model(rows, now=NOW).best_slot() is None


# ── learning from history ────────────────────────────────────────────


def test_a_reliable_student_scores_above_the_population_rate():
    rows = [sitting(i, completed=True) for i in range(20)]
    p = build_behavior_model(rows, now=NOW).completion_probability(
        course="Physics", slot="evening", minutes=45
    )
    assert p.probability > POPULATION_COMPLETION_RATE
    assert p.evidence.source is EvidenceSource.MEASURED


def test_a_student_who_abandons_everything_scores_below_it():
    rows = [sitting(i, completed=False) for i in range(20)]
    p = build_behavior_model(rows, now=NOW).completion_probability(
        course="Physics", slot="evening", minutes=45
    )
    assert p.probability < POPULATION_COMPLETION_RATE


def test_one_observation_nudges_rather_than_declares():
    """Shrinkage is the whole point: a single data point must not swing
    the estimate to its own value."""
    rows = [sitting(1, completed=False)]
    p = build_behavior_model(rows, now=NOW).completion_probability(course="Physics")
    assert 0.4 < p.probability < POPULATION_COMPLETION_RATE


def test_the_model_separates_a_bad_subject_from_a_good_one():
    rows = (
        [sitting(i, completed=True, course="Physics") for i in range(15)]
        + [sitting(i, completed=False, course="Chemistry") for i in range(15)]
    )
    model = build_behavior_model(rows, now=NOW)
    physics = model.completion_probability(course="Physics", slot="evening")
    chemistry = model.completion_probability(course="Chemistry", slot="evening")
    assert physics.probability > chemistry.probability + 0.2


def test_the_model_separates_a_bad_time_of_day_from_a_good_one():
    rows = (
        [sitting(i, completed=True, hour=9) for i in range(15)]
        + [sitting(i, completed=False, hour=22) for i in range(15)]
    )
    model = build_behavior_model(rows, now=NOW)
    morning = model.completion_probability(slot="morning")
    evening = model.completion_probability(slot="evening")
    assert morning.probability > evening.probability + 0.2
    assert model.best_slot() == "morning"


def test_a_measured_length_penalty_is_learned_not_assumed():
    """Long sittings abandoned, short ones finished — the factor must fall
    out of the data rather than out of a constant."""
    rows = (
        [sitting(i, completed=True, minutes=30) for i in range(15)]
        + [sitting(i, completed=False, minutes=120) for i in range(15)]
    )
    model = build_behavior_model(rows, now=NOW)
    assert model.completion_probability(minutes=120).length_factor < 0.95
    assert model.completion_probability(minutes=25).length_factor >= 1.0


# ── decay ────────────────────────────────────────────────────────────


def test_old_evidence_counts_for_less_than_recent_evidence():
    recent = [sitting(i, completed=False) for i in range(10)]
    ancient = [sitting(300 + i, completed=False) for i in range(10)]
    recent_p = build_behavior_model(recent, now=NOW).completion_probability(course="Physics")
    ancient_p = build_behavior_model(ancient, now=NOW).completion_probability(course="Physics")
    assert recent_p.probability < ancient_p.probability


# ── derived views ────────────────────────────────────────────────────


def test_stamina_is_measured_from_sittings_not_from_task_durations():
    """A 200-minute task-feedback row must not become a 200-minute focus
    length — a burst worker would then be scheduled blocks they never finish.
    """
    sessions = [sitting(i, completed=True, minutes=30) for i in range(10)]
    feedback = [{"actual": 200, "estimated": 180, "course": "Physics",
                 "completed_at": NOW - timedelta(days=1)}]
    model = build_behavior_model(sessions, feedback, now=NOW)
    assert model.stamina_minutes == 30


def test_workload_tolerance_uses_a_percentile_not_the_maximum():
    rows = [sitting(i, completed=True) for i in range(10)]
    daily = {f"d{i}": 60 for i in range(9)}
    daily["heroic-sunday"] = 600
    model = build_behavior_model(rows, daily_minutes=daily, now=NOW)
    assert model.workload_tolerance_minutes < 600


def test_the_profile_reports_its_own_evidence():
    model = build_behavior_model([sitting(i, completed=True) for i in range(20)], now=NOW)
    profile = model.profile()
    assert profile.completion_evidence.is_measured
    assert profile.observations > 4
    assert profile.abandon_rate == 0.0


# ── input handling ───────────────────────────────────────────────────


def test_rows_without_a_usable_duration_are_dropped_not_defaulted():
    model = build_behavior_model(
        [{"course": "Physics", "completed": True}, {"actual": 0, "completed": True}],
        now=NOW,
    )
    assert model.total_weight == 0.0


def test_fitting_without_a_clock_is_refused():
    """``now`` is required so the decay is reproducible from a log line."""
    with pytest.raises(ValueError):
        build_behavior_model([])


def test_late_night_hours_fold_into_evening_like_the_placement_engine():
    import scheduler_engine

    for hour in (0, 1, 5, 23):
        assert slot_for_hour(hour) == scheduler_engine.slot_for_hour(hour)
    for hour in (7, 13, 19):
        assert slot_for_hour(hour) == scheduler_engine.slot_for_hour(hour)
