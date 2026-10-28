"""First-party mobile age setup and parent-consent enforcement."""

import uuid

import pytest

import App


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    previous = App.limiter.enabled
    App.limiter.enabled = False
    with App.app.test_client() as client:
        yield client
    App.limiter.enabled = previous


def _account(client, *, email=None):
    email = email or f"mobile-{uuid.uuid4().hex}@example.test"
    with App.app.app_context():
        user = App.User(
            email=email,
            name="Mobile user",
            password_hash=App.bcrypt.generate_password_hash("correcthorsebattery1").decode(),
            birth_year=None,
            parent_consent_granted=False,
        )
        App.db.session.add(user)
        App.db.session.commit()
        user_id = user.id
    response = client.post("/api/v1/auth/token", json={
        "email": email, "password": "correcthorsebattery1",
    })
    assert response.status_code == 200
    return user_id, {"Authorization": f"Bearer {response.get_json()['token']}"}


def test_age_submission_requires_first_party_auth(client):
    response = client.post("/api/v1/age", json={"birth_year": 2000})
    assert response.status_code == 401


@pytest.mark.parametrize("year", [None, "not-a-year", 1899, App.utcnow().year + 1, 2000.5])
def test_invalid_or_future_birth_year_is_rejected(client, year):
    user_id, headers = _account(client)
    response = client.post("/api/v1/age", headers=headers, json={"birth_year": year})
    assert response.status_code == 400, response.get_json()
    with App.app.app_context():
        assert App.db.session.get(App.User, user_id).birth_year is None


def test_under_13_requires_a_distinct_parent_email(client):
    child_email = f"child-{uuid.uuid4().hex}@example.test"
    user_id, headers = _account(client, email=child_email)
    under_13_year = App.utcnow().year - 12

    missing = client.post("/api/v1/age", headers=headers,
                          json={"birth_year": under_13_year})
    self_parent = client.post("/api/v1/age", headers=headers,
                              json={"birth_year": under_13_year,
                                    "parent_email": child_email.upper()})
    assert missing.status_code == self_parent.status_code == 400
    with App.app.app_context():
        user = App.db.session.get(App.User, user_id)
        assert user.birth_year is None
        assert user.parent_email is None
        assert user.parent_consent_token is None
        assert user.parent_consent_granted is False


def test_age_status_is_account_scoped_and_never_exposes_parent_address_or_token(client):
    first_id, first_headers = _account(client)
    second_id, second_headers = _account(client)
    year = App.utcnow().year - 20

    saved = client.post("/api/v1/age", headers=first_headers,
                        json={"birth_year": year, "user_id": second_id,
                              "parent_consent_granted": True})
    assert saved.status_code == 201
    first_state = client.get("/api/v1/me", headers=first_headers).get_json()
    second_state = client.get("/api/v1/me", headers=second_headers).get_json()
    assert first_state["age_consent_status"] == "ready"
    assert first_state["email_verified"] is False
    assert second_state["age_consent_status"] == "age_required"
    assert "birth_year" not in first_state
    assert "parent_email" not in first_state
    assert "parent_consent_token" not in first_state
    with App.app.app_context():
        assert App.db.session.get(App.User, first_id).birth_year == year
        assert App.db.session.get(App.User, second_id).birth_year is None


def test_unknown_age_blocks_native_api_and_web_api_until_age_is_recorded(client):
    _user_id, headers = _account(client)
    for path in ("/api/v1/tasks", "/api/today"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403
        assert response.get_json()["reason"] == "age_required"

    year = App.utcnow().year - 20
    assert client.post("/api/v1/age", headers=headers,
                       json={"birth_year": year}).status_code == 201
    assert client.get("/api/v1/tasks", headers=headers).status_code == 200
    assert client.get("/api/today", headers=headers).status_code != 403


def test_email_verification_resend_requires_age_gate_and_first_party_user(client, monkeypatch):
    user_id, headers = _account(client)
    blocked = client.post("/api/v1/email-verification/resend", headers=headers, json={})
    assert blocked.status_code == 403
    assert blocked.get_json()["reason"] == "age_required"

    sent = []
    monkeypatch.setattr("intelliplan.email_verification.send_verification",
                        lambda user: sent.append(user.id) or (True, "sent"))
    with App.app.app_context():
        user = App.db.session.get(App.User, user_id)
        user.birth_year = App.utcnow().year - 20
        user.parent_consent_granted = True
        App.db.session.commit()

    response = client.post("/api/v1/email-verification/resend", headers=headers, json={})
    assert response.status_code == 200
    assert response.get_json()["email_sent"] is True
    assert sent == [user_id]

    api_key = client.post("/api/v1/email-verification/resend",
                          headers={"X-API-Key": "ip_live_invalid"}, json={})
    assert api_key.status_code == 401


def test_under_13_remains_blocked_until_existing_parent_consent_route_approves(client, monkeypatch):
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *args, **kwargs: sent.append(args) or True)
    monkeypatch.setattr(App, "send_welcome_email_on_signup", lambda _user_id: None)
    _user_id, headers = _account(client)
    parent_email = f"guardian-{uuid.uuid4().hex}@example.test"
    birth_year = App.utcnow().year - 12  # Conservative birth-year rule: definitely under 13.

    response = client.post("/api/v1/age", headers=headers,
                           json={"birth_year": birth_year, "parent_email": parent_email,
                                 "parent_consent_granted": True})
    assert response.status_code == 201
    assert response.get_json()["age_consent_status"] == "parent_consent_pending"
    assert response.get_json()["consent_email_sent"] is True
    assert len(sent) == 1 and sent[0][0] == parent_email
    token = sent[0][2].split("token=", 1)[1].split()[0]

    state = client.get("/api/v1/me", headers=headers)
    assert state.status_code == 200
    assert state.get_json()["age_consent_status"] == "parent_consent_pending"
    assert parent_email not in state.get_data(as_text=True)
    assert token not in state.get_data(as_text=True)
    for path in ("/api/v1/tasks", "/api/today"):
        blocked = client.get(path, headers=headers)
        assert blocked.status_code == 403
        assert blocked.get_json()["reason"] == "parent_consent_required"

    resent = client.post("/api/v1/age/parent-consent/resend", headers=headers, json={})
    assert resent.status_code == 200
    assert len(sent) == 2

    # The emailed one-time token enters the same review and explicit POST
    # consent route as a web signup. Client-supplied consent was ignored.
    review = client.get(f"/parent/consent?token={token}")
    assert review.status_code == 200
    approved = client.post("/parent/consent", data={"token": token, "acknowledged": "yes"})
    assert approved.status_code == 200

    state = client.get("/api/v1/me", headers=headers)
    assert state.status_code == 200
    assert state.get_json()["age_consent_status"] == "ready"
    assert client.get("/api/v1/tasks", headers=headers).status_code == 200
    assert client.get("/api/today", headers=headers).status_code != 403


def test_api_key_cannot_submit_an_age_instead_of_first_party_app(client):
    _user_id, headers = _account(client)
    # The test credentials are bearer-only; a random key must not be accepted
    # as an account age attestation.
    response = client.post("/api/v1/age", headers={"X-API-Key": "ip_live_invalid"},
                          json={"birth_year": 2000})
    assert response.status_code == 401
