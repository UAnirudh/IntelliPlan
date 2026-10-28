"""The API sign-up used by the phone app and extension must be no weaker
than the website's /register form, which requires 8 characters."""

from __future__ import annotations

import uuid

import pytest

import App as app_module
from auth_api import MIN_PASSWORD_LENGTH


@pytest.fixture
def client():
    app_module.app.config["RATELIMIT_ENABLED"] = False
    with app_module.app.app_context():
        app_module.db.create_all()
        yield app_module.app.test_client()


def _email() -> str:
    return f"pw-floor-{uuid.uuid4().hex[:10]}@example.com"


def test_floor_matches_the_website():
    assert MIN_PASSWORD_LENGTH == 8


def test_short_password_is_refused(client):
    r = client.post("/api/auth/register", json={"email": _email(), "password": "x" * 7})
    assert r.status_code == 400
    assert "8 characters" in r.get_json()["message"]


def test_password_at_the_floor_is_accepted(client):
    r = client.post("/api/auth/register", json={"email": _email(), "password": "Abcdef12"})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()["token"]


def test_api_signup_queues_verification_dispatch(client, monkeypatch):
    queued = []
    monkeypatch.setattr(app_module, "send_email_verification_on_signup",
                        lambda user_id: queued.append(user_id))
    r = client.post("/api/auth/register", json={"email": _email(), "password": "Abcdef12"})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert len(queued) == 1
    user = app_module.db.session.get(app_module.User, queued[0])
    # API signup has no age capture; dispatch is queued but eligibility keeps
    # actual delivery blocked until the age/parent-consent gate is satisfied.
    assert user.email_verified is False
