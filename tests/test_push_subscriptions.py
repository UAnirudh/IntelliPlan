"""Push subscriptions are per browser, not per student.

The bug these guard: ``/push/subscribe`` matched an existing row on
``user_id`` alone and overwrote it. A student who turned notifications on
at their laptop silently lost them on their phone, with nothing in the UI
to say so — the phone simply stopped buzzing. The endpoint is what a push
service addresses and is unique per browser install, so that is what the
upsert has to key on.
"""

from __future__ import annotations

import json

import pytest

import App
from App import MAX_PUSH_SUBSCRIPTIONS, PushSubscription, User, db


def sub_payload(endpoint: str) -> dict:
    return {
        "subscription": {
            "endpoint": endpoint,
            "keys": {"p256dh": "BFakeKeyForTests", "auth": "fakeauth"},
        }
    }


PHONE = "https://fcm.googleapis.com/fcm/send/phone-abc"
LAPTOP = "https://updates.push.services.mozilla.com/wpush/v2/laptop-xyz"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture
def user_id():
    email = "push+devices@example.com"
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email,
                    password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                    name="Push Tester")
        db.session.add(user)
        db.session.commit()
        uid = user.id
    yield uid
    with App.app.app_context():
        PushSubscription.query.filter_by(user_id=uid).delete(synchronize_session=False)
        User.query.filter_by(id=uid).delete(synchronize_session=False)
        db.session.commit()


def login(client, uid: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def subscribe(client, endpoint: str):
    return client.post("/push/subscribe", json=sub_payload(endpoint))


def rows_for(uid: int):
    with App.app.app_context():
        return PushSubscription.query.filter_by(user_id=uid).all()


# ── Multi-device ────────────────────────────────────────────────────


def test_two_devices_keep_two_subscriptions(client, user_id):
    login(client, user_id)
    assert subscribe(client, PHONE).status_code == 200
    assert subscribe(client, LAPTOP).status_code == 200

    endpoints = sorted(r.endpoint for r in rows_for(user_id))
    assert endpoints == sorted([PHONE, LAPTOP])


def test_resubscribing_the_same_browser_updates_in_place(client, user_id):
    """A browser refreshes its subscription routinely. That must not stack
    a new row every time the student opens the app."""
    login(client, user_id)
    subscribe(client, PHONE)
    subscribe(client, PHONE)
    subscribe(client, PHONE)
    assert len(rows_for(user_id)) == 1


def test_the_endpoint_is_stored_not_just_buried_in_the_json(client, user_id):
    login(client, user_id)
    subscribe(client, PHONE)
    row = rows_for(user_id)[0]
    assert row.endpoint == PHONE
    assert json.loads(row.subscription_json)["endpoint"] == PHONE


def test_subscriptions_are_capped_per_owner(client, user_id):
    """A client re-subscribing in a loop must not grow the table forever."""
    login(client, user_id)
    for i in range(MAX_PUSH_SUBSCRIPTIONS + 4):
        subscribe(client, f"https://fcm.googleapis.com/fcm/send/device-{i}")
    assert len(rows_for(user_id)) <= MAX_PUSH_SUBSCRIPTIONS


def test_the_cap_evicts_the_oldest_not_the_newest(client, user_id):
    login(client, user_id)
    for i in range(MAX_PUSH_SUBSCRIPTIONS + 2):
        subscribe(client, f"https://fcm.googleapis.com/fcm/send/device-{i}")
    kept = {r.endpoint for r in rows_for(user_id)}
    newest = f"https://fcm.googleapis.com/fcm/send/device-{MAX_PUSH_SUBSCRIPTIONS + 1}"
    assert newest in kept


# ── Validation ──────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [
    {},
    {"subscription": {}},
    {"subscription": {"endpoint": "http://insecure.example/push",
                      "keys": {"p256dh": "k", "auth": "a"}}},
    {"subscription": {"endpoint": "https://ok.example/push"}},
    {"subscription": {"endpoint": "https://ok.example/push", "keys": {"p256dh": "k"}}},
])
def test_a_malformed_subscription_is_rejected(client, user_id, bad):
    login(client, user_id)
    assert client.post("/push/subscribe", json=bad).status_code == 400
    assert rows_for(user_id) == []


def test_an_over_long_endpoint_is_rejected(client, user_id):
    login(client, user_id)
    payload = sub_payload("https://fcm.googleapis.com/fcm/send/" + "x" * 600)
    assert client.post("/push/subscribe", json=payload).status_code == 400


# ── The test button ─────────────────────────────────────────────────


