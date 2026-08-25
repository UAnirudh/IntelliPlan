"""Clarification answers have to reach the planner.

A student added five custom tasks, answered the clarifying questions for
each ("deadline: Tomorrow", "duration: 1 hour", "subject: AP CSP"), and got
back a plan that put one 45-minute block per day across the following
seven days — every deadline missed, and 4 of their 4 available hours unused
each day. The answers were collected, echoed back in the UI as "Used your
saved answers for …", saved as presets, and then dropped: only the title
survived the hand-off to the planner.

The planner behaved correctly on what it was given. A task with no deadline
*should* be spread onto the lightest days. The defect is upstream, at the
boundary where enriched task dicts were flattened back to bare strings.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import App
from App import User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            User.query.filter(User.email.like("clarify+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
    App.limiter.enabled = True


def _generate(client, custom_tasks, clarifications):
    return client.post("/generate_schedule", json={
        "assignments": [],
        "custom_tasks": custom_tasks,
        "hours_per_day": 4,
        "preferred_time": "morning",
        "clarifications": clarifications,
        "skip_clarify": True,
    })


def _answers_for(title, *, deadline, duration, subject):
    from scheduler_clarify import preset_key

    key = preset_key(title)
    return {
        f"{key}::deadline": deadline,
        f"{key}::duration": duration,
        f"{key}::subject": subject,
    }


# ── The unit that actually lost the data ──────────────────────────────


def test_a_clarified_custom_task_keeps_its_deadline(client):
    """``_planner_task_rows`` hardcoded due_date="" for every custom task, so
    an answered deadline could not survive even if it was passed in."""
    rows = App._planner_task_rows([], [{
        "title": "Learn Calc AB",
        "course": "AP Calculus",
        "due_date": "2026-08-26",
        "estimated_time": 45,
        "estimate_source": "student",
    }])
    assert len(rows) == 1
    assert rows[0]["due_date"] == "2026-08-26"
    assert rows[0]["course"] == "AP Calculus"
    assert rows[0]["est_minutes"] == 45


def test_a_bare_string_custom_task_still_works(client):
    """The un-clarified path must keep behaving as it did: no invented
    deadline, neutral defaults, sizing left to the estimation model."""
    rows = App._planner_task_rows([], ["Read chapter 4"])
    assert len(rows) == 1
    assert rows[0]["title"] == "Read chapter 4"
    assert rows[0]["due_date"] == ""


# ── End to end, as the student experienced it ─────────────────────────


def test_work_due_tomorrow_is_not_scheduled_next_week(client):
    """The reported bug, in one assertion."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    title = "Learn Calc AB"

    r = _generate(client, [title],
                  _answers_for(title, deadline=tomorrow, duration="45 min",
                               subject="AP Calculus"))
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok", body

    placed = [
        (day["date"], block)
        for day in body["data"]["schedule"]
        for block in day.get("blocks", [])
        if not block.get("is_break")
    ]
    assert placed, "the task was not scheduled at all"
    for day_str, block in placed:
        assert day_str <= tomorrow, (
            f"{block.get('assignment')} was placed on {day_str}, "
            f"after its {tomorrow} deadline"
        )


def test_several_tasks_due_tomorrow_share_the_days_that_are_left(client):
    """Five tasks, all due tomorrow, four hours a day. They have to be
    packed into today and tomorrow — not dealt out one per day across the
    week, which is what a deadline-blind planner does with them."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    titles = ["Learn some pre-calc", "Learn Calc AB",
              "Java Programming & Algorithms", "AI Fundamentals & Machine Learning"]
    clarifications = {}
    for t in titles:
        clarifications.update(_answers_for(t, deadline=tomorrow, duration="45 min",
                                           subject="Test Course"))

    r = _generate(client, titles, clarifications)
    body = r.get_json()
    assert body["status"] == "ok", body

    days_used = {
        day["date"]
        for day in body["data"]["schedule"]
        for block in day.get("blocks", [])
        if not block.get("is_break")
    }
    assert days_used, "nothing was scheduled"
    assert max(days_used) <= tomorrow, (
        f"work spilled past the deadline onto {sorted(days_used)}"
    )


def test_the_students_own_duration_is_respected(client):
    """"1 hour" typed by the student must not be re-estimated to something
    else — asking and then overruling the answer is worse than not asking."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    title = "Personal Project & Business"

    r = _generate(client, [title],
                  _answers_for(title, deadline=tomorrow, duration="1 hour",
                               subject="Business"))
    body = r.get_json()
    assert body["status"] == "ok", body

    total = sum(
        int(block.get("duration_minutes") or 0)
        for day in body["data"]["schedule"]
        for block in day.get("blocks", [])
        if not block.get("is_break")
    )
    assert 50 <= total <= 70, f"asked for 60 minutes, planned {total}"


def test_the_subject_reaches_the_block(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    title = "Java Programming & Algorithms"

    r = _generate(client, [title],
                  _answers_for(title, deadline=tomorrow, duration="1 hour",
                               subject="AP CSP"))
    body = r.get_json()
    courses = {
        (block.get("course") or "")
        for day in body["data"]["schedule"]
        for block in day.get("blocks", [])
        if not block.get("is_break")
    }
    assert "AP CSP" in courses, f"subject was lost; got {courses}"
