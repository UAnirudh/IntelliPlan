"""Read decks out of the formats students already have.

Nobody starts from an empty deck. A student arriving here has years of Anki
in a .apkg and a class set in Quizlet, and a flashcard feature that cannot
read either is a feature they will not use. Both are parsed here into the
same neutral shape, so the rest of the system never learns where a card came
from.

Nothing in this module touches the network or the database: it takes bytes or
text and returns dataclasses, which is what makes it testable without either.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Anki separates a note's fields with this character.
ANKI_FIELD_SEP = "\x1f"
#: Cards larger than this are pasted articles, not flashcards.
MAX_FIELD_CHARS = 4000
MAX_CARDS_PER_IMPORT = 5000


class ImportError_(Exception):
    """The file could not be read as the format it claimed to be."""


@dataclass
class ParsedCard:
    front: str
    back: str
    tags: list[str] = field(default_factory=list)
    card_type: str = "basic"      # basic | cloze
    extra: str = ""


@dataclass
class ParsedDeck:
    name: str
    cards: list[ParsedCard]
    source: str
    warnings: list[str] = field(default_factory=list)


# ── Shared cleaning ───────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_ANKI_SOUND_RE = re.compile(r"\[sound:[^\]]+\]")
_IMG_RE = re.compile(r"<img[^>]*>", re.I)
_CLOZE_RE = re.compile(r"\{\{c\d+::(.+?)(?:::.*?)?\}\}", re.S)
_WS_RE = re.compile(r"[ \t ]+")


def clean_field(raw: str) -> str:
    """Anki fields are HTML. Strip it down to text a card can display.

    Images and audio are dropped with a marker rather than silently: a card
    whose whole question was a diagram becomes obviously incomplete instead of
    mysteriously blank.
    """
    if not raw:
        return ""
    text = _ANKI_SOUND_RE.sub(" [audio] ", raw)
    text = _IMG_RE.sub(" [image] ", text)
    text = re.sub(r"<br\s*/?>|</div>|</p>|</li>", "\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_FIELD_CHARS]


def _is_cloze(text: str) -> bool:
    return bool(_CLOZE_RE.search(text or ""))


def cloze_to_plain(text: str) -> str:
    """``{{c1::Paris}}`` becomes ``Paris`` for the answer side."""
    return _CLOZE_RE.sub(r"\1", text or "")


# ── Anki ──────────────────────────────────────────────────────────

def parse_apkg(data: bytes, *, fallback_name: str = "Anki import") -> ParsedDeck:
    """Read an Anki .apkg export.

    A .apkg is a zip holding a SQLite collection. Anki 2.1.50+ writes
    ``collection.anki21b``, which is zstd-compressed and needs a library we do
    not ship; those exports are refused with the one instruction that fixes
    them (tick "Support older Anki versions" when exporting) rather than a
    parser error the student cannot act on.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ImportError_("That file is not a valid .apkg export.") from exc

    names = set(archive.namelist())
    if "collection.anki21b" in names and not (names & {"collection.anki2", "collection.anki21"}):
        raise ImportError_(
            "This export uses Anki's newer compressed format. Export it again "
            'with "Support older Anki versions" ticked and upload that file.'
        )
    member = next((n for n in ("collection.anki21", "collection.anki2") if n in names), None)
    if not member:
        raise ImportError_("That .apkg has no Anki collection inside it.")

    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "collection.sqlite"
        path.write_bytes(archive.read(member))
        # Read-only: the file is untrusted input, and nothing here should
        # ever write back to it.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            deck_name = _apkg_deck_name(conn) or fallback_name
            cloze_models = _apkg_cloze_models(conn)
            cards: list[ParsedCard] = []
            rows = conn.execute("SELECT mid, flds, tags FROM notes").fetchall()
            for row in rows:
                if len(cards) >= MAX_CARDS_PER_IMPORT:
                    warnings.append(
                        f"Only the first {MAX_CARDS_PER_IMPORT} cards were imported.")
                    break
                fields = (row["flds"] or "").split(ANKI_FIELD_SEP)
                front = clean_field(fields[0] if fields else "")
                back = clean_field(fields[1] if len(fields) > 1 else "")
                tags = [t for t in (row["tags"] or "").split() if t]
                if row["mid"] in cloze_models or _is_cloze(front):
                    text = fields[0] if fields else ""
                    if not _is_cloze(text):
                        continue
                    cards.append(ParsedCard(
                        front=clean_field(_CLOZE_RE.sub("[...]", text)),
                        back=clean_field(cloze_to_plain(text)),
                        tags=tags, card_type="cloze",
                        extra=clean_field(fields[1]) if len(fields) > 1 else "",
                    ))
                    continue
                if not front or not back:
                    continue
                cards.append(ParsedCard(front=front, back=back, tags=tags))
        finally:
            conn.close()

    if not cards:
        raise ImportError_("No usable cards were found in that deck.")
    return ParsedDeck(name=deck_name, cards=cards, source="anki", warnings=warnings)


