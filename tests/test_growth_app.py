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
    App.ProGift.query.delete()
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
        s["ip_founder_mode"] = True


def test_study_review_csv_groups_local_weeks_and_requires_paid_access(client, monkeypatch):
    from intelliplan import scheduler_clock

    now = datetime(2026, 11, 25, 20)
    monkeypatch.setattr(App, "utcnow", lambda: now)
    monkeypatch.setattr(growth_glue, "utcnow", lambda: now)
    monkeypatch.setattr(scheduler_clock, "timezone_for", lambda uid: ("America/Los_Angeles", 0))
    monkeypatch.setattr(App, "collect_lms_assignments_for_user", lambda uid, **_: [{
        "title": "Upcoming practice", "course": "Math", "due_date": "2026-12-01", "estimated_time": 40,
    }])
    url = "/my-stats/weekly-review"
    assert client.get(url + "?format=csv").location.startswith("/login")
    with App.app.app_context():
        paid = make_user(paid_until=now + timedelta(days=31))
        free = make_user()
        db.session.add_all([
            App.TaskFeedback(user_id=paid, title="Private title", completed_at=datetime(2026, 11, 23, 7, 59)),
            App.TaskFeedback(user_id=paid, title="Lab", estimated_time=25, actual_time=40,
                             completed_at=datetime(2026, 11, 20, 18)),
            App.TaskFeedback(user_id=paid, title="Essay", estimated_time=30, actual_time=45,
                             completed_at=datetime(2026, 11, 13, 18)),
            App.TaskFeedback(user_id=paid, title="Review", estimated_time=35, actual_time=50,
                             completed_at=datetime(2026, 11, 6, 18)),
            App.TaskFeedback(user_id=paid, title="Current week", estimated_time=1000, actual_time=1,
                             completed_at=now),
            App.TaskFeedback(user_id=free, title="Other student", estimated_time=1000, actual_time=1,
                             completed_at=datetime(2026, 11, 20, 18)),
            App.StudySession(user_id=paid, completed=True, duration_seconds=150, created_at=datetime(2026, 11, 23, 8)),
            App.StudySession(user_id=paid, completed=False, duration_seconds=900, created_at=now),
            App.StudySession(user_id=free, completed=True, duration_seconds=1800, created_at=now),
            App.ActiveSession(user_id=paid, state="completed", active_seconds=185,
                              title="Private focus title", ended_at=datetime(2026, 11, 20, 12)),
            App.ActiveSession(user_id=paid, state="completed", active_seconds=300,
                              ended_at=datetime(2026, 11, 13, 12)),
            App.ActiveSession(user_id=paid, state="abandoned", active_seconds=1000, ended_at=now),
            App.ActiveSession(user_id=free, state="completed", active_seconds=2000, ended_at=now),
        ])
        db.session.commit()
    try:
        sign_in(client, free)
        assert client.get(url + "?format=csv").location.endswith("/upgrade")
        assert client.get("/api/my-stats").status_code == 200
        assert client.get("/api/scheduler/assignments").status_code == 200
        free_review = client.get(url).get_data(as_text=True)
        assert "<strong>40 minutes</strong> for 1 assignment" in free_review
        assert '<time datetime="2026-12-01">2026-12-01</time> · 1 assignment due · 40 minutes' in free_review
        assert "Study history" not in free_review
        assert "Download spreadsheet" not in free_review
        preview = client.get("/pricing/study-review").get_data(as_text=True)
        assert "This preview uses sample data" in preview
        assert "136 study minutes" in preview
        assert 'value="136" max="136"' in preview
        assert 'class="review-current">In progress' in preview
        assert "40 minutes" in preview and "4 check-ins with both times" in preview
        assert "Download spreadsheet" not in preview
        sign_in(client, paid)
        review = client.get(url).get_data(as_text=True)
        assert "Weekly study review" in review
        assert "2 minutes less" in review
        assert "<strong>30 minutes</strong>" in review
        assert "<strong>45 minutes</strong>" in review
        assert "try budgeting <strong>45 minutes</strong>" in review
        assert "Upcoming practice" in review and "<strong>60 minutes</strong> for 1 assignment" in review
        assert "<strong>20 minutes more</strong>" in review
        assert 'Your busiest deadline day is <time datetime="2026-12-01">2026-12-01</time>' in review
        assert "Originally 40" in review
        assert "3 check-ins with both times" in review
        downloaded = client.get(url + "?format=csv")
        assert "no-store" in downloaded.headers["Cache-Control"]
        assert "attachment" in downloaded.headers["Content-Disposition"]
        lines = downloaded.get_data(as_text=True).splitlines()
        assert lines[1:4] == ["2026-11-23,1,1,2", "2026-11-16,2,1,3", "2026-11-09,1,1,5"]
        assert len(lines) == 13
        assert "Private title" not in downloaded.get_data(as_text=True)
        assert "Private focus title" not in downloaded.get_data(as_text=True)
        assert "Upcoming practice" not in downloaded.get_data(as_text=True)
    finally:
        with App.app.app_context():
            App.TaskFeedback.query.filter(App.TaskFeedback.user_id.in_([paid, free])).delete()
            App.StudySession.query.filter(App.StudySession.user_id.in_([paid, free])).delete()
            App.ActiveSession.query.filter(App.ActiveSession.user_id.in_([paid, free])).delete()
            db.session.commit()


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
        # Change the first character of the signature. The last one carries
        # unused base64 bits, so swapping it can decode to the same bytes.
        body, signature = token.rsplit(".", 1)
        swapped = ("B" if signature[0] == "A" else "A") + signature[1:]
        assert growth_glue.read_pay_token(f"{body}.{swapped}") is None


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


