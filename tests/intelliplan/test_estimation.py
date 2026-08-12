"""Tests for the effort estimation model."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from intelliplan.intelligence.estimation import (
    CONFIDENT_SAMPLES,
    EstimationModel,
    baseline_minutes,
    build_estimation_model,
)

NOW = datetime(2026, 3, 1, 12, 0, 0)


def rows(n: int, estimated: int, actual: int, course: str = "", kind: str = "", age_days: int = 1):
    return [
        {
            "estimated_time": estimated,
            "actual_time": actual,
            "course": course,
            "kind": kind,
            "completed_at": NOW - timedelta(days=age_days + i),
        }
        for i in range(n)
    ]


# ── Cold start ────────────────────────────────────────────────────────


def test_no_history_returns_estimate_unchanged():
    model = build_estimation_model(now=NOW)
    est = model.predict(60, "math", "homework")
    assert est.minutes == 60
    assert est.confidence == 0.0
    assert model.has_signal is False


def test_missing_estimate_falls_back_to_kind_baseline():
    model = build_estimation_model(now=NOW)
    assert model.predict(None, kind="exam").minutes == baseline_minutes("exam")
    assert model.predict(0, kind="classwork").minutes == baseline_minutes("classwork")


def test_baseline_scales_mildly_with_points():
    small = baseline_minutes("homework", points_possible=10)
    large = baseline_minutes("homework", points_possible=200)
    assert large > small
    # 20x the points must not be 20x the work.
    assert large < small * 3


# ── Learning bias ─────────────────────────────────────────────────────


def test_learns_that_student_is_faster_than_estimated():
    model = build_estimation_model(rows(8, 60, 35), now=NOW)
    est = model.predict(60, "math", "homework")
    assert 30 <= est.minutes <= 42, est
    assert est.ratio < 1.0
    assert est.confidence > 0.5


def test_learns_that_student_is_slower_than_estimated():
    model = build_estimation_model(rows(8, 45, 80), now=NOW)
    est = model.predict(45)
    assert est.minutes > 55
    assert est.ratio > 1.0


def test_one_observation_barely_moves_the_estimate():
    model = build_estimation_model(rows(1, 60, 20), now=NOW)
    est = model.predict(60)
    # A single wild data point must not halve every future estimate.
    assert est.minutes > 45, est
    assert est.confidence < 0.4


def test_more_evidence_moves_estimate_further():
    few = build_estimation_model(rows(2, 60, 30), now=NOW).predict(60).minutes
    many = build_estimation_model(rows(20, 60, 30), now=NOW).predict(60).minutes
    assert many < few


# ── Hierarchy ─────────────────────────────────────────────────────────


def test_course_specific_bias_beats_global():
    history = (
        rows(10, 60, 60, course="math", kind="homework")
        + rows(10, 60, 120, course="chemistry", kind="lab")
    )
    model = build_estimation_model(history, now=NOW)
    chem = model.predict(60, "chemistry", "lab")
    math_ = model.predict(60, "math", "homework")
    assert chem.minutes > math_.minutes
    assert chem.basis == "course+kind"


def test_unseen_course_falls_back_to_global_bias():
    model = build_estimation_model(rows(12, 60, 90, course="math", kind="homework"), now=NOW)
    unseen = model.predict(60, "art", "project")
    # Global bias still applies — the student runs long in general.
    assert unseen.minutes > 60
    assert unseen.basis == "global"


def test_kind_bias_transfers_across_courses():
    history = (
        rows(8, 60, 130, course="math", kind="project")
        + rows(8, 60, 60, course="math", kind="homework")
    )
    model = build_estimation_model(history, now=NOW)
    # A project in a course we have never seen should inherit "projects run long".
    assert model.predict(60, "history", "project").minutes > 70


# ── Recency ───────────────────────────────────────────────────────────


def test_recent_behaviour_outweighs_stale_behaviour():
    stale = rows(10, 60, 120, age_days=300)
    fresh = rows(10, 60, 40, age_days=1)
    model = build_estimation_model(stale + fresh, now=NOW)
    assert model.predict(60).minutes < 60


# ── Uncertainty ───────────────────────────────────────────────────────


def test_consistent_student_has_tighter_bounds_than_erratic_one():
    consistent = build_estimation_model(rows(12, 60, 60), now=NOW).predict(60)
    erratic = build_estimation_model(
        [
            {"estimated_time": 60, "actual_time": a, "completed_at": NOW - timedelta(days=i)}
            for i, a in enumerate([20, 150, 30, 140, 25, 160, 35, 130, 22, 145, 28, 155])
        ],
        now=NOW,
    ).predict(60)
    assert erratic.high_minutes - erratic.low_minutes > consistent.high_minutes - consistent.low_minutes
    assert erratic.buffer_minutes > consistent.buffer_minutes


def test_bounds_bracket_the_point_estimate():
    model = build_estimation_model(rows(8, 60, 90), now=NOW)
    est = model.predict(60)
    assert est.low_minutes <= est.minutes <= est.high_minutes


# ── Outliers and bad data ─────────────────────────────────────────────


def test_absurd_outlier_is_clamped_not_believed():
    # A timer left running overnight: 60 estimated, 900 actual.
    model = build_estimation_model(rows(6, 60, 60) + rows(1, 60, 900), now=NOW)
    assert model.predict(60).minutes < 110


def test_rows_with_missing_fields_are_ignored():
    model = build_estimation_model(
        [
            {"estimated_time": 60},
            {"actual_time": 30},
            {"estimated_time": 0, "actual_time": 30},
            {"estimated_time": "x", "actual_time": "y"},
        ],
        now=NOW,
    )
    assert model.sample_size == 0
    assert model.predict(60).minutes == 60


def test_abandoned_sessions_do_not_teach_bias():
    abandoned = [
        {"estimated": 60, "actual": 10, "completed": False, "at": NOW - timedelta(days=i)}
        for i in range(10)
    ]
    model = build_estimation_model(session_rows=abandoned, now=NOW)
    assert model.predict(60).minutes == 60
    assert model.session_completion_rate == 0.0


# ── Derived signals ───────────────────────────────────────────────────


def test_stamina_is_median_completed_session_length():
    model = build_estimation_model(
        session_rows=[
            {"estimated": 60, "actual": m, "completed": True, "at": NOW}
            for m in (25, 30, 28, 32, 27)
        ],
        now=NOW,
    )
    assert 25 <= model.stamina_minutes <= 32


def test_session_completion_rate_reflects_abandonment():
    sessions = [
        {"estimated": 60, "actual": 60, "completed": True, "at": NOW} for _ in range(6)
    ] + [{"estimated": 60, "actual": 20, "completed": False, "at": NOW} for _ in range(4)]
    model = build_estimation_model(session_rows=sessions, now=NOW)
    assert model.session_completion_rate == 0.6


def test_prompt_is_empty_without_signal_and_numeric_with_it():
    assert build_estimation_model(now=NOW).to_prompt() == ""
    prompt = build_estimation_model(rows(10, 60, 90), now=NOW).to_prompt()
    assert "%" in prompt and "focus length" in prompt


def test_summary_is_serialisable():
    model = build_estimation_model(rows(6, 60, 45, course="math", kind="homework"), now=NOW)
    summary = model.summary()
    assert summary["sample_size"] == 6
    assert summary["overall_ratio"] < 1.0
    assert "math" in summary["courses"]


# ── Guardrails ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [1, 5, 500, 10_000])
def test_predictions_stay_in_actionable_range(raw):
    model = build_estimation_model(rows(10, 60, 200), now=NOW)
    est = model.predict(raw)
    assert 5 <= est.minutes <= 300


def test_model_is_deterministic():
    history = rows(9, 60, 75, course="bio", kind="lab")
    a = build_estimation_model(history, now=NOW).predict(60, "bio", "lab")
    b = build_estimation_model(history, now=NOW).predict(60, "bio", "lab")
    assert a == b
