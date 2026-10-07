"""Courses a student takes outside school.

Three things are held here: the rules (which site a link is, what a week
is, when someone is behind), the routes (ownership, limits, the weekly
task that puts a course on the plan), and the line between time a student
reported and progress the extension read.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

import App
import course_tracking as rules
from App import CourseCheckin, ExtensionToken, ManualTask, TrackedCourse, User, db

MONDAY = date(2026, 10, 5)
THURSDAY = date(2026, 10, 8)
SUNDAY = date(2026, 10, 11)


# ── Rules ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("khanacademy.org/math/algebra", "https://khanacademy.org/math/algebra"),
        ("https://www.Coursera.org/learn/ml#syllabus", "https://www.coursera.org/learn/ml"),
        ("http://edx.org/course/x?utm=1", "https://edx.org/course/x?utm=1"),
        ("javascript:alert(1)", ""),
        ("data:text/html,<script>", ""),
        ("https://user:pw@evil.example/x", ""),
        ("not a link", ""),
        ("localhost/admin", ""),
        ("", ""),
        (None, ""),
        ("https://example.com/" + "a" * 600, ""),
    ],
)
def test_only_real_web_links_are_accepted(raw, cleaned):
    assert rules.clean_url(raw) == cleaned


@pytest.mark.parametrize(
    ("url", "provider"),
    [
        ("https://www.khanacademy.org/math/algebra", "khan"),
        ("https://www.coursera.org/learn/ml", "coursera"),
        ("https://courses.edx.org/course/x", "edx"),
        ("https://ocw.mit.edu/courses/18-01", "mitocw"),
        ("https://notkhanacademy.org/math", "other"),
        ("https://example.com/khanacademy.org", "other"),
    ],
)
def test_the_site_is_read_from_the_host_not_the_text(url, provider):
    assert rules.detect_provider(url).key == provider


@pytest.mark.parametrize(
    ("tracked", "seen", "same"),
    [
        ("https://www.khanacademy.org/math/algebra", "https://khanacademy.org/math/algebra/x/y", True),
        ("https://www.khanacademy.org/math/algebra", "https://www.khanacademy.org/math/algebra/", True),
        ("https://www.khanacademy.org/math/algebra", "https://www.khanacademy.org/math/algebra2", False),
        ("https://www.khanacademy.org/math/algebra", "https://www.khanacademy.org/science/biology", False),
        ("https://www.coursera.org/learn/ml", "https://www.khanacademy.org/learn/ml", False),
        # A bare site must not claim every page on it.
        ("https://www.khanacademy.org", "https://www.khanacademy.org/math/algebra", False),
    ],
)
def test_a_page_matches_only_the_course_it_is_inside(tracked, seen, same):
    assert rules.same_course(tracked, seen) is same


def test_a_week_runs_monday_to_sunday():
    assert rules.week_start(THURSDAY) == MONDAY
    assert rules.week_end(THURSDAY) == SUNDAY
    assert rules.week_start(SUNDAY) == MONDAY


@pytest.mark.parametrize(
    ("minutes", "day", "state"),
    [
        (0, MONDAY, "not_started"),     # nobody is behind on day one
        (0, THURSDAY, "behind"),
        (60, THURSDAY, "on_track"),     # 3 of 7 days gone, half the goal done
        (20, SUNDAY, "behind"),
        (120, THURSDAY, "done"),
        (500, THURSDAY, "done"),
    ],
)
def test_pace_is_judged_against_an_even_week(minutes, day, state):
    assert rules.week_status(minutes, 120, day).state == state


def test_a_course_added_midweek_is_paced_from_the_day_it_was_added():
    """Added Thursday with nothing logged: not three days behind already."""
    assert rules.week_status(0, 120, THURSDAY, started=THURSDAY).state == "not_started"
    assert rules.week_status(15, 120, THURSDAY, started=THURSDAY).state == "on_track"
    # By Sunday the same course does owe most of its goal.
    assert rules.week_status(15, 120, SUNDAY, started=THURSDAY).state == "behind"


def test_the_week_a_course_was_added_does_not_change_later_weeks():
    assert rules.week_status(0, 120, THURSDAY, started=date(2026, 9, 30)).state == "behind"


def test_remaining_time_never_goes_negative():
    status = rules.week_status(500, 120, THURSDAY)

    assert status.remaining == 0
    assert status.percent == 100


def test_weeks_in_a_row_ignores_an_unfinished_current_week():
    history = [(date(2026, 9, 21), 120), (date(2026, 9, 28), 130), (MONDAY, 10)]

    assert rules.weeks_met_in_a_row(history, 120, THURSDAY) == 2


def test_weeks_in_a_row_stops_at_a_missed_week():
    history = [(date(2026, 9, 14), 200), (date(2026, 9, 21), 30), (date(2026, 9, 28), 120)]

    assert rules.weeks_met_in_a_row(history, 120, THURSDAY) == 1


@pytest.mark.parametrize("raw", ["-1", "101", "nan", "abc", None])
def test_an_impossible_percentage_is_refused(raw):
    assert rules.clamp_percent(raw) is None


def test_recommendations_are_search_pages_on_known_sites():
    results = rules.recommendations("linear algebra & more")

    assert results
    for r in results:
        assert r["url"].startswith("https://")
        assert "linear+algebra+%26+more" in r["url"]
    assert rules.recommendations("   ") == []


# ── Routes ───────────────────────────────────────────────────


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
        yield c
    App.limiter.enabled = True


def _make_user() -> int:
    with App.app.app_context():
        user = User(email=f"course-{uuid.uuid4().hex[:10]}@example.test", password_hash="")
        db.session.add(user)
        db.session.commit()
        return user.id


def _sign_in(client, user_id: int) -> None:
    with client.session_transaction() as s:
        s["_user_id"] = str(user_id)
        s["_fresh"] = True


def _signed_in(client) -> int:
    user_id = _make_user()
    _sign_in(client, user_id)
    return user_id


def _add(client, url="https://www.khanacademy.org/math/algebra", **extra):
    return client.post("/api/courses", json={"url": url, **extra})


def _week_tasks(user_id: int) -> list[ManualTask]:
    with App.app.app_context():
        return ManualTask.query.filter_by(user_id=user_id, import_source="course").all()


def test_the_page_and_the_api_need_an_account(client):
    assert client.get("/my-courses").status_code == 302
    assert client.get("/api/courses").status_code == 401
    assert _add(client).status_code == 401


def test_adding_a_course_puts_this_weeks_time_on_the_plan(client):
    user_id = _signed_in(client)

    response = _add(client, title="Algebra 1", weekly_goal_minutes=90)

    course = response.get_json()["courses"][0]
    assert course["title"] == "Algebra 1"
    assert course["provider"] == "khan"
    assert course["weekly_goal_minutes"] == 90
    tasks = _week_tasks(user_id)
    assert len(tasks) == 1
    assert tasks[0].estimated_time == 90
    assert tasks[0].done is False


def test_reading_the_list_twice_does_not_duplicate_the_task(client):
    user_id = _signed_in(client)
    _add(client)

    client.get("/api/courses")
    client.get("/api/courses")

    assert len(_week_tasks(user_id)) == 1


def test_a_bad_link_is_refused_with_a_reason(client):
    _signed_in(client)

    response = _add(client, url="javascript:alert(1)")

    assert response.status_code == 400
    assert "link" in response.get_json()["message"].lower()


def test_the_same_course_cannot_be_tracked_twice(client):
    _signed_in(client)
    _add(client)

    response = _add(client, url="https://khanacademy.org/math/algebra/")

    assert response.status_code == 400


def test_there_is_a_limit_on_active_courses(client):
    _signed_in(client)
    for n in range(rules.MAX_ACTIVE_COURSES):
        assert _add(client, url=f"https://example.com/course-{n}").status_code == 200

    assert _add(client, url="https://example.com/one-too-many").status_code == 400


def test_logging_time_shrinks_the_task_and_meeting_the_goal_finishes_it(client):
    user_id = _signed_in(client)
    course_id = _add(client, weekly_goal_minutes=60).get_json()["courses"][0]["id"]

    first = client.post(f"/api/courses/{course_id}/checkin", json={"minutes": 25}).get_json()
    assert first["courses"][0]["week"]["minutes"] == 25
    assert _week_tasks(user_id)[0].estimated_time == 35

    second = client.post(f"/api/courses/{course_id}/checkin", json={"minutes": 40}).get_json()
    assert second["courses"][0]["week"]["state"] == "done"
    assert _week_tasks(user_id)[0].done is True


@pytest.mark.parametrize("minutes", [0, -5, "lots", None])
def test_a_check_in_needs_a_real_number_of_minutes(client, minutes):
    _signed_in(client)
    course_id = _add(client).get_json()["courses"][0]["id"]

    response = client.post(f"/api/courses/{course_id}/checkin", json={"minutes": minutes})

    assert response.status_code == 400


def test_one_check_in_cannot_claim_more_than_eight_hours(client):
    _signed_in(client)
    course_id = _add(client).get_json()["courses"][0]["id"]

    data = client.post(f"/api/courses/{course_id}/checkin", json={"minutes": 100000}).get_json()

    assert data["courses"][0]["week"]["minutes"] == rules.MAX_CHECKIN_MINUTES


def test_another_students_course_cannot_be_read_logged_or_removed(client):
    owner = _signed_in(client)
    course_id = _add(client).get_json()["courses"][0]["id"]

    _sign_in(client, _make_user())

    assert client.get("/api/courses").get_json()["courses"] == []
    assert client.post(f"/api/courses/{course_id}/checkin", json={"minutes": 30}).status_code == 404
    assert client.delete(f"/api/courses/{course_id}").status_code == 404
    assert client.patch(f"/api/courses/{course_id}", json={"title": "mine now"}).status_code == 404
    with App.app.app_context():
        course = db.session.get(TrackedCourse, course_id)
        assert course.user_id == owner
        assert course.archived is False
        assert CourseCheckin.query.filter_by(course_id=course_id).count() == 0


def test_removing_a_course_keeps_the_record_and_clears_the_open_task(client):
    user_id = _signed_in(client)
    course_id = _add(client).get_json()["courses"][0]["id"]
    client.post(f"/api/courses/{course_id}/checkin", json={"minutes": 20})

    data = client.delete(f"/api/courses/{course_id}").get_json()

    assert data["courses"] == []
    assert _week_tasks(user_id) == []
    with App.app.app_context():
        assert db.session.get(TrackedCourse, course_id).archived is True
        assert CourseCheckin.query.filter_by(course_id=course_id).count() == 1


def test_markup_in_a_title_is_stored_as_text_and_the_page_never_echoes_it(client):
    _signed_in(client)
    _add(client, title="<img src=x onerror=alert(1)>")

    html = client.get("/my-courses").data.decode("utf-8", "ignore")

    assert "onerror=alert(1)" not in html  # the page is rendered by script, from JSON


def test_course_search_returns_links_built_here(client):
    _signed_in(client)

    results = client.get("/api/courses/recommend?subject=statistics").get_json()["results"]

    assert {r["provider"] for r in results} >= {"khan", "coursera"}
    assert all("statistics" in r["url"] for r in results)


# ── What the extension may report ────────────────────────────


def _extension_token(user_id: int) -> dict[str, str]:
    token = uuid.uuid4().hex
    with App.app.app_context():
        db.session.add(ExtensionToken(user_id=user_id, token=token))
        db.session.commit()
    return {"Authorization": f"Bearer {token}"}


def _report(client, headers, url, percent):
    return client.post("/api/courses/progress", json={"url": url, "percent": percent},
                       headers=headers)


def test_the_extension_needs_a_token(client):
    response = _report(client, {}, "https://www.khanacademy.org/math/algebra", 40)

    assert response.status_code == 401


def test_a_reading_for_a_tracked_course_is_stored_as_verified(client):
    user_id = _signed_in(client)
    _add(client)
    headers = _extension_token(user_id)
    with client.session_transaction() as s:
        s.clear()  # the extension has no cookie

    response = _report(client, headers, "https://www.khanacademy.org/math/algebra/unit-1", 42.5)

    assert response.get_json()["matched"] is True
    _sign_in(client, user_id)
    course = client.get("/api/courses").get_json()["courses"][0]
    assert course["verified_percent"] == 42.5
    assert course["week"]["minutes"] == 0, "a percentage is not time spent"


def test_a_page_that_is_not_tracked_is_not_recorded(client):
    user_id = _signed_in(client)
    _add(client)
    headers = _extension_token(user_id)

    response = _report(client, headers, "https://www.khanacademy.org/science/biology", 80)

    assert response.get_json() == {"status": "ok", "matched": False}
    with App.app.app_context():
        assert TrackedCourse.query.filter_by(user_id=user_id).count() == 1
        assert CourseCheckin.query.filter_by(user_id=user_id).count() == 0


def test_typed_time_never_becomes_verified(client):
    _signed_in(client)
    course_id = _add(client).get_json()["courses"][0]["id"]

    data = client.post(f"/api/courses/{course_id}/checkin", json={"minutes": 60}).get_json()

    assert data["courses"][0]["verified_percent"] is None


def test_the_extension_is_told_which_pages_to_look_at(client):
    user_id = _signed_in(client)
    _add(client)
    headers = _extension_token(user_id)
    with client.session_transaction() as s:
        s.clear()

    data = client.get("/api/courses/progress", headers=headers).get_json()

    assert data["tracked"] == ["khanacademy.org/math/algebra"]


@pytest.mark.parametrize("payload", [
    {"url": "javascript:alert(1)", "percent": 50},
    {"url": "https://www.khanacademy.org/math/algebra", "percent": 400},
    {"url": "https://www.khanacademy.org/math/algebra"},
    {},
])
def test_a_malformed_reading_is_refused(client, payload):
    user_id = _signed_in(client)
    _add(client)

    response = client.post("/api/courses/progress", json=payload,
                           headers=_extension_token(user_id))

    assert response.status_code == 400


# ── The assistant ────────────────────────────────────────────


def _tool(name, args, user_id):
    import plani_agent

    with App.app.test_request_context():
        return plani_agent._execute_tool(name, args, user_id)


def test_the_assistant_can_track_log_and_list(client):
    user_id = _make_user()

    tracked = _tool("track_course", {"url": "coursera.org/learn/ml", "title": "Machine Learning",
                                     "weekly_goal_minutes": 60}, user_id)
    logged = _tool("log_course_progress", {"course": "machine", "minutes": 30}, user_id)
    listed = _tool("list_tracked_courses", {}, user_id)

    assert tracked["status"] == "ok"
    assert logged["week"]["minutes"] == 30
    assert listed["courses"][0]["title"] == "Machine Learning"


def test_the_assistant_gets_a_reason_when_a_link_is_bad(client):
    result = _tool("track_course", {"url": "not a link"}, _make_user())

    assert "error" in result


def test_connect_buttons_come_from_a_fixed_table(client):
    import plani_agent

    user_id = _make_user()
    known = _tool("connect_account", {"service": "Canvas"}, user_id)
    unknown = _tool("connect_account", {"service": "https://evil.example"}, user_id)

    assert known["link"] == {"label": "Connect Canvas", "url": "/oauth/canvas"}
    assert unknown["link"]["url"] == "/connect"
    assert all(url.startswith("/") for _, url in plani_agent.CONNECT_LINKS.values())


def test_deleting_an_account_removes_its_courses(client):
    source = open(App.__file__, encoding="utf-8").read()

    assert "DELETE FROM course_checkins WHERE user_id = :uid" in source
    assert "DELETE FROM tracked_courses WHERE user_id = :uid" in source
    assert source.index("DELETE FROM course_checkins") < source.index("DELETE FROM tracked_courses")
