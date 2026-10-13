"""Premium: a metered Claude tutor, model routing, and linked AI accounts.

The pure rules (prices, plans, routing) are tested without Flask. The glue is
tested through the real tutor endpoint with the model calls stubbed, so the
budget arithmetic, the ledger and the fallbacks are exercised end to end.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import ai_provider
import App
import chatbot_api
import premium_glue
from App import AICreditGrant, AISpendEvent, LinkedAIAccount, User, db
from intelliplan.premium import byok, catalog, claude_tutor, pricing, router
from time_utils import utcnow


# ── Pricing ─────────────────────────────────────────────────────────────


def test_a_sonnet_call_is_priced_from_its_usage():
    usage = pricing.Usage(input_tokens=1_000_000, output_tokens=100_000,
                          cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    # $2 input + $1 output + $0.20 cache read + $2.50 cache write
    assert pricing.cost_micro("claude-sonnet-5-5", usage) == 5_700_000


def test_cost_rounds_up_so_tiny_calls_are_never_free():
    assert pricing.cost_micro("claude-haiku-5-5", pricing.Usage(input_tokens=1)) == 1


def test_an_unknown_model_is_priced_as_the_most_expensive():
    usage = pricing.Usage(output_tokens=1000)
    assert pricing.cost_micro("mystery-model", usage) == pricing.cost_micro("claude-opus-5-5", usage)


def test_prices_can_change_without_a_deploy(monkeypatch):
    monkeypatch.setenv("PREMIUM_PRICE_OVERRIDES", json.dumps({"claude-sonnet-5-5": {"input": 1, "output": 1}}))
    assert pricing.cost_micro("claude-sonnet-5-5", pricing.Usage(output_tokens=1_000_000)) == 1_000_000


# ── Plans ───────────────────────────────────────────────────────────────


def test_premium_defaults_to_twenty_dollars_with_twenty_of_ai():
    plan = catalog.premium()
    assert plan.price_usd[catalog.MONTHLY] == 20.0
    assert plan.ai_budget_micro == 20_000_000


def test_linking_your_own_account_costs_less():
    assert catalog.premium_byok().price_usd[catalog.MONTHLY] < catalog.premium().price_usd[catalog.MONTHLY]


def test_plan_numbers_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("PREMIUM_AI_BUDGET_USD", "12.5")
    assert catalog.premium().ai_budget_micro == 12_500_000


def test_a_stripe_price_maps_back_to_its_plan(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_ID_PREMIUM_BYOK", "price_byok")
    assert catalog.plan_for_price("price_byok") == (catalog.PREMIUM_BYOK, catalog.MONTHLY)
    assert catalog.plan_for_price("price_other") is None


def test_a_top_up_credits_less_than_it_costs():
    t = catalog.topup()
    assert t.credit_usd < t.price_usd


# ── Routing ─────────────────────────────────────────────────────────────


def test_a_bare_request_for_help_gets_clarifying_questions():
    c = router.classify("help me with my homework", [])
    assert c.needs_clarification and c.clarifying_questions


def test_a_vague_reply_inside_a_conversation_is_answered():
    c = router.classify("explain it", [{"role": "assistant", "content": "Photosynthesis is…"}])
    assert not c.needs_clarification


def test_proofs_go_to_the_deep_tier_and_definitions_to_quick():
    assert router.classify("Prove that the square root of 2 is irrational", []).tier == router.DEEP
    assert router.classify("What is a noun?", []).tier == router.QUICK


def test_the_classifier_decides_when_it_answers_sensibly():
    raw = json.dumps({"intent": "solve", "subject": "Chemistry", "difficulty": 4, "tier": "deep",
                      "needs_clarification": False, "clarifying_questions": [], "summary": "stoich"})
    c = router.classify("balance this equation", [], classifier=lambda msgs: raw)
    assert (c.tier, c.subject, c.source) == (router.DEEP, "Chemistry", "classifier")


def test_a_broken_classifier_falls_back_to_the_heuristic():
    c = router.classify("What is a noun?", [], classifier=lambda msgs: "not json at all")
    assert c.source == "heuristic" and c.tier == router.QUICK


def test_a_classifier_asking_to_clarify_without_questions_is_ignored():
    raw = json.dumps({"tier": "balanced", "difficulty": 3, "needs_clarification": True,
                      "clarifying_questions": []})
    assert not router.classify("solve 2x+3=7", [], classifier=lambda m: raw).needs_clarification


def test_the_route_steps_down_to_fit_the_budget():
    c = router.Classification(tier=router.DEEP)
    deep_cost = pricing.worst_case_micro(router.tier_spec(router.DEEP).model, 2000,
                                         router.tier_spec(router.DEEP).max_tokens)
    route = router.decide(c, remaining_micro=deep_cost - 1, input_tokens=2000)
    assert route.tier != router.DEEP and route.downgraded


def test_no_route_when_nothing_fits():
    assert router.decide(router.Classification(), remaining_micro=0, input_tokens=100) is None


def test_the_students_own_key_is_never_downgraded():
    route = router.decide(router.Classification(tier=router.DEEP), remaining_micro=None, input_tokens=10)
    assert route.tier == router.DEEP


def test_a_chosen_depth_overrides_the_router():
    route = router.decide(router.Classification(tier=router.DEEP), preference=router.QUICK,
                          remaining_micro=None, input_tokens=10)
    assert route.tier == router.QUICK


def test_a_clarification_is_never_followed_by_another():
    text = router.clarification_text(["Which class?"])
    assert router.last_turn_was_clarification([{"role": "assistant", "content": text}])
    assert not router.last_turn_was_clarification([{"role": "assistant", "content": "Here's how."}])


# ── The Claude call ─────────────────────────────────────────────────────


class _FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _fake_client(text="Here's the idea.", stop="end_turn", model="claude-sonnet-5-5"):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, model=model,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300,
                              cache_read_input_tokens=0, cache_creation_input_tokens=900))
    messages = _FakeMessages(response)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)), messages


def test_sonnet_opts_into_refusal_fallbacks_and_caches_the_tutor_prompt():
    client, messages = _fake_client()
    result = claude_tutor.answer(api_key="k", model="claude-sonnet-5-5", effort="medium",
                                 max_tokens=4000, client=client,
                                 messages=[{"role": "user", "content": "Why is the sky blue?"}],
                                 dynamic_system=["Student likes biology."])
    call = messages.calls[0]
    assert call["fallbacks"] == "default"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"] == {"effort": "medium"}
    assert "temperature" not in call
    assert result.usage.cache_write_tokens == 900


def test_haiku_is_called_without_fallbacks():
    client, messages = _fake_client(model="claude-haiku-5-5")
    claude_tutor.answer(api_key="k", model="claude-haiku-5-5", effort="low", max_tokens=100,
                        client=client, messages=[{"role": "user", "content": "hi"}])
    assert "fallbacks" not in messages.calls[0]


def test_a_refusal_is_replaced_with_a_redirect_not_shown_raw():
    client, _ = _fake_client(text="", stop="refusal")
    result = claude_tutor.answer(api_key="k", model="claude-sonnet-5-5", effort="low",
                                 max_tokens=100, client=client,
                                 messages=[{"role": "user", "content": "x"}])
    assert result.refused and result.text == claude_tutor.REFUSAL_REPLY


def test_history_is_cleaned_for_the_messages_api():
    out = claude_tutor.to_claude_messages([
        {"role": "assistant", "content": "Hi!"}, {"role": "system", "content": "s"},
        {"role": "user", "content": "  "}, {"role": "user", "content": "q"}])
    assert out == [{"role": "user", "content": "q"}]


# ── Linked keys ─────────────────────────────────────────────────────────


def test_key_shapes_are_checked_before_any_network_call():
    assert byok.looks_valid("anthropic", "sk-proj-not-anthropic-1234567890") is not None
    assert byok.looks_valid("groq", "gsk_" + "a" * 40) is None
    assert byok.looks_valid("nope", "x" * 30) == "Unknown provider."


def test_only_the_last_four_characters_are_ever_shown():
    assert byok.hint("gsk_secretsecretsecret1234") == "…1234"


class _Http:
    def __init__(self, status):
        self.status = status
        self.seen = []

    def get(self, url, **kw):
        self.seen.append((url, kw))
        return SimpleNamespace(status_code=self.status)


def test_a_rejected_key_is_reported_as_invalid():
    check = byok.verify("openai", "sk-" + "a" * 40, session=_Http(401))
    assert not check.ok and check.key_invalid


def test_a_working_key_verifies_without_spending_tokens():
    http = _Http(200)
    assert byok.verify("groq", "gsk_" + "a" * 40, session=http).ok
    assert http.seen[0][0].endswith("/models")


# ── Through the app ─────────────────────────────────────────────────────


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("prem+%")).all()]
    if ids:
        for model in (AISpendEvent, AICreditGrant, LinkedAIAccount):
            model.query.filter(model.user_id.in_(ids)).delete(synchronize_session=False)
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()


def _user(client, *, tier="premium", age=20, email="prem+student@example.com"):
    with App.app.app_context():
        user = User(email=email, password_hash="", birth_year=utcnow().year - age)
        if tier:
            user.plan_tier = tier
            user.paid_until = utcnow() + timedelta(days=20)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


@pytest.fixture
def tutor_env(monkeypatch):
    """Stub everything around the model call in /api/tutor."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "platform-key")
    monkeypatch.setattr(premium_glue, "_classifier", lambda uid: None)
    monkeypatch.setattr(chatbot_api.ai_firewall, "guard",
                        lambda *a, **k: SimpleNamespace(max_output_tokens=1800, plan="paid"))
    monkeypatch.setattr(chatbot_api.ai_firewall, "record_tokens", lambda *a, **k: None)
    monkeypatch.setattr(chatbot_api, "_safety_check_user_message", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_safety_check_assistant_reply", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_load_tutor_memory", lambda: {"id": 1, "profile_json": "{}"})
    monkeypatch.setattr(chatbot_api, "_build_personalization_prompt", lambda **k: None)
    monkeypatch.setattr(chatbot_api, "_prepare_adaptive_turn", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_load_user_identity", lambda: {})
    monkeypatch.setattr(chatbot_api, "_save_tutor_profile", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_record_adaptive_turn", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_ensure_conversation", lambda *a: {"id": 7, "title": "Chat"})
    monkeypatch.setattr(chatbot_api, "_save_conversation", lambda *a: None)
    calls = {"claude": [], "free": []}

    def fake_answer(**kw):
        calls["claude"].append(kw)
        return claude_tutor.TutorResult(
            "Let's work through it.", kw["model"],
            pricing.Usage(input_tokens=2000, output_tokens=500), "end_turn")

    monkeypatch.setattr(claude_tutor, "answer", fake_answer)
    monkeypatch.setattr(chatbot_api, "_llm_chat",
                        lambda **kw: calls["free"].append(kw) or "Free model answer.")
    return calls


def ask(client, text, **extra):
    return client.post("/api/tutor", json={"messages": [{"role": "user", "content": text}], **extra})


def test_a_premium_question_is_answered_by_claude_and_charged(client, tutor_env):
    uid = _user(client)
    res = ask(client, "Solve 3x + 4 = 19 and show the steps")
    assert res.status_code == 200, res.json
    assert res.json["reply"] == "Let's work through it."
    meta = res.json["premium"]
    assert meta["model"] == "claude-sonnet-5-5" and meta["source"] == "platform"
    assert meta["cost_usd"] > 0
    with App.app.app_context():
        row = AISpendEvent.query.filter_by(user_id=uid, purpose="tutor").one()
        assert row.cost_micro == pricing.cost_micro("claude-sonnet-5-5",
                                                    pricing.Usage(input_tokens=2000, output_tokens=500))
        state = premium_glue.budget_state(db.session.get(User, uid))
    assert state["remaining_micro"] == 20_000_000 - row.cost_micro
    assert tutor_env["free"] == []


def test_the_tutor_prompt_is_the_cached_part(client, tutor_env):
    _user(client)
    ask(client, "Solve 3x + 4 = 19")
    call = tutor_env["claude"][0]
    assert call["stable_system"].startswith(chatbot_api.TUTOR_SYSTEM_PROMPT[:40])


def test_a_vague_question_gets_clarifying_questions_and_costs_nothing(client, tutor_env):
    uid = _user(client)
    res = ask(client, "[Subject: Math]\nhelp me with my homework")
    assert res.json["clarify"] is True and res.json["questions"]
    assert res.json["reply"].startswith(router.CLARIFY_PREFIX)
    assert tutor_env["claude"] == []
    with App.app.app_context():
        assert AISpendEvent.query.filter_by(user_id=uid, source="platform").count() == 0


def test_just_answer_skips_the_questions(client, tutor_env):
    _user(client)
    res = client.post("/api/tutor", json={"messages": [
        {"role": "user", "content": "help me"},
        {"role": "assistant", "content": router.clarification_text(["Which class?"])},
        {"role": "user", "content": "[Subject: Math]\nJust answer"},
    ]})
    assert "clarify" not in res.json
    assert len(tutor_env["claude"]) == 1


def test_clarifying_can_be_turned_off(client, tutor_env):
    uid = _user(client)
    with App.app.app_context():
        db.session.get(User, uid).premium_clarify = False
        db.session.commit()
    res = ask(client, "help me with my homework")
    assert "clarify" not in res.json and len(tutor_env["claude"]) == 1


def test_a_spent_budget_falls_back_to_the_free_model(client, tutor_env):
    uid = _user(client)
    with App.app.app_context():
        premium_glue.record_spend(uid, provider="anthropic", model="claude-opus-5-5",
                                  usage=pricing.Usage(output_tokens=1_000_000),
                                  source="platform", purpose="tutor")
    res = ask(client, "Solve 3x + 4 = 19")
    assert res.json["reply"] == "Free model answer."
    assert res.json["premium"]["budget_exhausted"] is True
    assert tutor_env["claude"] == []


def test_a_top_up_extends_the_budget(client, tutor_env):
    uid = _user(client)
    with App.app.app_context():
        premium_glue.record_spend(uid, provider="anthropic", model="claude-opus-5-5",
                                  usage=pricing.Usage(output_tokens=1_000_000),
                                  source="platform", purpose="tutor")
        assert premium_glue.grant_credit(uid, 4_000_000, stripe_ref="cs:test-topup")
        assert not premium_glue.grant_credit(uid, 4_000_000, stripe_ref="cs:test-topup")
    res = ask(client, "Solve 3x + 4 = 19")
    assert res.json["reply"] == "Let's work through it."


def test_a_free_student_without_a_linked_key_is_untouched(client, tutor_env):
    _user(client, tier=None)
    res = ask(client, "Solve 3x + 4 = 19")
    assert res.json["reply"] == "Free model answer."
    assert "premium" not in res.json


def test_a_linked_key_answers_without_touching_the_budget(client, tutor_env, monkeypatch):
    monkeypatch.setenv("BYOK_ENABLED", "1")
    uid = _user(client, tier="premium_byok")
    with App.app.app_context():
        db.session.add(LinkedAIAccount(user_id=uid, provider="groq", api_key="gsk_" + "k" * 40,
                                       key_hint="…kkkk"))
        db.session.commit()
    monkeypatch.setattr(byok, "chat", lambda *a, **k: byok.ByokResult(
        "From your account.", "openai/gpt-oss-120b", pricing.Usage(input_tokens=10, output_tokens=10)))
    res = ask(client, "Solve 3x + 4 = 19")
    assert res.json["reply"] == "From your account."
    assert res.json["premium"]["source"] == "byok"
    with App.app.app_context():
        state = premium_glue.budget_state(db.session.get(User, uid))
    assert state["spent_micro"] == 0


def test_a_rejected_linked_key_is_marked_and_claude_answers(client, tutor_env, monkeypatch):
    monkeypatch.setenv("BYOK_ENABLED", "1")
    uid = _user(client)
    with App.app.app_context():
        db.session.add(LinkedAIAccount(user_id=uid, provider="groq", api_key="gsk_" + "k" * 40))
        db.session.commit()

    def rejected(*a, **k):
        raise claude_tutor.TutorError("groq rejected the key", key_invalid=True)

    monkeypatch.setattr(byok, "chat", rejected)
    res = ask(client, "Solve 3x + 4 = 19")
    assert res.json["premium"]["source"] == "platform"
    with App.app.app_context():
        assert LinkedAIAccount.query.filter_by(user_id=uid).one().status == "invalid"


def test_paying_students_are_not_held_to_the_free_message_count(client):
    """The bypass read ``current_user.pro_active``, which no model defines."""
    uid = _user(client)
    with App.app.test_request_context():
        from flask_login import login_user

        user = db.session.get(User, uid)
        login_user(user)
        user.monthly_tutor_messages = 10_000
        assert chatbot_api._check_and_increment_tutor_limit() == (True, None, None)


# ── Linking ─────────────────────────────────────────────────────────────


def test_linking_is_off_until_the_flag_is_set(client):
    _user(client)
    res = client.post("/api/ai-accounts", json={"provider": "groq", "api_key": "gsk_" + "a" * 40})
    assert res.status_code == 503


def test_students_under_18_cannot_link_a_key(client, monkeypatch):
    monkeypatch.setenv("BYOK_ENABLED", "1")
    _user(client, age=16)
    res = client.post("/api/ai-accounts", json={"provider": "groq", "api_key": "gsk_" + "a" * 40})
    assert res.status_code == 403


def test_a_key_is_never_stored_without_encryption(client, monkeypatch):
    import secret_box

    monkeypatch.setenv("BYOK_ENABLED", "1")
    monkeypatch.setattr(secret_box, "is_enabled", lambda: False)
    _user(client)
    res = client.post("/api/ai-accounts", json={"provider": "groq", "api_key": "gsk_" + "a" * 40})
    assert res.status_code == 503


def test_a_verified_key_is_saved_and_shown_only_as_a_hint(client, monkeypatch):
    import secret_box

    monkeypatch.setenv("BYOK_ENABLED", "1")
    monkeypatch.setattr(secret_box, "is_enabled", lambda: True)
    monkeypatch.setattr(byok, "verify", lambda p, k: byok.Check(True, "Linked."))
    _user(client)
    key = "gsk_" + "a" * 36 + "WXYZ"
    res = client.post("/api/ai-accounts", json={"provider": "groq", "api_key": key})
    assert res.status_code == 200
    listing = client.get("/api/ai-accounts")
    assert key not in listing.get_data(as_text=True)
    assert listing.json["accounts"][0]["key_hint"] == "…WXYZ"
    with App.app.app_context():
        assert LinkedAIAccount.query.filter_by(provider="groq").first().api_key == key
    assert client.delete("/api/ai-accounts/groq").status_code == 200
    assert client.get("/api/ai-accounts").json["accounts"] == []


# ── Settings, status and billing ────────────────────────────────────────


def test_the_ai_settings_page_shows_the_budget(client):
    _user(client)
    page = client.get("/settings/ai").get_data(as_text=True)
    assert "$20.00" in page and "Linked AI accounts" in page


def test_preferences_are_validated_and_saved(client):
    _user(client)
    assert client.post("/api/premium/preferences", json={"model_pref": "turbo"}).status_code == 400
    res = client.post("/api/premium/preferences", json={"model_pref": "deep", "clarify": False})
    assert res.json["model_pref"] == "deep" and res.json["clarify"] is False


def test_status_reports_the_meter(client):
    _user(client)
    data = client.get("/api/premium/status").json
    assert data["tier"] == "premium" and data["budget_usd"] == 20.0


def test_a_checkout_event_sets_the_premium_tier(client):
    import growth_glue

    uid = _user(client, tier=None)
    event = {"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": str(uid), "customer": "cus_1",
        "metadata": {"user_id": str(uid), "plan": "premium"}}}}
    with App.app.app_context():
        assert growth_glue.apply_billing_event(event)
        assert premium_glue.current_tier(db.session.get(User, uid)) == "premium"


