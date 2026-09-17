"""Growth wiring inside the app: metering, referrals, checkout, metrics.

Runs against the shared in-memory database, so every test cleans the rows
it can touch before it starts.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest

import App
import ai_provider
import growth_glue
from App import AIUsage, SavedSchedule, User, UserStreak, db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("BILLING_ENABLED", raising=False)
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            _clean()
        yield c
        with App.app.app_context():
            _clean()


def _clean():
    AIUsage.query.delete()
    SavedSchedule.query.filter(SavedSchedule.user_id.isnot(None)).delete(synchronize_session=False)
    UserStreak.query.delete()
    User.query.filter(User.email.like("growth+%")).delete(synchronize_session=False)
    db.session.commit()


def make_user(**kw):
    defaults = {
        "email": f"growth+{uuid.uuid4().hex[:10]}@example.test",
        "password_hash": "x",
        "name": "Riley Test",
        "birth_year": 2000,
        "created_at": datetime.utcnow() - timedelta(days=3),
    }
    defaults.update(kw)
    user = User(**defaults)
    db.session.add(user)
    db.session.commit()
    return user.id


def sign_in(client, uid):
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True


# ── Metering ─────────────────────────────────────────────────────────


@pytest.fixture
def ai_stub(monkeypatch):
    monkeypatch.setattr(ai_provider, "model_chain", lambda tier="standard", plan="free": [("gemini", "m")])
    monkeypatch.setattr(ai_provider, "_gemini_chat", lambda *a, **k: "answer")


def _probe_route():
    """A throwaway endpoint that makes two AI calls in one request."""
    if "growth_probe" not in App.app.view_functions:
        def growth_probe():
            try:
                ai_provider.chat([{"role": "user", "content": "a"}])
                ai_provider.chat([{"role": "user", "content": "b"}])
            except ai_provider.AIAllowanceExceeded:
                return {"status": "wall"}, 402
            return {"status": "ok"}

        App.app.view_functions["growth_probe"] = growth_probe
        App.app.url_map.add(App.app.url_rule_class("/__growth_probe", endpoint="growth_probe", methods=["POST"]))
    return "/__growth_probe"


def test_free_account_is_charged_once_per_request_and_walled(client, ai_stub, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "1")
    monkeypatch.setenv("AI_FREE_MONTHLY_GENERATIONS", "2")
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    url = _probe_route()

    assert client.post(url).status_code == 200
    assert client.post(url).status_code == 200
    walled = client.post(url)
    assert walled.status_code == 402
    assert walled.headers.get("X-IntelliPlan-Paywall") == "ai_allowance"
    with App.app.app_context():
        assert AIUsage.query.filter_by(user_id=uid).one().count == 2

    plan = client.get("/api/plan").get_json()
    assert plan["metered"] is True and plan["remaining"] == 0 and plan["exhausted"] is True


def test_nothing_is_metered_while_billing_is_off(client, ai_stub, monkeypatch):
    monkeypatch.setenv("AI_FREE_MONTHLY_GENERATIONS", "0")
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    assert client.post(_probe_route()).status_code == 200
    with App.app.app_context():
        assert AIUsage.query.filter_by(user_id=uid).count() == 0


def test_paid_account_is_never_metered(client, ai_stub, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "1")
    monkeypatch.setenv("AI_FREE_MONTHLY_GENERATIONS", "0")
    with App.app.app_context():
        uid = make_user(paid_until=datetime.utcnow() + timedelta(days=5))
    sign_in(client, uid)
    assert client.post(_probe_route()).status_code == 200


# ── Referrals ────────────────────────────────────────────────────────


def test_referral_pays_invitee_at_signup_and_inviter_on_activation(client):
    with App.app.app_context():
        inviter_id = make_user()
        inviter = User.query.get(inviter_id)
        code = App._ensure_referral_code(inviter)

    # A shared link carrying ?ref= attributes the click...
    client.get(f"/pricing?ref={code}")
    with client.session_transaction() as s:
        assert s.get("pending_referral") == inviter_id

    with App.app.test_request_context():
        from flask import session
        session["pending_referral"] = inviter_id
        new_id = make_user(created_at=datetime.utcnow())
        new_user = User.query.get(new_id)
        App._grant_referral_bonus(new_user)
        new_user = User.query.get(new_id)
        assert new_user.referred_by_id == inviter_id
        assert new_user.paid_until > datetime.utcnow() + timedelta(days=29)

        # Not activated yet: the inviter gets nothing.
        inviter = User.query.get(inviter_id)
        assert growth_glue.settle_referral_rewards(inviter) == 0
        assert inviter.paid_until is None

        db.session.add(SavedSchedule(user_id=new_id, schedule_data="{}"))
        db.session.commit()
        assert growth_glue.settle_referral_rewards(inviter) == 1
        assert inviter.paid_until > datetime.utcnow() + timedelta(days=29)
        # Settling again pays nothing more.
        assert growth_glue.settle_referral_rewards(inviter) == 0


def test_referral_rewards_are_capped(client, monkeypatch):
    monkeypatch.setattr(growth_glue.plans, "REFERRAL_MAX_REWARDS", 2)
    with App.app.app_context():
        inviter_id = make_user()
        for _ in range(3):
            rid = make_user(referred_by_id=inviter_id)
            db.session.add(SavedSchedule(user_id=rid, schedule_data="{}"))
        db.session.commit()
        inviter = User.query.get(inviter_id)
        assert growth_glue.settle_referral_rewards(inviter) == 2
        assert User.query.filter(User.referred_by_id == inviter_id,
                                 User.referral_rewarded_at.isnot(None)).count() == 3


def test_group_invite_link_carries_the_sharers_code(client):
    with App.app.app_context():
        uid = make_user()
        group = App.StudyGroup(name="Calc", owner_id=uid)
        db.session.add(group)
        db.session.commit()
        db.session.add(App.StudyGroupMember(group_id=group.id, user_id=uid, role="owner"))
        db.session.commit()
        gid = group.id
    sign_in(client, uid)
    body = client.get(f"/api/groups/{gid}").get_json() or {}
    with App.app.app_context():
        code = User.query.get(uid).referral_code
        App.StudyGroupMember.query.filter_by(group_id=gid).delete()
        App.StudyGroup.query.filter_by(id=gid).delete()
        db.session.commit()
    invite = body.get("invite_url") or (body.get("group") or {}).get("invite_url")
    assert invite and invite.endswith(f"?ref={code}")


# ── Checkout ─────────────────────────────────────────────────────────


def test_pay_link_round_trips_and_rejects_tampering(client):
    with App.app.app_context():
        token = growth_glue.make_pay_token(42)
        assert growth_glue.read_pay_token(token) == 42
        assert growth_glue.read_pay_token(token[:-2] + "xx") is None


def test_minors_are_sent_to_the_parent_link(client, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "1")
    with App.app.app_context():
        uid = make_user(birth_year=datetime.utcnow().year - 15)
    sign_in(client, uid)
    res = client.post("/api/billing/checkout")
    assert res.status_code == 403 and res.get_json()["message"] == "use_parent_link"


def test_webhook_rejects_unsigned_events(client, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    res = client.post("/api/billing/webhook", data=b"{}", headers={"Stripe-Signature": "t=1,v1=bad"})
    assert res.status_code == 400


def test_billing_events_set_the_window_idempotently(client):
    with App.app.app_context():
        uid = make_user()
        end = int((datetime.utcnow() + timedelta(days=30)).timestamp())
        event = {
            "type": "invoice.paid",
            "data": {"object": {
                "customer": "cus_123",
                "subscription_details": {"metadata": {"user_id": str(uid)}},
                "lines": {"data": [{"period": {"end": end}}]},
            }},
        }
        assert growth_glue.apply_billing_event(event)
        first = User.query.get(uid).paid_until
        assert growth_glue.apply_billing_event(event)
        user = User.query.get(uid)
        assert user.paid_until == first
        assert user.stripe_customer_id == "cus_123"


def test_upgrade_page_renders_in_both_modes(client, monkeypatch):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    off = client.get("/upgrade")
    assert off.status_code == 200 and b"Everything is free right now" in off.data
    monkeypatch.setenv("BILLING_ENABLED", "1")
    on = client.get("/upgrade")
    assert on.status_code == 200 and b"AI generations left this month" in on.data


# ── Metrics and streak emails ────────────────────────────────────────


def test_admin_growth_is_hidden_from_students(client):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    assert client.get("/admin/growth").status_code == 404


def test_admin_growth_report(client, monkeypatch):
    with App.app.app_context():
        admin_id = make_user(email="growth+admin@example.test", created_at=datetime.utcnow() - timedelta(days=20))
        make_user(created_at=datetime.utcnow() - timedelta(days=20))
        db.session.add(SavedSchedule(user_id=admin_id, schedule_data="{}"))
        db.session.add(App.LinkedAccount(user_id=admin_id, login_type="ics", credentials="{}"))
        db.session.add(UserStreak(
            user_id=admin_id,
            qualified_dates_json=f'["{(date.today() - timedelta(days=12)).isoformat()}"]',
        ))
        db.session.commit()
    monkeypatch.setattr(App, "ADMIN_EMAILS", {"growth+admin@example.test"})
    sign_in(client, admin_id)
    res = client.get("/admin/growth?format=json")
    assert res.status_code == 200
    report = res.get_json()
    steps = {s["step"]: s["count"] for s in report["funnel"]}
    assert steps["signed_up"] >= 2 and steps["came_back"] >= 1
    assert report["biggest_leak"] is not None
    html = client.get("/admin/growth")
    assert html.status_code == 200 and b"Activation funnel" in html.data
    with App.app.app_context():
        App.LinkedAccount.query.filter_by(user_id=admin_id).delete()
        db.session.commit()


def test_streak_email_off_link_only_touches_streak_emails(client):
    from intelliplan.email.sender import make_unsubscribe_token

    with App.app.app_context():
        uid = make_user(marketing_emails_opt_in=True)
        address = User.query.get(uid).email
        good = make_unsubscribe_token(address, scope="streak")
        wrong_scope = make_unsubscribe_token(address, scope="marketing")
    assert client.get(f"/email/streak-off/{wrong_scope}").status_code == 400
    assert client.get(f"/email/streak-off/{good}").status_code == 200
    with App.app.app_context():
        user = User.query.get(uid)
        assert user.streak_emails_opt_in is False
        assert user.marketing_emails_opt_in is True


def test_streak_sweep_queues_an_email_for_an_unsubscribed_reminder_student(client, monkeypatch):
    import notifications_glue

    now = datetime(2026, 6, 14, 19, 0)  # 19:00 UTC
    with App.app.app_context():
        App.NotificationOutbox.query.delete()
        uid = make_user()
        db.session.add(UserStreak(user_id=uid, current_streak=4, timezone="UTC",
                                  last_qualifying_local_date="2026-06-13"))
        db.session.commit()
        result = notifications_glue.sweep_streaks(now=now)
        rows = App.NotificationOutbox.query.filter_by(user_id=uid).all()
        assert result["queued"] == 1
        assert [(r.kind, r.channel) for r in rows] == [("streak_at_risk", "email")]
        # Same evening, second sweep: deduped.
        assert notifications_glue.sweep_streaks(now=now + timedelta(minutes=30))["queued"] == 0
        App.NotificationOutbox.query.delete()
        db.session.commit()
