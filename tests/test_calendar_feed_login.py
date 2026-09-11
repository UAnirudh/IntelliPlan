"""Connecting with a calendar feed, end to end.

The point of this path is that it needs nothing from anybody: no Developer
Key (only a school's own Canvas admin can issue one, per school), no access
token, no settings page. So these tests check the whole way through -- a
pasted URL becomes a session, and that session produces tasks on
/tasks/unified -- because a feed that validates at login and then feeds
nothing into the planner is worse than not offering it.
"""

from __future__ import annotations

import types

import pytest

import App
import ics_feed


FEED_URL = "https://lakesideschool.instructure.com/feeds/calendars/user_abc123.ics"


def canvas_feed(*rows):
    events = "".join(
        "BEGIN:VEVENT\r\n"
        f"UID:event-assignment-{i}\r\n"
        f"DTSTART;VALUE=DATE:{due}\r\n"
        f"SUMMARY:{summary}\r\n"
        "END:VEVENT\r\n"
        for i, (summary, due) in enumerate(rows)
    )
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "PRODID:-//Instructure//Canvas//EN\r\n"
        f"{events}END:VCALENDAR\r\n"
    ).encode()


class _Resp:
    def __init__(self, content=b"", status=200):
        self.status_code = status
        self.content = content


def serve(monkeypatch, content, status=200):
    """Stand in for the network, patching only .get.

    Replacing the whole requests module would take RequestException with it,
    and fetch_feed catches on that -- so the module's own error handling
    would break and the test would be measuring the stub, not the code.
    """
    monkeypatch.setattr(ics_feed.requests, "get", lambda *a, **k: _Resp(content, status))


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture
def future_feed(monkeypatch):
    """A feed whose work is comfortably in the future, whenever tests run."""
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=5)).strftime("%Y%m%d")
    later = (date.today() + timedelta(days=12)).strftime("%Y%m%d")
    body = canvas_feed(("Essay 3 [AP US History]", soon), ("Lab report [Chemistry]", later))
    serve(monkeypatch, body)
    return body


# ── Connecting ───────────────────────────────────────────────────────


def test_a_pasted_feed_link_connects(client, future_feed):
    r = client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    assert r.status_code == 302
    assert "/command-center" in r.headers["Location"]
    with client.session_transaction() as sess:
        assert sess["login_type"] == "calendar_feed"
        assert sess["feed_url"] == FEED_URL


def test_a_webcal_link_connects_too(client, future_feed):
    """Canvas hands out webcal://, and students paste exactly what they copy."""
    r = client.post("/login/calendar-feed", data={
        "feed_url": FEED_URL.replace("https://", "webcal://")})
    assert r.status_code == 302
    with client.session_transaction() as sess:
        assert sess["feed_url"].startswith("https://")


def _all_tasks(payload):
    """/tasks/unified buckets by urgency rather than returning a flat list."""
    return [t for bucket in ("overdue", "today", "upcoming")
            for t in (payload or {}).get(bucket, [])]


def test_the_connected_feed_actually_produces_tasks(client, future_feed):
    """The test that matters: a feed that connects but plans nothing is
    worse than no feed at all."""
    client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    titles = [t["title"] for t in _all_tasks(client.get("/tasks/unified").get_json())]
    assert "Essay 3" in titles
    assert "Lab report" in titles


def test_the_course_name_survives_into_the_planner(client, future_feed):
    client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    tasks = _all_tasks(client.get("/tasks/unified").get_json())
    essay = next(t for t in tasks if t["title"] == "Essay 3")
    assert essay["course"] == "AP US History"
    assert essay["due_date"]


# ── Failing usefully ─────────────────────────────────────────────────


def test_an_unreachable_feed_explains_rather_than_crashing(client, monkeypatch):
    import requests as real_requests

    def boom(*a, **k):
        raise real_requests.ConnectionError("nope")
    monkeypatch.setattr(ics_feed.requests, "get", boom)
    r = client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    assert r.status_code == 200
    assert "Could not reach" in r.get_data(as_text=True)


def test_pasting_the_calendar_page_instead_of_the_feed_is_caught(client, monkeypatch):
    """The obvious mistake, and it returns a cheerful 200 of HTML."""
    serve(monkeypatch, b"<html>Canvas Calendar</html>")
    r = client.post("/login/calendar-feed", data={"feed_url":
                    "https://lakesideschool.instructure.com/calendar"})
    assert r.status_code == 200
    assert "did not return a calendar" in r.get_data(as_text=True)


