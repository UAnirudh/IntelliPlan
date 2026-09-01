"""Decks, the study queue and grading, over HTTP and against the database.

Two properties matter more than the rest here. Reviews come before new cards,
because a student shown new material first ends the session with the reviews
undone -- which is the backlog spaced repetition exists to prevent. And every
query is scoped to the account: a deck id from another student is a 404, not
somebody else's cards.
"""

import json

import pytest
from sqlalchemy import text

import App
from App import User, bcrypt, db
from flashcards import store

PASSWORD = "flashcards-test-pw"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            store.ensure_tables(db)
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    for table in ("fc_reviews", "fc_cards", "fc_decks"):
        db.session.execute(text(f"DELETE FROM {table}"))
    User.query.filter(User.email.like("fc+%")).delete(synchronize_session=False)
    db.session.commit()


def _account(email="fc+a@example.com"):
    with App.app.app_context():
        user = User(email=email,
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(client, email="fc+a@example.com"):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(_account(email))
        sess["_fresh"] = True


# ── Access ────────────────────────────────────────────────────────

def test_flashcards_require_an_account(client):
    assert client.get("/api/flashcards/decks").status_code == 401
    assert client.post("/api/flashcards/import", json={"text": "a\tb"}).status_code == 401


def test_another_students_deck_is_not_found(client):
    _login(client, "fc+owner@example.com")
    deck_id = client.post("/api/flashcards/decks", json={"name": "Mine"}).get_json()["id"]

    with client.session_transaction() as sess:
        sess["_user_id"] = str(_account("fc+other@example.com"))
    assert client.delete(f"/api/flashcards/decks/{deck_id}").status_code == 404
    assert client.patch(f"/api/flashcards/decks/{deck_id}",
                        json={"name": "Theirs"}).status_code == 404


# ── Decks and import ──────────────────────────────────────────────

def test_a_pasted_quizlet_set_becomes_a_deck(client):
    _login(client)
    res = client.post("/api/flashcards/import", json={
        "text": "mitosis\tidentical daughter cells\nmeiosis\tgametes",
        "name": "Cell division",
    })
    assert res.status_code == 201
    body = res.get_json()
    assert body["added"] == 2 and body["source"] == "quizlet"

    decks = client.get("/api/flashcards/decks").get_json()["decks"]
    assert decks[0]["name"] == "Cell division"
    assert decks[0]["total"] == 2 and decks[0]["new"] == 2


def test_an_unreadable_import_explains_itself(client):
    _login(client)
    res = client.post("/api/flashcards/import", json={"text": "no separators here"})
    assert res.status_code == 400
    assert "term and a definition" in res.get_json()["message"]


def test_deck_settings_are_clamped_to_sane_values(client):
    _login(client)
    deck_id = client.post("/api/flashcards/decks", json={"name": "Chem"}).get_json()["id"]
    client.patch(f"/api/flashcards/decks/{deck_id}",
                 json={"new_per_day": 9999, "target_retention": 0.999})
    with App.app.app_context():
        deck = store.get_deck(db, int(_current_uid(client)), deck_id)
    assert deck["new_per_day"] == 500
    assert deck["target_retention"] == pytest.approx(0.97)


def _current_uid(client):
    with client.session_transaction() as sess:
        return sess["_user_id"]


# ── Study loop ────────────────────────────────────────────────────

def test_grading_moves_the_card_and_writes_a_review(client):
    _login(client)
    client.post("/api/flashcards/import", json={"text": "photon\tquantum of light"})
    card = client.get("/api/flashcards/study").get_json()["cards"][0]

    res = client.post(f"/api/flashcards/cards/{card['id']}/grade", json={"rating": 3})
    assert res.status_code == 200
    body = res.get_json()
    assert body["state"] == "learning"
    assert body["interval_days"] > 0

    with App.app.app_context():
        logged = db.session.execute(
            text("SELECT rating, state_before FROM fc_reviews WHERE card_id = :c"),
            {"c": card["id"]}).first()
    assert logged and logged[0] == 3 and logged[1] == "new"


def test_the_button_previews_come_back_ordered(client):
    _login(client)
    client.post("/api/flashcards/import", json={"text": "entropy\tdisorder"})
    card = client.get("/api/flashcards/study").get_json()["cards"][0]
    intervals = client.get(
        f"/api/flashcards/cards/{card['id']}/preview").get_json()["intervals"]
    assert intervals["1"] <= intervals["2"] <= intervals["3"] <= intervals["4"]


def test_due_reviews_come_before_new_cards(client):
    """The ordering that keeps a backlog from forming."""
    _login(client)
    uid = int(_current_uid(client))
    client.post("/api/flashcards/import", json={"text": "old\tcard\nsecond\tcard"})
    with App.app.app_context():
        # Make one card an overdue review, leave the rest new.
        db.session.execute(text(
            "UPDATE fc_cards SET state = 'review', stability = 5, difficulty = 5, "
            "reps = 3, due = :due WHERE front = 'old'"),
            {"due": store._now().replace(year=2020)})
        db.session.commit()
    queue = client.get("/api/flashcards/study").get_json()["cards"]
    assert queue[0]["front"] == "old"
    assert queue[0]["state"] == "review"


def test_the_daily_new_card_cap_holds_back_a_large_import(client):
    """An 800-card Anki import must not put 800 cards in front of a student
    on the day they import it."""
    _login(client)
    rows = "\n".join(f"term{i}\tdefinition{i}" for i in range(60))
    deck_id = client.post("/api/flashcards/import",
                          json={"text": rows}).get_json()["deck_id"]
    client.patch(f"/api/flashcards/decks/{deck_id}", json={"new_per_day": 5})

    queue = client.get("/api/flashcards/study").get_json()["cards"]
    assert len(queue) == 5

    # Studying them uses the allowance up rather than refilling it.
    for card in queue:
        client.post(f"/api/flashcards/cards/{card['id']}/grade", json={"rating": 3})
    assert client.get("/api/flashcards/study?limit=60").get_json()["count"] <= 5


def test_deleting_a_deck_takes_its_cards_and_reviews(client):
    _login(client)
    deck_id = client.post("/api/flashcards/import",
                          json={"text": "a\tb"}).get_json()["deck_id"]
    card = client.get("/api/flashcards/study").get_json()["cards"][0]
    client.post(f"/api/flashcards/cards/{card['id']}/grade", json={"rating": 4})

    assert client.delete(f"/api/flashcards/decks/{deck_id}").status_code == 200
    with App.app.app_context():
        assert db.session.execute(text("SELECT COUNT(*) FROM fc_cards")).scalar() == 0
        assert db.session.execute(text("SELECT COUNT(*) FROM fc_reviews")).scalar() == 0


def test_stats_report_what_the_student_has_waiting(client):
    _login(client)
    client.post("/api/flashcards/import", json={"text": "x\ty\nz\tw"})
    stats = client.get("/api/flashcards/decks").get_json()["stats"]
    assert stats["cards"] == 2 and stats["new"] == 2
    assert stats["recall_rate"] is None  # nothing reviewed yet


def test_a_bad_rating_is_refused(client):
    _login(client)
    client.post("/api/flashcards/import", json={"text": "a\tb"})
    card = client.get("/api/flashcards/study").get_json()["cards"][0]
    assert client.post(f"/api/flashcards/cards/{card['id']}/grade",
                       json={"rating": 9}).status_code == 400