def test_the_test_button_says_so_when_nothing_is_subscribed(client, user_id):
    login(client, user_id)
    body = client.post("/push/test").get_json()
    assert body["status"] == "error"
    assert "notifications on" in body["message"].lower()


def test_the_test_button_says_so_when_the_server_has_no_vapid_key(
        client, user_id, monkeypatch):
    """A student pressing Test and getting a generic failure has no way to
    know the problem is server configuration, not their browser."""
    login(client, user_id)
    subscribe(client, PHONE)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    body = client.post("/push/test").get_json()
    assert body["status"] == "error"
    assert "vapid" in body["message"].lower()


def test_the_test_button_reaches_every_device(client, user_id, monkeypatch):
    """Testing only the first row answers "will I get reminders here?" for
    the wrong device as often as the right one."""
    login(client, user_id)
    subscribe(client, PHONE)
    subscribe(client, LAPTOP)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-key")

    seen = []

    def fake_webpush(**kwargs):
        seen.append(kwargs["subscription_info"]["endpoint"])
        return True

    import pywebpush
    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)

    body = client.post("/push/test").get_json()
    assert body["status"] == "ok"
    assert body["sent"] == 2
    assert sorted(seen) == sorted([PHONE, LAPTOP])


# ── TTL ─────────────────────────────────────────────────────────────
#
# pywebpush defaults to TTL 0, which means "deliver only if the device is
# connected this instant". Microsoft's WNS rejects that outright — every
# push to an Edge user came back `400 Ttl value conflicts with
# X-WNS-Cache-Policy` and was dropped — and where it is accepted it still
# loses the notification for any phone that happens to be asleep.


def test_a_push_is_never_sent_with_ttl_zero(client, user_id, monkeypatch):
    login(client, user_id)
    subscribe(client, PHONE)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-key")

    seen = {}

    def fake_webpush(**kwargs):
        seen.update(kwargs)
        return True

    import pywebpush
    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)

    assert client.post("/push/test").get_json()["status"] == "ok"
    assert seen.get("ttl", 0) > 0


def test_reminders_carry_a_ttl_that_outlives_a_pocketed_phone(monkeypatch):
    from App import PUSH_DEFAULT_TTL, _send_push_to_user

    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-key")
    seen = {}

    def fake_webpush(**kwargs):
        seen.update(kwargs)
        return True

    import pywebpush
    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)

    with App.app.app_context():
        user = User(email="push+ttl@example.com",
                    password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode())
        db.session.add(user)
        db.session.commit()
        db.session.add(PushSubscription(
            user_id=user.id, endpoint=PHONE,
            subscription_json=json.dumps(sub_payload(PHONE)["subscription"])))
        db.session.commit()
        try:
            assert _send_push_to_user(user.id, {"title": "t", "body": "b"}) == 1
            assert seen["ttl"] == PUSH_DEFAULT_TTL
        finally:
            PushSubscription.query.filter_by(user_id=user.id).delete(
                synchronize_session=False)
            User.query.filter_by(id=user.id).delete(synchronize_session=False)
            db.session.commit()


def test_an_outbox_message_expires_at_the_push_service_too():
    """The outbox already knows when a message stops being worth reading.
    Handing that deadline to the push service means a phone switched on
    after it does not receive a stale reminder at all."""
    from datetime import timedelta

    from notifications_glue import _push_ttl_for
    from time_utils import utcnow

    class Row:
        expires_at = utcnow() + timedelta(minutes=30)

    ttl = _push_ttl_for(Row())
    assert 25 * 60 < ttl <= 30 * 60


def test_an_already_expired_outbox_row_still_asks_for_a_floor_not_zero():
    from datetime import timedelta

    from App import PUSH_MIN_TTL
    from notifications_glue import _push_ttl_for
    from time_utils import utcnow

    class Row:
        expires_at = utcnow() - timedelta(hours=2)

    assert _push_ttl_for(Row()) == PUSH_MIN_TTL


def test_a_row_with_no_expiry_leaves_the_default_alone():
    from notifications_glue import _push_ttl_for

    class Row:
        expires_at = None

    assert _push_ttl_for(Row()) is None


def test_an_expired_subscription_is_dropped_rather_than_retried_forever(
        client, user_id, monkeypatch):
    login(client, user_id)
    subscribe(client, PHONE)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-key")

    def gone(**kwargs):
        raise RuntimeError("Push failed: 410 Gone")

    import pywebpush
    monkeypatch.setattr(pywebpush, "webpush", gone)

    body = client.post("/push/test").get_json()
    assert body["status"] == "error"
    assert "expired" in body["message"].lower()
    assert rows_for(user_id) == []
