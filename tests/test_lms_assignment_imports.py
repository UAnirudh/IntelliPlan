"""Exercise school payloads with synthetic students and no vendor traffic."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import App
import schoology_helper


def test_classroom_visits_all_course_and_coursework_pages(monkeypatch):
    soon = date.today() + timedelta(days=3)
    due = {"year": soon.year, "month": soon.month, "day": soon.day}
    pages = {
        ("courses", None): {"courses": [{"id": "science", "name": "Science"}], "nextPageToken": "course2"},
        ("courses", "course2"): {"courses": [{"id": "history", "name": "History"}]},
        ("science/courseWork", None): {"nextPageToken": "work2"},
        ("science/courseWork", "work2"): {"courseWork": [{"id": "lab", "title": "Lab", "dueDate": due}]},
        ("history/courseWork", None): {"courseWork": [{"id": "essay", "title": "Essay", "dueDate": due}]},
    }

    def get(url, *, headers, params, timeout):
        path = url.split("/v1/", 1)[1].removeprefix("courses/")
        assert params["courseStates" if path == "courses" else "courseWorkStates"] == (
            "ACTIVE" if path == "courses" else "PUBLISHED"
        )
        return Mock(json=Mock(return_value=pages[path, params.get("pageToken")]))

    monkeypatch.setattr(App.requests, "get", get)
    rows = App._classroom_fetch_assignments("synthetic")
    assert [(row["course"], row["title"]) for row in rows] == [("Science", "Lab"), ("History", "Essay")]


@pytest.mark.parametrize("course_listing_available", [False, True])
def test_moodle_uses_calendar_if_school_disables_course_or_assignment_api(monkeypatch, course_listing_available):
    due = date.today() + timedelta(days=3)
    timestamp = int(datetime(due.year, due.month, due.day, tzinfo=timezone.utc).timestamp())
    calls = []

    def call(url, token, function, **params):
        calls.append(function)
        if function == "core_enrol_get_users_courses" and course_listing_available:
            return [{"id": 42, "fullname": "Geometry"}]
        if function == "core_calendar_get_action_events_by_timesort":
            return {"events": [
                {"id": 1, "name": "Missing date", "timesort": "invalid"},
                {"id": 2, "name": "Problem set", "timesort": timestamp,
                 "course": {"id": 42, "fullname": "Geometry"}},
            ]}
        raise RuntimeError("Synthetic school permission restriction")

    monkeypatch.setattr(App, "_moodle_call", call)
    rows = App._moodle_fetch_assignments("https://school.example.invalid", "synthetic", "123")
    assert ("mod_assign_get_assignments" in calls) == course_listing_available
    assert [(row["course"], row["title"], row["due_date"]) for row in rows] == [
        ("Geometry", "Problem set", due.isoformat())
    ]


def test_moodle_prefers_assignment_data_when_available(monkeypatch):
    due = date.today() + timedelta(days=3)
    timestamp = int(datetime(due.year, due.month, due.day, tzinfo=timezone.utc).timestamp())

    def call(url, token, function, **params):
        if function == "core_enrol_get_users_courses":
            assert params["userid"] == 123
            return [{"id": 42, "fullname": "Geometry"}]
        assert function == "mod_assign_get_assignments"
        assert params["courseids[0]"] == 42
        return {"courses": [{"id": 42, "assignments": [{"id": 7, "name": "Proof", "duedate": timestamp}]}]}

    monkeypatch.setattr(App, "_moodle_call", call)
    rows = App._moodle_fetch_assignments("https://school.example.invalid", "synthetic", "123")
    assert [(row["id"], row["course"], row["title"]) for row in rows] == [("mdl-7", "Geometry", "Proof")]


def test_blackboard_imports_assignments_when_school_exposes_only_v1_columns(monkeypatch):
    due = date.today() + timedelta(days=3)
    responses = {
        "/v1/users/demo/courses": (200, {"results": [{"courseId": "math"}]}),
        "/v3/courses/math": (200, {"name": "Algebra"}),
        "/v2/courses/math/gradebook/columns": (404, {}),
        "/v1/courses/math/gradebook/columns": (200, {"results": [
            {"id": "a1", "name": "Practice", "grading": {"due": f"{due.isoformat()}T18:00:00Z"}},
            {"id": "a2", "name": "Hidden", "availability": {"available": "No"},
             "grading": {"due": f"{due.isoformat()}T18:00:00Z"}},
        ]}),
    }

    def get(url, *, headers, timeout, **kwargs):
        status, payload = responses[url.split("/learn/api/public", 1)[1]]
        return Mock(status_code=status, json=Mock(return_value=payload))

    monkeypatch.setattr(App.requests, "get", get)
    rows = App._blackboard_fetch_assignments("https://school.example.invalid", "synthetic", "demo")
    assert [(row["id"], row["course"], row["due_date"]) for row in rows] == [
        ("bb-math-a1", "Algebra", due.isoformat())
    ]


def test_schoology_keeps_zero_scores_and_assignment_points(monkeypatch):
    due = (date.today() + timedelta(days=3)).isoformat() + " 18:00:00"
    responses = {
        "/sections": {"section": [{"id": 3, "course_title": "Algebra"}]},
        "/sections/3/assignments": {"assignment": [
            {"id": 10, "title": "Practice", "due": due, "max_points": 0},
            {"id": 11, "title": "Review", "due": due, "max_points": "unavailable"},
        ]},
        "/users/me/grades": {"section": [{"course_title": "Algebra", "final_grade": {"score": 0}}]},
    }
    monkeypatch.setattr(schoology_helper, "make_schoology_request", lambda *args: responses[args[-1]])
    assignments = schoology_helper.get_schoology_assignments("synthetic", "synthetic")
    assert [(row["title"], row["points_possible"]) for row in assignments] == [
        ("Practice", 0), ("Review", None)
    ]
    grades = schoology_helper.get_schoology_grades("synthetic", "synthetic")
    assert len(grades) == 1 and grades[0]["percentage"] == 0
