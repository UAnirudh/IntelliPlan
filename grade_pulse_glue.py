"""Glue between App.py and Grade Pulse (intelliplan/services/grade_pulse.py).

Imported lazily, and every App import inside a function, matching
``notifications_glue.py``.

Where the observations come from
--------------------------------
Grades and assignments are not stored by IntelliPlan; they are read live
from the LMS whenever a page asks. So Grade Pulse watches the reads that
already happen rather than adding new ones:

* ``/grades/data`` and ``/gradebook/detail`` -- every grade source.
* ``/tasks/unified`` and ``collect_lms_assignments_for_user`` (Command
  Center, Plani) -- every assignment source.
* ``_persist_import`` -- the extension scraper, CSV and smart-paste.

That alone only notices a grade when the student opens the app, which is
the one time they did not need telling. So the notification tick also
*pulls* a few students per minute (see ``pull_due``): students who have
already been observed once (so there is a baseline) and who have a
notification channel on, at most every ``PULL_EVERY`` hours each.

Nothing here may break the page it is observing. Every entry point swallows
and logs: a failed alert is a missed notification, a failed /grades/data is
a broken grades page.

Delivery
--------
Alerts are ``NotificationEvent``s enqueued through the existing dispatcher,
so they get the existing channels (push, SMS, email -- and the in-app list
at /api/notifications/recent), the student's kind toggles, quiet hours in
their own timezone, and the outbox's (user, dedupe_key) uniqueness. Nothing
new is invented for delivery.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from flask import Blueprint, jsonify

from time_utils import utcnow

from intelliplan.notifications.events import EventKind, NotificationEvent
from intelliplan.services import grade_pulse as gp

logger = logging.getLogger(__name__)

grade_pulse_bp = Blueprint("grade_pulse", __name__)

#: Hours between background pulls of one student's LMS.
PULL_EVERY = timedelta(hours=float(os.getenv("GRADE_PULSE_PULL_HOURS", "3")))
#: Students pulled per tick. Each pull is several LMS round trips; a small
#: batch keeps one tick well inside the notification lease.
PULL_BATCH = int(os.getenv("GRADE_PULSE_PULL_BATCH", "5"))
#: A gradebook-detail snapshot younger than this makes course-level grade
#: changes for the same LMS redundant (see observe_course_grades).
DETAIL_FRESH = timedelta(days=7)
#: Most new assignments slotted from one observation. A teacher publishing a
#: whole unit should not rearrange the week in one go behind the student's back.
MAX_SLOTTED = 8


# ── Snapshot storage ──────────────────────────────────────────────────


def _snapshot_model():
    from App import GradePulseSnapshot

    return GradePulseSnapshot


def _load(user_id: int, scope: str) -> tuple[Any, dict | None]:
    Model = _snapshot_model()
    row = Model.query.filter_by(user_id=user_id, scope=scope).first()
    if row is None:
        return None, None
    try:
        data = json.loads(row.data_json or "{}")
    except (TypeError, ValueError):
        data = None
    return row, data if isinstance(data, dict) else None


def _save(row: Any, user_id: int, scope: str, data: dict) -> None:
    from App import db

    Model = _snapshot_model()
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    try:
        if row is None:
            row = Model(user_id=user_id, scope=scope)
            db.session.add(row)
        row.data_json = payload
        row.updated_at = utcnow()
        db.session.commit()
    except Exception as exc:
        # Most likely a concurrent first observation won the unique
        # constraint. Theirs is as good a baseline as ours.
        db.session.rollback()
        logger.info("grade pulse snapshot save skipped (%s): %s", scope, exc)


# ── Delivery ──────────────────────────────────────────────────────────


def _enqueue(user_id: int, kind: EventKind, pairs: Sequence[tuple[str, dict]], url: str) -> int:
    if not pairs:
        return 0
    from App import User
    import notifications_glue

    user = User.query.get(user_id)
    if user is None:
        return 0
    prefs = notifications_glue._preferences_for(user)
    dispatcher = notifications_glue.get_dispatcher()
    created = 0
    for key, context in pairs:
        event = NotificationEvent(kind=kind, user_id=user_id, dedupe_key=key,
                                  context=context, url=url)
        created += len(dispatcher.enqueue(event, prefs))
    return created


# ── Grades ────────────────────────────────────────────────────────────


def _source(name: Any) -> str:
    return (str(name or "lms").strip().lower() or "lms")[:40]


def observe_gradebook(user_id: int | None, lms: str, courses: Any) -> int:
    """Diff a gradebook-detail payload. Returns notifications queued."""
    if not user_id or not isinstance(courses, list):
        return 0
    try:
        scope = f"gradebook:{_source(lms)}"
        row, previous = _load(user_id, scope)
        alerts, snapshot = gp.diff_gradebook(previous, courses, source=_source(lms))
        _save(row, user_id, scope, snapshot)
        return _enqueue(user_id, EventKind.GRADE_POSTED,
                        gp.grade_event_contexts(alerts), "/grademodel")
    except Exception as exc:
        logger.warning("grade pulse gradebook observe failed for %s: %s", user_id, exc)
        _rollback()
        return 0


def observe_course_grades(user_id: int | None, lms: str, grades: Any) -> int:
    """Diff course-level percentages (sources with no per-assignment detail).

    When the same LMS also has a fresh gradebook-detail snapshot, this only
    keeps its baseline current: the detail observer has already said "78% on
    Unit 3 Test", and a second message saying "Chem 88.4 → 86.3" about the
    same grade is noise.
    """
    if not user_id or not isinstance(grades, list):
        return 0
    try:
        src = _source(lms)
        scope = f"grades:{src}"
        row, previous = _load(user_id, scope)
        alerts, snapshot = gp.diff_course_grades(previous, grades, source=src)
        _save(row, user_id, scope, snapshot)
        detail_row, _ = _load(user_id, f"gradebook:{src}")
        if detail_row is not None and detail_row.updated_at and \
                detail_row.updated_at > utcnow() - DETAIL_FRESH:
            return 0
        return _enqueue(user_id, EventKind.GRADE_POSTED,
                        gp.grade_event_contexts(alerts), "/grademodel")
    except Exception as exc:
        logger.warning("grade pulse course observe failed for %s: %s", user_id, exc)
        _rollback()
        return 0


# ── New assignments ───────────────────────────────────────────────────


def _active_plan(user_id: int):
    from App import SavedSchedule

    row = (
        SavedSchedule.query.filter_by(user_id=user_id, is_active=True)
        .order_by(SavedSchedule.created_at.desc())
        .first()
    )
    if row is None:
        return None, None
    try:
        data = json.loads(row.schedule_data) if isinstance(row.schedule_data, str) else row.schedule_data
    except (TypeError, ValueError):
        return row, None
    return row, data if isinstance(data, dict) else None


def _windows_for(user_id: int, plan: Mapping[str, Any]):
    """The student's real free windows, the same inputs /schedule/reflow uses."""
    import scheduler_engine
    from App import build_scheduler_personalization

    _dna, availability, commitments = build_scheduler_personalization(user_id=user_id)
    preferred = str(plan.get("preferred_time") or "evening")

    def windows(day: date):
        return scheduler_engine.windows_for_date(day, availability, preferred, commitments)

    return windows