def _apkg_deck_name(conn: sqlite3.Connection) -> str | None:
    """Deck names live in col.decks as JSON on old collections and in a decks
    table on newer ones. Try both, and do not fail the import over a name."""
    try:
        row = conn.execute("SELECT decks FROM col LIMIT 1").fetchone()
        if row and row[0]:
            decks = json.loads(row[0])
            for deck in decks.values():
                name = (deck or {}).get("name")
                if name and name != "Default":
                    return name.replace("\x1f", "::")
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT name FROM decks WHERE name != 'Default' ORDER BY id LIMIT 1").fetchone()
        if row and row[0]:
            return str(row[0]).replace("\x1f", "::")
    except Exception:
        pass
    return None


def _apkg_cloze_models(conn: sqlite3.Connection) -> set[int]:
    """Note type ids whose cards are cloze deletions (Anki model type 1)."""
    ids: set[int] = set()
    try:
        row = conn.execute("SELECT models FROM col LIMIT 1").fetchone()
        if row and row[0]:
            for mid, model in json.loads(row[0]).items():
                if (model or {}).get("type") == 1:
                    ids.add(int(mid))
    except Exception:
        pass
    try:
        for row in conn.execute("SELECT id, type FROM notetypes"):
            if row[1] == 1:
                ids.add(int(row[0]))
    except Exception:
        pass
    return ids


# ── Quizlet and other pasted text ─────────────────────────────────

#: Quizlet's own export dialog defaults to tab between term and definition
#: and newline between rows, but students change both, so every common
#: separator is accepted and the delimiter is detected when not given.
_TERM_SEPARATORS = ("\t", " - ", " – ", " — ", "|", ";", ",")
_ROW_SEPARATORS = ("\n\n", "\n", "\r\n")


def parse_quizlet(text: str, *, name: str = "Quizlet import",
                  term_sep: str | None = None, row_sep: str | None = None) -> ParsedDeck:
    """Parse a Quizlet export, or any pasted term/definition list.

    Quizlet has no public API to import from, so the path a student can
    actually use is Export → copy → paste. That means guessing the two
    separators when they do not say, which is why the detector prefers tab
    (Quizlet's own default) and only then falls back to punctuation.
    """
    if not (text or "").strip():
        raise ImportError_("There was nothing to import.")

    rsep = row_sep or _detect_row_separator(text)
    rows = [r for r in text.split(rsep) if r.strip()]
    tsep = term_sep or _detect_term_separator(rows)

    warnings: list[str] = []
    cards: list[ParsedCard] = []
    skipped = 0
    for row in rows:
        if len(cards) >= MAX_CARDS_PER_IMPORT:
            warnings.append(f"Only the first {MAX_CARDS_PER_IMPORT} cards were imported.")
            break
        if tsep not in row:
            skipped += 1
            continue
        front, back = row.split(tsep, 1)
        front, back = clean_field(front), clean_field(back)
        if not front or not back:
            skipped += 1
            continue
        cards.append(ParsedCard(front=front, back=back))
    if skipped:
        warnings.append(f"{skipped} line(s) had no {_describe(tsep)} and were skipped.")
    if not cards:
        raise ImportError_(
            "No cards were found. Each line needs a term and a definition "
            f"separated by {_describe(tsep)}.")
    return ParsedDeck(name=name.strip() or "Quizlet import", cards=cards,
                      source="quizlet", warnings=warnings)


def parse_csv(data: str, *, name: str = "CSV import") -> ParsedDeck:
    """Two columns: front, back. A third, if present, becomes tags."""
    reader = csv.reader(io.StringIO(data))
    cards: list[ParsedCard] = []
    for row in reader:
        if len(cards) >= MAX_CARDS_PER_IMPORT:
            break
        if len(row) < 2:
            continue
        front, back = clean_field(row[0]), clean_field(row[1])
        if not front or not back or front.lower() in ("front", "term", "question"):
            continue
        tags = [t.strip() for t in (row[2].split() if len(row) > 2 else []) if t.strip()]
        cards.append(ParsedCard(front=front, back=back, tags=tags))
    if not cards:
        raise ImportError_("No rows in that CSV had both a front and a back.")
    return ParsedDeck(name=name, cards=cards, source="csv")


def _detect_row_separator(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    # A blank line between cards means the cards themselves span lines.
    if text.count("\n\n") >= 2:
        return "\n\n"
    return "\n"


def _detect_term_separator(rows: list[str]) -> str:
    """Pick the separator that splits the most rows into exactly two parts."""
    best, best_score = "\t", -1
    for sep in _TERM_SEPARATORS:
        score = sum(1 for r in rows if r.count(sep) >= 1)
        # Tab wins ties: it is what Quizlet exports and it never appears
        # inside a term by accident, unlike a comma or a dash.
        if score > best_score:
            best, best_score = sep, score
    return best


def _describe(sep: str) -> str:
    return {"\t": "a tab", " - ": "a dash", " – ": "an en dash", " — ": "an em dash",
            "|": "a pipe", ";": "a semicolon", ",": "a comma"}.get(sep, f"'{sep}'")
