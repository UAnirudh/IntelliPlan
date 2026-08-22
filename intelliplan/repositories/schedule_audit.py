"""ScheduleAuditRepository — versions and decisions.

Same contract as the other repositories in this package: ORM models are
injected, and a failed write is logged and swallowed rather than allowed to
break the user-facing request that triggered it. Losing an audit row is a
degraded audit trail; losing the schedule the student just generated because
the audit row failed is a bug report.

The one exception is :meth:`next_version`, which must be correct or the
version numbering is worthless — see the note there.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from time_utils import utcnow

logger = logging.getLogger(__name__)

#: Reason-code and violation-key lists are stored in a bounded column. These
#: are the caps at which they are truncated, so a pathological input cannot
#: fail the insert.
MAX_CODES_LEN = 255


def _join_keys(keys: Iterable[str] | None) -> str:
    joined = ",".join(sorted({str(k).strip() for k in (keys or []) if str(k).strip()}))
    return joined[:MAX_CODES_LEN]


class ScheduleAuditRepository:
    def __init__(
        self,
        version_model: type,
        decision_model: type,
        session: Any,
    ) -> None:
        self._versions = version_model
        self._decisions = decision_model
        self._session = session

    # ── versions ─────────────────────────────────────────────────────

    def next_version(self, user_id: int) -> int:
        """The next version number for this student.

        Read-then-write under the caller's transaction. The unique constraint
        on ``(user_id, version)`` is what actually guarantees correctness: if
        two requests race, one insert fails and :meth:`record_version` retries
        rather than writing a duplicate.
        """
        try:
            row = (
                self._versions.query
                .filter_by(user_id=user_id)
                .order_by(self._versions.version.desc())
                .first()
            )
            return int(row.version) + 1 if row is not None else 1
        except Exception as exc:
            logger.warning("schedule version lookup failed: %s", exc)
            return 1

    def record_version(
        self,
        user_id: int,
        *,
        trigger: str = "generated",
        objective_key: str = "balanced",
        candidate_count: int = 1,
        selected_score: float = 0.0,
        deadline_risk: float = 0.0,
        stress: float = 0.0,
        completion_odds: float = 0.0,
        feasible: bool = True,
        violation_keys: Sequence[str] = (),
        optimisation_ms: int = 0,
        scheduled_minutes: int = 0,
        deferred_minutes: int = 0,
        changes: Sequence[dict[str, Any]] = (),
        saved_schedule_id: int | None = None,
    ) -> Any | None:
        """Insert one version row. Retries once on a version collision."""
        for attempt in (0, 1):
            try:
                row = self._versions(
                    user_id=user_id,
                    version=self.next_version(user_id),
                    saved_schedule_id=saved_schedule_id,
                    trigger=trigger[:48],
                    objective_key=objective_key[:32],
                    candidate_count=int(candidate_count),
                    selected_score=float(selected_score),
                    deadline_risk=float(deadline_risk),
                    stress=float(stress),
                    completion_odds=float(completion_odds),
                    feasible=bool(feasible),
                    violation_keys=_join_keys(violation_keys),
                    optimisation_ms=int(optimisation_ms),
                    scheduled_minutes=int(scheduled_minutes),
                    deferred_minutes=int(deferred_minutes),
                    changes_json=json.dumps(list(changes)[:100], ensure_ascii=False),
                )
                self._session.add(row)
                self._session.commit()
                return row
            except Exception as exc:
                try:
                    self._session.rollback()
                except Exception:
                    pass
                if attempt == 0:
                    continue
                logger.warning("schedule version insert failed: %s", exc)
                return None
        return None

    def versions_for(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        try:
            rows = (
                self._versions.query
                .filter_by(user_id=user_id)
                .order_by(self._versions.version.desc())
                .limit(max(1, min(100, limit)))
                .all()
            )
        except Exception as exc:
            logger.warning("schedule version query failed: %s", exc)
            return []
        return [self._version_to_dict(r) for r in rows]

    @staticmethod
    def _version_to_dict(r: Any) -> dict[str, Any]:
        try:
            changes = json.loads(r.changes_json or "[]")
        except (TypeError, ValueError):
            changes = []
        return {
            "version": r.version,
            "trigger": r.trigger,
            "objective": r.objective_key,
            "candidates": r.candidate_count,
            "score": round(float(r.selected_score or 0.0), 3),
            "deadline_risk": round(float(r.deadline_risk or 0.0), 3),
            "stress": round(float(r.stress or 0.0), 3),
            "completion_odds": round(float(r.completion_odds or 0.0), 3),
            "feasible": bool(r.feasible),
            "violations": [k for k in (r.violation_keys or "").split(",") if k],
            "optimisation_ms": r.optimisation_ms,
            "scheduled_minutes": r.scheduled_minutes,
            "deferred_minutes": r.deferred_minutes,
            "changes": changes,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    # ── decisions ────────────────────────────────────────────────────

    def record_decisions(
        self,
        user_id: int,
        actions: Sequence[Any],
        *,
        decision_type: str = "next_best_action",
        schedule_version_id: int | None = None,
    ) -> list[Any]:
        """Log the ranked list the student was shown.

        The whole list, not just the winner: knowing that a student skipped
        the top pick and took the third is the signal, and it does not exist
        if only the top pick was written down.
        """
        written: list[Any] = []
        try:
            for rank, action in enumerate(actions):
                candidate = action.candidate
                written.append(
                    self._decisions(
                        user_id=user_id,
                        decision_type=decision_type[:32],
                        action_kind=str(getattr(candidate.kind, "value", candidate.kind))[:32],
                        task_id=str(candidate.task_id or "")[:128],
                        course=str(candidate.course or "")[:160],
                        score=float(action.score),
                        confidence=float(action.confidence),
                        completion_probability=float(action.completion_probability),
                        reason_codes=_join_keys(action.reason_codes),
                        rank=rank,
                        accepted=None,
                        schedule_version_id=schedule_version_id,
                    )
                )
            for row in written:
                self._session.add(row)
            self._session.commit()
            return written
        except Exception as exc:
            logger.warning("schedule decision insert failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return []

    def record_override(
        self,
        user_id: int,
        *,
        action_kind: str,
        task_id: str = "",
        course: str = "",
        accepted: bool = True,
        schedule_version_id: int | None = None,
    ) -> Any | None:
        try:
            row = self._decisions(
                user_id=user_id,
                decision_type="override",
                action_kind=str(action_kind)[:32],
                task_id=str(task_id or "")[:128],
                course=str(course or "")[:160],
                reason_codes="",
                rank=0,
                accepted=bool(accepted),
                schedule_version_id=schedule_version_id,
                resolved_at=utcnow(),
            )
            self._session.add(row)
            self._session.commit()
            return row
        except Exception as exc:
            logger.warning("override decision insert failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return None

    def resolve(
        self, user_id: int, task_id: str, accepted: bool, *, within_hours: int = 12,
    ) -> bool:
        """Mark the most recent open recommendation for a task as taken or not.

        Scoped to a time window so an action taken on Thursday does not
        retroactively "accept" a recommendation made on Monday. An
        unanswered row simply stays unanswered, which is the honest state.
        """
        try:
            cutoff = utcnow() - timedelta(hours=max(1, within_hours))
            row = (
                self._decisions.query
                .filter_by(user_id=user_id, task_id=str(task_id or "")[:128])
                .filter(self._decisions.decision_type == "next_best_action")
                .filter(self._decisions.accepted.is_(None))
                .filter(self._decisions.created_at >= cutoff)
                .order_by(self._decisions.created_at.desc())
                .first()
            )
            if row is None:
                return False
            row.accepted = bool(accepted)
            row.resolved_at = utcnow()
            self._session.commit()
            return True
        except Exception as exc:
            logger.warning("decision resolve failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass
            return False

    def override_count(self, user_id: int, since: datetime | None = None) -> int:
        """How many times this student has moved, skipped, or shortened work.

        Feeds the behavioural model's reschedule rate. Counted from the
        decision log rather than inferred from plan diffs, because a plan
        diff cannot tell "the student moved this" apart from "the optimiser
        moved this", and only the first says anything about the student.
        """
        try:
            q = self._decisions.query.filter_by(
                user_id=user_id, decision_type="override",
            )
            if since is not None:
                q = q.filter(self._decisions.created_at >= since)
            return int(q.count())
        except Exception:
            return 0

    def acceptance_rate(self, user_id: int, since: datetime | None = None) -> float | None:
        """Share of answered recommendations the student took.

        ``None`` when nothing has been answered yet — which is different from
        zero, and the difference matters to anything that reads this.
        """
        try:
            q = (
                self._decisions.query
                .filter_by(user_id=user_id, decision_type="next_best_action")
                .filter(self._decisions.accepted.isnot(None))
            )
            if since is not None:
                q = q.filter(self._decisions.created_at >= since)
            rows = q.all()
            if not rows:
                return None
            return round(sum(1 for r in rows if r.accepted) / len(rows), 3)
        except Exception:
            return None
