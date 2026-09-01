"""HTTP surface for flashcards.

Everything is scoped to the signed-in student: every query in store.py takes
a user_id and every route here passes current_user's, so a deck id from
another account returns 404 rather than someone else's cards.
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from . import importers, store

logger = logging.getLogger(__name__)

flashcards_bp = Blueprint("flashcards", __name__)

#: An .apkg with a few thousand cards is normal; a 60 MB one is media we do
#: not import anyway.
MAX_UPLOAD_BYTES = 40 * 1024 * 1024


def _db():
    return current_app.extensions["sqlalchemy"]


def _uid() -> int | None:
    if not current_user.is_authenticated:
        return None
    store.ensure_tables(_db())
    return int(current_user.get_id())


def _auth_required():
    return jsonify({"error": "auth_required",
                    "message": "Sign in to use flashcards."}), 401


@flashcards_bp.route("/api/flashcards/decks", methods=["GET"])
def list_decks():
    uid = _uid()
    if uid is None:
        return _auth_required()
    return jsonify({"decks": store.list_decks(_db(), uid),
                    "stats": store.stats(_db(), uid)})


@flashcards_bp.route("/api/flashcards/decks", methods=["POST"])
def create_deck():
    uid = _uid()
    if uid is None:
        return _auth_required()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required", "message": "Give the deck a name."}), 400
    deck_id = store.create_deck(_db(), uid, name,
                                description=data.get("description") or "")
    return jsonify({"id": deck_id, "name": name}), 201


@flashcards_bp.route("/api/flashcards/decks/<int:deck_id>", methods=["PATCH"])
def patch_deck(deck_id: int):
    uid = _uid()
    if uid is None:
        return _auth_required()
    data = request.get_json(silent=True) or {}
    fields = {}
    if "name" in data:
        fields["name"] = (data["name"] or "").strip()[:200]
    if "description" in data:
        fields["description"] = (data["description"] or "")[:2000]
    if "new_per_day" in data:
        # Zero is meaningful (pause new cards); the ceiling stops a typo from
        # queueing a thousand unseen cards in one day.
        fields["new_per_day"] = max(0, min(int(data["new_per_day"] or 0), 500))
    if "target_retention" in data:
        # Below 0.7 the intervals get wild and above 0.97 the workload
        # explodes for almost no retention gain.
        fields["target_retention"] = max(0.7, min(float(data["target_retention"]), 0.97))
    if "archived" in data:
        fields["archived"] = bool(data["archived"])
    if not store.update_deck(_db(), uid, deck_id, **fields):
        return jsonify({"error": "not_found"}), 404
    return jsonify({"ok": True})


@flashcards_bp.route("/api/flashcards/decks/<int:deck_id>", methods=["DELETE"])
def remove_deck(deck_id: int):
    uid = _uid()
    if uid is None:
        return _auth_required()
    if not store.delete_deck(_db(), uid, deck_id):
        return jsonify({"error": "not_found"}), 404
    return jsonify({"ok": True})


@flashcards_bp.route("/api/flashcards/decks/<int:deck_id>/cards", methods=["POST"])
def add_cards(deck_id: int):
    uid = _uid()
    if uid is None:
        return _auth_required()
    if not store.get_deck(_db(), uid, deck_id):
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    cards = data.get("cards") or []
    if not isinstance(cards, list) or not cards:
        return jsonify({"error": "no_cards", "message": "Send at least one card."}), 400
    added = store.add_cards(_db(), uid, deck_id, cards[:importers.MAX_CARDS_PER_IMPORT])
    return jsonify({"added": added}), 201


@flashcards_bp.route("/api/flashcards/import", methods=["POST"])
def import_deck():
    """Import from an Anki .apkg upload, or pasted Quizlet / CSV text.

    Imports go straight into a new deck rather than a staging area: a student
    who exported the wrong set deletes the deck in one click, which is a
    cheaper mistake than a review step they have to complete every time.
    """
    uid = _uid()
    if uid is None:
        return _auth_required()

    try:
        if "file" in request.files:
            upload = request.files["file"]
            raw = upload.read(MAX_UPLOAD_BYTES + 1)
            if len(raw) > MAX_UPLOAD_BYTES:
                return jsonify({"error": "too_large",
                                "message": "That file is larger than 40 MB."}), 413
            name = (upload.filename or "deck.apkg")
            if name.lower().endswith(".apkg") or name.lower().endswith(".colpkg"):
                parsed = importers.parse_apkg(raw, fallback_name=name.rsplit(".", 1)[0])
            elif name.lower().endswith(".csv"):
                parsed = importers.parse_csv(raw.decode("utf-8", "replace"),
                                             name=name.rsplit(".", 1)[0])
            else:
                parsed = importers.parse_quizlet(raw.decode("utf-8", "replace"),
                                                 name=name.rsplit(".", 1)[0])
        else:
            data = request.get_json(silent=True) or {}
            text = data.get("text") or ""
            fmt = (data.get("format") or "quizlet").lower()
            deck_name = data.get("name") or "Imported deck"
            if fmt == "csv":
                parsed = importers.parse_csv(text, name=deck_name)
            else:
                parsed = importers.parse_quizlet(
                    text, name=deck_name,
                    term_sep=data.get("term_separator") or None,
                    row_sep=data.get("row_separator") or None)
    except importers.ImportError_ as exc:
        return jsonify({"error": "import_failed", "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("flashcard import blew up")
        return jsonify({"error": "import_failed",
                        "message": "That file could not be read."}), 400

    deck_id = store.create_deck(_db(), uid, parsed.name, source=parsed.source)
    added = store.add_cards(_db(), uid, deck_id, parsed.cards)
    return jsonify({"deck_id": deck_id, "name": parsed.name, "added": added,
                    "source": parsed.source, "warnings": parsed.warnings}), 201


@flashcards_bp.route("/api/flashcards/study", methods=["GET"])
def study_queue():
    uid = _uid()
    if uid is None:
        return _auth_required()
    deck_id = request.args.get("deck_id", type=int)
    limit = max(1, min(request.args.get("limit", 60, type=int), 200))
    queue = store.due_queue(_db(), uid, deck_id, limit)
    return jsonify({"cards": queue, "count": len(queue)})


@flashcards_bp.route("/api/flashcards/cards/<int:card_id>/grade", methods=["POST"])
def grade(card_id: int):
    uid = _uid()
    if uid is None:
        return _auth_required()
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_rating"}), 400
    if rating not in (1, 2, 3, 4):
        return jsonify({"error": "bad_rating",
                        "message": "Rating is 1 (again) to 4 (easy)."}), 400
    result = store.grade_card(_db(), uid, card_id, rating)
    if not result:
        return jsonify({"error": "not_found"}), 404
    return jsonify(result)


@flashcards_bp.route("/api/flashcards/cards/<int:card_id>/preview", methods=["GET"])
def preview_intervals(card_id: int):
    uid = _uid()
    if uid is None:
        return _auth_required()
    result = store.card_previews(_db(), uid, card_id)
    if result is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"intervals": result})
