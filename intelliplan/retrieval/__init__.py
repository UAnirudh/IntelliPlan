"""Per-user semantic retrieval over the student's own notes (RAG).

Pieces, smallest first:

* :mod:`chunking`   — split a note into overlapping, sentence-aligned passages.
* :mod:`embeddings` — two embedders. ``lexical`` is a local hashed bag of
  words/bigrams (no network, always available); ``dense`` calls the Gemini
  embedding model when a key is configured.
* :mod:`models`     — the ``memory_chunks`` table: one row per passage, both
  vectors stored as float32 blobs.
* :mod:`index`      — incremental sync (content-hashed, so an unchanged note
  is never re-embedded), hybrid scoring and MMR re-ranking.

Why brute force and not pgvector: a student has tens to low hundreds of
passages. A float32 matrix product over that is sub-millisecond, works the
same on SQLite in dev and Postgres in prod, and needs no database extension.
``index.search`` is the single seam to swap if a corpus ever outgrows it.
"""

from .index import purge_note, purge_user, retrieve_context, search, sync_user

__all__ = ["purge_note", "purge_user", "retrieve_context", "search", "sync_user"]
