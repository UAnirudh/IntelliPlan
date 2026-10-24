"""Exercise the registered photo routes with real auth, policy and deck storage."""

import io
import json
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import delete, select, text

import App
from App import User, db
import ai_provider
import ai_firewall
import plani_safety
from flashcards import store, photo_api
from intelliplan.learning import policy

SOURCE = "Mitosis produces two genetically identical daughter cells. DNA is copied before cell division."
CARD = {
    "front": "What does mitosis produce?",
    "back": "Two genetically identical daughter cells.",
    "source_quote": "Mitosis produces two genetically identical daughter cells.",
}
QUIZ = {
    "question": "Which outcome describes mitosis?",
    "options": [
        "One different cell",
        "Two identical daughter cells",
        "Four gametes",
        "No daughter cells",
    ],
    "answer": 1,
    "explanation": "Mitosis produces two genetically identical daughter cells.",
    "source_quote": CARD["source_quote"],
}


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    uid_list = []
    with App.app.app_context():
        db.create_all()
        store.ensure_tables(db)
        ai_firewall.ensure_tables()
    with App.app.test_client() as client:

        def account(**changes):
            with App.app.app_context():
                user = User(
                    email=f"photo+{uuid.uuid4().hex}@example.com",
                    password_hash="test-only",
                    birth_year=2000,
                    ai_personalization_opt_in=True,
                    role="student",
                )
                for key, value in changes.items():
                    setattr(user, key, value)
                db.session.add(user)
                db.session.commit()
                uid = user.id
                uid_list.append(uid)
            with client.session_transaction() as session:
                session["_user_id"] = str(uid)
                session["_fresh"] = True
            return uid

        client.account = account
        monkeypatch.setattr(plani_safety, "classify", lambda *a, **kw: None)
        monkeypatch.setattr(
            ai_provider,
            "vision",
            lambda **kw: json.dumps(
                {"title": "Cell division", "text": SOURCE, "warnings": []}
            ),
        )
        monkeypatch.setattr(
            ai_provider,
            "chat_json",
            lambda *a, **kw: {"title": "Cell division", "items": [dict(CARD)]},
        )
        yield client
    with App.app.app_context():
        for table in (store.REVIEWS, store.CARDS, store.DECKS):
            db.session.execute(delete(table).where(table.c.user_id.in_(uid_list)))
        User.query.filter(User.id.in_(uid_list)).delete(synchronize_session=False)
        for uid in uid_list:
            db.session.execute(
                text("DELETE FROM ai_quota_counters WHERE subject = :subject"),
                {"subject": f"user:{uid}"},
            )
        db.session.commit()
    App.limiter.enabled = True


def photo():
    data = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(data, format="PNG")
    data.seek(0)
    return {"image": (data, "notes.png")}


def generate(client, **changes):
    return client.post(
        "/api/flashcards/photo/generate",
        json={"source": SOURCE, "mode": "flashcards", "count": 4, **changes},
    )


@pytest.mark.parametrize("action", ["extract", "generate", "save"])
def test_photo_routes_require_authentication(client, action):
    assert client.post("/api/flashcards/photo/" + action, json={}).status_code == 401


@pytest.mark.parametrize("role", ["parent", "teacher"])
def test_adult_accounts_do_not_enter_student_photo_tools(client, role):
    client.account(role=role)
    assert generate(client).status_code == 403
    assert client.get("/flashcards/photo").status_code == 302


@pytest.mark.parametrize("capability", ["review", "study", "uploads", "tutor"])
@pytest.mark.parametrize("action", ["extract", "generate", "save"])
def test_each_photo_action_obeys_central_school_capabilities(
    client, monkeypatch, capability, action
):
    client.account()
    original = policy.decision
    monkeypatch.setattr(
        policy,
        "decision",
        lambda user, key: (
            (False, "school_restricted") if key == capability else original(user, key)
        ),
    )
    response = client.post("/api/flashcards/photo/" + action, json={})
    assert response.status_code == 403
    assert response.get_json()["feature"] == capability


