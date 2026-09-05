"""Setting and changing a password.

This endpoint mints a credential that outlives every session, so the tests
that matter are the ones about who is allowed to call it. The feature
exists because there was previously no way to set a password at all —
every generate_password_hash() in the codebase was a registration path —
which left Google-only accounts unable to sign in to the mobile and
desktop clients, and anyone who forgot a password locked out for good.
"""

from __future__ import annotations

import time
import uuid

import pytest

import App as app_module

ENDPOINT = "/api/auth/password"
STATUS = "/api/auth/password/status"
GOOD = "correcthorsebattery1"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    app_module.limiter.enabled = False
    with app_module.app.test_client() as c:
        yield c
    app_module.limiter.enabled = True


def make_user(password: str | None):
    """A user with or without a password, mirroring the two real cases:
    email signup, and Google signup with no hash at all."""
    email = f"pw-{uuid.uuid4().hex[:12]}@example.test"
    with app_module.app.app_context():
        user = app_module.User(
            email=email,
            name="Password Test",
            password_hash=(
                app_module.bcrypt.generate_password_hash(password).decode()
                if password else None
            ),
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()
        return user.id, email


def sign_in(client, user_id):
    """Establish a session without going through a password, which is the
    situation a Google user is actually in.

    Also stamps ``auth_time`` as App.py's ``user_logged_in`` receiver would
    on a real login (password or Google) — the passwordless branch of
    /api/auth/password requires a recent one, since there is no password
    to prove knowledge of otherwise."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["auth_time"] = time.time()


def hash_of(user_id):
    """The stored hash. Note "no password" is an empty string here rather
    than NULL, which is why every check below is on truthiness — that is
    also what the endpoint and the token exchange branch on."""
    with app_module.app.app_context():
        return app_module.db.session.get(app_module.User, user_id).password_hash


# ── Who may call it ─────────────────────────────────────────────────


def test_a_stranger_cannot_set_anyones_password(client):
    """The load-bearing test. No session, no change."""
    user_id, _ = make_user(None)
    before = hash_of(user_id)
    res = client.post(ENDPOINT, json={"new_password": GOOD})
    assert res.status_code == 401
    assert hash_of(user_id) == before


def test_status_requires_a_session(client):
    assert client.get(STATUS).status_code == 401


# ── The Google case: no password yet ────────────────────────────────


def test_an_account_with_no_password_can_set_one(client):
    """Why this endpoint exists. The session came from Google, so there is
    no old password to prove, and the session itself is the proof."""
    user_id, _ = make_user(None)
    sign_in(client, user_id)

    res = client.post(ENDPOINT, json={"new_password": GOOD})

    assert res.status_code == 200
    assert hash_of(user_id)


def test_a_session_that_never_actually_signed_in_cannot_set_one(client):
    """A session cookie with no recent login behind it — stolen, forged,
    or restored from a long-lived remember cookie — is not a Google
    sign-in. Without a password to check, this is the only guard the
    Google-only case has."""
    user_id, _ = make_user(None)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        # Deliberately no auth_time: this is what a session hijacked
        # without ever calling login_user() looks like.

    res = client.post(ENDPOINT, json={"new_password": GOOD})

    assert res.status_code == 401
    assert res.get_json().get("reason") == "reauth_required"
    assert not hash_of(user_id)


def test_a_stale_login_cannot_set_one(client):
    """auth_time exists but is old — same requirement, phrased as a
    timeout rather than a total absence."""
    user_id, _ = make_user(None)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["auth_time"] = time.time() - (60 * 60)  # an hour ago

    res = client.post(ENDPOINT, json={"new_password": GOOD})

    assert res.status_code == 401
    assert not hash_of(user_id)


def test_the_new_password_actually_works_for_a_token(client):
    """End to end: the whole point is being able to sign in to the mobile
    and desktop clients afterwards, which exchange email+password for a
    bearer token."""
    user_id, email = make_user(None)
    sign_in(client, user_id)
    client.post(ENDPOINT, json={"new_password": GOOD})

    token_res = client.post("/api/v1/auth/token", json={"email": email, "password": GOOD})

    assert token_res.status_code == 200
    assert token_res.get_json().get("token")


def test_status_reports_whether_a_password_exists(client):
    user_id, _ = make_user(None)
    sign_in(client, user_id)
    assert client.get(STATUS).get_json()["has_password"] is False

    client.post(ENDPOINT, json={"new_password": GOOD})
    assert client.get(STATUS).get_json()["has_password"] is True


# ── Changing one that already exists ────────────────────────────────


def test_changing_a_password_requires_the_current_one(client):
    """Otherwise an unlocked laptop or a stolen session cookie converts
    temporary access into a credential that survives every logout."""
    user_id, _ = make_user("theoldpassword1")
    sign_in(client, user_id)
    before = hash_of(user_id)

    res = client.post(ENDPOINT, json={"new_password": GOOD})

    assert res.status_code == 400
    assert hash_of(user_id) == before


def test_a_wrong_current_password_is_refused(client):
    user_id, _ = make_user("theoldpassword1")
    sign_in(client, user_id)
    before = hash_of(user_id)

    res = client.post(ENDPOINT, json={
        "current_password": "notthepassword", "new_password": GOOD,
    })

    assert res.status_code == 403
    assert hash_of(user_id) == before


def test_the_right_current_password_lets_it_through(client):
    user_id, _ = make_user("theoldpassword1")
    sign_in(client, user_id)
    before = hash_of(user_id)

    res = client.post(ENDPOINT, json={
        "current_password": "theoldpassword1", "new_password": GOOD,
    })

    assert res.status_code == 200
    assert hash_of(user_id) != before


def test_the_old_password_stops_working_afterwards(client):
    user_id, email = make_user("theoldpassword1")
    sign_in(client, user_id)
    client.post(ENDPOINT, json={
        "current_password": "theoldpassword1", "new_password": GOOD,
    })

    old = client.post("/api/v1/auth/token", json={"email": email, "password": "theoldpassword1"})
    new = client.post("/api/v1/auth/token", json={"email": email, "password": GOOD})

    assert old.status_code == 401
    assert new.status_code == 200


# ── Weak input ──────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "short", "1234567"])
def test_a_password_below_the_minimum_is_refused(client, bad):
    """Matches the web registration form. If these drift, a password can be
    set that cannot be used to register, or the reverse."""
    user_id, _ = make_user(None)
    sign_in(client, user_id)

    res = client.post(ENDPOINT, json={"new_password": bad})

    assert res.status_code == 400
    assert not hash_of(user_id)


def test_the_password_is_never_echoed_back(client):
    user_id, _ = make_user(None)
    sign_in(client, user_id)
    res = client.post(ENDPOINT, json={"new_password": GOOD})
    assert GOOD not in res.get_data(as_text=True)
