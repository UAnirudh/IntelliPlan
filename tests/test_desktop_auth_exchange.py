"""The exchange endpoint, which turns a one-time code into a real session.

tests/test_desktop_auth.py covers the arithmetic. This covers the part an
attacker actually touches: the HTTP route. A code rides to the app over
intelliplan://, a scheme any local program can register, so "someone else
has the code" is the assumed case rather than the unlucky one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import App as app_module
import desktop_auth as da

EXCHANGE = "/api/desktop/auth/exchange"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    app_module.limiter.enabled = False
    with app_module.app.test_client() as c:
        yield c
    app_module.limiter.enabled = True


@pytest.fixture
def user():
    email = "desktop-auth@example.test"
    with app_module.app.app_context():
        row = app_module.User.query.filter_by(email=email).first()
        if row is None:
            row = app_module.User(email=email, name="Desktop Auth")
            app_module.db.session.add(row)
            app_module.db.session.commit()
        return row.id


def mint(user_id, challenge, created_at=None):
    """Put a live code in the table exactly as the Google callback does."""
    code = da.new_code()
    with app_module.app.app_context():
        row = app_module.DesktopAuthCode(
            code_hash=da.hash_code(code),
            code_challenge=challenge,
            user_id=user_id,
        )
        if created_at is not None:
            row.created_at = created_at
        app_module.db.session.add(row)
        app_module.db.session.commit()
    return code


def used_at(code):
    with app_module.app.app_context():
        row = app_module.DesktopAuthCode.query.filter_by(
            code_hash=da.hash_code(code)
        ).first()
        return row.used_at if row else None


# ── The happy path ──────────────────────────────────────────────────


def test_a_valid_code_and_verifier_signs_the_app_in(client, user):
    verifier = da.new_verifier()
    code = mint(user, da.challenge_for(verifier))

    res = client.post(EXCHANGE, json={"code": code, "verifier": verifier})

    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
    assert used_at(code) is not None, "a spent code must be marked spent"


# ── What an attacker gets ───────────────────────────────────────────


def test_a_stolen_code_without_the_verifier_is_useless(client, user):
    """The reason PKCE is here. Another local app can register
    intelliplan:// and read the code straight off the deep link; it still
    cannot turn that into a session."""
    verifier = da.new_verifier()
    code = mint(user, da.challenge_for(verifier))

    res = client.post(EXCHANGE, json={"code": code, "verifier": da.new_verifier()})

    assert res.status_code == 400


def test_a_wrong_verifier_burns_the_code(client, user):
    """A valid code with the wrong verifier is the signature of a theft, so
    the real app does not get to spend it afterwards either. Better to make
    the student click sign-in again than to leave a known-leaked code live."""
    verifier = da.new_verifier()
    code = mint(user, da.challenge_for(verifier))

    client.post(EXCHANGE, json={"code": code, "verifier": da.new_verifier()})
    assert used_at(code) is not None

    retry = client.post(EXCHANGE, json={"code": code, "verifier": verifier})
    assert retry.status_code == 400


def test_a_code_cannot_be_replayed(client, user):
    verifier = da.new_verifier()
    code = mint(user, da.challenge_for(verifier))

    assert client.post(EXCHANGE, json={"code": code, "verifier": verifier}).status_code == 200
    assert client.post(EXCHANGE, json={"code": code, "verifier": verifier}).status_code == 400


def test_an_expired_code_is_refused(client, user):
    verifier = da.new_verifier()
    stale = datetime.utcnow() - timedelta(seconds=da.CODE_TTL_SECONDS + 5)
    code = mint(user, da.challenge_for(verifier), created_at=stale)

    assert client.post(EXCHANGE, json={"code": code, "verifier": verifier}).status_code == 400


def test_an_invented_code_is_refused(client):
    verifier = da.new_verifier()
    res = client.post(EXCHANGE, json={"code": da.new_code(), "verifier": verifier})
    assert res.status_code == 400


@pytest.mark.parametrize("payload", [
    {},
    {"code": ""},
    {"verifier": "x" * 43},
    {"code": "abc", "verifier": ""},
    {"code": "abc", "verifier": "tooshort"},
])
def test_malformed_requests_are_refused(client, payload):
    assert client.post(EXCHANGE, json=payload).status_code == 400


def test_failures_are_indistinguishable(client, user):
    """Every rejection returns the same body. Telling "no such code" apart
    from "wrong verifier" hands someone grinding at the endpoint a way to
    learn which half of their guess was right."""
    verifier = da.new_verifier()
    live = mint(user, da.challenge_for(verifier))

    unknown = client.post(EXCHANGE, json={"code": da.new_code(), "verifier": verifier})
    mismatch = client.post(EXCHANGE, json={"code": live, "verifier": da.new_verifier()})

    assert unknown.status_code == mismatch.status_code
    assert unknown.get_json() == mismatch.get_json()