def test_assessment_protection_blocks_generated_quiz_and_save(client, monkeypatch):
    client.account()
    original = policy.effective
    monkeypatch.setattr(
        policy,
        "effective",
        lambda user: {**original(user), "assessment_protection": True},
    )
    assert generate(client, mode="quiz").get_json()["error"] == "assessment_protection"
    assert client.post("/api/flashcards/photo/save", json={}).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [{"birth_year": None}, {"birth_year": 2020}, {"ai_personalization_opt_in": False}],
)
def test_photo_processing_requires_established_age_and_consent(client, changes):
    client.account(**changes)
    assert generate(client).status_code == 403


def test_school_can_disable_groq_processing(client, monkeypatch):
    client.account()
    monkeypatch.setattr(photo_api, "permitted_processing", lambda provider: False)
    assert client.post("/api/flashcards/photo/extract", data=photo()).status_code == 403


def test_extract_uses_disclosed_groq_provider_and_no_store(client, monkeypatch):
    client.account()
    calls = []

    def vision(**kwargs):
        calls.append(kwargs)
        return json.dumps(
            {
                "title": "Cell division",
                "text": SOURCE,
                "warnings": ["Check the diagram."],
            }
        )

    monkeypatch.setattr(ai_provider, "vision", vision)
    response = client.post("/api/flashcards/photo/extract", data=photo())
    assert response.status_code == 200
    assert response.get_json()["text"] == SOURCE
    assert response.get_json()["image_stored"] is False
    assert response.headers["Cache-Control"] == "no-store, private"
    assert calls[0]["provider"] == "groq" and calls[0]["image_mime"] == "image/jpeg"
    assert 1 <= calls[0]["max_tokens"] <= 3000


def test_entire_ocr_result_is_screened_before_any_preview(client, monkeypatch):
    client.account()
    checked = []
    raw = json.dumps({"title": "Notes", "text": SOURCE, "warnings": ["unsafe suffix"]})
    monkeypatch.setattr(ai_provider, "vision", lambda **kw: raw)

    def classify(messages, **kwargs):
        checked.append(messages[-1]["content"])
        return "violence"

    monkeypatch.setattr(plani_safety, "classify", classify)
    response = client.post("/api/flashcards/photo/extract", data=photo())
    assert response.status_code == 403 and "text" not in response.get_json()
    assert checked == [raw]


def test_safety_outage_withholds_ocr_and_generation(client, monkeypatch):
    client.account()

    def unavailable(*a, **kw):
        raise plani_safety.SafetyUnavailable("test")

    monkeypatch.setattr(plani_safety, "classify", unavailable)
    assert client.post("/api/flashcards/photo/extract", data=photo()).status_code == 503
    assert generate(client).status_code == 503


def test_unsafe_source_prevents_generation_and_unsafe_output_is_withheld(
    client, monkeypatch
):
    client.account()
    calls = []
    monkeypatch.setattr(ai_provider, "chat_json", lambda *a, **kw: calls.append(1))
    monkeypatch.setattr(plani_safety, "classify", lambda *a, **kw: "violence")
    assert generate(client).status_code == 403 and not calls
    monkeypatch.setattr(
        ai_provider, "chat_json", lambda *a, **kw: {"title": "Cells", "items": [CARD]}
    )
    monkeypatch.setattr(
        plani_safety,
        "classify",
        lambda messages, **kw: (
            "violence" if messages[-1]["content"].startswith("{") else None
        ),
    )
    assert generate(client).status_code == 403


