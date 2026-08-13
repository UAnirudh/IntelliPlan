"""Offline write safety: the replay ledger and the endpoints around it.

See :mod:`intelliplan.sync.models` for why a ledger is needed at all, and
``docs/offline-architecture.md`` for how the client half works.
"""

from __future__ import annotations

from typing import Any, Callable

from .api import build_blueprint
from .idempotency import install, prune
from .models import OP_ID_HEADER, REPLAY_HEADER, register

__all__ = [
    "OP_ID_HEADER",
    "REPLAY_HEADER",
    "build_blueprint",
    "install",
    "prune",
    "register",
    "setup",
]


def setup(app: Any, db: Any, current_user_id: Callable[[], int | None]) -> type:
    """Install the hooks and register the blueprint in one call."""
    SyncOp = install(app, db, current_user_id)
    app.register_blueprint(build_blueprint(SyncOp, current_user_id))
    return SyncOp