def _decorate_block(block: dict) -> dict:
    """Give an auto-added block what the Interactive View expects of every
    block (id, kind, checklist), using the same helpers humanize_schedule
    does -- otherwise the first render backfills the whole plan."""
    try:
        from App import BLOCK_KIND_REDIRECT, build_block_checklist, classify_block_kind

        ident = hashlib.sha256(
            f"{block.get('assignment')}|{block.get('start_iso')}".encode("utf-8")
        ).hexdigest()[:10]
        block["block_id"] = f"gp-{ident}"
        kind = classify_block_kind(block.get("assignment", ""), block.get("course", ""))
        block["kind"] = kind
        block["redirect"] = BLOCK_KIND_REDIRECT.get(kind)
        block["checklist"] = build_block_checklist(block, kind, {})
    except Exception as exc:
        logger.info("grade pulse block decoration skipped: %s", exc)
    return block


def observe_assignments(user_id: int | None, tasks: Any, today: date | None = None) -> int:
    """Diff an assignment list; slot anything new into the plan; notify."""
    if not user_id or not isinstance(tasks, list):
        return 0
    try:
        today = today or date.today()
        row, previous = _load(user_id, "assignments")
        fresh, snapshot = gp.diff_assignments(previous, tasks, today=today)
        _save(row, user_id, "assignments", snapshot)
        if not fresh:
            return 0
        fresh.sort(key=lambda t: t.due)
        placed = _slot(user_id, fresh[:MAX_SLOTTED], today)
        placed += [(t, gp.Placement(reason="no_room")) for t in fresh[MAX_SLOTTED:]]
        return _enqueue(user_id, EventKind.ASSIGNMENT_POSTED,
                        gp.assignment_event_contexts(placed, today), "/scheduler")
    except Exception as exc:
        logger.warning("grade pulse assignment observe failed for %s: %s", user_id, exc)
        _rollback()
        return 0


def _slot(user_id: int, items: Sequence[gp.NewAssignment], today: date):
    from App import db

    plan_row, plan = _active_plan(user_id)
    if plan_row is None or plan is None:
        return [(t, gp.Placement(reason="no_plan")) for t in items]
    windows = _windows_for(user_id, plan)
    results = []
    changed = False
    for task in items:
        placement = gp.slot_assignment(plan, task, windows_for=windows, today=today,
                                       block_factory=_decorate_block)
        changed = changed or placement.placed
        results.append((task, placement))
    if changed:
        try:
            plan_row.schedule_data = json.dumps(plan)
            db.session.commit()
            _plan_changed(user_id)
        except Exception as exc:
            db.session.rollback()
            logger.warning("grade pulse plan save failed for %s: %s", user_id, exc)
            # The plan did not change after all, so do not claim a slot.
            return [(t, gp.Placement(reason="no_room")) for t in items]
    return results


