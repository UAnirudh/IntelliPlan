"""``memory_chunks`` — the vector store.

One row per passage of one note. ``note_hash`` is the hash of the whole note
at index time: when a note is edited its hash changes and all its passages are
rebuilt; when it is unchanged nothing is re-embedded.

Never imports the app. ``App.py`` calls :func:`register` after ``db`` exists,
matching the other ``intelliplan.models`` modules.
"""

from __future__ import annotations

from typing import Any

from time_utils import utcnow

_MODEL: type | None = None


def register(db: Any) -> type:
    global _MODEL
    registry = getattr(db.Model, "registry", None)
    if registry is not None:
        for mapper in registry.mappers:
            if getattr(mapper.class_, "__tablename__", "") == "memory_chunks":
                _MODEL = mapper.class_
                return _MODEL

    class MemoryChunk(db.Model):
        __tablename__ = "memory_chunks"
        __table_args__ = (
            db.Index("ix_memory_chunks_user_source", "user_id", "source", "source_id"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        source = db.Column(db.String(24), nullable=False, default="note")
        source_id = db.Column(db.Integer, nullable=False)
        chunk_idx = db.Column(db.Integer, nullable=False, default=0)
        note_hash = db.Column(db.String(40), nullable=False)
        title = db.Column(db.String(255), default="")
        course = db.Column(db.String(255), default="")
        note_date = db.Column(db.String(32), default="")
        text = db.Column(db.Text, nullable=False)
        lexical_vec = db.Column(db.LargeBinary, nullable=False)
        dense_vec = db.Column(db.LargeBinary, nullable=True)
        dense_model = db.Column(db.String(64), nullable=True)
        created_at = db.Column(db.DateTime, default=utcnow)

    _MODEL = MemoryChunk
    return MemoryChunk


def model() -> type:
    if _MODEL is None:
        raise RuntimeError("memory_chunks model not registered; call register(db) first")
    return _MODEL
