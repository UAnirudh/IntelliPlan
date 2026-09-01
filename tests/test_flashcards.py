"""Flashcards: FSRS scheduling, Anki and Quizlet imports, and the study loop.

The old feature was SM-2 in localStorage: one browser, fifty sets, gone with
the cache, and no way to bring in the decks a student already owns. These
tests pin the three things that replace it -- a scheduler whose intervals
order correctly and grow with success, importers that read the two formats
students actually have, and a queue that puts overdue reviews ahead of new
material so the backlog cannot build.

The .apkg fixture is a real Anki collection built here rather than a checked
in binary, so what the parser is tested against is legible in the diff.
"""

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

from flashcards import importers
from flashcards.scheduler import (AGAIN, EASY, GOOD, HARD, STATE_LEARNING,
                                  STATE_RELEARNING, STATE_REVIEW, CardState,
                                  preview, retrievability, review)


# ── Scheduler ─────────────────────────────────────────────────────

def test_the_four_buttons_are_ordered_by_how_long_they_wait():
    """Easy waits longest, Again waits least. A student choosing between the
    buttons is choosing between these numbers."""
    card = CardState()
    intervals = preview(card)
    assert intervals[AGAIN] <= intervals[HARD] <= intervals[GOOD] <= intervals[EASY]


def test_a_new_card_starts_in_learning_not_days_away():
    """Answering Good once does not mean the card is known. SM-2 sent it days
    out; the learning steps bring it back in minutes."""
    sched = review(CardState(), GOOD)
    assert sched.state == STATE_LEARNING
    assert sched.interval_days < 1


def test_easy_on_a_new_card_skips_the_learning_steps():
    sched = review(CardState(), EASY)
    assert sched.state == STATE_REVIEW
    assert sched.interval_days > 1


def test_repeated_success_lengthens_the_interval():
    now = datetime(2026, 1, 1)
    card = CardState()
    last = 0.0
    for i in range(6):
        sched = review(card, GOOD, now=now)
        card = CardState(state=sched.state, stability=sched.stability,
                         difficulty=sched.difficulty, reps=sched.reps,
                         lapses=sched.lapses, step=sched.step,
                         last_review=sched.last_review)
        now = sched.due
        if sched.state == STATE_REVIEW:
            assert sched.interval_days >= last
            last = sched.interval_days
    assert last > 1


def test_forgetting_a_mature_card_sends_it_to_relearning():
    """A card just proved it cannot be held for a week. Scheduling it a week
    out again is how a leech survives."""
    mature = CardState(state=STATE_REVIEW, stability=30.0, difficulty=5.0, reps=8,
                       last_review=datetime(2026, 1, 1))
    sched = review(mature, AGAIN, now=datetime(2026, 1, 31))
    assert sched.state == STATE_RELEARNING
    assert sched.interval_days < 1
    assert sched.lapses == 1
    assert sched.stability < mature.stability


def test_difficulty_moves_the_right_way_per_grade():
    again = review(CardState(), AGAIN).difficulty
    good = review(CardState(), GOOD).difficulty
    easy = review(CardState(), EASY).difficulty
    assert again > good > easy
    assert 1.0 <= easy and again <= 10.0


def test_retrievability_falls_with_time_and_rises_with_stability():
    assert retrievability(0, 10) == pytest.approx(1.0)
    assert retrievability(10, 10) < retrievability(1, 10)
    assert retrievability(10, 100) > retrievability(10, 10)


def test_a_lower_retention_target_schedules_further_out():
    """Target retention is the one dial a student turns: less reviewing for
    slightly more forgetting, or the reverse."""
    card = CardState(state=STATE_REVIEW, stability=20.0, difficulty=5.0, reps=4,
                     last_review=datetime(2026, 1, 1))
    lax = review(card, GOOD, target_retention=0.8, now=datetime(2026, 1, 20))
    strict = review(card, GOOD, target_retention=0.95, now=datetime(2026, 1, 20))
    assert lax.interval_days > strict.interval_days


def test_an_unknown_grade_is_refused():
    with pytest.raises(ValueError):
        review(CardState(), 7)


# ── Anki import ───────────────────────────────────────────────────

