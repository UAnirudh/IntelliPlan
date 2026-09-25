"""The retrieval layer's boot-time surface. Deliberately numpy-free.

App.py imports only this module at startup. Registering the table and
purging rows need SQLAlchemy and nothing else; the vector math in
:mod:`intelliplan.retrieval.index` (and numpy with it) loads on the first
search. A problem in that stack can then cost a student the notes-grounded
answer, never the whole site: the first deploy of this layer imported numpy
during boot and production did not come back up.
"""

from __future__ import annotations

from typing import Any

from . import models as _models

_db: Any = None
_note_model: Any = None


def init(db: Any, note_model: Any) -> type:
    """Register ``memory_chunks`` and remember where notes live."""
    global _db, _note_model
    _db, _note_model = db, note_model
    return _models.register(db)


def ready() -> bool:
    return _db is not None and _note_model is not None


def db() -> Any:
    return _db


def note_model() -> Any:
    return _note_model


def purge_note(user_id: int, note_id: int) -> None:
    if not ready():
        return
    (_models.model().query
     .filter_by(user_id=user_id, source="note", source_id=note_id)
     .delete(synchronize_session=False))


def purge_user(user_id: int) -> None:
    if not ready():
        return
    _models.model().query.filter_by(user_id=user_id).delete(synchronize_session=False)