def test_a_reset_feed_link_says_so(client, monkeypatch):
    serve(monkeypatch, b"", 404)
    body = client.post("/login/calendar-feed", data={"feed_url": FEED_URL}).get_data(as_text=True)
    assert "no longer valid" in body


def test_an_empty_feed_is_rejected_at_connect_time(client, monkeypatch):
    """Better to say so now than to hand the student an empty planner and
    let them conclude the product is broken."""
    serve(monkeypatch, canvas_feed())
    r = client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    assert r.status_code == 200
    assert "no upcoming assignments" in r.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert sess.get("login_type") != "calendar_feed", "must not connect an empty feed"


def test_a_blank_url_asks_for_one(client):
    r = client.post("/login/calendar-feed", data={"feed_url": "   "})
    assert r.status_code == 200
    assert "Paste your calendar feed URL" in r.get_data(as_text=True)


# ── The page offers it ───────────────────────────────────────────────


def test_the_login_page_offers_the_feed_option(client):
    body = client.get("/login/canvas").get_data(as_text=True)
    assert 'action="/login/calendar-feed"' in body
    assert 'name="feed_url"' in body


def test_the_page_is_honest_that_a_feed_carries_no_grades(client):
    """A student who wants the gradebook should learn that here, not by
    finding it empty later."""
    body = client.get("/login/canvas").get_data(as_text=True).lower()
    assert "grade" in body


def test_the_token_path_is_still_offered(client):
    """The inverse guard: the feed is an addition, not a replacement."""
    body = client.get("/login/canvas").get_data(as_text=True)
    assert 'action="/login/canvas"' in body
    assert 'name="canvas_token"' in body


# ── Signed in, where credentials take a different route ──────────────


@pytest.fixture
def student(request):
    """A real account, so credentials go through LinkedAccount rather than
    the session. Both paths reach get_active_account, and only the signed-out
    one was covered when this feature landed."""
    from App import LinkedAccount, User, db
    with App.app.app_context():
        User.query.filter(User.email.like("feeduser+%")).delete(synchronize_session=False)
        db.session.commit()
        u = User(email="feeduser+a@example.com",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="Feed User")
        db.session.add(u)
        db.session.commit()
        uid = u.id

    def cleanup():
        with App.app.app_context():
            LinkedAccount.query.filter_by(user_id=uid).delete()
            User.query.filter_by(id=uid).delete()
            db.session.commit()
    request.addfinalizer(cleanup)
    return uid


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_a_signed_in_student_gets_a_linked_account(client, student, future_feed):
    from App import LinkedAccount
    login(client, student)
    assert client.post("/login/calendar-feed",
                       data={"feed_url": FEED_URL}).status_code == 302
    with App.app.app_context():
        acct = LinkedAccount.query.filter_by(user_id=student, is_active=True).first()
        assert acct is not None
        assert acct.login_type == "calendar_feed"
        assert acct.get_credentials()["feed_url"] == FEED_URL


def test_a_signed_in_students_feed_reaches_the_planner(client, student, future_feed):
    """The stored-credential route, not just the session one."""
    login(client, student)
    client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    titles = [t["title"] for t in _all_tasks(client.get("/tasks/unified").get_json())]
    assert "Essay 3" in titles


# ── A new login type must not break the surfaces it does not serve ───


@pytest.mark.parametrize("path", [
    "/tasks/unified", "/gradebook", "/grades/data", "/missing/data",
    "/scheduler", "/streak", "/priority", "/classes",
])
def test_no_surface_errors_for_a_feed_account(client, future_feed, path):
    """Adding a login type is exactly how unrelated pages start 500ing: every
    `elif login_type == ...` chain that has no branch for it falls through to
    whatever comes last. Nothing here needs to *serve* feed data -- it needs
    to not break."""
    client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    assert client.get(path).status_code < 500


def test_the_gradebook_is_empty_rather_than_wrong(client, future_feed):
    """A feed carries no grades. Empty is the honest answer; a fabricated 0%
    would read as a real average and drag a student's own maths off."""
    client.post("/login/calendar-feed", data={"feed_url": FEED_URL})
    assert client.get("/grades/data").get_json() == []
    assert client.get("/missing/data").get_json() == []
