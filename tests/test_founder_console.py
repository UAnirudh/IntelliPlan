from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import App
import pytest
from intelliplan.founder_metrics import feature_adoption


@pytest.fixture
def client():
    previous_limiter = App.limiter.enabled
    App.limiter.enabled = False
    App.app.config["TESTING"] = True
    with App.app.test_client() as test_client:
        yield test_client
    with App.app.app_context():
        user_ids = [row[0] for row in App.db.session.query(App.User.id).filter(
            App.User.email.like("founder-console-%@example.test")
        ).all()]
        App.ProductEvent.query.filter(App.ProductEvent.user_id.in_(user_ids)).delete(
            synchronize_session=False
        ) if user_ids else None
        App.User.query.filter(App.User.id.in_(user_ids)).delete(synchronize_session=False) if user_ids else None
        App.db.session.commit()
    App.limiter.enabled = previous_limiter


def _user(*, verified=False, created_at=None):
    user = App.User(
        email=f"founder-console-{uuid4().hex}@example.test",
        password_hash="x",
        birth_year=2000,
        email_verified=verified,
        created_at=created_at or datetime.utcnow(),
    )
    App.db.session.add(user)
    App.db.session.commit()
    return user


def _sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_feature_adoption_suppresses_small_counts_and_groups_unique_accounts():
    rows = [("/dashboard", f"u:{i}") for i in range(1, 5)]
    rows.extend([("/scheduler", "u:1"), ("/scheduler", "u:1")])
    features = {row["feature"]: row for row in feature_adoption(rows)}

    assert features["Today and command center"]["status"] == "below_threshold"
    assert features["Today and command center"]["accounts"] is None
    assert features["Calendar and planning"]["status"] == "below_threshold"

    rows.extend(("/dashboard", f"u:{i}") for i in range(5, 7))
    features = {row["feature"]: row for row in feature_adoption(rows)}
    assert features["Today and command center"]["accounts"] == 6


def test_unverified_matching_email_cannot_claim_owner_privileges(client, monkeypatch):
    with App.app.app_context():
        user = _user(verified=False)
        user_id = user.id
        monkeypatch.setattr(App, "FOUNDER_EMAIL", user.email.lower())
        assert not App.is_owner(user)
    _sign_in(client, user_id)

    assert client.post("/api/founder/mode", data={"mode": "founder"}).status_code == 404
    assert client.get("/founder").status_code == 404
    assert client.get("/api/founder/growth").status_code == 404


def test_owner_privileges_fail_closed_when_founder_email_is_not_configured(monkeypatch):
    with App.app.app_context():
        user = _user(verified=True)
        monkeypatch.setattr(App, "FOUNDER_EMAIL", "")
        assert not App.is_owner(user)
        assert not App.is_admin(user)


def test_founder_mode_unlocks_aggregate_growth_console_only_for_verified_owner(client, monkeypatch):
    now = datetime.utcnow()
    monkeypatch.delenv("BILLING_ENABLED", raising=False)
    with App.app.app_context():
        owner = _user(verified=True)
        active = _user(verified=True, created_at=now - timedelta(days=10))
        owner_id, active_id = owner.id, active.id
        monkeypatch.setattr(App, "FOUNDER_EMAIL", owner.email.lower())
        App.db.session.add_all([
            App.ProductEvent(actor=f"u:{active_id}", user_id=active_id,
                             kind="view", rule="/dashboard", created_at=now - timedelta(days=2)),
            App.ProductEvent(actor=f"u:{active_id}", user_id=active_id,
                             kind="view", rule="/dashboard", created_at=now - timedelta(days=9)),
        ])
        App.db.session.commit()
    _sign_in(client, owner_id)

    assert client.get("/founder").status_code == 404
    assert client.get("/api/owner/schools").status_code == 404
    assert client.get("/admin/accounts").status_code == 403
    response = client.post("/api/founder/mode", data={"mode": "founder"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/founder")
    assert b"Growth and engagement" in client.get("/founder").data
    assert client.get("/api/owner/schools").status_code == 200
    assert client.get("/admin/accounts").status_code == 200

    report = client.get("/api/founder/growth").get_json()
    assert set(report["periods"]) == {"day", "week", "month"}
    assert report["periods"]["week"]["active_accounts"] == 1
    assert report["periods"]["week"]["repeat_active_accounts"] is None
    assert report["periods"]["week"]["retention_status"] == "below_threshold"
    assert report["periods"]["week"]["new_accounts"] >= 1
    assert report["billing_status"] == "not_configured"
    assert report["paid_customers_now"] is None
    assert "email" not in str(report).lower()

    response = client.post("/api/founder/mode", data={"mode": "normal"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert client.get("/founder").status_code == 404


def test_verified_admin_allowlist_email_does_not_receive_founder_privileges(monkeypatch):
    with App.app.app_context():
        admin = _user(verified=True)
        monkeypatch.setattr(App, "ADMIN_EMAILS", {admin.email.lower()})
        monkeypatch.setattr(App, "FOUNDER_EMAIL", "founder@example.test")
        assert not App.is_owner(admin)
        assert not App.is_admin(admin)
