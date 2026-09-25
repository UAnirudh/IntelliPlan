"""Per-user semantic retrieval over the student's own notes (RAG).

Pieces, smallest first:

* :mod:`chunking`   — split a note into overlapping, sentence-aligned passages.
* :mod:`embeddings` — two embedders. ``lexical`` is a local hashed bag of
  words/bigrams (no network, always available); ``dense`` calls the Gemini
  embedding model when a key is configured.
* :mod:`models`     — the ``memory_chunks`` table: one row per passage, both
  vectors stored as float32 blobs.
* :mod:`store`      — boot-time surface (register, purge). Imports no numpy.
* :mod:`index`      — incremental sync (content-hashed, so an unchanged note
  is never re-embedded), hybrid scoring and MMR re-ranking.

Importing this package is cheap and cannot fail on the vector stack: the
search functions below load :mod:`index` (and numpy) on first call.

Why brute force and not pgvector: a student has tens to low hundreds of
passages. A float32 matrix product over that is sub-millisecond, works the
same on SQLite in dev and Postgres in prod, and needs no database extension.
``index.search`` is the single seam to swap if a corpus ever outgrows it.
"""

from __future__ import annotations

from typing import Any

from .store import init, purge_note, purge_user


def sync_user(user_id: int) -> dict:
    from .index import sync_user as _sync
    return _sync(user_id)


def search(user_id: int, query: str, k: int = 5) -> list[dict[str, Any]]:
    from .index import search as _search
    return _search(user_id, query, k=k)


def retrieve_context(user_id: int, query: str, k: int = 4, max_chars: int = 2600) -> str:
    """Grounding block for a chat turn. Never raises; "" on any failure."""
    try:
        from .index import retrieve_context as _retrieve
    except Exception:  # vector stack unavailable: answer without notes
        return ""
    return _retrieve(user_id, query, k=k, max_chars=max_chars)


__all__ = ["init", "purge_note", "purge_user", "retrieve_context", "search", "sync_user"]
