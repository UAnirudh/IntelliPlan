"""Push notifications to the phone app.

Expo tokens share the PushSubscription table with browser endpoints, which
is the crux of these tests: a phone token must never reach pywebpush (it is
not a URL, and the failure is confusing), and a browser endpoint must never
reach Expo. Everything else is about not losing deliveries in the counting.

No test sends a real notification; the transport is injected.
"""

from __future__ import annotations

import json
import uuid

import pytest

import App as app_module
from intelliplan.notifications import expo_push

TOKEN = "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"
TOKEN_2 = "ExponentPushToken[yyyyyyyyyyyyyyyyyyyyyy]"


# ── Telling the two kinds of subscription apart ─────────────────────


@pytest.mark.parametrize("value", [
    TOKEN,
    "ExpoPushToken[abc]",
])
def test_expo_tokens_are_recognised(value):
    assert expo_push.is_expo_token(value)


@pytest.mark.parametrize("value", [
    None, "", "   ",
    "https://fcm.googleapis.com/fcm/send/abc123",       # a real browser endpoint
    "https://updates.push.services.mozilla.com/wpush/v2/xyz",
])
def test_browser_endpoints_are_not_mistaken_for_expo_tokens(value):
    """The load-bearing check. A browser endpoint routed to Expo silently
    stops that student's notifications."""
    assert not expo_push.is_expo_token(value)


# ── Message shape ───────────────────────────────────────────────────


def test_the_payload_maps_onto_expos_fields():
    msgs = expo_push.build_messages([TOKEN], {
        "title": "Essay due", "body": "In one hour", "url": "/active",
    })
    assert len(msgs) == 1
    m = msgs[0]
    assert m["to"] == TOKEN
    assert m["title"] == "Essay due"
    assert m["body"] == "In one hour"
    # Anything that is not title/body rides along so a tap can open the
    # right screen.
    assert m["data"]["url"] == "/active"


def test_non_expo_tokens_are_dropped_before_sending():
    msgs = expo_push.build_messages(["https://fcm.googleapis.com/x", TOKEN], {"title": "x"})
    assert [m["to"] for m in msgs] == [TOKEN]


def test_a_deadline_reminder_is_sent_at_high_priority():
    """A reminder the OS defers by an hour is not a reminder."""
    assert expo_push.build_messages([TOKEN], {"title": "x"})[0]["priority"] == "high"


# ── Sending ─────────────────────────────────────────────────────────


def test_successful_receipts_are_counted():
    def fake(url, batch):
        return {"data": [{"status": "ok"} for _ in batch]}

    out = expo_push.send([TOKEN, TOKEN_2], {"title": "x"}, opener=fake)
    assert out == {"sent": 2, "invalid": []}


def test_an_uninstalled_app_is_reported_for_deletion():
    def fake(url, batch):
        return {"data": [{"status": "error", "message": "gone",
                          "details": {"error": "DeviceNotRegistered"}}]}

    out = expo_push.send([TOKEN], {"title": "x"}, opener=fake)
    assert out["sent"] == 0
    assert out["invalid"] == [TOKEN]


def test_a_transient_error_does_not_discard_the_device():
    """Rate limits and Expo hiccups come back next sweep. Dropping the row
    would silently unsubscribe a real phone."""
    def fake(url, batch):
        return {"data": [{"status": "error", "message": "too many requests",
                          "details": {"error": "MessageRateExceeded"}}]}

    out = expo_push.send([TOKEN], {"title": "x"}, opener=fake)
    assert out["invalid"] == []


def test_a_network_failure_is_swallowed():
    """A push that cannot be delivered must not take down the cron sweep
    that was delivering it."""
    def boom(url, batch):
        raise OSError("network down")

    assert expo_push.send([TOKEN], {"title": "x"}, opener=boom) == {"sent": 0, "invalid": []}


def test_nothing_to_send_makes_no_request():
    def explode(url, batch):
        raise AssertionError("should not have been called")

    assert expo_push.send([], {"title": "x"}, opener=explode)["sent"] == 0


# ── Registration endpoint ───────────────────────────────────────────


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    app_module.limiter.enabled = False
    with app_module.app.test_client() as c:
        yield c
    app_module.limiter.enabled = True


@pytest.fixture
def auth(client):
    email = f"push-{uuid.uuid4().hex[:12]}@example.test"
    password = "correcthorsebattery1"
    with app_module.app.app_context():
        u = app_module.User(
            email=email, name="Push",
            password_hash=app_module.bcrypt.generate_password_hash(password).decode(),
        )
        app_module.db.session.add(u)
        app_module.db.session.commit()
        uid = u.id
    token = client.post("/api/v1/auth/token",
                        json={"email": email, "password": password}).get_json()["token"]
    return {"headers": {"Authorization": f"Bearer {token}"}, "user_id": uid}


def rows_for(user_id):
    with app_module.app.app_context():
        return app_module.PushSubscription.query.filter_by(user_id=user_id).all()


def test_registering_stores_the_token(client, auth):
    tok = f"ExponentPushToken[{uuid.uuid4().hex[:22]}]"
    res = client.post("/api/v1/push/register", headers=auth["headers"], json={"token": tok})
    assert res.status_code == 200
    assert any(r.endpoint == tok for r in rows_for(auth["user_id"]))


def test_registering_twice_does_not_double_the_device(client, auth):
    """Reopening the app hands back the same token. A row per launch would
    multiply every notification by how often the app was opened."""
    tok = f"ExponentPushToken[{uuid.uuid4().hex[:22]}]"
    client.post("/api/v1/push/register", headers=auth["headers"], json={"token": tok})
    client.post("/api/v1/push/register", headers=auth["headers"], json={"token": tok})
    assert sum(1 for r in rows_for(auth["user_id"]) if r.endpoint == tok) == 1


def test_a_bogus_token_is_refused(client, auth):
    res = client.post("/api/v1/push/register", headers=auth["headers"],
                      json={"token": "not-a-token"})
    assert res.status_code == 400


def test_registering_requires_authentication(client):
    res = client.post("/api/v1/push/register", json={"token": TOKEN})
    assert res.status_code in {401, 403}


def test_unregistering_removes_the_device(client, auth):
    tok = f"ExponentPushToken[{uuid.uuid4().hex[:22]}]"
    client.post("/api/v1/push/register", headers=auth["headers"], json={"token": tok})
    res = client.post("/api/v1/push/unregister", headers=auth["headers"], json={"token": tok})
    assert res.status_code == 200
    assert not any(r.endpoint == tok for r in rows_for(auth["user_id"]))


def test_unregistering_an_unknown_token_is_still_success(client, auth):
    """Idempotent: signing out twice is not an error."""
    res = client.post("/api/v1/push/unregister", headers=auth["headers"],
                      json={"token": f"ExponentPushToken[{uuid.uuid4().hex[:22]}]"})
    assert res.status_code == 200
