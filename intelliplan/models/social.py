"""Tables for Focus Shield and Study Buddies.

Three additive tables, created by ``db.create_all()`` on boot:

* ``focus_shield_settings`` -- one row per student: whether the extension
  blocks during study blocks, which sites, and the per-block break and
  "done early" state the extension reads back.
* ``study_buddies`` -- one row per *pair*, stored low id first so a pair
  can only exist once. Holds the shared friend streak.
* ``buddy_nudges`` -- one row per nudge sent. This is the rate limit's
  ledger and the "Maya nudged you" line on the recipient's card.

Nothing here stores browsing history. The extension asks "should I block
right now?"; it never reports what was visited, to us or to anyone.

Call :func:`register` from ``App.py`` after ``db`` exists.
"""

from __future__ import annotations

from typing import Any

from time_utils import utcnow

BUDDY_STATES = ("pending", "active", "blocked")


def register(db: Any) -> tuple[type, type, type]:
    """Define the models against ``db``. Idempotent."""

    registry = getattr(db.Model, "registry", None)
    existing: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            existing[getattr(cls, "__tablename__", "")] = cls
    names = ("focus_shield_settings", "study_buddies", "buddy_nudges")
    if set(names) <= set(existing):
        return tuple(existing[n] for n in names)  # type: ignore[return-value]

    class FocusShieldSettings(db.Model):
        __tablename__ = "focus_shield_settings"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
        #: On by default: it only does anything once the student installs
        #: the extension and signs in, and then only during their own
        #: study blocks.
        enabled = db.Column(db.Boolean, default=True)
        #: JSON list of domains. Null means "the defaults".
        blocklist_json = db.Column(db.Text, nullable=True)
        breaks_per_block = db.Column(db.Integer, default=2)
        #: Break accounting for the block currently being studied. Keyed by
        #: block so a new block starts with a full allowance.
        break_block_key = db.Column(db.String(80), default="")
        breaks_used = db.Column(db.Integer, default=0)
        break_until = db.Column(db.DateTime, nullable=True)
        #: "I'm done early": stop blocking this block until it ends.
        released_block_key = db.Column(db.String(80), default="")
        released_until = db.Column(db.DateTime, nullable=True)
        updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    class StudyBuddy(db.Model):
        __tablename__ = "study_buddies"
        __table_args__ = (
            db.UniqueConstraint("user_low_id", "user_high_id", name="uq_study_buddy_pair"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_low_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        user_high_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        #: Who asked. The other one has to confirm before anything is shared.
        requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        status = db.Column(db.String(16), default="pending", index=True)
        blocked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=utcnow)
        accepted_at = db.Column(db.DateTime, nullable=True)
        # ── Friend streak (see intelliplan/services/buddies.py) ──
        streak_count = db.Column(db.Integer, default=0)
        streak_longest = db.Column(db.Integer, default=0)
        streak_last_date = db.Column(db.String(16), default="")

        def other(self, user_id: int) -> int:
            return self.user_high_id if int(user_id) == self.user_low_id else self.user_low_id

        def involves(self, user_id: int) -> bool:
            return int(user_id) in (self.user_low_id, self.user_high_id)

    class BuddyNudge(db.Model):
        __tablename__ = "buddy_nudges"
        __table_args__ = (
            db.Index("ix_buddy_nudges_sender_date", "sender_id", "local_date"),
            db.Index("ix_buddy_nudges_recipient_date", "recipient_id", "local_date"),
        )

        id = db.Column(db.Integer, primary_key=True)
        sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
        #: The sender's local date, which is what "one per day" means to them.
        local_date = db.Column(db.String(16), nullable=False)
        created_at = db.Column(db.DateTime, default=utcnow)

    return FocusShieldSettings, StudyBuddy, BuddyNudge
