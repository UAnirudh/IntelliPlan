"""The desktop app asks for its reminders, because nothing can push them.

Electron has no push service behind it, so a push row queued for someone
using the desktop build dies with "no active push subscriptions". The
reminder was still due. ``/api/notifications/desktop-feed`` returns those
rows so the app can raise them as operating-system notifications.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import App
from App import NotificationOutbox, User, db
from time_utils import utcnow

FEED = "/api/notifications/desktop-feed"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


def _make_user(email: str) -> int:
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email,
                    password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                    name="Desktop Tester")
        db.session.add(user)
        db.session.commit()
        return user.id


def _drop_user(uid: int) -> None:
    with App.app.app_context():
        NotificationOutbox.query.filter_by(user_id=uid).delete(synchronize_session=False)
        User.query.filter_by(id=uid).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def user_id():
    uid = _make_user("desktop+feed@example.com")
    yield uid
    _drop_user(uid)


@pytest.fixture
def other_user_id():
    uid = _make_user("desktop+other@example.com")
    yield uid
    _drop_user(uid)


def login(client, uid: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def queue(uid: int, key: str, *, minutes_ago: float = 1, channel: str = "push",
          state: str = "dead", expires_in: float | None = 60) -> int:
    """Insert an outbox row scheduled ``minutes_ago`` (negative = future)."""
    with App.app.app_context():
        now = utcnow()
        row = NotificationOutbox(
            user_id=uid, kind="session_upcoming", channel=channel,
            dedupe_key=key, title=f"Title {key}", body=f"Body {key}", url="/active",
            state=state, scheduled_for=now - timedelta(minutes=minutes_ago),
            expires_at=None if expires_in is None else now + timedelta(minutes=expires_in),
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def feed_ids(client) -> list[int]:
    response = client.get(FEED)
    assert response.status_code == 200
    return [item["id"] for item in response.get_json()["notifications"]]


def test_the_feed_needs_a_signed_in_account(client):
    assert client.get(FEED).status_code == 401


def test_a_due_reminder_is_returned_with_what_to_show(client, user_id):
    row_id = queue(user_id, "due")
    login(client, user_id)

    items = client.get(FEED).get_json()["notifications"]

    assert items == [{"id": row_id, "kind": "session_upcoming",
                      "title": "Title due", "body": "Body due", "url": "/active"}]


def test_a_reminder_that_could_not_be_pushed_is_still_shown(client, user_id):
    """The desktop case: no subscription, so the dispatcher marked it dead."""
    row_id = queue(user_id, "dead", state="dead")
    login(client, user_id)

    assert feed_ids(client) == [row_id]


def test_a_reminder_held_for_quiet_hours_waits_for_its_time(client, user_id):
    queue(user_id, "later", minutes_ago=-45, state="pending")
    login(client, user_id)

    assert feed_ids(client) == []


def test_old_reminders_are_not_replayed(client, user_id):
    queue(user_id, "yesterday", minutes_ago=24 * 60, expires_in=None)
    login(client, user_id)

    assert feed_ids(client) == []


def test_an_expired_reminder_is_left_out(client, user_id):
    queue(user_id, "stale", minutes_ago=20, expires_in=-5)
    login(client, user_id)

    assert feed_ids(client) == []


def test_a_cancelled_reminder_is_left_out(client, user_id):
    queue(user_id, "cancelled", state="cancelled")
    login(client, user_id)

    assert feed_ids(client) == []


def test_email_and_sms_copies_are_not_shown_twice(client, user_id):
    push_id = queue(user_id, "push-copy", channel="push")
    queue(user_id, "email-copy", channel="email", state="sent")
    queue(user_id, "sms-copy", channel="sms", state="sent")
    login(client, user_id)

    assert feed_ids(client) == [push_id]


def test_another_students_reminders_stay_theirs(client, user_id, other_user_id):
    queue(other_user_id, "theirs")
    login(client, user_id)

    assert feed_ids(client) == []


# ── Registering a desktop install ────────────────────────────

REGISTER = "/push/desktop-register"
INSTALL = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _endpoints(uid: int) -> list[str]:
    with App.app.app_context():
        return [s.endpoint for s in App.PushSubscription.query.filter_by(user_id=uid).all()]


@pytest.fixture
def registered_user_id(user_id):
    yield user_id
    with App.app.app_context():
        App.PushSubscription.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        db.session.commit()


def test_registering_needs_a_signed_in_account(client):
    assert client.post(REGISTER, json={"install_id": INSTALL}).status_code == 401


def test_registering_twice_keeps_one_row(client, registered_user_id):
    login(client, registered_user_id)

    assert client.post(REGISTER, json={"install_id": INSTALL}).status_code == 200
    assert client.post(REGISTER, json={"install_id": INSTALL}).status_code == 200

    assert _endpoints(registered_user_id) == [f"desktop:{INSTALL}"]


@pytest.mark.parametrize("bad", ["", "short", "has spaces in it!!", "x" * 65, "https://evil.example/x"])
def test_a_malformed_install_id_is_refused(client, registered_user_id, bad):
    login(client, registered_user_id)

    assert client.post(REGISTER, json={"install_id": bad}).status_code == 400
    assert _endpoints(registered_user_id) == []


def test_a_desktop_install_turns_the_push_channel_on(client, registered_user_id):
    """Without a subscription row the sweep never queues push reminders."""
    import notifications_glue
    from intelliplan.notifications import Channel

    with App.app.app_context():
        user = db.session.get(User, registered_user_id)
        user.push_reminders_opt_in = True
        db.session.commit()
        assert Channel.PUSH not in notifications_glue._preferences_for(user).channels

    login(client, registered_user_id)
    client.post(REGISTER, json={"install_id": INSTALL})

    with App.app.app_context():
        user = db.session.get(User, registered_user_id)
        assert Channel.PUSH in notifications_glue._preferences_for(user).channels


def test_a_desktop_install_is_never_handed_to_the_push_service(client, registered_user_id, monkeypatch):
    import pywebpush

    def explode(**_kwargs):
        raise AssertionError("webpush called for a desktop install")

    monkeypatch.setattr(pywebpush, "webpush", explode)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-key")
    login(client, registered_user_id)
    client.post(REGISTER, json={"install_id": INSTALL})

    with App.app.app_context():
        delivered = App._send_push_to_user(registered_user_id, {"title": "t", "body": "b", "url": "/"})

    assert delivered == 1


# ── The page loads the poller ────────────────────────────────


def test_signed_in_pages_load_the_desktop_poller(client, user_id):
    login(client, user_id)

    page = client.get("/settings", follow_redirects=True).get_data(as_text=True)

    assert "/static/js/ip-desktop-feed.js?v=" in page
    assert "/static/js/ip-reminders.js?v=" in page


def test_signed_out_pages_do_not_load_the_desktop_poller(client):
    assert "ip-desktop-feed.js" not in client.get("/").get_data(as_text=True)
