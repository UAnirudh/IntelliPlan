"""ConceptMasteryRepository — upsert and query concept mastery rows."""

from __future__ import annotations

import logging
from datetime import datetime
from time_utils import utcnow
from typing import Any

logger = logging.getLogger(__name__)


class ConceptMasteryRepository:
    def __init__(self, mastery_model: type, session: Any) -> None:
        self._model = mastery_model
        self._session = session

    def _find(
        self, user_id: int, subject: str, topic: str, concept: str,
    ) -> Any | None:
        return (
            self._model.query
            .filter_by(
                user_id=user_id,
                subject=subject,
                topic=topic,
                concept=concept,
            )
            .first()
        )

    def upsert(
        self,
        user_id: int,
        subject: str,
        topic: str,
        concept: str,
        *,
        correct: bool | None = None,
        confidence_delta: float = 0.0,
    ) -> bool:
        try:
            row = self._find(user_id, subject, topic, concept)
            now = utcnow()

            if row is None:
                row = self._model(
                    user_id=user_id,
                    subject=subject,
                    topic=topic,
                    concept=concept,
                    times_seen=1,
                    times_correct=1 if correct else 0,
                    times_incorrect=0 if correct is None or correct else 1,
                    mastery_score=0.3 if correct else 0.1,
                    confidence_score=max(0.0, min(1.0, 0.1 + confidence_delta)),
                    last_reviewed=now,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(row)
            else:
                row.times_seen += 1
                if correct is True:
                    row.times_correct += 1
                elif correct is False:
                    row.times_incorrect += 1
                row.last_reviewed = now
                row.updated_at = now

                accuracy = row.times_correct / max(1, row.times_seen)
                row.mastery_score = max(0.0, min(1.0, accuracy))
                row.confidence_score = max(
                    0.0,
                    min(1.0, row.confidence_score + confidence_delta),
                )

            self._session.commit()
            return True
        except Exception as exc:
            logger.warning("concept mastery upsert failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return False

    def for_user(
        self, user_id: int, *, subject: str | None = None,
    ) -> list[Any]:
        try:
            q = self._model.query.filter_by(user_id=user_id)
            if subject is not None:
                q = q.filter_by(subject=subject)
            return q.order_by(self._model.subject, self._model.topic).all()
        except Exception:
            return []

    def weakest(self, user_id: int, limit: int = 10) -> list[Any]:
        try:
            return (
                self._model.query
                .filter_by(user_id=user_id)
                .filter(self._model.times_seen >= 1)
                .order_by(self._model.mastery_score.asc())
                .limit(limit)
                .all()
            )
        except Exception:
            return []

    def strongest(self, user_id: int, limit: int = 10) -> list[Any]:
        try:
            return (
                self._model.query
                .filter_by(user_id=user_id)
                .filter(self._model.times_seen >= 1)
                .order_by(self._model.mastery_score.desc())
                .limit(limit)
                .all()
            )
        except Exception:
            return []

    def forgetting_soon(
        self, user_id: int, now: datetime, threshold_days: int = 7,
    ) -> list[Any]:
        from datetime import timedelta
        try:
            cutoff = now - timedelta(days=threshold_days)
            return (
                self._model.query
                .filter_by(user_id=user_id)
                .filter(self._model.last_reviewed <= cutoff)
                .filter(self._model.times_seen >= 2)
                .order_by(self._model.last_reviewed.asc())
                .limit(20)
                .all()
            )
        except Exception:
            return []