def _plan_changed(user_id: int) -> None:
    """Drop the cached copies of the plan so the dashboard (and the
    calendar feed's next fetch) show the new block at once."""
    try:
        from App import invalidate_schedule_cache

        invalidate_schedule_cache(user_id=user_id)
    except Exception:
        pass
    try:
        from intelliplan.api.command_center import invalidate_today

        invalidate_today(user_id)
    except Exception:
        pass


def _rollback() -> None:
    try:
        from App import db

        db.session.rollback()
    except Exception:
        pass


# ── Background pull ───────────────────────────────────────────────────


def pull_user(user_id: int) -> dict[str, int]:
    """Fetch one student's LMS now and observe it. Network: only here."""
    from App import LinkedAccount, collect_lms_assignments_for_user

    out = {"assignments": 0, "grades": 0}
    # collect_lms_assignments_for_user observes what it fetches itself (see
    # the hook in App.py), so its return value needs no second look here.
    try:
        collect_lms_assignments_for_user(user_id, use_cache=False)
    except Exception as exc:
        logger.info("grade pulse pull: assignments failed for %s: %s", user_id, exc)

    acct = LinkedAccount.query.filter_by(user_id=user_id, is_active=True).first()
    if acct is not None:
        try:
            creds = acct.get_credentials() or {}
        except Exception:
            creds = {}
        kind = acct.login_type
        try:
            if kind == "studentvue":
                from studentvue_helper import get_gradebook_detail

                out["grades"] = observe_gradebook(user_id, kind, get_gradebook_detail(
                    creds.get("sv_district_url"), creds.get("sv_username"), creds.get("sv_password")))
            elif kind == "canvas" and creds.get("canvas_token"):
                from canvas_helper import get_gradebook_detail

                out["grades"] = observe_gradebook(user_id, kind, get_gradebook_detail(
                    creds.get("canvas_url") or "https://canvas.instructure.com",
                    creds["canvas_token"]))
            elif kind == "schoology":
                from schoology_helper import get_schoology_grades

                out["grades"] = observe_course_grades(user_id, kind, get_schoology_grades(
                    creds.get("schoology_key"), creds.get("schoology_secret")))
        except Exception as exc:
            logger.info("grade pulse pull: grades failed for %s: %s", user_id, exc)

    _mark_pulled(user_id)
    return out


def _mark_pulled(user_id: int) -> None:
    from App import db

    try:
        _snapshot_model().query.filter_by(user_id=user_id).update(
            {"pulled_at": utcnow()}, synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()


def due_user_ids(limit: int = PULL_BATCH, now: datetime | None = None) -> list[int]:
    """Students to pull this tick: observed before (so a baseline exists),
    not pulled or seen recently, and with some channel to hear about it."""
    from App import User, db

    now = now or utcnow()
    cutoff = now - PULL_EVERY
    Model = _snapshot_model()
    try:
        rows = (
            db.session.query(Model.user_id)
            .join(User, User.id == Model.user_id)
            .filter(
                Model.updated_at < cutoff,
                db.or_(Model.pulled_at.is_(None), Model.pulled_at < cutoff),
                db.or_(
                    User.push_reminders_opt_in.is_(True),
                    User.sms_reminders_opt_in.is_(True),
                    User.email_reminders_opt_in.is_(True),
                ),
            )
            .order_by(Model.pulled_at.asc())
            .limit(max(1, limit) * 4)
            .all()
        )
    except Exception as exc:
        logger.warning("grade pulse due query failed: %s", exc)
        _rollback()
        return []
    seen: list[int] = []
    for (uid,) in rows:
        if uid not in seen:
            seen.append(uid)
        if len(seen) >= limit:
            break
    return seen


def pull_due(limit: int = PULL_BATCH) -> dict[str, int]:
    if os.getenv("GRADE_PULSE_PULL", "1") == "0":
        return {"pulled": 0}
    pulled = 0
    for uid in due_user_ids(limit):
        try:
            pull_user(uid)
            pulled += 1
        except Exception as exc:
            logger.warning("grade pulse pull failed for %s: %s", uid, exc)
            _rollback()
    return {"pulled": pulled}


@grade_pulse_bp.route("/cron/grade-pulse", methods=["GET", "POST"])
def cron_grade_pulse():
    """Pull a batch now. Same secret as /cron/notifications. The in-process
    notification tick already calls pull_due; this exists for an external
    scheduler, as the other /cron/* endpoints do."""
    import notifications_glue

    if not notifications_glue._cron_authorised():
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    return jsonify({"status": "ok", **pull_due()})
