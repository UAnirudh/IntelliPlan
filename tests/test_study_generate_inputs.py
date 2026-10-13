"""Input handling for /study/generate.

Two things were wrong with the way this endpoint read its request body:

  * ``int(data.get("num_questions", 8))`` sat outside the try block, so a
    non-numeric value returned Flask's HTML 500 page. The study page does
    ``await r.json()`` on the response, so the student saw a JSON parser
    error rather than the endpoint's own "try again" message. Nothing
    bounded the value either -- a request for 500 questions was billed as a
    request for 500 questions.
  * ``mode`` was read and then not returned, so the page decided locally
    which session to launch. Generation now requires a real signed-in
    identity so school rules can be checked; anonymous requests are refused.
"""

import json
from uuid import uuid4

import pytest

import App


CANNED = json.dumps({
    "title": "Cell Biology",
    "key_concepts": [{"term": "Mitochondrion", "definition": "Powerhouse."}],
    "questions": [{"id": 1, "type": "recall", "question": "Q?", "answer": "A."}],
})

MATERIAL = "The mitochondrion is the powerhouse of the cell. " * 5


@pytest.fixture
def client(monkeypatch):
    """Use an actual signed-in identity, not only a mocked guest flag."""
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setattr(App, "ai_chat", lambda *a, **k: CANNED)
    monkeypatch.setattr(App, "_is_guest", lambda: False)
    with App.app.app_context():
        row = App.User(email=f'study-input-{uuid4().hex}@example.test', password_hash='x', birth_year=2000)
        App.db.session.add(row)
        App.db.session.commit()
        uid = row.id
    with App.app.test_client() as c:
        with c.session_transaction() as session:
            session['_user_id'] = str(uid)
            session['_fresh'] = True
        yield c
    with App.app.app_context():
        App.User.query.filter_by(id=uid).delete()
        App.db.session.commit()
    App.limiter.enabled = True


@pytest.fixture
def guest(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setattr(App, "ai_chat", lambda *a, **k: CANNED)
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


def generate(client, **body):
    payload = {"content": MATERIAL}
    payload.update(body)
    return client.post("/study/generate", json=payload)


def test_a_normal_request_succeeds(client):
    body = generate(client).get_json()
    assert body["status"] == "ok"
    assert body["data"]["questions"]


def test_a_non_numeric_question_count_is_an_answer_not_a_crash(client):
    resp = generate(client, num_questions="eight")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_a_missing_question_count_is_fine(client):
    assert generate(client, num_questions=None).get_json()["status"] == "ok"


def test_an_absurd_question_count_is_clamped(client, monkeypatch):
    asked = {}
    monkeypatch.setattr(
        App, "ai_chat",
        lambda msgs, **k: asked.update(prompt=msgs[0]["content"]) or CANNED,
    )
    generate(client, num_questions=500)
    assert "exactly 20 study questions" in asked["prompt"]


def test_a_zero_question_count_is_floored_to_one(client, monkeypatch):
    asked = {}
    monkeypatch.setattr(
        App, "ai_chat",
        lambda msgs, **k: asked.update(prompt=msgs[0]["content"]) or CANNED,
    )
    generate(client, num_questions=0)
    assert "exactly 1 study questions" in asked["prompt"]


def test_empty_content_is_rejected_with_a_readable_message(client):
    resp = client.post("/study/generate", json={"content": "   "})
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "error"


def test_the_response_names_the_mode_the_session_will_run_in(client):
    body = generate(client, mode="serious").get_json()
    assert body["data"]["mode"] == "serious"


def test_an_unknown_mode_falls_back_to_casual(client):
    body = generate(client, mode="hyperdrive").get_json()
    assert body["data"]["mode"] == "casual"


# ── Guests ────────────────────────────────────────────────────────────


def test_guest_generation_requires_identity_even_with_extreme_mode(guest, monkeypatch):
    called = []
    monkeypatch.setattr(App, 'ai_chat', lambda *a, **k: called.append(a))
    response = generate(guest, mode='extreme')
    assert response.status_code == 401
    assert response.get_json()['error'] == 'authentication_required'
    assert not called


def test_guest_question_count_cannot_bypass_identity(guest, monkeypatch):
    called = []
    monkeypatch.setattr(App, 'ai_chat', lambda *a, **k: called.append(a))
    assert generate(guest, num_questions=20).status_code == 401
    assert not called
