"""Native adaptive learner records over the migration's existing collections."""
from __future__ import annotations

from datetime import datetime
import json

from pymongo import ReturnDocument

from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from time_utils import utcnow


PROFILE = "adaptive_student_profile"
MASTERY = "adaptive_subject_mastery"
MISTAKE = "adaptive_mistake_pattern"
MEMORY = "adaptive_learner_memory"
IMPORT = "adaptive_memory_import"
SUMMARY = "adaptive_session_summary"
COLLECTIONS = (PROFILE, MASTERY, MISTAKE, MEMORY, IMPORT, SUMMARY)


def public(row):
    result = unpack_document(row)
    result.pop("_id", None)
    result.pop("_ip_encoding", None)
    for key in ("created_at", "updated_at", "last_practiced", "last_seen",
                "last_analyzed_at", "started_at", "ended_at"):
        if isinstance(result.get(key), str):
            result[key] = datetime.fromisoformat(result[key])
    return result


class MongoTutor:
    def __init__(self, store):
        self.store = store
        self.db = store.database

    def ensure_indexes(self):
        for name in COLLECTIONS:
            self.db[name].create_index("id", unique=True)
        for field in ("user_id", "guest_session_id"):
            kind = "number" if field == "user_id" else "string"
            self.db[PROFILE].create_index(field, unique=True,
                partialFilterExpression={field: {"$type": kind}})
        self.db[MASTERY].create_index([("profile_id", 1), ("subject", 1), ("topic", 1)], unique=True)
        self.db[MISTAKE].create_index(
            [("profile_id", 1), ("subject", 1), ("topic", 1), ("mistake_type", 1)],
            unique=True, partialFilterExpression={"resolved": False})
        self.db[MEMORY].create_index("profile_id", unique=True)
        self.db[SUMMARY].create_index([("profile_id", 1), ("conversation_id", 1)],
            unique=True, partialFilterExpression={"conversation_id": {"$type": "number"}})
        for name in (MASTERY, MISTAKE, IMPORT, SUMMARY):
            self.db[name].create_index("profile_id")

    def _owner(self):
        from adaptive_tutor.store import current_owner
        user_id, guest_id = current_owner()
        return {"user_id": user_id} if user_id is not None else {"guest_session_id": guest_id, "user_id": None}

    def _profile(self, profile_id, *, active=None, write=False):
        query = {"id": int(profile_id), **self._owner()}
        if write:
            # Every mutation touches the profile in the same transaction as
            # child writes. Concurrent deletion/change gets a write conflict.
            row = self.db[PROFILE].find_one_and_update(query, {"$inc": {"_revision": 1}},
                return_document=ReturnDocument.AFTER, session=active)
        else:
            row = self.db[PROFILE].find_one(query, session=active)
        if row is None:
            raise LookupError("Learner profile does not belong to this session")
        return row

    def _insert(self, name, values, *, active=None):
        values = {"id": self.store.next_id(name, session=active), **values}
        self.db[name].insert_one(pack_document(values), session=active)
        return values["id"]

    def _rows(self, name, profile_id, sort, limit, extra=None):
        self._profile(profile_id)
        limit = int(limit)
        if limit == 0:
            return []
        if limit < 0:
            raise ValueError("Learner list limit must be nonnegative")
        return [public(row) for row in self.db[name].find(
            {"profile_id": int(profile_id), **(extra or {})}).sort(sort, -1).limit(limit)]

    def get_or_create_profile(self):
        from adaptive_tutor.store import _profile_to_dict
        owner = self._owner()
        row = self.db[PROFILE].find_one(owner)
        if row is None:
            now = utcnow().isoformat()
            values = {"id": self.store.next_id(PROFILE), "user_id": None,
                "guest_session_id": None, **owner, "grade_level": None,
                "subjects_json": "[]", "short_term_goals": None, "long_term_goals": None,
                "explanation_style": "balanced", "explanation_length": "medium",
                "difficulty_level": "medium", "interests_json": "[]",
                "onboarding_completed": False, "learning_modality": "auto",
                "created_at": now, "updated_at": now}
            # The unique owner index makes concurrent first requests converge.
            from pymongo.errors import DuplicateKeyError
            try:
                self.db[PROFILE].update_one(owner, {"$setOnInsert": values}, upsert=True)
            except DuplicateKeyError:
                if self.db[PROFILE].find_one(owner) is None:
                    raise
            row = self.db[PROFILE].find_one(owner)
        return _profile_to_dict(public(row))

    def update_profile(self, profile_id, payload):
        from adaptive_tutor.store import _profile_to_dict, profile_patch
        values = profile_patch(payload)
        values["updated_at"] = values["updated_at"].isoformat()
        query = {"id": int(profile_id), **self._owner()}
        row = self.db[PROFILE].find_one_and_update(query, update_fields(values),
            return_document=ReturnDocument.AFTER)
        if row is None:
            raise LookupError("Learner profile does not belong to this session")
        return _profile_to_dict(public(row))

    def update_mastery(self, profile_id, subject, topic, correct):
        subject = (subject or "General").strip()[:120]
        topic = (topic or subject).strip()[:200]

        def write(db, active):
            self._profile(profile_id, active=active, write=True)
            query = {"profile_id": int(profile_id), "subject": subject, "topic": topic}
            row = db[MASTERY].find_one(query, session=active)
            now = utcnow().isoformat()
            if row is None:
                self._insert(MASTERY, {**query, "total_attempts": 1,
                    "correct_attempts": int(bool(correct)), "mastery_score": 60.0 if correct else 20.0,
                    "confidence_level": 20.0, "last_practiced": now,
                    "created_at": now, "updated_at": now}, active=active)
            else:
                attempts = int(row["total_attempts"]) + 1
                corrects = int(row["correct_attempts"]) + int(bool(correct))
                score = float(row["mastery_score"]) * 0.7 + corrects / attempts * 100 * 0.3
                db[MASTERY].update_one({"_id": row["_id"]}, {"$set": {
                    "total_attempts": attempts, "correct_attempts": corrects,
                    "mastery_score": round(score, 2), "confidence_level": float(min(100, 20 + attempts * 8)),
                    "last_practiced": now, "updated_at": now}}, session=active)
        self.store.transaction(write)

    def list_mastery(self, profile_id, limit=20):
        return self._rows(MASTERY, profile_id, "mastery_score", limit)

    def record_mistake(self, profile_id, mistake):
        subject = str(mistake.get("subject") or "General").strip()[:120]
        description = str(mistake.get("description") or "").strip()[:2000]
        if not description:
            return
        query = {"profile_id": int(profile_id), "subject": subject,
            "topic": str(mistake.get("topic") or subject).strip()[:200],
            "mistake_type": str(mistake.get("mistakeType") or mistake.get("mistake_type") or "misconception").strip()[:160],
            "resolved": False}

        def write(db, active):
            self._profile(profile_id, active=active, write=True)
            now = utcnow().isoformat()
            row = db[MISTAKE].find_one(query, session=active)
            if row is None:
                self._insert(MISTAKE, {**query, "description": description,
                    "frequency": 1, "last_seen": now, "created_at": now}, active=active)
            else:
                db[MISTAKE].update_one({"_id": row["_id"]}, {
                    "$inc": {"frequency": 1}, "$set": {"last_seen": now}}, session=active)
        self.store.transaction(write)

    def list_mistakes(self, profile_id, limit=15, include_resolved=False):
        return self._rows(MISTAKE, profile_id, "frequency", limit,
                          None if include_resolved else {"resolved": False})

    def resolve_mistake(self, profile_id, mistake_id):
        def write(db, active):
            self._profile(profile_id, active=active, write=True)
            return bool(db[MISTAKE].update_one({"profile_id": int(profile_id), "id": int(mistake_id)},
                {"$set": {"resolved": True}}, session=active).matched_count)
        return self.store.transaction(write)

    def get_learner_memory(self, profile_id):
        from adaptive_tutor.store import learner_memory_view
        self._profile(profile_id)
        row = self.db[MEMORY].find_one({"profile_id": int(profile_id)})
        return learner_memory_view(public(row)) if row else None

    def upsert_learner_memory(self, profile_id, analysis, source_count, blended_scores, detected_modality):
        from adaptive_tutor.store import learner_memory_values
        values = learner_memory_values(analysis, source_count, blended_scores, detected_modality)
        values["last_analyzed_at"] = values["last_analyzed_at"].isoformat()

        def write(db, active):
            self._profile(profile_id, active=active, write=True)
            row = db[MEMORY].find_one({"profile_id": int(profile_id)}, session=active)
            if row is None:
                self._insert(MEMORY, {**values, "profile_id": int(profile_id), "evidence_count": 1}, active=active)
            else:
                patch = update_fields(values)
                patch["$inc"] = {"evidence_count": 1}
                db[MEMORY].update_one({"_id": row["_id"]}, patch, session=active)
        self.store.transaction(write)

    def save_memory_import(self, profile_id, provider, raw_text, source_label=None,
                           extracted_summary=None, learner_signals=None):
        def write(db, active):
            self._profile(profile_id, active=active, write=True)
            return self._insert(IMPORT, {"profile_id": int(profile_id),
                "provider": str(provider or "unknown")[:64],
                "source_label": str(source_label)[:160] if source_label else None,
                "raw_text": str(raw_text)[:200000], "extracted_summary": extracted_summary,
                "learner_signals_json": json.dumps(learner_signals) if learner_signals else None,
                "created_at": utcnow().isoformat()}, active=active)
        return self.store.transaction(write)

    def list_memory_imports(self, profile_id, limit=10):
        return self._rows(IMPORT, profile_id, "created_at", limit)

    def delete_memory_import(self, profile_id, import_id):
        def write(db, active):
            self._profile(profile_id, active=active, write=True)
            return bool(db[IMPORT].delete_one({"profile_id": int(profile_id), "id": int(import_id)},
                session=active).deleted_count)
        return self.store.transaction(write)

    def save_session_summary(self, profile_id, conversation_id, summary):
        from adaptive_tutor.store import session_summary_values
        values = session_summary_values(summary)
        values["ended_at"] = values["ended_at"].isoformat()

        def write(db, active):
            self._profile(profile_id, active=active, write=True)
            query = {"profile_id": int(profile_id), "conversation_id": conversation_id}
            row = db[SUMMARY].find_one(query, session=active) if conversation_id is not None else None
            if row is None:
                self._insert(SUMMARY, {**query, **values, "started_at": values["ended_at"]}, active=active)
            else:
                db[SUMMARY].update_one({"_id": row["_id"]}, update_fields(values), session=active)
        self.store.transaction(write)

    def list_session_summaries(self, profile_id, limit=5):
        from adaptive_tutor.store import session_summary_view
        return [session_summary_view(row) for row in self._rows(SUMMARY, profile_id, "started_at", limit)]