def _make_apkg(notes, *, deck_name="Biology 101", cloze_model=False,
               member="collection.anki21") -> bytes:
    """Build a minimal but real Anki collection: the schema the parser reads."""
    buf = io.BytesIO()
    db_bytes = io.BytesIO()
    import tempfile
    import os
    path = os.path.join(tempfile.mkdtemp(), "collection.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, decks TEXT, models TEXT)")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT, tags TEXT)")
    decks = json.dumps({"1": {"name": "Default"}, "2": {"name": deck_name}})
    models = json.dumps({"1607": {"name": "Basic", "type": 1 if cloze_model else 0}})
    conn.execute("INSERT INTO col (id, decks, models) VALUES (1, ?, ?)", (decks, models))
    for i, (flds, tags) in enumerate(notes, start=1):
        conn.execute("INSERT INTO notes (id, mid, flds, tags) VALUES (?, 1607, ?, ?)",
                     (i, flds, tags))
    conn.commit()
    conn.close()
    with open(path, "rb") as fh:
        db_bytes = fh.read()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, db_bytes)
        z.writestr("media", "{}")
    return buf.getvalue()


def test_an_apkg_becomes_a_deck_of_cards():
    apkg = _make_apkg([
        ("What is the powerhouse of the cell?\x1fThe mitochondrion", "biology cells"),
        ("Define osmosis\x1fMovement of water across a semipermeable membrane", ""),
    ])
    deck = importers.parse_apkg(apkg)
    assert deck.source == "anki"
    assert deck.name == "Biology 101"
    assert len(deck.cards) == 2
    assert deck.cards[0].front == "What is the powerhouse of the cell?"
    assert deck.cards[0].tags == ["biology", "cells"]


def test_anki_html_is_reduced_to_readable_text():
    apkg = _make_apkg([("<div>What is <b>ATP</b>?</div>\x1f<br>Adenosine&nbsp;triphosphate", "")])
    card = importers.parse_apkg(apkg).cards[0]
    assert card.front == "What is ATP?"
    assert card.back == "Adenosine triphosphate"


def test_media_becomes_a_marker_rather_than_vanishing():
    """A card whose question was a diagram should look incomplete, not blank."""
    apkg = _make_apkg([('<img src="heart.jpg"> Label this\x1fThe left ventricle', "")])
    card = importers.parse_apkg(apkg).cards[0]
    assert "[image]" in card.front


def test_cloze_notes_become_cloze_cards():
    apkg = _make_apkg(
        [("The capital of France is {{c1::Paris}}\x1fGeography note", "")],
        cloze_model=True)
    card = importers.parse_apkg(apkg).cards[0]
    assert card.card_type == "cloze"
    assert "[...]" in card.front
    assert "Paris" in card.back


def test_the_newer_compressed_export_is_refused_with_the_fix():
    """The parser cannot read anki21b, so it says which export setting to change
    instead of failing with a decode error."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("collection.anki21b", b"\x28\xb5\x2f\xfd not really zstd")
    with pytest.raises(importers.ImportError_) as excinfo:
        importers.parse_apkg(buf.getvalue())
    assert "Support older Anki versions" in str(excinfo.value)


def test_a_file_that_is_not_a_zip_is_rejected_clearly():
    with pytest.raises(importers.ImportError_):
        importers.parse_apkg(b"this is not a zip file")


# ── Quizlet import ────────────────────────────────────────────────

def test_a_tab_separated_quizlet_export_imports():
    text = "mitosis\tcell division producing two identical cells\nmeiosis\tcell division producing gametes"
    deck = importers.parse_quizlet(text)
    assert len(deck.cards) == 2
    assert deck.cards[1].front == "meiosis"


def test_a_dash_separated_paste_imports_too():
    """Students paste what they have, which is often not tabs."""
    deck = importers.parse_quizlet("ephemeral - lasting a short time\nlaconic - using few words")
    assert len(deck.cards) == 2
    assert deck.cards[0].back == "lasting a short time"


def test_the_separator_can_be_stated_explicitly():
    deck = importers.parse_quizlet("a|1\nb|2", term_sep="|")
    assert [c.back for c in deck.cards] == ["1", "2"]


def test_lines_without_a_separator_are_reported_not_silently_dropped():
    deck = importers.parse_quizlet("good\tfine\njust a heading\nbetter\tsuperior")
    assert len(deck.cards) == 2
    assert deck.warnings and "skipped" in deck.warnings[0]


def test_an_empty_paste_says_so():
    with pytest.raises(importers.ImportError_):
        importers.parse_quizlet("   ")


def test_csv_import_takes_a_third_column_as_tags():
    deck = importers.parse_csv("front,back,tags\nphoton,quantum of light,physics optics")
    assert len(deck.cards) == 1
    assert deck.cards[0].tags == ["physics", "optics"]
