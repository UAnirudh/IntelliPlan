"""LearningEventRepository — append-only writes to ``learning_events``.

Same contract as ``SignalRepository``: failures are swallowed with a log
line so telemetry never breaks user-facing requests.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class LearningEventRepository:
    def __init__(self, event_model: type, session: Any) -> None:
        self._model = event_model
        self._session = session

    def emit(
        self,
        user_id: int,
        event_type: str,
        source: str,
        *,
        subject: str = "",
        reference_id: str = "",
        value: dict[str, Any] | None = None,
    ) -> bool:
        try:
            row = self._model(
                user_id=user_id,
                event_type=event_type,
                source=source,
                subject=subject or None,
                reference_id=reference_id or None,
                value_json=json.dumps(value or {}, ensure_ascii=False),
            )
            self._session.add(row)
            self._session.commit()
            return True
        except Exception as exc:
            logger.warning("learning event emit failed (type=%s): %s", event_type, exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return False

    def for_user(
        self,
        user_id: int,
        *,
        since: datetime | None = None,
        event_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        try:
            q = self._model.query.filter_by(user_id=user_id)
            if since is not None:
                q = q.filter(self._model.occurred_at >= since)
            if event_type is not None:
                q = q.filter_by(event_type=event_type)
            q = q.order_by(self._model.occurred_at.desc()).limit(limit)
            rows = q.all()
            return [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "source": r.source,
                    "subject": r.subject,
                    "reference_id": r.reference_id,
                    "value": json.loads(r.value_json or "{}"),
                    "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("learning event query failed: %s", exc)
            return []

    def count_by_type(
        self,
        user_id: int,
        event_type: str,
        since: datetime,
    ) -> int:
        try:
            return (
                self._model.query
                .filter_by(user_id=user_id, event_type=event_type)
                .filter(self._model.occurred_at >= since)
                .count()
            )
        except Exception:
            return 0

    def count_since(self, user_id: int, since: datetime) -> int:
        try:
            return (
                self._model.query
                .filter_by(user_id=user_id)
                .filter(self._model.occurred_at >= since)
                .count()
            )
        except Exception:
            return 0
