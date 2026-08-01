"""The API-key application process, and what the key can then reach.

The point of these tests is the boundary: a read-only application clears on
its own, a write application waits for a person, a revoked key stops working
immediately, and a key that was never granted a scope cannot use it — no
matter that it belongs to a real, logged-in user.
"""

from datetime import datetime, timedelta

import pytest

import App
import api_keys
from App import ApiKey, User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            ApiKey.query.delete()
            User.query.filter(User.email.like("apitest+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            ApiKey.query.delete()
            User.query.filter(User.email.like("apitest+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def make_user(email, age=timedelta(days=30)):
    with App.app.app_context():
        u = User(
            email=email,
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
            name="API Tester",
            created_at=datetime.utcnow() - age,
        )
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


APPLICATION = {
    "app_name": "Study Widget",
    "app_url": "https://example.com",
    "use_case": "A macOS menu bar widget that shows the next three assignments "
                "so a student does not have to open the dashboard.",
    "expected_volume": "low",
    "accepted_terms": True,
}


def apply_for(client, scopes):
    return client.post("/developers/apply", json={**APPLICATION, "scopes": scopes})


# ── the application form is a real gate ───────────────────────────


def test_applying_requires_being_signed_in(client):
    r = apply_for(client, ["read:profile"])
    assert r.status_code in (302, 401)


def test_a_one_line_use_case_is_rejected(client):
    login(client, make_user("apitest+short@example.com"))
    r = client.post("/developers/apply",
                    json={**APPLICATION, "use_case": "an app", "scopes": ["read:profile"]})
    assert r.status_code == 400
    assert r.get_json()["field"] == "use_case"


def test_terms_must_be_accepted(client):
    login(client, make_user("apitest+terms@example.com"))
    r = client.post("/developers/apply",
                    json={**APPLICATION, "accepted_terms": False, "scopes": ["read:profile"]})
    assert r.status_code == 400
    assert r.get_json()["field"] == "accepted_terms"


def test_unknown_scopes_are_dropped_not_granted(client):
    login(client, make_user("apitest+bogus@example.com"))
    r = apply_for(client, ["read:profile", "admin:everything"])
    assert r.status_code == 201
    assert r.get_json()["application"]["scopes"] == ["read:profile"]


def test_a_brand_new_account_cannot_auto_approve(client):
    """The account-age floor: a signup from a minute ago waits like anyone else."""
    login(client, make_user("apitest+fresh@example.com", age=timedelta(minutes=2)))
    r = apply_for(client, ["read:profile"])
    assert r.status_code == 202
    assert r.get_json()["status"] == "pending"


# ── read auto-approves, write waits for a person ──────────────────


def test_read_only_application_returns_the_key_immediately(client):
    login(client, make_user("apitest+read@example.com"))
    r = apply_for(client, ["read:profile", "read:assignments"])
    assert r.status_code == 201
    body = r.get_json()
    assert body["status"] == "approved"
    assert body["key"].startswith("ip_live_")
    assert body["application"]["status"] == "active"


def test_write_application_is_held_for_review_with_no_key(client):
    login(client, make_user("apitest+write@example.com"))
    r = apply_for(client, ["read:profile", "write:tasks"])
    assert r.status_code == 202
    body = r.get_json()
    assert body["status"] == "pending"
    assert "key" not in body
    assert body["application"]["key_prefix"] == ""


def test_the_secret_is_never_stored(client):
    login(client, make_user("apitest+hash@example.com"))
    secret = apply_for(client, ["read:profile"]).get_json()["key"]
    with App.app.app_context():
        row = ApiKey.query.filter_by(key_prefix=secret[:14]).first()
        assert row.key_hash != secret
        assert secret not in (row.key_hash or "")
        assert row.key_hash == api_keys.hash_key(secret)


def test_open_applications_are_capped(client):
    login(client, make_user("apitest+cap@example.com"))
    for i in range(api_keys.MAX_KEYS_PER_USER):
        assert apply_for(client, ["read:profile"]).status_code == 201
    r = apply_for(client, ["read:profile"])
    assert r.status_code == 409


# ── the key at the API boundary ───────────────────────────────────


def test_the_key_authenticates_and_names_its_app(client):
    login(client, make_user("apitest+use@example.com"))
    secret = apply_for(client, ["read:profile"]).get_json()["key"]
    r = client.get("/api/v1/me", headers={"X-API-Key": secret})
    assert r.status_code == 200
    assert r.get_json()["credential"]["app_name"] == "Study Widget"


def test_a_key_pasted_into_the_authorization_header_still_works(client):
    """The most common integration mistake. Holding a valid credential should
    not 401 over which header it arrived in."""
    login(client, make_user("apitest+hdr@example.com"))
    secret = apply_for(client, ["read:profile"]).get_json()["key"]
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 200


def test_a_scope_that_was_not_granted_is_403_not_401(client):
    login(client, make_user("apitest+scope@example.com"))
    secret = apply_for(client, ["read:profile"]).get_json()["key"]
    r = client.post("/api/v1/tasks", json={"title": "x"},
                    headers={"X-API-Key": secret})
    assert r.status_code == 403
    body = r.get_json()
    assert body["error"] == "insufficient_scope"
    assert "write:tasks" in body["message"]


def test_a_made_up_key_is_rejected(client):
    r = client.get("/api/v1/me", headers={"X-API-Key": "ip_live_not_a_real_key"})
    assert r.status_code == 401


def test_revoking_stops_the_key_that_same_moment(client):
    login(client, make_user("apitest+revoke@example.com"))
    body = apply_for(client, ["read:profile"]).get_json()
    secret, key_id = body["key"], body["application"]["id"]
    assert client.get("/api/v1/me", headers={"X-API-Key": secret}).status_code == 200
    assert client.post(f"/developers/keys/{key_id}/revoke").status_code == 200
    assert client.get("/api/v1/me", headers={"X-API-Key": secret}).status_code == 401


def test_rolling_replaces_the_secret(client):
    login(client, make_user("apitest+roll@example.com"))
    body = apply_for(client, ["read:profile"]).get_json()
    old, key_id = body["key"], body["application"]["id"]
    new = client.post(f"/developers/keys/{key_id}/roll").get_json()["key"]
    assert new != old
    assert client.get("/api/v1/me", headers={"X-API-Key": old}).status_code == 401
    assert client.get("/api/v1/me", headers={"X-API-Key": new}).status_code == 200


def test_one_developer_cannot_revoke_another_developers_key(client):
    login(client, make_user("apitest+owner@example.com"))
    key_id = apply_for(client, ["read:profile"]).get_json()["application"]["id"]
    login(client, make_user("apitest+stranger@example.com"))
    assert client.post(f"/developers/keys/{key_id}/revoke").status_code == 404


def test_usage_is_recorded_against_the_key(client):
    login(client, make_user("apitest+count@example.com"))
    body = apply_for(client, ["read:profile"]).get_json()
    secret, key_id = body["key"], body["application"]["id"]
    client.get("/api/v1/me", headers={"X-API-Key": secret})
    client.get("/api/v1/me", headers={"X-API-Key": secret})
    with App.app.app_context():
        row = db.session.get(ApiKey, key_id)
        assert row.request_count >= 2
        assert row.last_used_at is not None


# ── the review queue ──────────────────────────────────────────────


def test_the_review_queue_is_admins_only(client):
    login(client, make_user("apitest+nosy@example.com"))
    assert client.get("/developers/admin/applications").status_code == 403


def test_an_approved_write_application_gets_a_working_key(client):
    login(client, make_user("apitest+pending@example.com"))
    key_id = apply_for(client, ["read:profile", "write:tasks"]).get_json()["application"]["id"]
    with App.app.app_context():
        row = db.session.get(ApiKey, key_id)
        secret = api_keys.approve(row, note="Looks legitimate.", by_admin=True)
    r = client.get("/api/v1/me", headers={"X-API-Key": secret})
    assert r.status_code == 200
    assert "write:tasks" in r.get_json()["credential"]["scopes"]


# ── responses a developer has to debug against ────────────────────


def test_every_api_response_carries_a_request_id(client):
    r = client.get("/api/v1/me")
    assert r.headers.get("X-Request-Id")
    assert r.get_json()["request_id"] == r.headers["X-Request-Id"]


def test_an_unknown_endpoint_returns_json_not_the_html_404(client):
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_the_wrong_verb_says_what_is_allowed(client):
    r = client.delete("/api/v1/health")
    assert r.status_code == 405
    assert r.get_json()["error"] == "method_not_allowed"


def test_health_needs_no_credential(client):
    assert client.get("/api/v1/health").status_code == 200


def test_docs_list_every_scope(client):
    scopes = client.get("/api/v1/docs").get_json()["scopes"]
    assert set(scopes) == set(api_keys.SCOPES)
