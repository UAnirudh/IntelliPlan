"""The replay ledger that makes offline writes safe to retry.

Why this exists
---------------
An offline client cannot know whether a request it never saw a response to
actually landed. The phone goes into a tunnel halfway through ``POST
/dismiss``; the server applied it, awarded the streak, awarded the pet XP,
and then failed to deliver 200 bytes of JSON. When the tunnel ends, the
queue replays the request. Without a ledger the student silently earns the
streak twice, and "sync" quietly corrupts the thing it was meant to protect.

So every queued mutation carries a client-generated ``X-IP-Op-Id``. The
first time the server sees one it records the response it produced. Every
later arrival of the same id returns that recorded response verbatim,
without touching a handler.

The uniqueness contract
-----------------------
``(user_id, op_id)`` is UNIQUE, and the insert is allowed to lose that
race rather than checking first — two tabs flushing the same queue at the
same moment is the normal case, not the exotic one. The loser re-reads the
winner's row and returns it. Scoping to ``user_id`` means one student's
op id can never collide with, or reveal anything about, another's.

Anonymous callers get ``user_id = 0``. They have no durable identity to
scope to, so their ops are deduplicated only against each other within the
retention window, which is the most that can honestly be offered.

Retention
---------
Rows are pruned after :data:`RETENTION_DAYS`. A replay arriving later than
that is treated as new — by then the client has either given up or synced
by other means, and keeping every mutation forever turns a safety net into
an audit log nobody asked for.
"""

from __future__ import annotations

from typing import Any

from time_utils import utcnow

#: How long a recorded response can still satisfy a replay.
RETENTION_DAYS = 14

#: Cap on the stored body. Responses are small JSON envelopes; anything
#: larger is almost certainly a page render that should not have been
#: queued, and we would rather truncate than carry it.
MAX_BODY_BYTES = 16_384

#: The header a client uses to claim an idempotency key.
OP_ID_HEADER = "X-IP-Op-Id"

#: Set on a response that came from the ledger rather than a handler, so
#: the client can tell "applied just now" from "already applied earlier".
REPLAY_HEADER = "X-IP-Replay"


#: Per-``db`` memo of the defined model, keyed by id() of the SQLAlchemy
#: extension object. SQLAlchemy's own registries are not a dependable
#: lookup here: a declaratively-defined class is missing from
#: ``registry.mappers`` until it is configured, and ``_class_registry``
#: holds marker objects alongside classes. Both make the second
#: :func:`register` call redefine the table and raise. A memo is smaller
#: than the workaround and cannot drift.
_MODELS: dict[int, type] = {}


def _defined_model(db: Any) -> type | None:
    """Return the already-defined ``sync_ops`` model for ``db``, or None."""
    return _MODELS.get(id(db))


def register(db: Any) -> type:
    """Define the ledger model against ``db``. Idempotent.

    Called twice on purpose — once beside the other models so
    ``create_all`` builds the table, once from :func:`.idempotency.install`
    so the hooks have the class. The second call must return the first
    class rather than redefining it, and ``registry.mappers`` alone is not
    a reliable way to find it: a class defined but not yet configured is
    absent from that list, and redefining then raises "Table is already
    defined for this MetaData". Scanning the declarative class registry
    finds it in both states.
    """

    existing = _defined_model(db)
    if existing is not None:
        return existing

    class SyncOp(db.Model):
        __tablename__ = "sync_ops"

        id = db.Column(db.Integer, primary_key=True)
        #: 0 for anonymous. Deliberately not a foreign key: the ledger has
        #: to survive a user row being deleted, and a dangling replay is
        #: harmless where a failed cascade would not be.
        user_id = db.Column(db.Integer, nullable=False, default=0, index=True)
        op_id = db.Column(db.String(64), nullable=False)

        #: Kept for support and for the ``/api/sync/ops`` lookup — enough
        #: to answer "what was this?" without storing the request body,
        #: which can contain assignment titles and notes.
        method = db.Column(db.String(8), nullable=False, default="POST")
        path = db.Column(db.String(256), nullable=False, default="")

        status_code = db.Column(db.Integer, nullable=False, default=200)
        content_type = db.Column(db.String(128), default="application/json")
        body = db.Column(db.Text, default="")

        created_at = db.Column(db.DateTime, default=utcnow, index=True)

        __table_args__ = (
            db.UniqueConstraint("user_id", "op_id", name="uq_sync_ops_user_op"),
        )

        def to_dict(self) -> dict[str, Any]:
            return {
                "op_id": self.op_id,
                "method": self.method,
                "path": self.path,
                "status": self.status_code,
                "applied_at": self.created_at.isoformat() if self.created_at else None,
            }

    _MODELS[id(db)] = SyncOp
    return SyncOp
