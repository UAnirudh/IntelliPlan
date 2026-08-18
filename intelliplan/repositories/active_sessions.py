"""ActiveSessionRepository — the write and read side of the feedback loop.

The scheduler learns from what this table records, so two things matter
more here than in a typical CRUD repository:

1. **Never lose a finished session.** A session that ends without being
   written is a lesson the model never gets.
2. **Never let telemetry break the page.** Focus-sample writes are
   best-effort and swallow their own failures; a dropped attention bucket
   costs almost nothing, and a 500 during a study session costs the session.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from time_utils import utcnow

logger = logging.getLogger(__name__)

#: A running session with no heartbeat for this long is dead — the tab was
#: closed, the laptop slept, the student walked away. We close it as
#: ``expired`` rather than letting it accrue imaginary hours.
STALE_SESSION_MINUTES = 90

#: How far back the estimator looks. Older behaviour is already decayed to
#: near-nothing by the model's half-life, so reading it is wasted I/O.
HISTORY_WINDOW_DAYS = 180

#: Cap on rows pulled for model fitting. Bounds both memory and the cost of
#: fitting for a heavy user with years of history.
HISTORY_ROW_LIMIT = 400


class ActiveSessionRepository:
    def __init__(self, session_model: type, focus_model: type, db_session: Any) -> None:
        self._model = session_model
        self._focus = focus_model
        self._session = db_session

    # ── Scoping ───────────────────────────────────────────────────────

    def _scope(self, query: Any, user_id: int | None, guest_id: str | None) -> Any:
        """Restrict a query to one identity.

        A query with neither a user nor a guest id would read every
        student's sessions, so that case returns nothing rather than
        everything — the failure mode of an over-broad scope is a data
        leak, and the failure mode of an empty one is a blank page.
        """
        if user_id:
            return query.filter(self._model.user_id == user_id)
        if guest_id:
            return query.filter(
                self._model.user_id.is_(None),
                self._model.guest_session_id == guest_id,
            )
        return query.filter(False)

    # ── Writes ────────────────────────────────────────────────────────

    def start(
        self,
        *,
        user_id: int | None,
        guest_id: str | None,
        title: str,
        planned_minutes: int,
        block_id: str | None = None,
        task_id: str | None = None,
        course: str = "",
        kind: str = "homework",
        difficulty: str = "medium",
        due_date: date | None = None,
        focus_enabled: bool = False,
    ) -> Any:
        """Open a session, retiring any the student left running.

        Two live sessions for one student is always a bug in the making —
        the timer the page shows and the row the model learns from would
        disagree — so starting one closes the others.
        """
        self.expire_stale(user_id=user_id, guest_id=guest_id, force=True)
        row = self._model(
            user_id=user_id,
            guest_session_id=None if user_id else guest_id,
            block_id=block_id,
            task_id=task_id,
            title=(title or "Study session")[:512],
            course=(course or "")[:256],
            kind=(kind or "homework")[:32],
            difficulty=(difficulty or "medium")[:16],
            planned_minutes=max(0, int(planned_minutes or 0)),
            due_date=due_date,
            state="running",
            focus_enabled=bool(focus_enabled),
            started_at=utcnow(),
        )
        self._session.add(row)
        self._session.commit()
        return row

    def get(self, session_id: int, *, user_id: int | None, guest_id: str | None) -> Any | None:
        return self._scope(
            self._model.query.filter(self._model.id == session_id), user_id, guest_id
        ).first()

    def active(self, *, user_id: int | None, guest_id: str | None) -> Any | None:
        """The student's currently open session, if any."""
        return (
            self._scope(
                self._model.query.filter(self._model.state.in_(("running", "paused"))),
                user_id,
                guest_id,
            )
            .order_by(self._model.started_at.desc())
            .first()
        )

    def update_progress(
        self,
        row: Any,
        *,
        active_seconds: int | None = None,
        paused_seconds: int | None = None,
        pause_count: int | None = None,
        progress_percent: int | None = None,
        state: str | None = None,
    ) -> Any:
        """Apply a heartbeat.

        Timers only move forward. A client that reconnects, or one whose
        clock jumped, must not be able to reduce recorded effort — so
        counters take the maximum of old and new rather than the new value.
        """
        if active_seconds is not None:
            row.active_seconds = max(int(row.active_seconds or 0), max(0, int(active_seconds)))
        if paused_seconds is not None:
            row.paused_seconds = max(int(row.paused_seconds or 0), max(0, int(paused_seconds)))
        if pause_count is not None:
            row.pause_count = max(int(row.pause_count or 0), max(0, int(pause_count)))
        if progress_percent is not None:
            row.progress_percent = max(0, min(100, int(progress_percent)))
        if state in ("running", "paused"):
            row.state = state
        row.updated_at = utcnow()
        self._session.commit()
        return row

    def record_forfeit(self, row: Any, sparks: int) -> Any:
        """Store sparks this session gave up to focus enforcement.

        Monotonic, like every other counter here: the client sends a
        running total once a second while the student is away, and those
        requests arrive out of order often enough that taking the newest
        value would let the count go backwards.
        """
        row.sparks_forfeited = max(int(row.sparks_forfeited or 0), max(0, int(sparks)))
        row.updated_at = utcnow()
        self._session.commit()
        return row

    def finish(
        self,
        row: Any,
        *,
        completed: bool,
        active_seconds: int | None = None,
        progress_percent: int | None = None,
        perceived_difficulty: int | None = None,
        notes: str | None = None,
    ) -> Any:
        """Close a session as completed or abandoned.

        ``completed`` is the student's own answer, not an inference from the
        clock. Someone who finishes a 45-minute task in 20 minutes has
        completed it, and someone who runs the timer for the full 45 and
        gets nowhere has not.
        """
        if active_seconds is not None:
            row.active_seconds = max(int(row.active_seconds or 0), max(0, int(active_seconds)))
        row.completed_work = bool(completed)
        row.state = "completed" if completed else "abandoned"
        row.ended_at = utcnow()
        if progress_percent is not None:
            row.progress_percent = max(0, min(100, int(progress_percent)))
        elif completed:
            row.progress_percent = 100
        if perceived_difficulty is not None:
            row.perceived_difficulty = max(1, min(5, int(perceived_difficulty)))
        if notes is not None:
            row.notes = notes[:2000]
        row.updated_at = utcnow()
        self._session.commit()
        return row

    def expire_stale(
        self,
        *,
        user_id: int | None,
        guest_id: str | None,
        force: bool = False,
    ) -> int:
        """Close sessions whose heartbeat stopped. Returns how many.

        Expired sessions keep whatever active time they had accrued — that
        time was real — but are never marked completed, because we do not
        know that the work got done.
        """
        cutoff = utcnow() - timedelta(minutes=STALE_SESSION_MINUTES)
        query = self._scope(
            self._model.query.filter(self._model.state.in_(("running", "paused"))),
            user_id,
            guest_id,
        )
        if not force:
            query = query.filter(self._model.updated_at < cutoff)
        rows = query.all()
        for row in rows:
            row.state = "expired"
            row.ended_at = row.updated_at or utcnow()
        if rows:
            self._session.commit()
        return len(rows)

    def record_focus_bucket(
        self,
        session_id: int,
        *,
        offset_seconds: int,
        present: int,
        away: int,
        absent: int,
        confidence: float,
    ) -> bool:
        """Append one aggregated attention bucket. Best-effort.

        Never raises: a lost telemetry row must not interrupt studying.
        """
        try:
            self._session.add(
                self._focus(
                    session_id=session_id,
                    offset_seconds=max(0, int(offset_seconds)),
                    present=max(0, int(present)),
                    away=max(0, int(away)),
                    absent=max(0, int(absent)),
                    confidence=max(0.0, min(1.0, float(confidence))),
                )
            )
            self._session.commit()
            return True
        except Exception as exc:
            logger.warning("focus bucket write failed (session=%s): %s", session_id, exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return False

    def apply_focus_summary(
        self,
        row: Any,
        *,
        samples: int,
        present: int,
        away: int,
        absent: int,
        distraction_events: int,
        longest_streak_seconds: int,
    ) -> Any:
        row.focus_samples = max(0, int(samples))
        row.focus_present_samples = max(0, int(present))
        row.focus_away_samples = max(0, int(away))
        row.focus_absent_samples = max(0, int(absent))
        row.distraction_events = max(0, int(distraction_events))
        row.longest_focus_streak = max(0, int(longest_streak_seconds))
        row.updated_at = utcnow()
        self._session.commit()
        return row

    # ── Reads for the model ───────────────────────────────────────────

    def observations(
        self,
        *,
        user_id: int | None,
        guest_id: str | None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Rows the estimation model fits on, newest first.

        Only terminal sessions with real recorded time are returned. A
        running session has not finished teaching us anything yet, and a
        session with a zero timer teaches us that the student opened a tab.
        """
        now = now or utcnow()
        since = now - timedelta(days=HISTORY_WINDOW_DAYS)
        rows = (
            self._scope(
                self._model.query.filter(
                    self._model.state.in_(("completed", "abandoned")),
                    self._model.started_at >= since,
                    self._model.active_seconds > 60,
                    self._model.planned_minutes > 0,
                ),
                user_id,
                guest_id,
            )
            .order_by(self._model.started_at.desc())
            .limit(HISTORY_ROW_LIMIT)
            .all()
        )
        return [r.to_observation() for r in rows]

    def recent(
        self,
        *,
        user_id: int | None,
        guest_id: str | None,
        limit: int = 20,
    ) -> list[Any]:
        return (
            self._scope(self._model.query, user_id, guest_id)
            .order_by(self._model.started_at.desc())
            .limit(max(1, min(100, limit)))
            .all()
        )

    def stats(self, *, user_id: int | None, guest_id: str | None) -> dict[str, Any]:
        """Headline numbers for the Active page and the settings panel."""
        rows = self.recent(user_id=user_id, guest_id=guest_id, limit=100)
        terminal = [r for r in rows if r.is_terminal]
        finished = [r for r in terminal if r.completed_work]
        total_minutes = sum(r.active_minutes for r in terminal)
        focus_ratios = [r.focus_ratio for r in terminal if r.focus_ratio is not None]
        return {
            "sessions": len(terminal),
            "completed": len(finished),
            "completion_rate": round(len(finished) / len(terminal), 3) if terminal else None,
            "total_minutes": total_minutes,
            "median_minutes": _median([r.active_minutes for r in finished]),
            "mean_focus_ratio": round(sum(focus_ratios) / len(focus_ratios), 3)
            if focus_ratios
            else None,
        }

    def reality_for(
        self,
        task_ids: Iterable[str],
        *,
        user_id: int | None,
        guest_id: str | None,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        """Aggregate what happened per task, for adaptive rescheduling.

        Returns ``{"completed": {...}, "abandoned": {...}, "finished": {...}}``
        in the shape :class:`intelliplan.intelligence.planner.Reality` wants.
        """
        wanted = {str(t) for t in task_ids if t}
        if not wanted:
            return {"completed": {}, "abandoned": {}, "finished": set()}
        since = since or (utcnow() - timedelta(days=HISTORY_WINDOW_DAYS))
        rows = (
            self._scope(
                self._model.query.filter(
                    self._model.task_id.in_(wanted),
                    self._model.started_at >= since,
                    self._model.state.in_(("completed", "abandoned", "expired")),
                ),
                user_id,
                guest_id,
            )
            .order_by(self._model.started_at.asc())
            .all()
        )
        completed: dict[str, int] = {}
        abandoned: dict[str, int] = {}
        finished: set[str] = set()
        for row in rows:
            key = str(row.task_id)
            if row.completed_work:
                completed[key] = completed.get(key, 0) + row.active_minutes
                if (row.progress_percent or 0) >= 100:
                    finished.add(key)
            else:
                abandoned[key] = abandoned.get(key, 0) + row.active_minutes
        return {"completed": completed, "abandoned": abandoned, "finished": finished}


def _median(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return int(round((ordered[mid - 1] + ordered[mid]) / 2))
