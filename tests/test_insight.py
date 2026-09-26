"""Consented product insight: the gate, what is stored, and the questions.

The gate tests are the important ones. Everything else here is a reporting
convenience; recording something we were not allowed to record is the kind
of mistake that ends a school's trust in the product.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest

import App
import cookie_policy
import insight_glue
from App import InsightAnswer, ProductEvent, SavedSchedule, User, db
from intelliplan.insight import events as ev
from intelliplan.insight import prompts as pr
from intelliplan.insight import reports


# ── Pure: what an event may contain ──────────────────────────────────


def test_only_allowlisted_props_with_small_values_survive():
    clean = ev.sanitize_props({
        "step": "connect",
        "count": 3,
        "ok": True,
        "essay": "My essay about my mother's illness",   # not allowlisted
        "reason": "x" * 200,                              # free text shaped
        "source": "tiktok",
    })
    assert clean == {"step": "connect", "count": 3, "ok": True, "source": "tiktok"}


def test_unknown_event_names_are_refused():
    assert ev.is_allowed_event("plan_viewed")
    assert not ev.is_allowed_event("password_typed")
    assert not ev.is_allowed_event(None)


@pytest.mark.parametrize("rule", [
    "/login", "/oauth/google/callback", "/api/billing/webhook", "/cron/notifications",
    "/admin/insight", "/email/streak-off/<token>", "/ref/<code>", "/api/insight/event",
    "/foundations", "/api/primer/learners/<int:learner_id>/answer",
])
def test_sensitive_routes_are_never_recorded(rule):
    assert ev.should_record_rule(rule) is False


def test_an_unmatched_request_has_no_rule_and_is_not_recorded():
    assert ev.should_record_rule(None) is False
    assert ev.should_record_rule("") is False


def test_normal_routes_are_recorded():
    assert ev.should_record_rule("/dashboard")
    assert ev.should_record_rule("/api/groups/<int:group_id>/join")


@pytest.mark.parametrize("utm, referrer, expected", [
    ("tiktok", "", "tiktok"),
    ("", "https://www.google.com/search?q=study+planner", "search"),
    ("", "https://m.youtube.com/watch", "youtube"),
    ("", "", "direct"),
    ("", "https://intelliplan.tech/pricing", "direct"),
    ("", "https://someschool.edu/links", "someschool.edu"),
])
def test_channel_classification(utm, referrer, expected):
    assert ev.classify_channel(utm, referrer, "intelliplan.tech") == expected


def test_a_referrer_never_keeps_its_path_or_query():
    assert ev.referrer_host("https://mail.example.com/inbox?token=abc") == "mail.example.com"


# ── Pure: which question, and when ───────────────────────────────────


def _due(**kw):
    base = dict(account_age_days=30, has_plan=True, settled=(),
                hours_since_last_prompt=None, is_child=False)
    base.update(kw)
    return pr.prompt_for(**base)


def test_the_first_question_is_where_they_heard_about_us():
    assert _due(account_age_days=0).key == "heard_from"


def test_one_question_a_day_at_most():
    assert _due(hours_since_last_prompt=3) is None
    assert _due(hours_since_last_prompt=25) is not None


def test_a_settled_question_never_returns():
    assert _due(settled={"heard_from"}).key == "main_need"
    assert _due(settled={"heard_from", "main_need", "invite", "missing"}) is None


def test_questions_wait_until_they_are_fair_to_ask():
    assert _due(account_age_days=0, settled={"heard_from"}) is None
    assert _due(account_age_days=3, settled={"heard_from"}).key == "main_need"


def test_the_invite_prompt_waits_for_a_plan():
    settled = {"heard_from", "main_need"}
    assert _due(settled=settled, has_plan=False).key != "invite"
    assert _due(settled=settled, has_plan=True).key == "invite"


def test_children_are_never_prompted():
    assert _due(is_child=True) is None


def test_answers_must_come_from_the_prompts_own_options():
    heard = pr.prompt_by_key("heard_from")
    assert pr.is_valid_answer(heard, "tiktok")
    assert not pr.is_valid_answer(heard, "something-invented")
    assert pr.is_valid_answer(pr.prompt_by_key("missing"), None)  # free text


# ── Pure: reports ────────────────────────────────────────────────────


def test_usage_counts_people_not_page_refreshes():
    rows = [("view", "/dashboard", "u:1"), ("view", "/dashboard", "u:1"),
            ("view", "/dashboard", "u:2"), ("view", "/groups", "u:1")]
    usage = reports.feature_usage(rows)
    assert usage[0] == {"kind": "view", "rule": "/dashboard", "actors": 2}


def test_channel_table_reports_signup_rate_per_visitor():
    table = reports.channel_table(
        signups=[("tiktok", True, True), ("tiktok", False, False), ("search", True, False)],
        visitors=[("tiktok", "v:1"), ("tiktok", "v:1"), ("tiktok", "v:2"), ("search", "v:3")],
    )
    tiktok = next(r for r in table if r["channel"] == "tiktok")
    assert tiktok["visitors"] == 2 and tiktok["signups"] == 2
    assert tiktok["activation_rate"] == 0.5 and tiktok["retention_rate"] == 0.5


def test_visit_depth_separates_bouncers_from_the_curious():
    depth = reports.visit_depth([
        ("view", "/", "v:1"), ("view", "/", "v:2"), ("view", "/pricing", "v:2"),
        ("view", "/dashboard", "u:9"),
    ])
    assert depth == {"visitors": 2, "one_page_only": 1, "one_page_share": 0.5,
                     "multi_page": 1, "signed_in_actors": 1}


def test_skipped_answers_are_counted_too():
    tally = reports.survey_tally([("heard_from", "tiktok"), ("heard_from", None)])
    assert {row["answer"] for row in tally["heard_from"]} == {"tiktok", "(skipped)"}


# ── The gate, in the running app ─────────────────────────────────────


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            _clean()
        yield c
        with App.app.app_context():
            _clean()


def _clean():
    ProductEvent.query.delete()
    InsightAnswer.query.delete()
    SavedSchedule.query.filter(SavedSchedule.user_id.isnot(None)).delete(synchronize_session=False)
    User.query.filter(User.email.like("insight+%")).delete(synchronize_session=False)
    db.session.commit()


def make_user(**kw):
    defaults = {
        "email": f"insight+{uuid.uuid4().hex[:10]}@example.test",
        "password_hash": "x",
        "birth_year": 2000,
        "created_at": datetime.utcnow() - timedelta(days=30),
    }
    defaults.update(kw)
    user = User(**defaults)
    db.session.add(user)
    db.session.commit()
    return user.id


def accept(client):
    client.set_cookie("ip_cookie_consent", cookie_policy.serialize_consent(["analytics"]))


def sign_in(client, uid):
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True


def events():
    return ProductEvent.query.all()


def test_nothing_is_recorded_before_the_visitor_is_asked(client):
    client.get("/pricing")
    with App.app.app_context():
        assert events() == []


def test_declining_records_nothing(client):
    client.set_cookie("ip_cookie_consent", cookie_policy.serialize_consent([]))
    client.get("/pricing")
    with App.app.app_context():
        assert events() == []


def test_accepting_records_the_route_and_sets_one_visitor_cookie(client):
    accept(client)
    res = client.get("/pricing?utm_source=tiktok")
    assert "ip_vid" in res.headers.get("Set-Cookie", "")
    with App.app.app_context():
        rows = events()
        assert len(rows) == 1
        row = rows[0]
        assert row.rule == "/pricing" and row.kind == "view"
        assert row.channel == "tiktok"
        assert row.actor.startswith("v:") and row.user_id is None


def test_a_childs_page_view_is_never_recorded_even_having_accepted(client):
    accept(client)
    with App.app.app_context():
        uid = make_user(birth_year=datetime.utcnow().year - 11)
    sign_in(client, uid)
    client.get("/pricing")
    with App.app.app_context():
        assert events() == []


def test_first_touch_is_attached_to_the_account(client):
    accept(client)
    with App.app.app_context():
        uid = make_user()
    client.get("/pricing?utm_source=reddit&utm_campaign=ap-week")
    sign_in(client, uid)
    client.get("/pricing")
    with App.app.app_context():
        touch = json.loads(User.query.get(uid).first_touch_json or "{}")
    assert touch["channel"] == "reddit" and touch["utm_campaign"] == "ap-week"


def test_turning_analytics_off_deletes_what_was_recorded(client):
    accept(client)
    client.get("/pricing")
    with App.app.app_context():
        assert len(events()) == 1
    res = client.post("/api/cookies/consent", json={"granted": []})
    assert res.status_code == 200
    with App.app.app_context():
        assert events() == []


def test_client_events_are_allowlisted_and_answer_204_either_way(client):
    accept(client)
    assert client.post("/api/insight/event", json={"name": "plan_viewed", "props": {"count": 2}}).status_code == 204
    assert client.post("/api/insight/event", json={"name": "not_a_real_event"}).status_code == 204
    with App.app.app_context():
        rows = [r for r in events() if r.name]
        assert [r.name for r in rows] == ["plan_viewed"]
        assert json.loads(rows[0].props) == {"count": 2}


def test_a_client_event_without_consent_is_dropped_silently(client):
    res = client.post("/api/insight/event", json={"name": "plan_viewed"})
    assert res.status_code == 204  # says nothing about the consent state
    with App.app.app_context():
        assert events() == []


def test_old_events_are_purged(client):
    with App.app.app_context():
        db.session.add(ProductEvent(actor="v:old", kind="view", rule="/",
                                    created_at=datetime.utcnow() - timedelta(days=400)))
        db.session.add(ProductEvent(actor="v:new", kind="view", rule="/"))
        db.session.commit()
        assert insight_glue.purge_old_events() == 1
        assert [r.actor for r in events()] == ["v:new"]


# ── Questions, end to end ────────────────────────────────────────────


def test_the_prompt_is_served_answered_and_not_repeated(client):
    with App.app.app_context():
        uid = make_user(created_at=datetime.utcnow())
    sign_in(client, uid)

    body = client.get("/api/insight/prompt").get_json()
    assert body["prompt"]["key"] == "heard_from"
    assert {o["value"] for o in body["prompt"]["options"]} >= {"friend", "tiktok"}

    saved = client.post("/api/insight/prompt", json={
        "key": "heard_from", "answer": "friend", "detail": "my lab partner"})
    assert saved.status_code == 200
    with App.app.app_context():
        row = InsightAnswer.query.filter_by(user_id=uid, question="heard_from").one()
        assert row.status == "answered" and row.answer == "friend"
        assert row.detail == "my lab partner"

    # Same day: nothing more, however many times the page loads.
    assert client.get("/api/insight/prompt").get_json()["prompt"] is None


def test_an_invented_answer_is_refused(client):
    with App.app.app_context():
        uid = make_user(created_at=datetime.utcnow())
    sign_in(client, uid)
    client.get("/api/insight/prompt")
    res = client.post("/api/insight/prompt", json={"key": "heard_from", "answer": "<script>"})
    assert res.status_code == 400


def test_a_dismissal_settles_the_question(client):
    with App.app.app_context():
        # Old enough that the *next* question is fair to ask, too.
        uid = make_user(created_at=datetime.utcnow() - timedelta(days=5))
    sign_in(client, uid)
    client.get("/api/insight/prompt")
    assert client.post("/api/insight/prompt",
                       json={"key": "heard_from", "dismissed": True}).status_code == 200
    with App.app.app_context():
        row = InsightAnswer.query.filter_by(user_id=uid, question="heard_from").one()
        assert row.status == "dismissed" and row.answer is None
        # And the next question is a different one, tomorrow.
        row.shown_at = datetime.utcnow() - timedelta(days=2)
        db.session.commit()
    assert client.get("/api/insight/prompt").get_json()["prompt"]["key"] == "main_need"


def test_the_invite_prompt_carries_the_students_own_link(client):
    with App.app.app_context():
        uid = make_user(created_at=datetime.utcnow() - timedelta(days=5))
        db.session.add(SavedSchedule(user_id=uid, schedule_data="{}"))
        for question in ("heard_from", "main_need"):
            db.session.add(InsightAnswer(user_id=uid, question=question, status="answered",
                                         shown_at=datetime.utcnow() - timedelta(days=2)))
        db.session.commit()
    sign_in(client, uid)
    prompt = client.get("/api/insight/prompt").get_json()["prompt"]
    assert prompt["key"] == "invite"
    with App.app.app_context():
        code = User.query.get(uid).referral_code
    assert prompt["invite_url"].endswith(f"/ref/{code}")


def test_anonymous_visitors_are_never_prompted(client):
    assert client.get("/api/insight/prompt").get_json()["prompt"] is None


# ── Admin ────────────────────────────────────────────────────────────


def test_the_insight_dashboard_is_admin_only(client):
    with App.app.app_context():
        uid = make_user()
    sign_in(client, uid)
    assert client.get("/admin/insight").status_code == 404


def test_the_insight_dashboard_reports(client, monkeypatch):
    accept(client)
    with App.app.app_context():
        admin_id = make_user(email="insight+admin@example.test")
        db.session.add(ProductEvent(actor=f"u:{admin_id}", user_id=admin_id,
                                    kind="view", rule="/dashboard", channel="tiktok"))
        db.session.add(InsightAnswer(user_id=admin_id, question="heard_from",
                                     answer="friend", detail="lab partner",
                                     status="answered", answered_at=datetime.utcnow()))
        db.session.commit()
    monkeypatch.setattr(App, "ADMIN_EMAILS", {"insight+admin@example.test"})
    sign_in(client, admin_id)

    report = client.get("/admin/insight?format=json").get_json()
    assert any(row["rule"] == "/dashboard" for row in report["features"])
    assert report["survey"]["heard_from"][0]["answer"] == "friend"
    assert any("said: friend" == row["channel"] for row in report["channels"])
    assert report["notes"][0]["detail"] == "lab partner"

    html = client.get("/admin/insight")
    assert html.status_code == 200 and b"Where students come from" in html.data


# ── Third-party analytics obeys the same gate ────────────────────────


def test_posthog_events_are_dropped_until_a_gate_says_yes(monkeypatch):
    """PostHog sends a user id off-site, so it is not exempt from consent.

    It used to fire on every streak action for everyone, including
    children, with no gate at all.
    """
    import analytics

    sent = []

    class FakeClient:
        def capture(self, **kw):
            sent.append(kw)

        def identify(self, **kw):
            sent.append(kw)

    monkeypatch.setattr(analytics, "_client", FakeClient())
    monkeypatch.setattr(analytics, "_disabled", False)

    monkeypatch.setattr(analytics, "_gate", None)
    analytics.track(7, "streak_extended")
    assert sent == []

    monkeypatch.setattr(analytics, "_gate", lambda: False)
    analytics.track(7, "streak_extended")
    analytics.identify(7, {"cohort": "a"})
    assert sent == []

    monkeypatch.setattr(analytics, "_gate", lambda: True)
    analytics.track(7, "streak_extended")
    assert [e["event"] for e in sent] == ["streak_extended"]


def test_a_broken_gate_is_read_as_no(monkeypatch):
    import analytics

    monkeypatch.setattr(analytics, "_gate", lambda: 1 / 0)
    assert analytics._permitted() is False
