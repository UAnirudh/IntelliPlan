"""Index maintenance and hybrid search over a user's notes.

Ranking, per passage::

    score = 0.7 * cos(dense_q, dense_p) + 0.3 * cos(lex_q, lex_p)   # dense available
    score =       cos(lex_q, lex_p)                                 # lexical only

then Maximal Marginal Relevance picks the final ``k`` so the model is not
handed four near-identical passages from the same page of notes.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import numpy as np

from . import models as _models
from .chunking import chunk_text
from .embeddings import (
    dense_embed,
    dense_model,
    from_blob,
    lexical_embed,
    to_blob,
)

logger = logging.getLogger(__name__)

DENSE_WEIGHT = 0.7
MMR_LAMBDA = 0.7
MIN_DENSE_SCORE = 0.5
MIN_LEXICAL_SCORE = 0.12
MAX_NOTES_PER_SYNC = 25       # bounds the latency a single chat turn can pay
MAX_BACKFILL_PER_SYNC = 64
MAX_ROWS_SCANNED = 3000

_db: Any = None
_note_model: Any = None


def init(db: Any, note_model: Any) -> type:
    """Register the table and remember where notes live. Called from App.py."""
    global _db, _note_model
    _db, _note_model = db, note_model
    return _models.register(db)


def _ready() -> bool:
    return _db is not None and _note_model is not None


def _note_text(note: Any) -> str:
    body = (note.text_content or "").strip() or (note.summary_cache or "").strip()
    return f"{note.title or ''}\n\n{body}".strip()


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ── Maintenance ─────────────────────────────────────────────────────────

def sync_user(user_id: int) -> dict:
    """Bring the user's index in line with their notes. Idempotent, bounded."""
    if not _ready() or not user_id:
        return {"built": 0, "removed": 0, "backfilled": 0}
    Chunk = _models.model()
    Note = _note_model

    notes = (Note.query.filter_by(user_id=user_id)
             .order_by(Note.id.desc()).all())
    current = {n.id: n for n in notes}
    texts = {n.id: _note_text(n) for n in notes}
    hashes = {nid: _hash(t) for nid, t in texts.items()}

    indexed = dict(
        _db.session.query(Chunk.source_id, Chunk.note_hash)
        .filter(Chunk.user_id == user_id, Chunk.source == "note")
        .distinct().all()
    )

    stale = [sid for sid, h in indexed.items() if hashes.get(sid) != h]
    if stale:
        (Chunk.query.filter(Chunk.user_id == user_id, Chunk.source == "note",
                            Chunk.source_id.in_(stale))
         .delete(synchronize_session=False))

    todo = [nid for nid in current
            if nid not in indexed or nid in stale][:MAX_NOTES_PER_SYNC]

    new_rows = []
    for nid in todo:
        note = current[nid]
        for idx, passage in enumerate(chunk_text(texts[nid])):
            new_rows.append(Chunk(
                user_id=user_id, source="note", source_id=nid, chunk_idx=idx,
                note_hash=hashes[nid], title=(note.title or "")[:255],
                course=(note.course_name or "")[:255],
                note_date=(note.note_date or "")[:32], text=passage,
                lexical_vec=to_blob(lexical_embed(f"{note.title} {passage}")),
            ))

    if new_rows:
        dense = dense_embed([r.text for r in new_rows], is_query=False)
        if dense:
            for row, vec in zip(new_rows, dense):
                row.dense_vec, row.dense_model = to_blob(vec), dense_model()
        _db.session.add_all(new_rows)

    backfilled = _backfill_dense(user_id) if not new_rows else 0
    _db.session.commit()
    return {"built": len(new_rows), "removed": len(stale), "backfilled": backfilled}


def _backfill_dense(user_id: int) -> int:
    """Embed passages indexed while the dense model was unreachable/changed."""
    Chunk = _models.model()
    rows = (Chunk.query.filter(Chunk.user_id == user_id)
            .filter((Chunk.dense_vec.is_(None)) | (Chunk.dense_model != dense_model()))
            .limit(MAX_BACKFILL_PER_SYNC).all())
    if not rows:
        return 0
    dense = dense_embed([r.text for r in rows], is_query=False)
    if not dense:
        return 0
    for row, vec in zip(rows, dense):
        row.dense_vec, row.dense_model = to_blob(vec), dense_model()
    return len(rows)


