"""Input handling for /study/generate.

Two things were wrong with the way this endpoint read its request body:

  * ``int(data.get("num_questions", 8))`` sat outside the try block, so a
    non-numeric value returned Flask's HTML 500 page. The study page does
    ``await r.json()`` on the response, so the student saw a JSON parser
    error rather than the endpoint's own "try again" message. Nothing
    bounded the value either -- a request for 500 questions was billed as a
    request for 500 questions.
  * ``mode`` was read, overridden to "casual" for guests, and then never
    used or returned. The page had already decided locally which session to
    launch, so the guest downgrade existed only inside the function.
"""

import json

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
    """A signed-in student. Guests take a separate path -- fewer questions,
    forced casual -- which is exercised by the guest tests at the bottom."""
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setattr(App, "ai_chat", lambda *a, **k: CANNED)
    monkeypatch.setattr(App, "_is_guest", lambda: False)
    with App.app.test_client() as c:
        yield c
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


def test_a_guest_who_picks_extreme_is_told_they_are_running_casual(guest):
    """The downgrade was already happening server-side; it just never left
    the function, so the page launched the fullscreen lock anyway."""
    body = generate(guest, mode="extreme").get_json()
    assert body["status"] == "ok"
    assert body["data"]["mode"] == "casual"


def test_a_guest_cannot_ask_for_more_questions_than_their_limit(guest, monkeypatch):
    asked = {}
    monkeypatch.setattr(
        App, "ai_chat",
        lambda msgs, **k: asked.update(prompt=msgs[0]["content"]) or CANNED,
    )
    generate(guest, num_questions=20)
    cap = App.GUEST_STUDY_LIMITS["max_questions"]
    assert f"exactly {cap} study questions" in asked["prompt"]
