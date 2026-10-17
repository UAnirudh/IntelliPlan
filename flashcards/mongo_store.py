"""Native MongoDB flashcard persistence with the existing API's return shapes."""
from __future__ import annotations

from datetime import datetime, timedelta

from intelliplan.persistence.codec import unpack_document, update_fields
from .scheduler import CardState, STATE_NEW, preview, review


def now():
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def public(row):
    if row is None:
        return None
    result = unpack_document(row)
    result.pop("_id", None)
    for field in ("created_at", "updated_at", "due", "last_review", "reviewed_at"):
        if result.get(field):
            result[field] = timestamp(result[field])
    return result


class MongoFlashcards:
    def __init__(self, store):
        self.store = store
        self.db = store.database

    def ensure_tables(self):
        for name in ("fc_decks", "fc_cards", "fc_reviews"):
            self.db[name].create_index("id", unique=True)
            self.db[name].create_index("user_id")
        self.db.fc_cards.create_index([("user_id", 1), ("due", 1)])
        self.db.fc_cards.create_index([("user_id", 1), ("deck_id", 1)])
        self.db.fc_reviews.create_index([("user_id", 1), ("reviewed_at", 1)])

    def create_deck(self, user_id, name, *, description="", source="manual"):
        def write(db, active):
            identifier = self.store.next_id("fc_decks", session=active)
            created = now().isoformat()
            db.fc_decks.insert_one({"id": identifier, "user_id": user_id,
                "name": name.strip()[:200] or "Untitled deck", "description": (description or "")[:2000],
                "source": source, "new_per_day": 20, "target_retention": 0.9,
                "archived": False, "created_at": created, "updated_at": created}, session=active)
            return identifier
        return self.store.transaction(write)

    def get_deck(self, user_id, deck_id):
        return public(self.db.fc_decks.find_one({"id": deck_id, "user_id": user_id}))

    def list_decks(self, user_id):
        counts = {row["_id"]: row for row in self.db.fc_cards.aggregate([
            {"$match": {"user_id": user_id, "suspended": {"$ne": True}}},
            {"$group": {"_id": "$deck_id", "total": {"$sum": 1},
                "new": {"$sum": {"$cond": [{"$eq": ["$state", STATE_NEW]}, 1, 0]}},
                "due": {"$sum": {"$cond": [{"$and": [
                    {"$ne": ["$state", None]}, {"$ne": ["$state", STATE_NEW]},
                    {"$ne": ["$due", None]}, {"$lte": ["$due", now().isoformat()]},
                ]}, 1, 0]}},
            }},
        ])}
        result = []
        for row in self.db.fc_decks.find({"user_id": user_id, "archived": {"$ne": True}}).sort("updated_at", -1):
            row = public(row)
            count = counts.get(row["id"], {})
            result.append({**{key: row.get(key) for key in ("id", "name", "description", "source", "new_per_day", "target_retention")},
                "total": count.get("total", 0), "new": count.get("new", 0), "due": count.get("due", 0)})
        return result

    def update_deck(self, user_id, deck_id, **fields):
        allowed = {key: value for key, value in fields.items()
                   if key in {"name", "description", "new_per_day", "target_retention", "archived"}}
        if not allowed:
            return False
        allowed["updated_at"] = now().isoformat()
        return self.db.fc_decks.update_one({"id": deck_id, "user_id": user_id}, update_fields(allowed)).matched_count == 1

    def delete_deck(self, user_id, deck_id):
        def write(db, active):
            if db.fc_decks.find_one({"id": deck_id, "user_id": user_id}, session=active) is None:
                return False
            ids = [r["id"] for r in db.fc_cards.find({"deck_id": deck_id, "user_id": user_id}, {"id": 1}, session=active)]
            db.fc_reviews.delete_many({"user_id": user_id, "card_id": {"$in": ids}}, session=active)
            db.fc_cards.delete_many({"deck_id": deck_id, "user_id": user_id}, session=active)
            db.fc_decks.delete_one({"id": deck_id, "user_id": user_id}, session=active)
            return True
        return self.store.transaction(write)

    def add_cards(self, user_id, deck_id, cards):
        if not cards:
            return 0
        normalized = []
        for card in cards:
            get = card.get if isinstance(card, dict) else lambda key, default=None: getattr(card, key, default)
            front, back = str(get("front") or "").strip(), str(get("back") or "").strip()
            if not front or not back:
                continue
            normalized.append({"deck_id": deck_id, "user_id": user_id, "front": front, "back": back,
                "extra": str(get("extra") or "")[:4000], "tags": " ".join(str(t) for t in (get("tags") or []))[:500],
                "card_type": get("card_type") or "basic", "state": STATE_NEW, "stability": 0.0,
                "difficulty": 0.0, "reps": 0, "lapses": 0, "step": 0, "suspended": False,
                "due": now().isoformat(), "created_at": now().isoformat(), "last_review": None})
        if not normalized:
            return 0

        def write(db, active):
            if db.fc_decks.find_one({"id": deck_id, "user_id": user_id}, session=active) is None:
                return 0
            identifiers = self.store.reserve_ids("fc_cards", len(normalized), session=active)
            rows = [{**row, "id": identifier} for row, identifier in zip(normalized, identifiers)]
            db.fc_cards.insert_many(rows, session=active)
            db.fc_decks.update_one({"id": deck_id, "user_id": user_id}, {"$set": {"updated_at": now().isoformat()}}, session=active)
            return len(rows)
        return self.store.transaction(write)

    def remaining_new_today(self, user_id, deck_id):
        scope = {"user_id": user_id, **({"id": deck_id} if deck_id else {})}
        cap = sum(int(row.get("new_per_day") or 0) for row in self.db.fc_decks.find(scope))
        review_scope = {"user_id": user_id, "state_before": STATE_NEW,
                        "reviewed_at": {"$gte": now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}}
        if deck_id:
            review_scope["card_id"] = {"$in": [row["id"] for row in self.db.fc_cards.find({"user_id": user_id, "deck_id": deck_id}, {"id": 1})]}
        started = self.db.fc_reviews.distinct("card_id", review_scope)
        return max(0, cap - len(started))

    def due_queue(self, user_id, deck_id=None, limit=60):
        if limit <= 0:
            return []
        from .store import _card_dict
        scope = {"user_id": user_id, "suspended": {"$ne": True}, **({"deck_id": deck_id} if deck_id else {})}
        due = self.db.fc_cards.find({**scope, "state": {"$nin": [STATE_NEW, None], "$exists": True}, "due": {"$ne": None, "$lte": now().isoformat()}}).sort("due", 1).limit(limit)
        result = [_card_dict(public(row)) for row in due]
        allowance = min(limit - len(result), self.remaining_new_today(user_id, deck_id))
        if allowance > 0:
            new = self.db.fc_cards.find({**scope, "state": STATE_NEW}).sort("id", 1).limit(allowance)
            result.extend(_card_dict(public(row)) for row in new)
        return result

    def due_forecast(self, user_id, start, days=7):
        first = start.date()
        end = datetime.combine(first + timedelta(days=max(1, days)), datetime.min.time())
        result = {}
        for row in self.db.fc_cards.find({"user_id": user_id, "suspended": {"$ne": True},
                "state": {"$nin": [STATE_NEW, None], "$exists": True}, "due": {"$ne": None, "$lt": end.isoformat()}}, {"due": 1}):
            day = max(first, timestamp(row["due"]).date()).isoformat()
            result[day] = result.get(day, 0) + 1
        return result

    def grade_card(self, user_id, card_id, rating):
        def write(db, active):
            row = public(db.fc_cards.find_one({"id": card_id, "user_id": user_id}, session=active))
            if row is None:
                return None
            deck = db.fc_decks.find_one({"id": row["deck_id"], "user_id": user_id}, session=active)
            if deck is None:
                return None
            state = self.state(row)
            reviewed = now()
            elapsed = (reviewed - state.last_review).total_seconds() / 86400 if state.last_review else 0
            scheduled = review(state, int(rating), target_retention=float(deck.get("target_retention") or 0.9), now=reviewed)
            db.fc_cards.update_one({"id": card_id, "user_id": user_id}, {"$set": {
                "state": scheduled.state, "stability": scheduled.stability, "difficulty": scheduled.difficulty,
                "reps": scheduled.reps, "lapses": scheduled.lapses, "step": scheduled.step,
                "due": scheduled.due.isoformat(), "last_review": scheduled.last_review.isoformat(),
            }}, session=active)
            db.fc_reviews.insert_one({"id": self.store.next_id("fc_reviews", session=active),
                "card_id": card_id, "user_id": user_id, "rating": int(rating), "state_before": state.state,
                "elapsed_days": elapsed, "scheduled_days": scheduled.interval_days,
                "reviewed_at": reviewed.isoformat()}, session=active)
            return {"card_id": card_id, "state": scheduled.state, "due": scheduled.due.isoformat() + "Z",
                "interval_days": round(scheduled.interval_days, 4), "stability": round(scheduled.stability, 4),
                "difficulty": round(scheduled.difficulty, 4)}
        return self.store.transaction(write)

    @staticmethod
    def state(row):
        return CardState(state=row.get("state") or STATE_NEW, stability=float(row.get("stability") or 0),
            difficulty=float(row.get("difficulty") or 0), reps=int(row.get("reps") or 0),
            lapses=int(row.get("lapses") or 0), step=int(row.get("step") or 0), last_review=row.get("last_review"))

    def card_previews(self, user_id, card_id):
        row = public(self.db.fc_cards.find_one({"id": card_id, "user_id": user_id}))
        if row is None:
            return None
        deck = self.db.fc_decks.find_one({"id": row["deck_id"], "user_id": user_id})
        if deck is None:
            return None
        return {str(grade): round(value, 4) for grade, value in preview(self.state(row),
                 target_retention=float(deck.get("target_retention") or 0.9)).items()}

    def stats(self, user_id):
        recent = {"user_id": user_id, "reviewed_at": {"$gte": (now() - timedelta(days=7)).isoformat()}}
        reviewed = self.db.fc_reviews.count_documents(recent)
        again = self.db.fc_reviews.count_documents({**recent, "rating": 1})
        return {"cards": self.db.fc_cards.count_documents({"user_id": user_id}),
            "due": self.db.fc_cards.count_documents({"user_id": user_id, "suspended": {"$ne": True},
                "state": {"$nin": [STATE_NEW, None], "$exists": True}, "due": {"$ne": None, "$lte": now().isoformat()}}),
            "new": self.db.fc_cards.count_documents({"user_id": user_id, "state": STATE_NEW}),
            "reviews_this_week": reviewed, "recall_rate": round(1 - again / reviewed, 3) if reviewed else None}
