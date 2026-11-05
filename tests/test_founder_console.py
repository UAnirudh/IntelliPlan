from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import App
import pytest
from intelliplan.founder_metrics import feature_adoption, feature_optimization_actions


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


def _event(user_id, created_at):
    App.db.session.add(App.ProductEvent(
        actor=f"u:{user_id}", user_id=user_id, kind="view",
        rule="/dashboard", created_at=created_at,
    ))


def _freeze_app_clock(monkeypatch, now):
    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return now

    monkeypatch.setattr(App, "datetime", FrozenDateTime)


def test_feature_adoption_suppresses_small_counts_and_groups_unique_accounts():
    rows = [("/dashboard", f"u:{i}") for i in range(1, 5)]
    rows.extend([("/scheduler", "u:1"), ("/scheduler", "u:1")])
    features = {row["feature"]: row for row in feature_adoption(rows)}

    assert features["Today and command center"]["status"] == "suppressed"
    assert features["Today and command center"]["accounts"] is None
    assert features["Calendar and planning"]["status"] == "suppressed"

    rows.extend(("/dashboard", f"u:{i}") for i in range(5, 7))
    features = {row["feature"]: row for row in feature_adoption(rows)}
    assert features["Today and command center"]["accounts"] == 6


def test_feature_actions_wait_for_safe_denominator_and_suggest_discovery_experiments():
    features = [
        {"feature": "Calendar and planning", "accounts": 5, "status": "measured"},
        {"feature": "Tutor and Plani", "accounts": None, "status": "suppressed"},
        {"feature": "Grades", "accounts": 12, "status": "measured"},
    ]
    assert feature_optimization_actions(features, active_accounts=19) == {
        "status": "insufficient_data", "items": [],
    }

    report = feature_optimization_actions(features, active_accounts=40)
    assert report["status"] == "measured"
    assert [item["feature"] for item in report["items"]] == ["Calendar and planning"]