def test_billing_events_set_the_window_idempotently(client, monkeypatch):
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_pro_test')
    with App.app.app_context():
        uid = make_user()
        end = int((datetime.utcnow() + timedelta(days=30)).timestamp())
        event = {
            "type": "invoice.paid",
            "data": {"object": {
                "status": "paid",
                "customer": "cus_123",
                "subscription_details": {"metadata": {"user_id": str(uid)}},
                "lines": {"data": [{"type": "subscription", "price": {"id": "price_pro_test"},
                                      "period": {"end": end}}]},
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


class _FakeStripe:
    """Records what checkout and the portal were asked for."""

    def __init__(self):
        self.checkouts, self.portals, self.retrieved_prices = [], [], []
        self.prices = {
            "price_month": {
                "active": True,
                "livemode": True,
                "type": "recurring",
                "currency": "usd",
                "unit_amount": 500,
                "billing_scheme": "per_unit",
                "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"},
            },
            "price_year": {
                "active": True,
                "livemode": True,
                "type": "recurring",
                "currency": "usd",
                "unit_amount": 4800,
                "billing_scheme": "per_unit",
                "recurring": {"interval": "year", "interval_count": 1, "usage_type": "licensed"},
            },
        }
        outer = self

        class _Prices:
            @staticmethod
            def retrieve(price_id):
                outer.retrieved_prices.append(price_id)
                return outer.prices[price_id]

        class _Sessions:
            @staticmethod
            def create(**params):
                outer.checkouts.append(params)
                return type("S", (), {"url": "https://checkout.test/session"})()

        class _Portal:
            @staticmethod
            def create(**params):
                outer.portals.append(params)
                return type("P", (), {"url": "https://billing.test/portal"})()

        self.Price = _Prices
        self.checkout = type("C", (), {"Session": _Sessions})
        self.billing_portal = type("B", (), {"Session": _Portal})


@pytest.fixture
def stripe_stub(monkeypatch):
    import sys

    fake = _FakeStripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setenv("BILLING_ENABLED", "1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_month")
    monkeypatch.setenv("STRIPE_PRICE_ID_YEARLY", "price_year")
    return fake


def test_checkout_defaults_to_the_monthly_price(client, stripe_stub):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    res = client.post("/api/billing/checkout", json={})
    assert res.status_code == 200 and res.get_json()["url"].startswith("https://checkout.test")
    assert stripe_stub.checkouts[0]["line_items"] == [{"price": "price_month", "quantity": 1}]


def test_pro_gift_checkout_uses_recipient_link_and_one_time_price(client, stripe_stub, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO_GIFT", "price_gift")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic_gift")
    stripe_stub.prices["price_gift"] = {
        **stripe_stub.prices["price_month"], "type": "one_time", "recurring": None,
    }
    with App.app.app_context():
        uid = make_user()
        original = growth_glue.make_pay_token(uid)
        token = growth_glue._pay_serializer(growth_glue.GIFT_LINK_SALT).dumps({"uid": uid})
    url = f"/gift/{token}"
    assert client.get(f"/gift/{original}").status_code == 404
    assert "no-store" in client.get(url).headers["Cache-Control"]
    assert client.post(url, data={"months": "13", "adult_payer": "yes"}).status_code == 400
    assert client.post(url, data={"months": "3"}).status_code == 400
    response = client.post(url, data={"months": "3", "adult_payer": "yes", "user_id": 999})
    assert response.status_code == 303
    assert response.headers["Referrer-Policy"] == "no-referrer"
    checkout, = stripe_stub.checkouts
    assert checkout["mode"] == "payment"
    assert token not in checkout["success_url"] and token not in checkout["cancel_url"]
    assert checkout["line_items"] == [{"price": "price_gift", "quantity": 3}]
    assert checkout["metadata"] == checkout["payment_intent_data"]["metadata"] == {
        "kind": "pro_gift", "user_id": str(uid), "months": "3",
    }
    monkeypatch.delenv("BILLING_ENABLED")
    assert client.get(url).status_code == 404


def test_gift_credits_confirmed_payments_once_and_handles_early_reversals(client):
    now = datetime(2026, 11, 26)
    with App.app.app_context():
        uid = make_user(paid_until=now + timedelta(days=5), plan_tier="premium")
        obj = {
            "metadata": {"kind": "pro_gift", "user_id": str(uid), "months": "2"},
            "payment_intent": "pi_test_gift", "mode": "payment", "currency": "usd",
            "payment_status": "paid", "amount_total": 1000,
        }

        def apply(kind, payload):
            return growth_glue.apply_billing_event({"type": kind, "data": {"object": payload}}, now=now)

        assert not apply("checkout.session.completed", {**obj, "amount_total": 999})
        assert not apply("checkout.session.completed", {**obj, "payment_status": "unpaid"})
        assert apply("checkout.session.completed", obj)
        assert apply("checkout.session.async_payment_succeeded", obj)
        entry = App.ProGift.query.one()
        assert entry.starts_at == now + timedelta(days=5)
        assert entry.ends_at == now + timedelta(days=65)
        user = db.session.get(User, uid)
        assert user.paid_until == now + timedelta(days=5)
        assert growth_glue.current_plan(user, now + timedelta(days=10)) == "paid"
        import premium_glue

        assert premium_glue.current_tier(user, now + timedelta(days=10)) == "pro"
        assert apply("charge.refunded", {**obj, "amount_refunded": 1000})
        assert growth_glue.current_plan(user, now + timedelta(days=10)) == "free"
        other = {**obj, "payment_intent": "pi_reversed_before_checkout"}
        assert apply("charge.dispute.created", other)
        assert apply("checkout.session.completed", other)
        assert growth_glue.current_plan(user, now + timedelta(days=10)) == "free"


def test_checkout_uses_the_yearly_price_when_asked(client, stripe_stub):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    res = client.post("/api/billing/checkout", json={"interval": "year"})
    assert res.status_code == 200
    params = stripe_stub.checkouts[0]
    assert params["line_items"] == [{"price": "price_year", "quantity": 1}]
    assert params["client_reference_id"] == str(uid)


def test_yearly_is_not_sold_until_its_price_is_configured(client, stripe_stub, monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_ID_YEARLY")
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    res = client.post("/api/billing/checkout", json={"interval": "year"})
    assert res.status_code == 503 and not stripe_stub.checkouts
    page = client.get("/upgrade")
    assert b"per year" not in page.data and b"per month" in page.data


def test_yearly_only_checkout_shows_and_submits_the_yearly_offer(client, stripe_stub, monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_ID")
    with App.app.app_context():
        uid = make_user()
        token = growth_glue.make_pay_token(uid)
    sign_in(client, uid)
    upgrade = client.get("/upgrade").data
    assert b'id="upgCheckout" data-interval="year"' in upgrade
    assert b"$48<small> per year" in upgrade
    assert client.post("/api/billing/checkout", json={"interval": "year"}).status_code == 200

    parent = client.get(f"/pay/{token}").data
    assert b'name="interval" value="year"' in parent
    assert client.post(f"/pay/{token}", data={"interval": "year"}).status_code == 303
    assert [checkout["line_items"][0]["price"] for checkout in stripe_stub.checkouts] == [
        "price_year", "price_year",
    ]


def test_an_unknown_interval_falls_back_to_monthly(client, stripe_stub):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    client.post("/api/billing/checkout", json={"interval": "lifetime"})
    assert stripe_stub.checkouts[0]["line_items"][0]["price"] == "price_month"


def test_parent_link_can_pay_yearly(client, stripe_stub):
    with App.app.app_context():
        uid = make_user()
        token = growth_glue.make_pay_token(uid)
    page = client.get(f"/pay/{token}")
    assert page.status_code == 200 and b"$48" in page.data and b"$5" in page.data
    res = client.post(f"/pay/{token}", data={"interval": "year"})
    assert res.status_code == 303 and res.headers["Location"].startswith("https://checkout.test")
    assert stripe_stub.checkouts[0]["line_items"][0]["price"] == "price_year"
    assert stripe_stub.checkouts[0]["client_reference_id"] == str(uid)


def test_yearly_invoice_opens_a_year_long_window(client, monkeypatch):
    monkeypatch.setenv('STRIPE_PRICE_ID_YEARLY', 'price_year_test')
    with App.app.app_context():
        uid = make_user()
        end = int((datetime.utcnow() + timedelta(days=365)).timestamp())
        event = {
            "type": "invoice.paid",
            "data": {"object": {
                "status": "paid",
                "customer": "cus_year",
                "subscription_details": {"metadata": {"user_id": str(uid)}},
                "lines": {"data": [{"type": "subscription", "price": {"id": "price_year_test"},
                                      "period": {"end": end}}]},
            }},
        }
        assert growth_glue.apply_billing_event(event)
        assert User.query.get(uid).paid_until > datetime.utcnow() + timedelta(days=360)


def test_subscriber_gets_a_billing_portal_link(client, stripe_stub):
    with App.app.app_context():
        uid = make_user(stripe_customer_id="cus_abc", paid_until=datetime.utcnow() + timedelta(days=20))
    sign_in(client, uid)
    res = client.post("/api/billing/portal")
    assert res.status_code == 200 and res.get_json()["url"] == "https://billing.test/portal"
    assert stripe_stub.portals[0]["customer"] == "cus_abc"
    assert b"Manage or cancel" in client.get("/upgrade").data


def test_portal_needs_a_stripe_customer(client, stripe_stub):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    assert client.post("/api/billing/portal").status_code == 404
    assert not stripe_stub.portals


def test_portal_requires_login(client, stripe_stub):
    assert client.post("/api/billing/portal").status_code == 401


def test_public_pages_only_advertise_pro_checkout_once_billing_is_on(client, monkeypatch):
    pricing = client.get("/pricing").data
    assert b"A free planner for your school week." in pricing
    assert b"$48" not in pricing and b'A 12-week study review' in pricing
    assert b'Checkout is currently closed.' in pricing
    assert b'href="/pricing/study-review"' in pricing
    assert b'href="/upgrade" class="btn-secondary pricing-cta"' not in pricing
    assert b"free plan" in client.get("/faq").data
    assert b'id="t-pro"' not in client.get("/legal").data
    monkeypatch.setenv("BILLING_ENABLED", "1")
    pricing = client.get("/pricing").data
    assert b"$5" in pricing and b"$48" in pricing and b"No tiers" not in pricing
    assert b"A 12-week study review" in pricing
    assert b'href="/upgrade" class="btn-secondary pricing-cta"' in pricing
    assert b"no extra tiers" not in client.get("/faq").data
    assert b"There are no extra tiers" not in client.get("/legal").data


# ── Metrics and streak emails ────────────────────────────────────────


def test_admin_growth_is_hidden_from_students(client):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    assert client.get("/admin/growth").status_code == 404


def test_admin_growth_report(client, monkeypatch):
    with App.app.app_context():
        admin_id = make_user(email="growth+admin@example.test", email_verified=True,
                             created_at=datetime.utcnow() - timedelta(days=20))
        make_user(created_at=datetime.utcnow() - timedelta(days=20))
        db.session.add(SavedSchedule(user_id=admin_id, schedule_data="{}"))
        db.session.add(App.LinkedAccount(user_id=admin_id, login_type="ics", credentials="{}"))
        db.session.add(UserStreak(
            user_id=admin_id,
            qualified_dates_json=f'["{(date.today() - timedelta(days=12)).isoformat()}"]',
        ))
        db.session.commit()
    monkeypatch.setattr(App, "FOUNDER_EMAIL", "growth+admin@example.test")
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