def purge_note(user_id: int, note_id: int) -> None:
    if not _ready():
        return
    Chunk = _models.model()
    (Chunk.query.filter_by(user_id=user_id, source="note", source_id=note_id)
     .delete(synchronize_session=False))


def purge_user(user_id: int) -> None:
    if not _ready():
        return
    _models.model().query.filter_by(user_id=user_id).delete(synchronize_session=False)


# ── Search ──────────────────────────────────────────────────────────────

def _mmr(cands: list[int], rel: np.ndarray, vecs: np.ndarray, k: int) -> list[int]:
    chosen: list[int] = []
    pool = list(cands)
    while pool and len(chosen) < k:
        if not chosen:
            best = max(pool, key=lambda i: rel[i])
        else:
            sel = vecs[chosen]
            best = max(pool, key=lambda i: MMR_LAMBDA * rel[i]
                       - (1 - MMR_LAMBDA) * float(np.max(sel @ vecs[i])))
        chosen.append(best)
        pool.remove(best)
    return chosen


def search(user_id: int, query: str, k: int = 5) -> list[dict]:
    query = (query or "").strip()
    if not _ready() or not user_id or not query:
        return []
    Chunk = _models.model()
    rows = (Chunk.query.filter_by(user_id=user_id)
            .order_by(Chunk.id.desc()).limit(MAX_ROWS_SCANNED).all())
    if not rows:
        return []

    lex = np.stack([from_blob(r.lexical_vec) for r in rows])
    lex_scores = lex @ lexical_embed(query)

    model_id = dense_model()
    has_dense = np.array([bool(r.dense_vec) and r.dense_model == model_id for r in rows])
    dense_scores = np.zeros(len(rows), dtype=np.float32)
    q_dense = dense_embed([query], is_query=True) if has_dense.any() else None

    if q_dense:
        qd = q_dense[0]
        for i, r in enumerate(rows):
            if has_dense[i]:
                dense_scores[i] = float(from_blob(r.dense_vec) @ qd)
        score = np.where(has_dense,
                         DENSE_WEIGHT * dense_scores + (1 - DENSE_WEIGHT) * lex_scores,
                         lex_scores)
        keep = np.where(has_dense,
                        (dense_scores >= MIN_DENSE_SCORE) | (lex_scores >= MIN_LEXICAL_SCORE),
                        lex_scores >= MIN_LEXICAL_SCORE)
    else:
        score, keep = lex_scores, lex_scores >= MIN_LEXICAL_SCORE

    cands = [int(i) for i in np.argsort(-score)[:30] if keep[i]]
    if not cands:
        return []
    picked = _mmr(cands, score, lex, k)
    return [{
        "note_id": rows[i].source_id,
        "title": rows[i].title,
        "course": rows[i].course,
        "date": rows[i].note_date,
        "text": rows[i].text,
        "score": round(float(score[i]), 4),
        "method": "hybrid" if (q_dense and has_dense[i]) else "lexical",
    } for i in picked]


def retrieve_context(user_id: int, query: str, k: int = 4, max_chars: int = 2600) -> str:
    """Sync, search, and format passages as a grounded prompt block.

    Never raises: retrieval is an enhancement to a chat turn, not a
    precondition for it.
    """
    try:
        sync_user(user_id)
        hits = search(user_id, query, k=k)
    except Exception as exc:
        logger.warning("RAG retrieval failed for user %s: %s", user_id, exc)
        try:
            _db.session.rollback()
        except Exception:
            pass
        return ""
    if not hits:
        return ""
    lines, used = [], 0
    for n, h in enumerate(hits, 1):
        label = " · ".join(x for x in (h["course"], h["title"], h["date"]) if x)
        entry = f"[{n}] {label}\n{h['text']}"
        if used + len(entry) > max_chars:
            break
        lines.append(entry)
        used += len(entry)
    return (
        "\n=== FROM THE STUDENT'S OWN NOTES (retrieved for this question) ===\n"
        "Passages below were retrieved from notes this student saved, ranked by "
        "relevance to their latest message. Prefer them over general knowledge "
        "when they answer the question, cite them as [1], [2]…, and say so "
        "plainly if they do not cover what was asked.\n"
        + "\n\n".join(lines)
        + "\n=== END NOTES ===\n"
    )
