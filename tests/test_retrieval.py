"""Retrieval (RAG) index: chunking, lexical ranking, incremental sync, purge.

Runs against a throwaway Flask-SQLAlchemy app so it never imports App.py.
Dense embeddings are forced off, which is also the no-API-key production path.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from intelliplan.retrieval import chunking, embeddings, index


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(index, "dense_embed", lambda texts, is_query: None)
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
    db = SQLAlchemy(app)

    class User(db.Model):
        __tablename__ = "users"
        id = db.Column(db.Integer, primary_key=True)

    class Note(db.Model):
        __tablename__ = "course_notes"
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer)
        course_name = db.Column(db.String(255), default="")
        note_date = db.Column(db.String(32), default="")
        title = db.Column(db.String(255), default="")
        text_content = db.Column(db.Text, default="")
        summary_cache = db.Column(db.Text, default="")

    index.init(db, Note)
    with app.app_context():
        db.create_all()
        db.session.add_all([User(id=1), User(id=2)])
        db.session.commit()
        yield db, Note


def _add(db, Note, uid, title, text, course="Bio"):
    n = Note(user_id=uid, title=title, text_content=text, course_name=course, note_date="2026-09-20")
    db.session.add(n)
    db.session.commit()
    return n


def test_chunking_respects_size_and_overlaps():
    text = " ".join(f"Sentence number {i} talks about topic {i}." for i in range(80))
    chunks = chunking.chunk_text(text, target=300)
    assert len(chunks) > 3
    assert all(len(c) <= chunking.MAX_CHARS for c in chunks)
    # Last sentence of one passage opens the next.
    assert chunks[0].split(". ")[-1].rstrip(".") in chunks[1]


def test_chunking_empty_text_yields_nothing():
    assert chunking.chunk_text("") == []
    assert chunking.chunk_text("   \n\n  ") == []


def test_lexical_embedding_is_deterministic_and_normalised():
    a = embeddings.lexical_embed("Photosynthesis converts light energy")
    b = embeddings.lexical_embed("Photosynthesis converts light energy")
    assert (a == b).all()
    assert abs(float((a * a).sum()) - 1.0) < 1e-5


def test_search_ranks_relevant_note_first(env):
    db, Note = env
    _add(db, Note, 1, "Cell division", "Mitosis has four phases: prophase, metaphase, anaphase and telophase.")
    _add(db, Note, 1, "Civil war", "The Battle of Gettysburg was fought in July 1863.", course="History")
    index.sync_user(1)
    hits = index.search(1, "what are the phases of mitosis")
    assert hits and hits[0]["title"] == "Cell division"
    assert hits[0]["method"] == "lexical"


def test_search_is_scoped_to_user(env):
    db, Note = env
    _add(db, Note, 2, "Secret", "Mitosis phases private to user two.")
    index.sync_user(1)
    index.sync_user(2)
    assert index.search(1, "mitosis phases") == []


def test_unrelated_query_returns_nothing(env):
    db, Note = env
    _add(db, Note, 1, "Cell division", "Mitosis has four phases.")
    index.sync_user(1)
    assert index.search(1, "quarterback touchdown stadium") == []


def test_edit_rebuilds_and_delete_removes(env):
    db, Note = env
    n = _add(db, Note, 1, "Chem", "Covalent bonds share electrons.")
    assert index.sync_user(1)["built"] >= 1
    assert index.sync_user(1)["built"] == 0  # unchanged → nothing re-embedded

    n.text_content = "Ionic bonds transfer electrons between atoms."
    db.session.commit()
    stats = index.sync_user(1)
    assert stats["removed"] == 1 and stats["built"] >= 1
    assert index.search(1, "ionic bonds transfer")[0]["note_id"] == n.id

    db.session.delete(n)
    db.session.commit()
    index.sync_user(1)
    assert index.search(1, "ionic bonds") == []


def test_purge_user_clears_index(env):
    db, Note = env
    _add(db, Note, 1, "Chem", "Covalent bonds share electrons.")
    index.sync_user(1)
    index.purge_user(1)
    db.session.commit()
    assert index.search(1, "covalent bonds") == []


def test_mmr_prefers_diverse_passages(env):
    db, Note = env
    dup = "Newton's second law says force equals mass times acceleration."
    _add(db, Note, 1, "Physics A", dup)
    _add(db, Note, 1, "Physics B", dup)
    _add(db, Note, 1, "Physics C", "Force and acceleration: friction opposes motion between surfaces.")
    index.sync_user(1)
    hits = index.search(1, "force mass acceleration", k=2)
    assert {h["title"] for h in hits} != {"Physics A", "Physics B"}


def test_retrieve_context_formats_citations(env):
    db, Note = env
    _add(db, Note, 1, "Cell division", "Mitosis has four phases: prophase, metaphase, anaphase and telophase.")
    block = index.retrieve_context(1, "phases of mitosis")
    assert "[1]" in block and "Cell division" in block
    assert index.retrieve_context(1, "") == ""


def test_booting_the_app_does_not_import_numpy():
    """The first deploy of this layer imported numpy at boot and production
    never came back up. Boot must only touch the numpy-free store."""
    import subprocess
    import sys

    code = "import sys, App; sys.exit(3 if 'numpy' in sys.modules else 0)"
    env = {**__import__("os").environ, "SECRET_KEY": "t", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=600)
    assert proc.returncode == 0, "App boot imported numpy" if proc.returncode == 3 else proc.stderr[-2000:]
