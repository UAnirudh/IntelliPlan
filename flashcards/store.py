"""Storage for decks, cards and the review log.

Cards used to live in localStorage: one browser, fifty sets, gone with the
cache. They live in the database now because spaced repetition only works if
the schedule follows the student to their phone, and because the review log
is the training data an FSRS model needs to be fitted per student later.

Tables are declared with SQLAlchemy Core rather than the app's declarative
models to keep this package importable without App -- the scheduler and the
importers are pure, and the tests exercise them without a Flask context.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Index,
                        Integer, MetaData, String, Table, Text, delete, func,
                        select, update)

from .scheduler import (CardState, STATE_NEW, STATE_REVIEW, preview, review)

META = MetaData()

DECKS = Table(
    "fc_decks", META,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("name", String(200), nullable=False),
    Column("description", Text, default=""),
    #: manual | anki | quizlet | csv | ai
    Column("source", String(24), default="manual"),
    Column("new_per_day", Integer, default=20),
    Column("target_retention", Float, default=0.9),
    Column("archived", Boolean, default=False),
    Column("created_at", DateTime, default=lambda: _now()),
    Column("updated_at", DateTime, default=lambda: _now()),
)

CARDS = Table(
    "fc_cards", META,
    Column("id", Integer, primary_key=True),
    Column("deck_id", Integer, ForeignKey("fc_decks.id"), nullable=False, index=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("front", Text, nullable=False),
    Column("back", Text, nullable=False),
    Column("extra", Text, default=""),
    Column("tags", Text, default=""),
    Column("card_type", String(16), default="basic"),
    # ── FSRS state ──
    Column("state", String(16), default=STATE_NEW),
    Column("stability", Float, default=0.0),
    Column("difficulty", Float, default=0.0),
    Column("reps", Integer, default=0),
    Column("lapses", Integer, default=0),
    Column("step", Integer, default=0),
    Column("due", DateTime, nullable=True, index=True),
    Column("last_review", DateTime, nullable=True),
    Column("suspended", Boolean, default=False),
    Column("created_at", DateTime, default=lambda: _now()),
    Index("ix_fc_cards_user_due", "user_id", "due"),
)

REVIEWS = Table(
    "fc_reviews", META,
    Column("id", Integer, primary_key=True),
    Column("card_id", Integer, ForeignKey("fc_cards.id"), nullable=False, index=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("rating", Integer, nullable=False),
    Column("state_before", String(16), default=""),
    Column("elapsed_days", Float, default=0.0),
    Column("scheduled_days", Float, default=0.0),
    Column("reviewed_at", DateTime, default=lambda: _now(), index=True),
)

_READY = False


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_tables(db) -> None:
    global _READY
    if _READY:
        return
    META.create_all(bind=db.engine, tables=[DECKS, CARDS, REVIEWS])
    _READY = True


# ── Decks ─────────────────────────────────────────────────────────

def create_deck(db, user_id: int, name: str, *, description: str = "",
                source: str = "manual") -> int:
    res = db.session.execute(DECKS.insert().values(
        user_id=user_id, name=name.strip()[:200] or "Untitled deck",
        description=(description or "")[:2000], source=source,
        created_at=_now(), updated_at=_now(),
    ))
    db.session.commit()
    return int(res.inserted_primary_key[0])


def list_decks(db, user_id: int) -> list[dict]:
    """Every deck with the two counts a student actually acts on: how many
    cards are due right now, and how many have never been seen."""
    now = _now()
    rows = db.session.execute(
        select(DECKS).where(DECKS.c.user_id == user_id, DECKS.c.archived.is_(False))
        .order_by(DECKS.c.updated_at.desc())
    ).mappings().all()
    out = []
    for row in rows:
        counts = db.session.execute(
            select(
                func.count().label("total"),
                func.sum(func.coalesce(
                    (CARDS.c.state == STATE_NEW).cast(Integer), 0)).label("new"),
            ).where(CARDS.c.deck_id == row["id"], CARDS.c.suspended.is_(False))
        ).mappings().first() or {}
        due = db.session.execute(
            select(func.count()).where(
                CARDS.c.deck_id == row["id"], CARDS.c.suspended.is_(False),
                CARDS.c.state != STATE_NEW, CARDS.c.due <= now)
        ).scalar() or 0
        out.append({
            "id": row["id"], "name": row["name"], "description": row["description"],
            "source": row["source"], "new_per_day": row["new_per_day"],
            "target_retention": row["target_retention"],
            "total": int(counts.get("total") or 0),
            "new": int(counts.get("new") or 0),
            "due": int(due),
        })
    return out


def get_deck(db, user_id: int, deck_id: int) -> dict | None:
    row = db.session.execute(
        select(DECKS).where(DECKS.c.id == deck_id, DECKS.c.user_id == user_id)
    ).mappings().first()
    return dict(row) if row else None


def delete_deck(db, user_id: int, deck_id: int) -> bool:
    if not get_deck(db, user_id, deck_id):
        return False
    card_ids = [r[0] for r in db.session.execute(
        select(CARDS.c.id).where(CARDS.c.deck_id == deck_id)).all()]
    if card_ids:
        db.session.execute(delete(REVIEWS).where(REVIEWS.c.card_id.in_(card_ids)))
    db.session.execute(delete(CARDS).where(CARDS.c.deck_id == deck_id))
    db.session.execute(delete(DECKS).where(DECKS.c.id == deck_id))
    db.session.commit()
    return True


def update_deck(db, user_id: int, deck_id: int, **fields) -> bool:
    allowed = {k: v for k, v in fields.items()
               if k in ("name", "description", "new_per_day", "target_retention", "archived")}
    if not allowed or not get_deck(db, user_id, deck_id):
        return False
    allowed["updated_at"] = _now()
    db.session.execute(update(DECKS).where(DECKS.c.id == deck_id).values(**allowed))
    db.session.commit()
    return True


# ── Cards ─────────────────────────────────────────────────────────

def add_cards(db, user_id: int, deck_id: int, cards: list) -> int:
    """Bulk insert. ``cards`` are ParsedCard-shaped: front, back, tags, type."""
    if not cards:
        return 0
    now = _now()
    rows = []
    for c in cards:
        # Callers pass either a ParsedCard from the importers or a plain dict
        # from the API, and an empty-but-present field on the dataclass must
        # not fall through to a dict lookup that does not exist there.
        get = (lambda k, d="": c.get(k, d)) if isinstance(c, dict) else               (lambda k, d="": getattr(c, k, d))
        front = str(get("front") or "").strip()
        back = str(get("back") or "").strip()
        if not front or not back:
            continue
        tags = get("tags") or []
        rows.append({
            "deck_id": deck_id, "user_id": user_id, "front": front, "back": back,
            "extra": str(get("extra") or "")[:4000],
            "tags": " ".join(str(t) for t in tags)[:500],
            "card_type": get("card_type") or "basic",
            "state": STATE_NEW, "stability": 0.0, "difficulty": 0.0,
            "reps": 0, "lapses": 0, "step": 0,
            # New cards are due immediately; the per-day cap in due_queue is
            # what stops an 800-card import burying the student on day one.
            "due": now, "created_at": now,
        })
    if not rows:
        return 0
    db.session.execute(CARDS.insert(), rows)
    db.session.execute(update(DECKS).where(DECKS.c.id == deck_id).values(updated_at=now))
    db.session.commit()
    return len(rows)


def due_queue(db, user_id: int, deck_id: int | None = None, limit: int = 60) -> list[dict]:
    """Cards to study now: everything overdue, then the day's new allowance.

    Reviews come first deliberately. A student with 40 due cards and 20 new
    ones who is shown new material first ends the session with the reviews
    undone, which is exactly the backlog spaced repetition exists to prevent.
    """
    now = _now()
    where = [CARDS.c.user_id == user_id, CARDS.c.suspended.is_(False)]
    if deck_id:
        where.append(CARDS.c.deck_id == deck_id)

    due_rows = db.session.execute(
        select(CARDS).where(*where, CARDS.c.state != STATE_NEW, CARDS.c.due <= now)
        .order_by(CARDS.c.due.asc()).limit(limit)
    ).mappings().all()

    out = [_card_dict(r) for r in due_rows]
    if len(out) >= limit:
        return out

    new_allowance = _remaining_new_today(db, user_id, deck_id)
    if new_allowance > 0:
        new_rows = db.session.execute(
            select(CARDS).where(*where, CARDS.c.state == STATE_NEW)
            .order_by(CARDS.c.id.asc()).limit(min(new_allowance, limit - len(out)))
        ).mappings().all()
        out.extend(_card_dict(r) for r in new_rows)
    return out


def _remaining_new_today(db, user_id: int, deck_id: int | None) -> int:
    """How many new cards the student may still start today.

    Counted from the review log rather than a counter column, so it is right
    after a device swap, a rollback, or two tabs open at once.
    """
    decks = db.session.execute(
        select(DECKS.c.id, DECKS.c.new_per_day).where(
            DECKS.c.user_id == user_id,
            *( [DECKS.c.id == deck_id] if deck_id else [] ))
    ).all()
    cap = sum(int(d[1] or 0) for d in decks)
    if cap <= 0:
        return 0
    midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    started = db.session.execute(
        select(func.count(func.distinct(REVIEWS.c.card_id))).select_from(
            REVIEWS.join(CARDS, CARDS.c.id == REVIEWS.c.card_id)
        ).where(
            REVIEWS.c.user_id == user_id,
            REVIEWS.c.reviewed_at >= midnight,
            REVIEWS.c.state_before == STATE_NEW,
            *( [CARDS.c.deck_id == deck_id] if deck_id else [] ),
        )
    ).scalar() or 0
    return max(0, cap - int(started))


def due_forecast(db, user_id: int, start: datetime, days: int = 7) -> dict[str, int]:
    """Cards falling due on each of the next ``days``, keyed by ISO date.

    FSRS stores a real due date per card, so this is a forecast rather than
    a guess -- which is what makes it safe for the planner to reserve time
    against. Anything already overdue is counted on the first day, because
    that is when the student will actually face it.
    """
    ensure_tables(db)
    first = start.date()
    last = first + timedelta(days=max(1, days) - 1)
    rows = db.session.execute(
        select(CARDS.c.due, func.count()).where(
            CARDS.c.user_id == user_id,
            CARDS.c.suspended.is_(False),
            CARDS.c.state != STATE_NEW,
            CARDS.c.due.isnot(None),
            CARDS.c.due < datetime.combine(last + timedelta(days=1), datetime.min.time()),
        ).group_by(CARDS.c.due)
    ).all()
    out: dict[str, int] = {}
    for due, count in rows:
        day = due.date() if hasattr(due, "date") else None
        if day is None:
            continue
        if day < first:
            day = first
        out[day.isoformat()] = out.get(day.isoformat(), 0) + int(count)
    return out


def grade_card(db, user_id: int, card_id: int, rating: int) -> dict | None:
    """Apply a grade and persist both the new schedule and the review row."""
    row = db.session.execute(
        select(CARDS).where(CARDS.c.id == card_id, CARDS.c.user_id == user_id)
    ).mappings().first()
    if not row:
        return None
    deck = db.session.execute(
        select(DECKS.c.target_retention).where(DECKS.c.id == row["deck_id"])
    ).scalar()
    state = CardState(
        state=row["state"] or STATE_NEW,
        stability=float(row["stability"] or 0.0),
        difficulty=float(row["difficulty"] or 0.0),
        reps=int(row["reps"] or 0),
        lapses=int(row["lapses"] or 0),
        step=int(row["step"] or 0),
        last_review=row["last_review"],
    )
    now = _now()
    elapsed = ((now - state.last_review).total_seconds() / 86400.0
               if state.last_review else 0.0)
    sched = review(state, int(rating), target_retention=float(deck or 0.9), now=now)

    db.session.execute(update(CARDS).where(CARDS.c.id == card_id).values(
        state=sched.state, stability=sched.stability, difficulty=sched.difficulty,
        reps=sched.reps, lapses=sched.lapses, step=sched.step,
        due=sched.due, last_review=sched.last_review,
    ))
    db.session.execute(REVIEWS.insert().values(
        card_id=card_id, user_id=user_id, rating=int(rating),
        state_before=state.state, elapsed_days=elapsed,
        scheduled_days=sched.interval_days, reviewed_at=now,
    ))
    db.session.commit()
    return {
        "card_id": card_id, "state": sched.state,
        "due": sched.due.isoformat() + "Z",
        "interval_days": round(sched.interval_days, 4),
        "stability": round(sched.stability, 4),
        "difficulty": round(sched.difficulty, 4),
    }


def card_previews(db, user_id: int, card_id: int) -> dict[str, float] | None:
    """What each button would schedule, for the button labels."""
    row = db.session.execute(
        select(CARDS).where(CARDS.c.id == card_id, CARDS.c.user_id == user_id)
    ).mappings().first()
    if not row:
        return None
    deck = db.session.execute(
        select(DECKS.c.target_retention).where(DECKS.c.id == row["deck_id"])).scalar()
    state = CardState(
        state=row["state"] or STATE_NEW, stability=float(row["stability"] or 0.0),
        difficulty=float(row["difficulty"] or 0.0), reps=int(row["reps"] or 0),
        lapses=int(row["lapses"] or 0), step=int(row["step"] or 0),
        last_review=row["last_review"],
    )
    return {str(g): round(v, 4)
            for g, v in preview(state, target_retention=float(deck or 0.9)).items()}


def stats(db, user_id: int) -> dict:
    """Counts a student reads at a glance, plus the week's review history."""
    now = _now()
    total = db.session.execute(
        select(func.count()).where(CARDS.c.user_id == user_id)).scalar() or 0
    due = db.session.execute(
        select(func.count()).where(
            CARDS.c.user_id == user_id, CARDS.c.suspended.is_(False),
            CARDS.c.state != STATE_NEW, CARDS.c.due <= now)).scalar() or 0
    new = db.session.execute(
        select(func.count()).where(
            CARDS.c.user_id == user_id, CARDS.c.state == STATE_NEW)).scalar() or 0
    week_ago = now - timedelta(days=7)
    reviewed = db.session.execute(
        select(func.count()).where(
            REVIEWS.c.user_id == user_id, REVIEWS.c.reviewed_at >= week_ago)).scalar() or 0
    again = db.session.execute(
        select(func.count()).where(
            REVIEWS.c.user_id == user_id, REVIEWS.c.reviewed_at >= week_ago,
            REVIEWS.c.rating == 1)).scalar() or 0
    return {
        "cards": int(total), "due": int(due), "new": int(new),
        "reviews_this_week": int(reviewed),
        # Retention as the student experiences it: how often they did not
        # press Again. Fitted FSRS accuracy is a different, later number.
        "recall_rate": round(1 - (again / reviewed), 3) if reviewed else None,
    }


def _card_dict(row) -> dict:
    return {
        "id": row["id"], "deck_id": row["deck_id"],
        "front": row["front"], "back": row["back"], "extra": row["extra"],
        "tags": (row["tags"] or "").split(), "card_type": row["card_type"],
        "state": row["state"], "reps": row["reps"], "lapses": row["lapses"],
        "due": row["due"].isoformat() + "Z" if row["due"] else None,
    }
