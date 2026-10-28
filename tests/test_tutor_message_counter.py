"""The free-plan tutor counter reads two User columns that must exist.

``_check_and_increment_tutor_limit`` read ``tutor_reset_date`` and
``monthly_tutor_messages`` before the model defined them. Every signed-in
student's tutor message raised AttributeError and came back as "Sorry, I hit
a snag" (production, 2026-10-07).
"""

from datetime import datetime

import pytest

import App
import chatbot_api
from App import User, db


@pytest.fixture
def user_id():
    App.app.config["TESTING"] = True
    with App.app.app_context():
        db.create_all()
        User.query.filter_by(email="counter+student@example.com").delete()
        user = User(email="counter+student@example.com", password_hash="", birth_year=2005)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    yield uid
    with App.app.app_context():
        User.query.filter_by(id=uid).delete()
        db.session.commit()


def _check(uid):
    from flask_login import login_user

    with App.app.test_request_context():
        login_user(db.session.get(User, uid))
        return chatbot_api._check_and_increment_tutor_limit()


@pytest.fixture
def tutor_client(user_id):
    App.limiter.enabled = False
    client = App.app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    yield client
    App.limiter.enabled = True


def _used(uid):
    with App.app.app_context():
        db.session.expire_all()
        return db.session.get(User, uid).monthly_tutor_messages


def _stub_tutor_turn(monkeypatch, *, model_reply="A helpful answer."):
    from types import SimpleNamespace

    monkeypatch.setattr(chatbot_api.ai_firewall, "guard", lambda *a, **k: SimpleNamespace(
        max_output_tokens=1800, plan="free"))
    monkeypatch.setattr(chatbot_api.ai_firewall, "record_tokens", lambda *a, **k: None)
    monkeypatch.setattr(chatbot_api, "_safety_check_user_message", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_safety_check_assistant_reply", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_load_tutor_memory", lambda: {"id": 1, "profile_json": "{}"})
    monkeypatch.setattr(chatbot_api, "_build_personalization_prompt", lambda **k: None)
    monkeypatch.setattr(chatbot_api, "_prepare_adaptive_turn", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_load_user_identity", lambda: {})
    monkeypatch.setattr(chatbot_api, "_save_tutor_profile", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_record_adaptive_turn", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_ensure_conversation", lambda *a: {"id": 5, "title": "Existing chat"})
    monkeypatch.setattr(chatbot_api, "_save_conversation", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_premium_tutor_turn", lambda *a: None)
    monkeypatch.setattr(chatbot_api, "_llm_chat", lambda **k: model_reply)


def test_the_counter_runs_for_a_signed_in_student(user_id):
    allowed, remaining, limit = _check(user_id)
    assert allowed is True and limit == 50 and remaining == 49


def test_the_counter_resets_on_the_first_of_next_month(user_id):
    _check(user_id)
    with App.app.app_context():
        user = db.session.get(User, user_id)
        assert user.monthly_tutor_messages == 1
        assert user.tutor_reset_date > datetime.utcnow()
        assert user.tutor_reset_date.day == 1


def test_the_limit_is_enforced(user_id):
    with App.app.app_context():
        user = db.session.get(User, user_id)
        user.monthly_tutor_messages = 50
        user.tutor_reset_date = datetime(2999, 1, 1)
        db.session.commit()
    assert _check(user_id) == (False, 0, 50)


def test_atomic_reservations_stop_at_the_free_limit(user_id):
    with App.app.app_context():
        user = db.session.get(User, user_id)
        user.monthly_tutor_messages = 49
        user.tutor_reset_date = datetime(2999, 1, 1)
        db.session.commit()
    assert _check(user_id) == (True, 0, 50)
    assert _check(user_id) == (False, 0, 50)
    assert _used(user_id) == 50


def test_a_paying_student_is_not_counted(user_id):
    from datetime import timedelta

    with App.app.app_context():
        user = db.session.get(User, user_id)
        user.paid_until = datetime.utcnow() + timedelta(days=10)
        user.monthly_tutor_messages = 500
        db.session.commit()
    assert _check(user_id) == (True, None, None)


def test_blank_normalized_message_does_not_consume_quota(tutor_client, user_id, monkeypatch):
    def unexpected_guard(*args, **kwargs):
        pytest.fail("invalid normalized messages should be rejected before the AI firewall")

    monkeypatch.setattr(chatbot_api.ai_firewall, "guard", unexpected_guard)
    response = tutor_client.post("/api/tutor", json={
        "messages": [{"role": "user", "content": "   "}],
    })
    assert response.status_code == 400
    assert _used(user_id) == 0


def test_firewall_block_does_not_consume_quota(tutor_client, user_id, monkeypatch):
    from ai_firewall import AIBlocked

    monkeypatch.setattr(chatbot_api.ai_firewall, "guard", lambda *a, **k: (_ for _ in ()).throw(
        AIBlocked("rate_limited", "Please wait before trying again.")))
    response = tutor_client.post("/api/tutor", json={
        "messages": [{"role": "user", "content": "Help me study fractions."}],
    })
    assert response.status_code == 429
    assert _used(user_id) == 0


def test_successful_tutor_request_consumes_exactly_one_message(tutor_client, user_id, monkeypatch):
    _stub_tutor_turn(monkeypatch)
    response = tutor_client.post("/api/tutor", json={
        "messages": [{"role": "user", "content": "Help me study fractions."}],
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["reply"] == "A helpful answer."
    assert _used(user_id) == 1


def test_provider_failure_releases_the_reserved_message(tutor_client, user_id, monkeypatch):
    from ai_provider import AIUnavailable

    _stub_tutor_turn(monkeypatch)
    monkeypatch.setattr(chatbot_api, "_llm_chat", lambda **k: (_ for _ in ()).throw(
        AIUnavailable("no provider configured")))
    response = tutor_client.post("/api/tutor", json={
        "messages": [{"role": "user", "content": "Help me study fractions."}],
    })
    assert response.status_code == 503
    assert response.get_json()["error"] == "ai_unavailable"
    assert _used(user_id) == 0