def test_feature_adoption_suppresses_a_small_nonuse_complement():
    rows = [("/scheduler", f"u:{index}") for index in range(1, 17)]
    feature = next(row for row in feature_adoption(rows, total_accounts=18)
                   if row["feature"] == "Calendar and planning")
    assert feature == {
        "feature": "Calendar and planning",
        "accounts": None,
        "status": "suppressed",
    }
    assert feature_optimization_actions([feature], active_accounts=40)["items"] == []


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
    # Keep this API integration assertion independent of account fixtures
    # created by other modules during the full test suite.
    now = datetime(2040, 6, 1, 12)
    _freeze_app_clock(monkeypatch, now)
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
    assert client.get("/api/founder/growth").status_code == 404
    response = client.post("/api/founder/mode", data={"mode": "founder"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/founder")
    dashboard = client.get("/founder").data
    assert b"Growth and engagement" in dashboard
    assert b"Signup cohort retention" in dashboard
    assert b"Product improvement opportunities" in dashboard
    assert b"Paid access started" in dashboard and b"Paid access ended" in dashboard
    assert b"Fewer than 5" in dashboard
    assert client.get("/api/owner/schools").status_code == 200
    assert client.get("/admin/accounts").status_code == 200

    report = client.get("/api/founder/growth").get_json()
    assert set(report["periods"]) == {"day", "week", "month"}
    assert set(report["signup_cohort_retention"]["horizons"]) == {"d1", "d7", "d30"}
    assert report["periods"]["week"]["active_accounts"] is None
    assert report["periods"]["week"]["repeat_active_accounts"] is None
    assert report["periods"]["week"]["retention_status"] == "below_threshold"
    assert report["periods"]["week"]["new_accounts"] is None
    assert report["billing_status"] == "not_configured"
    assert report["paid_customers_now"] is None
    assert report["periods"]["day"]["paid_access_started"] is None
    assert report["periods"]["day"]["paid_access_ended"] is None
    assert "email" not in str(report).lower()
    response = client.post("/api/founder/mode", data={"mode": "normal"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert client.get("/founder").status_code == 404
    assert client.get("/api/founder/growth").status_code == 404


def test_founder_growth_report_suppresses_small_signup_active_and_paid_cells(client, monkeypatch):
    now = datetime(2040, 6, 1, 12)
    monkeypatch.setenv("BILLING_ENABLED", "1")
    with App.app.app_context():
        owner = _user(verified=True, created_at=now - timedelta(days=1))
        members = [_user(verified=index < 2, created_at=now - timedelta(days=2)) for index in range(4)]
        for user in members[:2]:
            user.stripe_customer_id = f"cus_{user.id}"
            user.stripe_paid_until = now + timedelta(days=30)
            _event(user.id, now - timedelta(hours=2))
        _event(owner.id, now - timedelta(hours=1))
        App.db.session.commit()
        monkeypatch.setattr(App, "FOUNDER_EMAIL", owner.email.lower())
        report = App._founder_growth_report(now)

    assert report["periods"]["day"]["new_accounts"] is None
    assert report["periods"]["day"]["verified_new_accounts"] is None
    assert report["periods"]["day"]["active_accounts"] is None
    assert report["periods"]["day"]["new_accounts_change"] is None
    assert report["periods"]["day"]["new_accounts_change_status"] == "suppressed"
    assert report["paid_customers_now"] is None
    assert report["paid_customers_suppressed"] is True
    assert report["optimization_actions"]["status"] == "insufficient_data"


def test_growth_report_hides_small_unverified_complement_and_growth_delta(client):
    now = datetime(2040, 6, 1, 12)
    with App.app.app_context():
        for index in range(10):
            _user(verified=index < 6, created_at=now - timedelta(hours=2, minutes=index))
        for index in range(6):
            _user(verified=True, created_at=now - timedelta(days=1, hours=12, minutes=index))
        App.db.session.commit()
        report = App._founder_growth_report(now)

    day = report["periods"]["day"]
    assert day["new_accounts"] == 10
    assert day["verified_new_accounts"] is None  # only four new accounts remain unverified
    assert day["new_accounts_change"] is None  # both windows are reportable, but delta is under five
    assert day["new_accounts_change_status"] == "suppressed"


def test_founder_paid_transition_metrics_use_calendar_buckets_and_suppress_small_cells(monkeypatch):
    now = datetime(2040, 6, 1, 12)
    monkeypatch.setenv("BILLING_ENABLED", "1")
    digests = []
    with App.app.app_context():
        App.db.create_all()
        rows = []
        for kind, day, count in (
            ("paid_access_started", now.date(), 5),
            ("paid_access_ended", now.date(), 4),
            ("paid_access_ended", (now - timedelta(days=1)).date(), 6),
        ):
            for _ in range(count):
                digest = uuid4().hex * 2
                digests.append(digest)
                rows.append(App.BillingLifecycleEvent(
                    event_digest=digest,
                    event_day=day,
                    event_kind=kind,
                    plan_tier="pro",
                ))
        App.db.session.add_all(rows)
        App.db.session.commit()
        report = App._founder_growth_report(now)
        App.BillingLifecycleEvent.query.filter(
            App.BillingLifecycleEvent.event_digest.in_(digests)
        ).delete(synchronize_session=False)
        App.db.session.commit()

    assert report["billing_status"] == "tracked"
    assert report["periods"]["day"]["paid_access_started"] == 5
    assert report["periods"]["day"]["paid_access_ended"] is None
    assert report["periods"]["week"]["paid_access_ended"] == 10
    assert report["periods"]["month"]["paid_access_ended"] == 10


def test_signup_cohort_retention_uses_exact_utc_day_windows_and_consent_events(client):
    now = datetime(2026, 11, 4, 12)
    signup = datetime(2026, 9, 5, 10)
    with App.app.app_context():
        user_ids = []
        for index in range(13):
            user = _user(created_at=signup + timedelta(minutes=index))
            user_ids.append(user.id)
            # The signup-day event makes the account eligible. With no event,
            # the thirteenth account represents an account with no recorded
            # analytics consent and must not enter the denominator.
            if index < 12:
                _event(user.id, user.created_at + timedelta(hours=1))
            if index < 7:
                _event(user.id, user.created_at + timedelta(days=1, hours=2))
                _event(user.id, user.created_at + timedelta(days=7, hours=2))
                _event(user.id, user.created_at + timedelta(days=30, hours=2))
            else:
                # Adjacent days do not qualify for the exact D1/D7/D30 days.
                _event(user.id, user.created_at + timedelta(days=2, hours=2))
                _event(user.id, user.created_at + timedelta(days=8, hours=2))
                _event(user.id, user.created_at + timedelta(days=31, hours=2))
        App.db.session.commit()

        report = App._founder_signup_cohort_retention(now)

    assert report["cohort_window_end_exclusive"] == "2026-10-05"
    assert report["horizons"]["d1"] == {
        "cohort_accounts": 12, "returned_accounts": 7, "rate": 0.583,
        "status": "measured",
    }
    assert report["horizons"]["d7"] == report["horizons"]["d1"]
    assert report["horizons"]["d30"] == report["horizons"]["d1"]
    assert set(report) == {
        "cohort_window_start", "cohort_window_end_exclusive", "cohort_days",
        "minimum_accounts", "basis", "horizons",
    }
    assert set(report["horizons"]) == {"d1", "d7", "d30"}
    assert all("user_id" not in metric and "email" not in metric
               for metric in report["horizons"].values())


def test_signup_cohort_retention_suppresses_small_cohort_and_return_cells(client):
    now = datetime(2026, 11, 4, 12)
    signup = datetime(2026, 9, 5, 10)
    with App.app.app_context():
        for index in range(4):
            user = _user(created_at=signup + timedelta(minutes=index))
            _event(user.id, user.created_at + timedelta(hours=1))
            _event(user.id, user.created_at + timedelta(days=1, hours=2))
        App.db.session.commit()

        small_cohort = App._founder_signup_cohort_retention(now)
        metric = small_cohort["horizons"]["d1"]
        assert metric == {
            "cohort_accounts": None, "returned_accounts": None,
            "rate": None, "status": "below_threshold",
        }

        # Add one more eligible account, but only four D1 returns. The cohort
        # size is reportable while the small returned cell remains suppressed.
        user = _user(created_at=signup + timedelta(minutes=10))
        _event(user.id, user.created_at + timedelta(hours=1))
        App.db.session.commit()
        metric = App._founder_signup_cohort_retention(now)["horizons"]["d1"]

    assert metric == {
        "cohort_accounts": 5, "returned_accounts": None,
        "rate": None, "status": "small_cell_suppressed",
    }


def test_signup_cohort_retention_hides_a_small_nonreturn_complement(client):
    now = datetime(2026, 11, 4, 12)
    signup = datetime(2026, 9, 5, 10)
    with App.app.app_context():
        for index in range(10):
            user = _user(created_at=signup + timedelta(minutes=index))
            _event(user.id, user.created_at + timedelta(hours=1))
            if index < 9:
                _event(user.id, user.created_at + timedelta(days=1, hours=2))
        App.db.session.commit()
        metric = App._founder_signup_cohort_retention(now)["horizons"]["d1"]

    assert metric == {
        "cohort_accounts": 10, "returned_accounts": None,
        "rate": None, "status": "small_cell_suppressed",
    }


def test_signup_cohort_retention_excludes_under_14_and_pending_parent_accounts(client):
    now = datetime(2026, 11, 4, 12)
    signup = datetime(2026, 9, 5, 10)
    with App.app.app_context():
        # Four eligible users are not enough to report. These two accounts
        # must not lift the cohort above the threshold despite stale events.
        for index in range(4):
            user = _user(created_at=signup + timedelta(minutes=index))
            _event(user.id, user.created_at + timedelta(hours=1))

        minor = _user(created_at=signup + timedelta(minutes=5))
        minor.birth_year = 2014  # Conservative age is below 13 in 2026.
        _event(minor.id, minor.created_at + timedelta(hours=1))

        pending = _user(created_at=signup + timedelta(minutes=6))
        pending.parent_email = "parent@example.test"
        pending.parent_consent_granted = False
        _event(pending.id, pending.created_at + timedelta(hours=1))
        App.db.session.commit()

        metric = App._founder_signup_cohort_retention(now)["horizons"]["d1"]

    assert metric == {
        "cohort_accounts": None, "returned_accounts": None,
        "rate": None, "status": "below_threshold",
    }


def test_signup_cohort_retention_handles_empty_and_no_mature_history(client, monkeypatch):
    now = datetime(2026, 11, 4, 12)
    with App.app.app_context():
        empty = App._founder_signup_cohort_retention(now)
        assert all(
            metric["status"] == "below_threshold"
            and metric["cohort_accounts"] is None
            for metric in empty["horizons"].values()
        )

        monkeypatch.setenv("INSIGHT_RETENTION_DAYS", "30")
        no_mature = App._founder_signup_cohort_retention(now)

    assert no_mature["cohort_days"] == 0
    assert all(
        metric["status"] == "no_mature_cohorts"
        and metric["returned_accounts"] is None
        for metric in no_mature["horizons"].values()
    )


def test_verified_admin_allowlist_email_does_not_receive_founder_privileges(monkeypatch):
    with App.app.app_context():
        admin = _user(verified=True)
        monkeypatch.setattr(App, "ADMIN_EMAILS", {admin.email.lower()})
        monkeypatch.setattr(App, "FOUNDER_EMAIL", "founder@example.test")
        assert not App.is_owner(admin)
        assert not App.is_admin(admin)