def test_photo_flashcards_save_into_existing_private_review_queue(client):
    client.account()
    generated = generate(client)
    assert generated.status_code == 200
    receipt = generated.get_json()["receipt"]
    response = client.post("/api/flashcards/photo/save", json={"receipt": receipt})
    assert response.status_code == 201 and response.get_json()["added"] == 1
    deck_id = response.get_json()["id"]
    queue = client.get(f"/api/flashcards/study?deck_id={deck_id}").get_json()
    assert queue["cards"][0]["front"] == CARD["front"]
    assert queue["cards"][0]["back"] == CARD["back"]
    again = client.post("/api/flashcards/photo/save", json={"receipt": receipt})
    assert again.get_json()["id"] == deck_id and again.get_json()["already_saved"]


def test_photo_quiz_has_feedback_and_can_be_saved_as_recall_cards(client, monkeypatch):
    client.account()
    monkeypatch.setattr(
        ai_provider, "chat_json", lambda *a, **kw: {"title": "Cells", "items": [QUIZ]}
    )
    response = generate(client, mode="quiz")
    assert (
        response.status_code == 200 and response.get_json()["items"][0]["answer"] == 1
    )
    saved = client.post(
        "/api/flashcards/photo/save", json={"receipt": response.get_json()["receipt"]}
    )
    assert saved.status_code == 201
    queue = client.get(
        "/api/flashcards/study?deck_id=" + str(saved.get_json()["id"])
    ).get_json()
    assert QUIZ["options"][1] in queue["cards"][0]["back"]


def test_receipt_cannot_be_forged_or_saved_by_another_student(client):
    client.account()
    receipt = generate(client).get_json()["receipt"]
    assert (
        client.post(
            "/api/flashcards/photo/save", json={"receipt": receipt + "broken"}
        ).status_code
        == 422
    )
    client.account()
    assert (
        client.post("/api/flashcards/photo/save", json={"receipt": receipt}).status_code
        == 404
    )


def test_saving_rechecks_safety_and_rejects_expired_receipt(client, monkeypatch):
    client.account()
    receipt = generate(client).get_json()["receipt"]
    monkeypatch.setattr(plani_safety, "classify", lambda *a, **kw: "violence")
    assert (
        client.post("/api/flashcards/photo/save", json={"receipt": receipt}).status_code
        == 403
    )
    monkeypatch.setattr(plani_safety, "classify", lambda *a, **kw: None)
    monkeypatch.setattr(photo_api, "RECEIPT_LIFETIME", -1)
    assert (
        client.post("/api/flashcards/photo/save", json={"receipt": receipt}).status_code
        == 422
    )


def test_failed_card_insert_rolls_back_new_deck(client, monkeypatch):
    uid = client.account()
    receipt = generate(client).get_json()["receipt"]
    original = db.session.execute

    def execute(statement, *args, **kwargs):
        if (
            getattr(getattr(statement, "table", None), "name", None) == "fc_cards"
            and statement.is_insert
        ):
            raise RuntimeError("simulated insert failure")
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(db.session, "execute", execute)
    assert (
        client.post("/api/flashcards/photo/save", json={"receipt": receipt}).status_code
        == 503
    )
    with App.app.app_context():
        assert (
            db.session.execute(
                select(store.DECKS.c.id).where(store.DECKS.c.user_id == uid)
            ).first()
            is None
        )


def test_groq_only_vision_never_routes_photo_to_gemini(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_provider, "_apply_account_hooks", lambda plan: "free")
    monkeypatch.setattr(ai_provider, "groq_vision_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(ai_provider, "gemini_available", lambda: True)
    monkeypatch.setattr(
        ai_provider,
        "_gemini_client",
        lambda: pytest.fail("Photo sent to unexpected provider"),
    )
    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: (
                    calls.append(kw)
                    or SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content='{"text":"notes"}'),
                                finish_reason="stop",
                            )
                        ]
                    )
                )
            )
        )
    )
    monkeypatch.setattr(ai_provider, "_groq_client", lambda key: fake)
    assert (
        ai_provider.vision(
            system_prompt="Read",
            user_text="Notes",
            image_b64="synthetic",
            provider="groq",
        )
        == '{"text":"notes"}'
    )
    assert calls[0]["model"] == "qwen/qwen3.8-27b"
    assert calls[0]["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