def test_a_top_up_event_credits_without_extending_the_plan(client):
    import growth_glue

    uid = _user(client)
    with App.app.app_context():
        before = db.session.get(User, uid).paid_until
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_topup_1", "metadata": {"user_id": str(uid), "kind": "ai_topup"}}}}
    with App.app.app_context():
        assert growth_glue.apply_billing_event(event)
        assert growth_glue.apply_billing_event(event)  # retried webhook
        assert AICreditGrant.query.filter_by(user_id=uid).count() == 1
        assert db.session.get(User, uid).paid_until == before


def test_a_referral_month_on_a_lapsed_premium_account_is_pro(client):
    import growth_glue

    uid = _user(client)
    with App.app.app_context():
        user = db.session.get(User, uid)
        user.paid_until = utcnow() - timedelta(days=3)
        user.referred_by_id = uid
        growth_glue.grant_signup_reward(user)
        assert premium_glue.current_tier(user) == "pro"


def test_other_features_stop_using_claude_once_the_budget_is_spent(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "platform-key")
    uid = _user(client)
    with App.app.test_request_context():
        from flask_login import login_user

        login_user(db.session.get(User, uid))
        assert ("claude", ai_provider.CLAUDE_STANDARD) in ai_provider.model_chain("standard", "paid")
        premium_glue.record_spend(uid, provider="anthropic", model="claude-opus-5-5",
                                  usage=pricing.Usage(output_tokens=1_000_000),
                                  source="platform", purpose="feature")
        assert all(p != "claude" for p, _ in ai_provider.model_chain("standard", "paid"))


def test_claude_feature_calls_are_metered(client, monkeypatch):
    uid = _user(client)
    with App.app.test_request_context():
        from flask_login import login_user

        login_user(db.session.get(User, uid))
        premium_glue._spend_recorder("anthropic", "claude-sonnet-5-5",
                                     SimpleNamespace(input_tokens=100, output_tokens=100,
                                                     cache_read_input_tokens=None,
                                                     cache_creation_input_tokens=None), "platform")
        assert AISpendEvent.query.filter_by(user_id=uid, purpose="feature").count() == 1


def test_account_deletion_covers_the_new_tables():
    import inspect

    source = inspect.getsource(App)
    for table in ("ai_spend_events", "ai_credit_grants", "linked_ai_accounts"):
        assert f"DELETE FROM {table} WHERE user_id = :uid" in source
