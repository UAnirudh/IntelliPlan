"""StudentProfileRepository — single-row per user profile management."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class StudentProfileRepository:
    def __init__(self, profile_model: type, session: Any) -> None:
        self._model = profile_model
        self._session = session

    def get_or_create(self, user_id: int) -> Any:
        try:
            row = self._model.query.filter_by(user_id=user_id).first()
            if row is None:
                row = self._model(user_id=user_id, created_at=datetime.utcnow())
                self._session.add(row)
                self._session.commit()
            return row
        except Exception as exc:
            logger.warning("student profile get_or_create failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return None

    def save(self, profile_row: Any) -> bool:
        try:
            self._session.add(profile_row)
            self._session.commit()
            return True
        except Exception as exc:
            logger.warning("student profile save failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return False
