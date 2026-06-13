"""BriefingService — cached Narrator access.

The cache contract (see docs/command-center/02-database-design.md):

* One ``briefing_cache`` row per user.
* Reuse when ``payload_hash`` matches AND ``generated_at`` < 6h old.
* Refresh otherwise. The hash is over the *facts*, so a change in the
  student's plan invalidates immediately, while an unchanged plan never
  burns an AI call.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from intelliplan.domain.plan import BriefingText
from intelliplan.intelligence import narrator

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(hours=6)


def facts_hash(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BriefingService:
    def __init__(
        self,
        cache_model: type,
        session: Any,
        *,
        ai_chat_json: Callable[..., dict] | None = None,
        now: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self._model = cache_model
        self._session = session
        self._ai = ai_chat_json
        self._now = now

    def get_or_refresh(
        self,
        user_id: int,
        facts: dict[str, Any],
        *,
        student_name: str = "",
        force: bool = False,
    ) -> BriefingText:
        """Return the cached briefing, refreshing when stale or forced."""
        h = facts_hash(facts)
        now = self._now()

        row = None
        try:
            row = self._session.query(self._model).filter_by(user_id=user_id).first()
        except Exception as exc:
            logger.warning("briefing cache read failed: %s", exc)

        if row is not None and not force:
            fresh = row.payload_hash == h and (row.expires_at or now) > now
            if fresh:
                cached = self._deserialize(row)
                if cached is not None:
                    return cached

        text = narrator.brief(facts, student_name=student_name, ai_chat_json=self._ai)
        self._store(user_id, h, text, row=row, now=now)
        return text

    def peek(self, user_id: int) -> BriefingText | None:
        """Return whatever is cached, regardless of freshness. Used by the
        degraded path when a rebuild fails but stale text exists."""
        try:
            row = self._session.query(self._model).filter_by(user_id=user_id).first()
        except Exception:
            return None
        if row is None:
            return None
        return self._deserialize(row)

    # ── internals ────────────────────────────────────────────────────

    def _deserialize(self, row: Any) -> BriefingText | None:
        try:
            data = json.loads(row.text_json or "{}")
            return BriefingText(
                headline=data["headline"],
                body=data["body"],
                tone=data.get("tone", "calm-focused"),
                generated_by=row.model or data.get("generated_by", "ai"),
                cached=True,
            )
        except Exception as exc:
            logger.warning("briefing cache deserialize failed: %s", exc)
            return None

    def _store(
        self,
        user_id: int,
        payload_hash: str,
        text: BriefingText,
        *,
        row: Any,
        now: datetime,
    ) -> None:
        try:
            if row is None:
                row = self._model(user_id=user_id)
                self._session.add(row)
            row.payload_hash = payload_hash
            row.text_json = json.dumps(
                {"headline": text.headline, "body": text.body, "tone": text.tone,
                 "generated_by": text.generated_by},
                ensure_ascii=False,
            )
            row.model = text.generated_by
            row.generated_at = now
            row.expires_at = now + CACHE_TTL
            self._session.commit()
        except Exception as exc:
            logger.warning("briefing cache write failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass
