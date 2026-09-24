"""The Follow-Through engine as the app sees it, through SchedulingService."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import scheduler_engine
from intelliplan.services.scheduling import (
    SchedulingService,
    StudentContext,
    overrides_from_json,
)

NOW = datetime(2026, 3, 2, 8, 0)
TODAY = NOW.date()
AVAILABILITY = {d: {"start": "16:00", "end": "20:00"} for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}

ROWS = [
    {"id": "chem", "title": "Chem lab", "course": "Chem", "kind": "lab",
     "due_date": (TODAY + timedelta(days=5)).isoformat(), "est_minutes": 120, "priority": 75},
    {"id": "math", "title": "Math set", "course": "Math", "kind": "homework",
     "due_date": (TODAY + timedelta(days=3)).isoformat(), "est_minutes": 60, "priority": 60},
]


def service(follow_through=True, **ctx):
    ctx.setdefault("availability", AVAILABILITY)
    return SchedulingService(StudentContext(**ctx), follow_through=follow_through,
                             decompose_stages=False)


def test_off_by_default_so_nothing_existing_changes():
    svc = SchedulingService(StudentContext(availability=AVAILABILITY, weak_days=("Tue",)))
    assert not svc.follow_through_enabled
    assert svc.completion_fn() is None
    caps = svc.capacities(TODAY, now=NOW)
    assert next(c for c in caps if c.day.weekday() == 1).quality < 1.0


def test_on_it_fits_a_model_and_forecasts_the_plan():
    svc = service()
    plan = svc.plan(ROWS, today=TODAY, now=NOW)
    assert plan.sessions
    assert svc.last_risk is not None
    data = svc.to_schedule_data(plan, now=NOW)
    assert data["engine"] == "followthrough-v1"
    assert {t["key"] for t in data["forecast"]["tasks"]} == {"chem", "math"}


def test_session_history_personalises_the_model():
    rows = [
        {"started_at": NOW - timedelta(days=d, hours=12), "planned_minutes": 45,
         "actual": 45 if d % 3 else 10, "completed": bool(d % 3), "course": "Chem"}
        for d in range(1, 40)
    ]
    svc = service(session_rows=rows)
    assert svc.followthrough.n_observations > 20


def test_an_adjustment_remembers_what_the_student_said_about_a_day():
    svc = service()
    plan = svc.plan(ROWS, today=TODAY, now=NOW)
    data = svc.to_schedule_data(plan, now=NOW)
    tomorrow = TODAY + timedelta(days=1)
    result, out = service().adjust(data, {}, {"action": "skip_day", "day": tomorrow.isoformat()},
                                   today=TODAY, now=NOW)
    assert out["capacity_overrides"] == {tomorrow.isoformat(): {"set": 0}}
    # A later adjustment on the saved result still honours it.
    _, again = service().adjust(out, {}, {"action": "catch_up"}, today=TODAY, now=NOW)
    assert not [b for d in again["schedule"] if d["date"] == tomorrow.isoformat()
                for b in d["blocks"] if not b.get("is_break")]
    assert again["capacity_overrides"] == out["capacity_overrides"]


def test_extra_time_accumulates_and_reaches_the_clock():
    svc = service()
    data = svc.to_schedule_data(svc.plan(ROWS, today=TODAY, now=NOW), now=NOW)
    day = TODAY + timedelta(days=1)
    _, once = service().adjust(data, {}, {"action": "add_time", "day": day.isoformat(), "minutes": 60},
                               today=TODAY, now=NOW)
    _, twice = service().adjust(once, {}, {"action": "add_time", "day": day.isoformat(), "minutes": 30},
                                today=TODAY, now=NOW)
    assert twice["capacity_overrides"][day.isoformat()] == {"add": 90}
    base = service().capacities(TODAY, now=NOW)
    boosted = service()
    boosted.set_capacity_overrides(twice["capacity_overrides"], TODAY)
    more = boosted.capacities(TODAY, now=NOW)
    assert next(c.minutes for c in more if c.day == day) > next(c.minutes for c in base if c.day == day)


def test_stored_overrides_for_past_days_are_forgotten():
    parsed = overrides_from_json({
        (TODAY - timedelta(days=1)).isoformat(): {"set": 0},
        TODAY.isoformat(): {"add": 45},
        "garbage": {"set": 1},
        (TODAY + timedelta(days=1)).isoformat(): {"set": "nope"},
    }, TODAY)
    assert parsed == {TODAY: {"add": 45}}


def test_the_forecast_is_honest_about_a_student_with_no_history():
    svc = service()
    data = svc.to_schedule_data(svc.plan(ROWS, today=TODAY, now=NOW), now=NOW)
    body = service().forecast(data, {}, today=TODAY, now=NOW)
    assert body["risk"]["tasks"]
    assert body["insights"] == []
    assert body["model"]["personalised"] is False
    assert "coefficients" not in body["model"]


# ── extra time on the clock ──────────────────────────────────────────


def _windows(day):
    return scheduler_engine.windows_for_date(day, AVAILABILITY, "evening", None, now=NOW)


def test_extra_time_extends_the_evening_then_starts_earlier():
    day = TODAY + timedelta(days=1)
    base = _windows(day)
    assert [(w.start.hour, w.end.hour) for w in base] == [(16, 20)]
    later = scheduler_engine.extend_windows(base, day, 120, "evening", now=NOW)
    assert (later[0].start.hour, later[-1].end.hour) == (16, 22)
    much = scheduler_engine.extend_windows(base, day, 300, "evening", now=NOW)
    assert much[-1].end.hour == 23 and much[0].start.hour < 16


def test_extra_time_on_a_day_with_none_uses_the_preferred_hour():
    day = TODAY + timedelta(days=1)
    out = scheduler_engine.extend_windows([], day, 90, "afternoon", now=NOW)
    assert [(w.start.hour, w.minutes) for w in out] == [(14, 90)]


def test_extra_time_is_never_in_the_past():
    late = datetime(2026, 3, 2, 22, 50)
    assert scheduler_engine.extend_windows([], TODAY, 90, "evening", now=late) == []
