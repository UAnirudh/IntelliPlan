"""Request hooks that turn any mutating endpoint into a replay-safe one.

This is deliberately a pair of ``before_request`` / ``after_request`` hooks
rather than a decorator. There are 306 routes; decorating the mutating ones
means finding them all, agreeing forever to decorate new ones, and getting
it wrong once in a way nobody notices until a student's streak doubles. A
hook that keys off a header the client opted into applies uniformly and
costs one dictionary lookup on requests that do not carry the header.

What is and is not recorded
---------------------------
Recorded: any non-GET request carrying :data:`OP_ID_HEADER` that produced a
status below 500. That includes 4xx — a validation failure is a real,
stable outcome and replaying it must not suddenly succeed against
different server state.

Not recorded: 5xx. The server broke; the client should genuinely retry, and
freezing "500" into the ledger would make the failure permanent.

Not recorded: streamed or file responses, which have no body to store and
should never have been queued offline in the first place.
"""

from __future__ import annotations

from typing import Any, Callable

from flask import g, request

from .models import (
    MAX_BODY_BYTES,
    OP_ID_HEADER,
    REPLAY_HEADER,
    RETENTION_DAYS,
    register,
)

#: Methods whose effects we assume are not naturally repeatable.
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Op ids are client-generated. Bound the length so a hostile or buggy
#: client cannot use the ledger as free storage, and keep it opaque —
#: the server never parses it.
MAX_OP_ID_LEN = 64


def _clean_op_id(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    if not value or len(value) > MAX_OP_ID_LEN:
        return None
    # Printable ASCII only: this ends up in a log line and a SQL parameter.
    if any(ch < " " or ch > "~" for ch in value):
        return None
    return value


def install(app: Any, db: Any, current_user_id: Callable[[], int | None]) -> type:
    """Wire the ledger into ``app``. Returns the model class.

    ``current_user_id`` is injected rather than imported so this module
    stays testable without dragging in Flask-Login and the whole of
    ``App.py``.
    """

    SyncOp = register(db)

    def _owner() -> int:
        try:
            return int(current_user_id() or 0)
        except (TypeError, ValueError):
            return 0

    @app.before_request
    def _short_circuit_replays():  # pragma: no cover - exercised via client
        g.ip_sync_op_id = None
        if request.method not in MUTATING:
            return None
        op_id = _clean_op_id(request.headers.get(OP_ID_HEADER))
        if not op_id:
            return None
        g.ip_sync_op_id = op_id

        try:
            prior = SyncOp.query.filter_by(user_id=_owner(), op_id=op_id).first()
        except Exception:
            # A ledger that cannot be read must not take the app down with
            # it — the worst case without it is the duplicate we were
            # trying to avoid, which is strictly better than a 500.
            try:
                db.session.rollback()
            except Exception:
                pass
            return None
        if prior is None:
            return None

        response = app.response_class(
            prior.body or "",
            status=prior.status_code,
            content_type=prior.content_type or "application/json",
        )
        response.headers[REPLAY_HEADER] = "1"
        return response

    @app.after_request
    def _record_op(response):  # pragma: no cover - exercised via client
        op_id = getattr(g, "ip_sync_op_id", None)
        if not op_id or response.headers.get(REPLAY_HEADER):
            return response
        if response.status_code >= 500:
            return response
        if response.direct_passthrough or response.is_streamed:
            return response

        try:
            body = response.get_data(as_text=True)
        except Exception:
            return response
        if len(body.encode("utf-8", "ignore")) > MAX_BODY_BYTES:
            return response

        try:
            db.session.add(SyncOp(
                user_id=_owner(),
                op_id=op_id,
                method=request.method,
                path=request.path[:256],
                status_code=response.status_code,
                content_type=response.content_type or "application/json",
                body=body,
            ))
            db.session.commit()
        except Exception:
            # Losing the insert race means another tab recorded the same op
            # first; that is the ledger working, not failing.
            try:
                db.session.rollback()
            except Exception:
                pass
        return response

    return SyncOp


def prune(db: Any, SyncOp: type, *, days: int = RETENTION_DAYS) -> int:
    """Delete ledger rows older than ``days``. Returns the row count."""
    from datetime import timedelta

    from time_utils import utcnow

    cutoff = utcnow() - timedelta(days=days)
    deleted = SyncOp.query.filter(SyncOp.created_at < cutoff).delete(
        synchronize_session=False)
    db.session.commit()
    return int(deleted or 0)
