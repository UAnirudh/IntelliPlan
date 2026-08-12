"""The notification outbox table.

Why an outbox rather than sending inline
----------------------------------------
Sending inside the request that caused the event couples a student's page
load to Twilio's latency and their SMTP server's mood. It also loses the
notification entirely if the provider is briefly down, because there is
nowhere to retry *from*. Writing a row first and delivering it later turns
"did this send?" into a query, and "the provider was down" into a retry
instead of a lost message.

The dedupe contract
-------------------
``(user_id, dedupe_key)`` is UNIQUE. Every event declares a key that is
stable for the same real-world fact, so a cron that runs twice, overlaps
itself, or replays after a crash inserts a duplicate row and the database
rejects it. That is the only deduplication strategy that holds with more
than one worker; checking "have we sent this?" before inserting is a race
with a comfortable-looking shape.

Call :func:`register` from ``App.py`` after ``db`` exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from time_utils import utcnow

#: Delivery states. ``dead`` is terminal after repeated failure — kept, not
#: deleted, because "we gave up on this" is something support needs to see.
STATES = ("pending", "sending", "sent", "failed", "dead", "cancelled")

#: Attempts before a message is declared dead.
MAX_ATTEMPTS = 5

#: Exponential backoff, in seconds, indexed by attempt count. Deliberately
#: reaches ~30 minutes rather than hours: a reminder that a session starts
#: in 15 minutes is worthless tomorrow, and expiry (below) will bin it
#: before the backoff finishes anyway.
BACKOFF_SECONDS = (60, 300, 900, 1800, 1800)


def backoff_for(attempts: int) -> timedelta:
    index = max(0, min(len(BACKOFF_SECONDS) - 1, attempts - 1))
    return timedelta(seconds=BACKOFF_SECONDS[index])


def register(db: Any) -> type:
    """Define the outbox model against ``db``. Idempotent."""

    registry = getattr(db.Model, "registry", None)
    existing: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            existing[getattr(cls, "__tablename__", "")] = cls
    if "notification_outbox" in existing:
        return existing["notification_outbox"]

    class NotificationOutbox(db.Model):
        __tablename__ = "notification_outbox"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

        kind = db.Column(db.String(48), nullable=False)
        channel = db.Column(db.String(16), nullable=False)
        #: Unique per user — see the module docstring.
        dedupe_key = db.Column(db.String(256), nullable=False)

        title = db.Column(db.String(256), default="")
        body = db.Column(db.Text, default="")
        url = db.Column(db.String(512), default="/")

        state = db.Column(db.String(16), default="pending", index=True)
        attempts = db.Column(db.Integer, default=0)
        last_error = db.Column(db.String(512), nullable=True)

        #: Earliest delivery time. Set in the future by quiet hours and by
        #: retry backoff; the dispatcher never looks at rows before it.
        scheduled_for = db.Column(db.DateTime, default=utcnow, index=True)
        #: After this, the message is stale and is binned rather than sent.
        #: A "starts in 15 minutes" push delivered at midnight is worse than
        #: no push at all.
        expires_at = db.Column(db.DateTime, nullable=True)

        created_at = db.Column(db.DateTime, default=utcnow)
        sent_at = db.Column(db.DateTime, nullable=True)
        #: Guards against two dispatchers grabbing the same row.
        claimed_at = db.Column(db.DateTime, nullable=True)

        __table_args__ = (
            db.UniqueConstraint("user_id", "dedupe_key", name="uq_outbox_user_dedupe"),
            db.Index("ix_outbox_due", "state", "scheduled_for"),
        )

        @property
        def is_expired(self) -> bool:
            return bool(self.expires_at and self.expires_at <= utcnow())

        @property
        def can_retry(self) -> bool:
            return (self.attempts or 0) < MAX_ATTEMPTS

        def to_dict(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "kind": self.kind,
                "channel": self.channel,
                "state": self.state,
                "title": self.title,
                "body": self.body,
                "url": self.url,
                "attempts": self.attempts or 0,
                "last_error": self.last_error,
                "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
                "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            }

    return NotificationOutbox
