"""The clarification gate and preset round-trip, through the real endpoint.

No AI is called: a vague request must be answered with questions *before* the
model is ever reached, so these run without an API key.
"""

import json

import pytest

import App
from App import SchedulerPreset, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    # The endpoint is capped at 10/hour; several of these tests post more than
    # that between them.
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            SchedulerPreset.query.delete()
            db.session.commit()
        yield c
        with App.app.app_context():
            SchedulerPreset.query.delete()
            db.session.commit()
    App.limiter.enabled = True


VAGUE = {"assignments": [], "custom_tasks": ["Study"]}
SPECIFIC = {"assignments": [{
    "title": "Hamlet essay draft", "course": "AP English", "due_date": "2026-08-05",
    "priority": "High", "estimated_time": 90}], "custom_tasks": []}


def post(client, payload):
    # Note the underscore: /generate-schedule is a redirecting alias.
    return client.post("/generate_schedule", json=payload)


# ── the gate ──────────────────────────────────────────────────────


def test_a_vague_request_is_asked_about_not_guessed_at(client):
    body = post(client, VAGUE).get_json()
    assert body["status"] == "needs_clarification"
    assert body["questions"]
    fields = {q["field"] for q in body["questions"]}
    assert "deliverable" in fields


def test_questions_carry_everything_the_ui_needs(client):
    q = post(client, VAGUE).get_json()["questions"][0]
    for k in ("id", "task", "field", "question", "why", "options",
              "placeholder", "allow_free_text"):
        assert k in q


def test_the_gate_can_be_skipped_explicitly(client):
    # skip_clarify must bypass questioning — it then proceeds to the AI path,
    # which without a key returns a service error rather than questions.
    body = post(client, {**VAGUE, "skip_clarify": True}).get_json()
    assert body["status"] != "needs_clarification"


def test_an_empty_request_still_errors_normally(client):
    body = post(client, {"assignments": [], "custom_tasks": []}).get_json()
    assert body["status"] == "error"


# ── presets ───────────────────────────────────────────────────────


ANSWER_TEXT = {"deliverable": "Redo the failed quiz", "subject": "Chemistry",
               "duration": "45 min", "deadline": "Friday"}


def answer_everything(client, payload=VAGUE, rounds=4):
    """Answer clarifying questions until the gate lets the request through.

    Questions are capped per round, so a fully blank task legitimately takes
    more than one pass. Returns the final response body.
    """
    body = post(client, payload).get_json()
    for _ in range(rounds):
        if body.get("status") != "needs_clarification":
            return body
        answers = {q["id"]: ANSWER_TEXT[q["field"]] for q in body["questions"]}
        body = post(client, {**payload, "clarifications": answers}).get_json()
    return body


def test_answering_saves_a_preset(client):
    qs = post(client, VAGUE).get_json()["questions"]
    answers = {q["id"]: ANSWER_TEXT[q["field"]] for q in qs}
    post(client, {**VAGUE, "clarifications": answers})
    with App.app.app_context():
        rows = SchedulerPreset.query.all()
        assert len(rows) == 1
        assert rows[0].task_key == "study"
        assert rows[0].label == "Study"
        assert rows[0].answers()


def test_answers_survive_a_second_round_of_questions(client):
    """Round one's answers must not be lost when round two is needed."""
    first = post(client, VAGUE).get_json()
    round1 = {q["id"]: ANSWER_TEXT[q["field"]] for q in first["questions"]}
    post(client, {**VAGUE, "clarifications": round1})
    with App.app.app_context():
        stored = SchedulerPreset.query.filter_by(task_key="study").one().answers()
    for q in first["questions"]:
        assert q["field"] in stored


def test_answering_everything_eventually_clears_the_gate(client):
    assert answer_everything(client).get("status") != "needs_clarification"


def test_a_saved_preset_stops_the_questions_being_asked_again(client):
    answer_everything(client)
    body = post(client, VAGUE).get_json()
    assert body["status"] != "needs_clarification", body.get("questions")


def test_presets_are_offered_back_with_the_questions(client):
    # Seed through the endpoint so the preset lands under the same owner the
    # endpoint reads with.
    first = post(client, VAGUE).get_json()
    subject_q = next((q for q in first["questions"] if q["field"] == "subject"), None)
    assert subject_q is not None
    post(client, {**VAGUE, "clarifications": {subject_q["id"]: "Chemistry"}})
    body = post(client, VAGUE).get_json()
    assert body["status"] == "needs_clarification"
    assert body["presets"]["study"]["answers"]["subject"] == "Chemistry"


def test_presets_merge_rather_than_overwrite(client):
    with App.app.app_context():
        App.save_scheduler_presets(
            {"study": {"label": "Study", "answers": {"subject": "Chemistry"}}})
        App.save_scheduler_presets(
            {"study": {"label": "Study", "answers": {"duration": "45 min"}}})
        row = SchedulerPreset.query.filter_by(task_key="study").one()
        assert row.answers() == {"subject": "Chemistry", "duration": "45 min"}


def test_a_specific_request_is_never_gated(client):
    body = post(client, SPECIFIC).get_json()
    assert body["status"] != "needs_clarification"


# ── management endpoints ──────────────────────────────────────────


def test_presets_can_be_listed(client):
    answer_everything(client)
    body = client.get("/scheduler/presets").get_json()
    assert body["status"] == "ok"
    assert "study" in body["presets"]


def test_a_preset_can_be_forgotten(client):
    answer_everything(client)
    r = client.post("/scheduler/presets/delete", json={"task_key": "study"})
    assert r.get_json()["deleted"] == 1
    assert client.get("/scheduler/presets").get_json()["presets"] == {}


def test_deleting_without_a_key_is_rejected(client):
    assert client.post("/scheduler/presets/delete", json={}).status_code == 400
