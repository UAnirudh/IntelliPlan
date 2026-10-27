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
        App.StudyMastery.query.filter_by(user_id=uid).delete()
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


@pytest.mark.parametrize('body', [[], 'notes', {'content': None}, {'content': ['notes']}])
def test_malformed_generation_never_calls_provider(client, monkeypatch, body):
    called = []
    monkeypatch.setattr(App, 'ai_chat', lambda *a, **k: called.append(a))
    response = client.post('/study/generate', json=body)
    assert response.status_code == 400 and response.is_json
    assert not called


@pytest.mark.parametrize('result', [[], {}, {'title': 'Broken', 'questions': []},
    {'title': 'Broken', 'questions': [{'type': 'recall', 'question': 'Q?', 'answer': None}]}])
def test_unusable_generated_questions_are_not_a_success(client, monkeypatch, result):
    monkeypatch.setattr(App, 'ai_chat', lambda *a, **k: json.dumps(result))
    response = generate(client)
    assert response.is_json and response.json['status'] == 'error'


def test_negated_answer_is_not_upgraded_by_word_overlap(client, monkeypatch):
    monkeypatch.setattr(App, 'ai_chat', lambda *a, **k: json.dumps({
        'verdict': 'incorrect', 'score': 10, 'critique': 'The answer reverses the relationship.'}))
    response = client.post('/study/evaluate', json={
        'question': 'Does insulin decrease blood glucose?',
        'correct_answer': 'Insulin decreases blood glucose.',
        'user_answer': 'Insulin does not decrease blood glucose.', 'confidence': 'high'})
    assert response.json['evaluation']['verdict'] == 'incorrect'
    assert response.json['evaluation']['score'] == 10


@pytest.mark.parametrize('body', [[], {'question': []},
    {'question': 'Q', 'correct_answer': 'A', 'user_answer': 'A', 'confidence': []},
    {'question': 'Q', 'correct_answer': 'A', 'user_answer': 'x' * 6001}])
def test_malformed_evaluation_never_calls_provider(client, monkeypatch, body):
    called = []
    monkeypatch.setattr(App, 'ai_chat', lambda *a, **k: called.append(a))
    response = client.post('/study/evaluate', json=body)
    assert response.status_code == 400 and response.is_json
    assert not called


@pytest.mark.parametrize('score', [True, '90', -1, 1000, 85])
def test_invalid_or_conflicting_scores_are_not_accepted(client, monkeypatch, score):
    monkeypatch.setattr(App, 'ai_chat', lambda *a, **k: json.dumps({'verdict': 'incorrect', 'score': score}))
    response = client.post('/study/evaluate', json={
        'question': 'Q?', 'correct_answer': 'A', 'user_answer': 'B'})
    assert response.json['status'] == 'error'


@pytest.mark.parametrize('verdict,score', [('correct', 90), ('partial', 50), ('incorrect', 10)])
def test_first_review_is_persisted_with_initialized_counters(client, verdict, score):
    key = f'first-review-{uuid4().hex}'
    response = client.post('/study/mastery/update', json={
        'question_key': key, 'question_text': 'What is a cell?', 'answer_text': 'A unit of life.',
        'topic': 'Biology', 'verdict': verdict, 'score': score})
    assert response.json['status'] == 'ok'
    with App.app.app_context():
        row = App.StudyMastery.query.filter_by(question_key=key).one()
        assert row.times_seen == 1
        assert row.times_correct == int(verdict == 'correct')
        assert row.times_partial == int(verdict == 'partial')
        assert row.next_review and row.easiness_factor >= 1.3


@pytest.mark.parametrize('body', [[], {'question_key': ['invalid']},
    {'question_key': 'x', 'score': 'no'}, {'question_key': 'x', 'score': True},
    {'question_key': 'x', 'verdict': []}])
def test_malformed_review_is_a_json_validation_error(client, body):
    response = client.post('/study/mastery/update', json=body)
    assert response.status_code == 400 and response.json['status'] == 'error'
