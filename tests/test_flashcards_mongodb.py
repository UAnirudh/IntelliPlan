from datetime import datetime, timedelta, timezone

import pytest

from flashcards import store as flashcards
from intelliplan.persistence.mongo import MongoStore

mongomock = pytest.importorskip("mongomock")


@pytest.fixture
def db():
    client = mongomock.MongoClient()
    result = MongoStore("mongodb://test", "intelliplan_test", client_factory=lambda *a, **kw: client)
    # Unit tests exercise business behavior. Real replica-set transaction
    # rollback/retry is a separate required Atlas acceptance check.
    result.transaction = lambda callback: callback(result.database, None)
    result.database.users.insert_many([{'id': 1}, {'id': 2}])
    flashcards.ensure_tables(result)
    return result


def test_decks_cards_review_forecast_and_delete_use_native_mongo(db):
    deck = flashcards.create_deck(db, 1, "Biology")
    assert flashcards.add_cards(db, 1, deck, [{"front": "Q", "back": "A", "tags": ["cell"]}]) == 1
    assert flashcards.list_decks(db, 1)[0]["new"] == 1
    queue = flashcards.due_queue(db, 1, deck)
    assert queue[0]["front"] == "Q"
    assert flashcards.card_previews(db, 1, queue[0]["id"])
    reviewed = flashcards.grade_card(db, 1, queue[0]["id"], 3)
    assert reviewed["card_id"] == queue[0]["id"]
    assert flashcards.stats(db, 1)["reviews_this_week"] == 1
    assert flashcards.due_forecast(db, 1, datetime.now(timezone.utc).replace(tzinfo=None), 365)
    assert flashcards.delete_deck(db, 1, deck)
    assert flashcards.stats(db, 1)["cards"] == 0
    assert db.database.fc_reviews.count_documents({}) == 0


def test_owner_scoping_on_every_flashcard_operation(db):
    deck = flashcards.create_deck(db, 1, "Private")
    flashcards.add_cards(db, 1, deck, [{"front": "Q", "back": "A"}])
    card = flashcards.due_queue(db, 1)[0]["id"]
    assert flashcards.get_deck(db, 2, deck) is None
    assert flashcards.list_decks(db, 2) == []
    assert flashcards.due_queue(db, 2, deck) == []
    assert flashcards.add_cards(db, 2, deck, [{"front": "X", "back": "Y"}]) == 0
    assert flashcards.grade_card(db, 2, card, 3) is None
    assert flashcards.card_previews(db, 2, card) is None
    assert not flashcards.update_deck(db, 2, deck, name="Stolen")
    assert not flashcards.delete_deck(db, 2, deck)
    assert flashcards.get_deck(db, 1, deck)["name"] == "Private"


def test_new_card_daily_limit_and_overdue_reviews(db):
    deck = flashcards.create_deck(db, 1, "Deck")
    flashcards.update_deck(db, 1, deck, new_per_day=1)
    flashcards.add_cards(db, 1, deck, [{"front": str(i), "back": "A"} for i in range(3)])
    queue = flashcards.due_queue(db, 1)
    assert len(queue) == 1
    flashcards.grade_card(db, 1, queue[0]["id"], 3)
    assert flashcards.due_queue(db, 1) == []
    db.database.fc_cards.update_one({"id": queue[0]["id"]}, {"$set": {
        "due": (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)).isoformat()}})
    assert flashcards.due_queue(db, 1)[0]["id"] == queue[0]["id"]


def test_listing_and_editing_migrated_compressed_fields(db):
    from intelliplan.persistence.codec import pack_document
    row = {"id": 100, "user_id": 1, "name": "Migrated", "description": "A" * 10000,
           "source": "manual", "archived": False, "new_per_day": 20, "target_retention": 0.9}
    db.database.fc_decks.insert_one(pack_document(row))
    assert flashcards.list_decks(db, 1)[0]["description"] == "A" * 10000
    assert flashcards.update_deck(db, 1, 100, description="new")
    assert flashcards.get_deck(db, 1, 100)["description"] == "new"
    assert flashcards.update_deck(db, 1, 100, description="B" * 10000)
    assert flashcards.get_deck(db, 1, 100)["description"] == "B" * 10000


def test_null_state_preserves_sql_review_filter_semantics(db):
    deck = flashcards.create_deck(db, 1, "Legacy")
    db.database.fc_cards.insert_one({"id": 100, "user_id": 1, "deck_id": deck,
        "state": None, "due": "2020-01-01T00:00:00", "suspended": False})
    assert flashcards.due_queue(db, 1) == []
    assert flashcards.list_decks(db, 1)[0]["due"] == 0
    assert flashcards.stats(db, 1)["due"] == 0
    assert flashcards.due_forecast(db, 1, datetime.now()) == {}


def test_large_import_preserves_all_cards_and_seeded_ids(db):
    deck = flashcards.create_deck(db, 1, "Large import")
    db.seed_id("fc_cards", 5000)
    assert flashcards.add_cards(db, 1, deck, [{"front": str(i), "back": "A"} for i in range(800)]) == 800
    assert flashcards.list_decks(db, 1)[0]["total"] == 800
    identifiers = db.database.fc_cards.distinct("id")
    assert len(identifiers) == 800
    assert min(identifiers) == 5001
    assert max(identifiers) == 5800
    assert flashcards.add_cards(db, 1, deck, None) == 0
