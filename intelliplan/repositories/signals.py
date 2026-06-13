"""SignalRepository — append-only writes to ``student_signals``.

Every meaningful Command Center interaction emits one row. Failures are
swallowed (with a log line): a telemetry write must never break a
user-facing request.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SignalRepository:
    def __init__(self, signal_model: type, session: Any) -> None:
        self._model = signal_model
        self._session = session

    def emit(
        self,
        user_id: int,
        kind: str,
        *,
        subject_type: str = "",
        subject_ref: str = "",
        value: dict[str, Any] | None = None,
    ) -> bool:
        """Insert one signal row. Returns False (and logs) on failure."""
        try:
            row = self._model(
                user_id=user_id,
                kind=kind,
                subject_type=subject_type or None,
                subject_ref=subject_ref or None,
                value_json=json.dumps(value or {}, ensure_ascii=False),
            )
            self._session.add(row)
            self._session.commit()
            return True
        except Exception as exc:
            logger.warning("signal emit failed (kind=%s): %s", kind, exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return False
