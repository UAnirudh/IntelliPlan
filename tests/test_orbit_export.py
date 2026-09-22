"""The read-only export an operator hub pulls from.

The point of these tests is the boundary. This app's database holds grades,
birth years, parent addresses and phone numbers about minors, and the export
sits one HTTP request away from all of it. So: it is closed unless a key is
configured, it rejects a near-miss key, and the fields it returns are an
allow-list that a future column cannot silently join.
"""

from datetime import datetime, timedelta

import pytest

import App
from App import ProductEvent, User, UserStreak, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            _clear()
        yield c
        with App.app.app_context():
            _clear()
        App.app.config.pop("ORBIT_PULL_KEY", None)
    App.limiter.enabled = True


def _clear():
    emails = User.query.filter(User.email.like("orbittest+%")).all()
    ids = [user.id for user in emails]
    if ids:
        ProductEvent.query.filter(ProductEvent.user_id.in_(ids)).delete(
            synchronize_session=False)
        UserStreak.query.filter(UserStreak.user_id.in_(ids)).delete(
            synchronize_session=False)
    User.query.filter(User.email.like("orbittest+%")).delete(synchronize_session=False)
    db.session.commit()


def make_user(email, **extra):
    with App.app.app_context():
        user = User(
            email=email,
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
            name="Orbit Tester",
            created_at=datetime.utcnow() - timedelta(days=3),
            **extra,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


AUTH = {"Authorization": "Bearer orbit-test-key"}


def enable_key(value="orbit-test-key"):
    App.app.config["ORBIT_PULL_KEY"] = value


class TestAuthorisation:
    def test_refuses_everything_when_no_key_is_configured(self, client):
        # An export that runs open until somebody remembers to configure it is
        # an export that leaks on the first deploy.
        App.app.config.pop("ORBIT_PULL_KEY", None)
        assert client.get("/api/orbit/people", headers=AUTH).status_code == 401
        assert client.get("/api/orbit/events", headers=AUTH).status_code == 401

    def test_refuses_a_missing_or_wrong_key(self, client):
        enable_key()
        assert client.get("/api/orbit/people").status_code == 401
        assert client.get(
            "/api/orbit/people", headers={"Authorization": "Bearer wrong"}
        ).status_code == 401

    def test_refuses_a_key_that_is_a_prefix_of_the_real_one(self, client):
        enable_key()
        assert client.get(
            "/api/orbit/people", headers={"Authorization": "Bearer orbit-test"}
        ).status_code == 401

    def test_accepts_the_configured_key(self, client):
        enable_key()
        assert client.get("/api/orbit/people", headers=AUTH).status_code == 200

    def test_tolerates_a_lowercase_scheme(self, client):
        enable_key()
        assert client.get(
            "/api/orbit/people", headers={"Authorization": "bearer orbit-test-key"}
        ).status_code == 200


class TestPeople:
    def test_returns_the_allow_listed_fields_and_nothing_else(self, client):
        enable_key()
        make_user(
            "orbittest+fields@school.edu",
            phone="+15551234567",
            birth_year=2011,
            parent_email="parent@example.com",
            stripe_customer_id="cus_123",
        )

        payload = client.get("/api/orbit/people", headers=AUTH).get_json()
        row = next(r for r in payload["data"] if r["email"] == "orbittest+fields@school.edu")

        assert set(row) == {
            "id", "email", "name", "created_at", "role", "plan",
            "canvas_connected", "streak_days", "longest_streak", "school_domain",
        }

        # The ones that matter. This app holds data about minors, and none of
        # it is needed to chart adoption.
        serialised = str(row)
        assert "+15551234567" not in serialised
        assert "2011" not in serialised
        assert "parent@example.com" not in serialised
        assert "cus_123" not in serialised

    def test_reports_the_school_domain_without_naming_a_student(self, client):
        enable_key()
        make_user("orbittest+domain@lincolnhs.org")

        payload = client.get("/api/orbit/people", headers=AUTH).get_json()
        row = next(r for r in payload["data"] if r["email"].startswith("orbittest+domain"))
        assert row["school_domain"] == "lincolnhs.org"

    def test_reports_whether_they_pay_and_not_how_much(self, client):
        enable_key()
        make_user("orbittest+paid@school.edu", paid_until=datetime.utcnow() + timedelta(days=30))
        make_user("orbittest+free@school.edu", paid_until=datetime.utcnow() - timedelta(days=1))

        rows = {r["email"]: r for r in client.get("/api/orbit/people", headers=AUTH).get_json()["data"]}
        assert rows["orbittest+paid@school.edu"]["plan"] == "paid"
        # A lapsed subscription is the free plan, not a paid one.
        assert rows["orbittest+free@school.edu"]["plan"] == "free"

    def test_caps_the_page_size_so_one_request_cannot_take_the_table(self, client):
        enable_key()
        payload = client.get("/api/orbit/people?limit=100000", headers=AUTH).get_json()
        assert payload["limit"] == 500

    def test_treats_an_unparseable_since_as_absent(self, client):
        # A full page instead of an incremental one is slower and still
        # correct. Failing would stop the sync over a formatting detail.
        enable_key()
        make_user("orbittest+since@school.edu")
        payload = client.get("/api/orbit/people?since=banana", headers=AUTH).get_json()
        assert any(r["email"].startswith("orbittest+since") for r in payload["data"])

    def test_honours_a_real_since(self, client):
        enable_key()
        make_user("orbittest+old@school.edu")
        future = (datetime.utcnow() + timedelta(days=1)).isoformat()
        payload = client.get(f"/api/orbit/people?since={future}", headers=AUTH).get_json()
        assert not any(r["email"].startswith("orbittest+old") for r in payload["data"])


class TestEvents:
    def test_exports_the_kind_but_never_the_props(self, client):
        enable_key()
        user_id = make_user("orbittest+events@school.edu")

        with App.app.app_context():
            db.session.add(
                ProductEvent(
                    actor=f"u:{user_id}",
                    user_id=user_id,
                    kind="action",
                    rule="/scheduler/generate",
                    name="plan.generated",
                    # The field that will one day hold something nobody meant to
                    # send.
                    props='{"essay":"my private draft"}',
                    created_at=datetime.utcnow(),
                )
            )
            db.session.commit()

        payload = client.get("/api/orbit/events", headers=AUTH).get_json()
        row = next(r for r in payload["data"] if r["user_id"] == str(user_id))

        assert row["type"] == "plan.generated"
        assert set(row) == {"id", "user_id", "type", "occurred_at", "channel"}
        assert "my private draft" not in str(payload)

    def test_falls_back_to_the_route_area_for_an_unnamed_event(self, client):
        enable_key()
        user_id = make_user("orbittest+area@school.edu")

        with App.app.app_context():
            db.session.add(
                ProductEvent(
                    actor=f"u:{user_id}",
                    user_id=user_id,
                    kind="view",
                    rule="/flashcards/<int:deck_id>",
                    name=None,
                    created_at=datetime.utcnow(),
                )
            )
            db.session.commit()

        payload = client.get("/api/orbit/events", headers=AUTH).get_json()
        row = next(r for r in payload["data"] if r["user_id"] == str(user_id))
        # The area, not the route pattern: countable without exporting internal
        # structure.
        assert row["type"] == "view.flashcards"
        assert "<int:deck_id>" not in str(payload)

    def test_skips_events_with_no_signed_in_user(self, client):
        enable_key()
        with App.app.app_context():
            db.session.add(
                ProductEvent(
                    actor="v:anonymous-visitor",
                    user_id=None,
                    kind="view",
                    rule="/",
                    created_at=datetime.utcnow(),
                )
            )
            db.session.commit()

        payload = client.get("/api/orbit/events", headers=AUTH).get_json()
        assert all(r["user_id"] is not None for r in payload["data"])
