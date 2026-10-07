"""Leaks and redirects found in the October audit.

Two of these were live: the Google callback printed the one-time
authorization code into the hosting logs on every sign-in, and finishing
onboarding followed ``?next=//another-site``.
"""

from __future__ import annotations

import pytest

import App
from App import User, UserIdentity, db

AUTH_CODE = "4/0AX-secret-authorization-code"
STATE = "state-token-that-should-not-be-logged"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture
def user_id():
    email = "audit+hardening@example.com"
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email,
                    password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                    name="Audit Tester", birth_year=2000)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    yield uid
    with App.app.app_context():
        UserIdentity.query.filter_by(user_id=uid).delete(synchronize_session=False)
        User.query.filter_by(id=uid).delete(synchronize_session=False)
        db.session.commit()


def _sign_in(client, uid: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


@pytest.mark.parametrize("path", ["/oauth2callback", "/oauth/google/callback"])
def test_the_google_callback_does_not_log_the_authorization_code(client, capsys, path):
    client.get(f"{path}?code={AUTH_CODE}&state={STATE}")

    logged = capsys.readouterr().out
    assert "[GOOGLE CALLBACK]" in logged, "the callback no longer logs at all"
    assert AUTH_CODE not in logged
    assert STATE not in logged


@pytest.mark.parametrize("target", [
    "//evil.example/steal",
    "/\evil.example",
    "https://evil.example/",
])
def test_finishing_onboarding_does_not_follow_an_off_site_next(client, user_id, target):
    _sign_in(client, user_id)

    response = client.post("/onboarding", query_string={"next": target},
                           data={"grade_level": "11"})

    assert response.status_code == 302
    assert response.headers["Location"] == "/command-center"


def test_finishing_onboarding_still_follows_a_path_on_this_site(client, user_id):
    _sign_in(client, user_id)

    response = client.post("/onboarding", query_string={"next": "/connect"},
                           data={"grade_level": "11"})

    assert response.headers["Location"] == "/connect"
