"""Glue between App.py and the Follow-Through engine.

Imported by App.py at the bottom, next to the other blueprint registrations.
Every ``App`` import is lazy (inside a function), the same pattern
``next_action_glue`` uses, so there is no circular import.

This module computes nothing. It reads the student's data out of the ORM,
hands it to :class:`intelliplan.services.scheduling.SchedulingService`, and
writes the result back. Every provider degrades on failure — a model input
that cannot be loaded costs that input, never the student's plan.

It is also imported by App's own scheduling routes (generation and recovery)
for :func:`student_context_extras` and :func:`followthrough_enabled`, so the
generated plan and the adjusted plan learn from exactly the same evidence.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
import zlib
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from flask import jsonify, request
from flask_login import current_user

from time_utils import utcnow

from intelliplan.api.adjust import FLAG_KEY, AdjustDeps, create_adjust_blueprint
from intelliplan.intelligence.followthrough import (
    DEFAULT_PRIOR,
    Prior,
    fit_population_prior,
    harvest_plan_outcomes,
    observations_from_outcomes,
    observations_from_sessions,
)

logger = logging.getLogger(__name__)

PRIOR_KEY = "followthrough-v1"
OUTCOME_SIGNAL = "plan_outcomes"
#: History window for every training read; the models' own 45-day decay does
#: the real weighting, this only bounds the query.
HISTORY_DAYS = 120
#: Older saved plans harvested for outcomes, newest first.
SAVED_PLAN_LIMIT = 12
#: Population refit bounds. Sized to finish in seconds on the web worker.
REFIT_SESSION_ROWS = 20000
REFIT_SIGNAL_ROWS = 4000
PRIOR_CACHE_SECONDS = 600

_prior_cache: dict[str, Any] = {"at": 0.0, "prior": None}
_prior_lock = threading.Lock()


# ── identity & gating ────────────────────────────────────────────────


def _identity() -> tuple[int | None, str | None]:
    try:
        if current_user.is_authenticated:
            return int(current_user.id), None
    except Exception:
        pass
    try:
        from App import get_guest_session_id

        return None, get_guest_session_id()
    except Exception:
        return None, None


def followthrough_enabled(user_id: int | None) -> bool:
    """Kill switch, bucketed per user like every other rollout."""
    try:
        from App import feature_enabled, feature_enabled_for_user

        if user_id:
            return bool(feature_enabled_for_user(FLAG_KEY, user_id))
        return bool(feature_enabled(FLAG_KEY))
    except Exception:
        return False


def _now() -> datetime:
    # Local wall clock, like next_action_glue: "today" is a question about
    # the student's day, and the window engine works in local time.
    return datetime.now()


# ── the plan ─────────────────────────────────────────────────────────


def _active_row(user_id: int | None, guest_id: str | None):
    from App import SavedSchedule

    query = SavedSchedule.query
    if user_id is not None:
        query = query.filter_by(user_id=user_id, is_active=True)
    else:
        query = query.filter_by(guest_session_id=guest_id, is_active=True)
    return query.order_by(SavedSchedule.created_at.desc()).first()


def _json(text: Any, default):
    try:
        value = json.loads(text) if text else default
    except Exception:
        return default
    return value if isinstance(value, type(default)) else default


def _load_plan(user_id: int | None, guest_id: str | None) -> tuple[dict | None, dict]:
    try:
        row = _active_row(user_id, guest_id)
    except Exception as exc:
        logger.warning("plan load failed: %s", exc)
        return None, {}
    if row is None:
        return None, {}
    return _json(row.schedule_data, {}) or None, _json(row.progress_json, {})


def _save_plan(user_id: int | None, guest_id: str | None, data: dict, progress: dict) -> bool:
    """Write the adjusted plan in place, keeping the row's identity.

    Progress is replaced, not merged: every block in the adjusted plan has a
    fresh id (see :func:`_finalize`), so the old progress keys refer to
    blocks that no longer exist — and, keyed by position as they were, would
    otherwise light up the wrong blocks as done.
    """
    from App import db, invalidate_schedule_cache

    try:
        row = _active_row(user_id, guest_id)
        if row is None:
            return False
        row.schedule_data = json.dumps(data)
        row.progress_json = json.dumps(progress or {})
        db.session.commit()
    except Exception as exc:
        logger.warning("plan save failed: %s", exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False
    try:
        invalidate_schedule_cache(user_id=user_id, guest_id=guest_id)
    except Exception:
        pass
    return True


def _payload() -> Mapping[str, Any]:
    try:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            return body
    except Exception:
        pass
    return dict(request.args or {})


def _hours(payload: Mapping[str, Any]) -> float:
    try:
        return max(0.25, min(16.0, float(payload.get("hours_per_day") or 2)))
    except (TypeError, ValueError):
        return 2.0


def _priority_label(block: Mapping[str, Any]) -> str:
    score = block.get("priority_score", block.get("priority"))
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return "High" if score >= 75 else "Medium" if score >= 45 else "Low"
    label = str(score or "Medium").strip().title()
    return label if label in ("High", "Medium", "Low") else "Medium"


def _finalize(new_data: dict, old_data: dict, old_progress: dict, today: date) -> tuple[dict, dict]:
    """Make an adjusted plan render exactly like a generated one.

    1. Enrichment (colours, workload level, energy) — fed with assignment
       facts read back off the old plan's own blocks, so priority labels and
       difficulty survive instead of being re-inferred from nothing.
    2. Today's finished blocks are carried across, still ticked. A student
       who did two blocks before their evening fell apart should still see
       those two blocks done.
    3. Fresh block ids with a per-adjustment prefix, plus the Interactive
       View's kind / redirect / checklist. Ids used to be positional
       (``d1-b1``), so a replanned week re-used ids for different work and
       browser-stored progress lit up the wrong blocks.
    """
    from App import (
        BLOCK_KIND_REDIRECT,
        build_block_checklist,
        classify_block_kind,
        enrich_schedule_data,
    )

    payload = _payload()
    meta: dict[str, dict] = {}
    carried: list[tuple[dict, Any]] = []
    for day in (old_data or {}).get("schedule") or []:
        if not isinstance(day, dict):
            continue
        is_today = str(day.get("date") or "")[:10] == today.isoformat()
        for block in day.get("blocks") or []:
            if not isinstance(block, dict) or block.get("is_break"):
                continue
            title = block.get("parent_title") or block.get("assignment")
            if title and title not in meta:
                meta[title] = {
                    "title": title,
                    "priority": _priority_label(block),
                    "difficulty": str(block.get("difficulty") or "Medium").title(),
                    "due_date": block.get("due_date") or "",
                }
            key = str(block.get("block_id") or block.get("id") or "")
            entry = old_progress.get(key) if key else None
            done = entry is True or (isinstance(entry, dict) and entry.get("done"))
            if is_today and done:
                carried.append((dict(block), entry))

    try:
        new_data = enrich_schedule_data(
            new_data, list(meta.values()),
            str(payload.get("preferred_time") or "evening"), _hours(payload),
        )
    except Exception as exc:
        logger.warning("enrich failed (non-fatal): %s", exc)

    schedule = new_data.setdefault("schedule", [])
    if carried:
        today_row = next(
            (d for d in schedule if str(d.get("date") or "")[:10] == today.isoformat()),
            None,
        )
        if today_row is None:
            today_row = {
                "date": today.isoformat(),
                "day_name": today.strftime("%A"),
                "blocks": [],
            }
            schedule.insert(0, today_row)
        for block, _entry in carried:
            block["carried_done"] = True
        today_row["blocks"] = [b for b, _ in carried] + list(today_row.get("blocks") or [])

    # Autopilot's settings and history belong to the plan and survive any
    # change; its undo copy does not — once the student changes the plan
    # themselves, "undo autopilot" would also undo them.
    old_auto = (old_data or {}).get("autopilot")
    if isinstance(old_auto, dict):
        new_data["autopilot"] = {k: v for k, v in old_auto.items() if k != "undo"}

    revision = format(zlib.crc32(f"{time.time_ns()}".encode()) & 0xFFFFFF, "06x")
    progress: dict[str, Any] = {}
    carried_ids = {id(b) for b, _ in carried}
    entries = {id(b): e for b, e in carried}
    n = 0
    for day_index, day in enumerate(schedule, start=1):
        for block in day.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            n += 1
            prefix = "unplaced" if block.get("unplaced") else f"a{revision}-d{day_index}"
            block["block_id"] = f"{prefix}-b{n}"
            if block.get("is_break"):
                block["kind"] = "break"
                block.setdefault("redirect", None)
                continue
            if id(block) in carried_ids:
                entry = entries[id(block)]
                progress[block["block_id"]] = entry if isinstance(entry, dict) else {"done": True}
                continue
            kind = classify_block_kind(block.get("assignment", ""), block.get("course", ""))
            block["kind"] = kind
            block["redirect"] = BLOCK_KIND_REDIRECT.get(kind)
            try:
                block["checklist"] = build_block_checklist(block, kind, {})
            except Exception:
                block.setdefault("checklist", [])
    return new_data, progress


# ── training data ────────────────────────────────────────────────────


def _record_outcomes(user_id, guest_id, old_data, old_progress, today) -> None:
    """Append the outgoing plan's past blocks to the durable outcome log."""
    if user_id is None:
        return
    rows = harvest_plan_outcomes(old_data, old_progress, today)
    if not rows:
        return
    from App import StudentSignal, db

    from intelliplan.repositories.signals import SignalRepository

    SignalRepository(StudentSignal, db.session).emit(
        user_id, OUTCOME_SIGNAL, subject_type="plan", value={"rows": rows[-300:]},
    )


def _outcome_rows(user_id: int | None, guest_id: str | None, today: date) -> list[dict]:
    """Every labelled past block we know about, de-duplicated downstream.

    Three sources: the durable log, the live plan's past days, and older
    saved plans — each bounded to the days it was actually the student's
    plan, so a superseded week is not counted as a week they abandoned.
    """
    rows: list[dict] = []
    since = utcnow() - timedelta(days=HISTORY_DAYS)
    try:
        from App import SavedSchedule, StudentSignal

        if user_id is not None:
            signals = (
                StudentSignal.query
                .filter(StudentSignal.user_id == user_id)
                .filter(StudentSignal.kind == OUTCOME_SIGNAL)
                .filter(StudentSignal.occurred_at >= since)
                .order_by(StudentSignal.occurred_at.desc())
                .limit(200)
                .all()
            )
            for s in signals:
                rows.extend(_json(s.value_json, {}).get("rows") or [])

        query = SavedSchedule.query
        if user_id is not None:
            query = query.filter(SavedSchedule.user_id == user_id)
        else:
            query = query.filter(SavedSchedule.guest_session_id == guest_id)
        plans = (
            query.filter(SavedSchedule.created_at >= since)
            .order_by(SavedSchedule.created_at.desc())
            .limit(SAVED_PLAN_LIMIT)
            .all()
        )
        valid_until = None
        for plan in plans:
            created = plan.created_at.date() if plan.created_at else None
            rows.extend(harvest_plan_outcomes(
                _json(plan.schedule_data, {}), _json(plan.progress_json, {}), today,
                valid_from=created, valid_until=valid_until,
            ))
            valid_until = created
    except Exception as exc:
        logger.warning("outcome load failed: %s", exc)
    return rows


def load_prior() -> Prior:
    """The latest population prior, cached in-process for ten minutes."""
    with _prior_lock:
        cached = _prior_cache.get("prior")
        if cached is not None and time.monotonic() - _prior_cache["at"] < PRIOR_CACHE_SECONDS:
            return cached
    prior = DEFAULT_PRIOR
    try:
        from App import ModelPrior

        row = ModelPrior.query.filter_by(key=PRIOR_KEY).first()
        if row is not None:
            prior = Prior.from_dict(_json(row.payload_json, {}))
    except Exception as exc:
        logger.warning("prior load failed, using default: %s", exc)
    with _prior_lock:
        _prior_cache.update(at=time.monotonic(), prior=prior)
    return prior


def student_context_extras(user_id: int | None, guest_id: str | None) -> dict[str, Any]:
    """The Follow-Through inputs for a ``StudentContext``."""
    return {
        "outcome_rows": tuple(_outcome_rows(user_id, guest_id, date.today())),
        "followthrough_prior": load_prior(),
    }


# ── the service ──────────────────────────────────────────────────────


def _build_service(user_id: int | None, guest_id: str | None, payload: Mapping[str, Any]):
    from App import (
        _planner_busy_by_date,
        _planner_concept_mastery,
        _planner_feedback_rows,
        _planner_session_rows,
        build_scheduler_personalization,
    )

    from intelliplan.intelligence.planner import PlannerConfig
    from intelliplan.services.scheduling import SchedulingService, StudentContext

    dna, availability, commitments = build_scheduler_personalization(
        user_id=user_id, guest_id=guest_id
    )
    comfort = int(round(_hours(payload) * 60)) if payload.get("hours_per_day") else None
    try:
        busy = _planner_busy_by_date()
    except Exception:
        busy = {}
    context = StudentContext(
        availability=availability,
        commitments=commitments,
        preferred_time=str(payload.get("preferred_time") or "evening"),
        feedback_rows=_planner_feedback_rows(user_id, guest_id),
        session_rows=_planner_session_rows(user_id, guest_id),
        concept_mastery=_planner_concept_mastery(user_id),
        weak_days=tuple(getattr(dna, "weak_days", ()) or ()),
        daily_target_minutes=comfort,
        busy_by_date=busy,
        **student_context_extras(user_id, guest_id),
    )
    return SchedulingService(context, PlannerConfig(), follow_through=True)


def _session_reality(task_ids, user_id, guest_id) -> dict:
    from App import _session_reality as reality

    return reality(task_ids, user_id, guest_id)


def _record_version(user_id: int, result, data: dict) -> None:
    from App import _record_plan_version

    _record_plan_version(
        user_id, result.plan, None, data, trigger=f"adjust:{result.disruption.kind}"[:48],
    )


def _emit_signal(user_id: int, kind: str, value: dict) -> None:
    from App import StudentSignal, db

    from intelliplan.repositories.signals import SignalRepository

    SignalRepository(StudentSignal, db.session).emit(user_id, kind, value=value)


# ── autopilot ────────────────────────────────────────────────────────


def _build_rows(assignments: list) -> list:
    """The page's assignment rows → planner rows, the same way generation does."""
    from App import _planner_task_rows, _saved_descriptions_for, infer_task_difficulty

    normalized = []
    for a in assignments[:300]:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        normalized.append({
            **a,
            "difficulty": a.get("difficulty") or infer_task_difficulty(
                a.get("points_possible"), a.get("priority", "Medium"), a.get("due_date")
            ),
        })
    if not normalized:
        return []
    try:
        descriptions = _saved_descriptions_for(normalized)
    except Exception:
        descriptions = None
    return _planner_task_rows(normalized, [], descriptions=descriptions)


def _finalize_autopilot(user_id, guest_id, run, old_data, old_progress, today):
    """Stamp the autonomous plan and keep the old one for a one-tap undo."""
    data, progress = _finalize(run.data, old_data, old_progress, today)
    previous = {k: v for k, v in (old_data or {}).items() if k != "autopilot"}
    old_auto = (old_data or {}).get("autopilot")
    if isinstance(old_auto, dict):
        previous["autopilot"] = {k: v for k, v in old_auto.items() if k != "undo"}
    data["autopilot"] = {
        **run.state.to_json(),
        "undo": {"data": previous, "progress": old_progress or {}, "at": run.state.to_json().get("last_run")},
    }
    return data, progress


def _notify(user_id: int, body: dict) -> None:
    """Tell the student what autopilot did while they were away."""
    try:
        import notifications_glue

        notifications_glue.on_plan_rescheduled(
            user_id, int(body.get("moved_sittings") or 0) or 1, "autopilot"
        )
    except Exception as exc:
        logger.warning("autopilot notification failed: %s", exc)


def _active_owners() -> list:
    """Signed-in students with a live plan, for the cron."""
    from App import SavedSchedule

    try:
        rows = (
            SavedSchedule.query
            .filter(SavedSchedule.is_active.is_(True))
            .filter(SavedSchedule.user_id.isnot(None))
            .order_by(SavedSchedule.created_at.desc())
            .limit(2000)
            .all()
        )
    except Exception as exc:
        logger.warning("active plan listing failed: %s", exc)
        return []
    seen, out = set(), []
    for r in rows:
        if r.user_id in seen or not followthrough_enabled(r.user_id):
            continue
        seen.add(r.user_id)
        out.append((r.user_id, None))
    return out


# ── population prior ─────────────────────────────────────────────────


def _cron_guard():
    from App import _cron_secret_from_request, _cron_unauthorised

    expected = os.getenv("CRON_SECRET", "")
    if not expected:
        return jsonify({"status": "error", "message": "cron not configured"}), 503
    if not hmac.compare_digest(str(expected), str(_cron_secret_from_request())):
        return jsonify(_cron_unauthorised()), 401
    return None


def refit_population_prior() -> dict[str, Any]:
    """Pool every student's outcomes into the prior new students start from.

    Reads Active-study sittings and the durable plan-outcome log — never
    titles — and writes one row of coefficients. Safe to run as often as
    you like; the result only changes as the population's behaviour does.
    """
    from App import ActiveSession, ModelPrior, StudentSignal, db

    now = utcnow()
    since = now - timedelta(days=HISTORY_DAYS)
    by_user: dict[Any, list] = {}

    sessions = (
        ActiveSession.query
        .filter(ActiveSession.user_id.isnot(None))
        .filter(ActiveSession.state.in_(("completed", "abandoned")))
        .filter(ActiveSession.started_at >= since)
        .filter(ActiveSession.active_seconds > 60)
        .order_by(ActiveSession.started_at.desc())
        .limit(REFIT_SESSION_ROWS)
        .all()
    )
    grouped: dict[Any, list[dict]] = {}
    for s in sessions:
        grouped.setdefault(s.user_id, []).append(s.to_observation())
    for uid, rows in grouped.items():
        by_user.setdefault(uid, []).extend(observations_from_sessions(rows))

    signals = (
        StudentSignal.query
        .filter(StudentSignal.kind == OUTCOME_SIGNAL)
        .filter(StudentSignal.occurred_at >= since)
        .order_by(StudentSignal.occurred_at.desc())
        .limit(REFIT_SIGNAL_ROWS)
        .all()
    )
    outcome_rows: dict[Any, list[dict]] = {}
    for s in signals:
        outcome_rows.setdefault(s.user_id, []).extend(_json(s.value_json, {}).get("rows") or [])
    for uid, rows in outcome_rows.items():
        by_user.setdefault(uid, []).extend(observations_from_outcomes(rows))

    prior = fit_population_prior(by_user, now=now)
    payload = prior.to_dict()
    row = ModelPrior.query.filter_by(key=PRIOR_KEY).first()
    if row is None:
        row = ModelPrior(key=PRIOR_KEY)
        db.session.add(row)
    row.payload_json = json.dumps(payload)
    row.sample_size = prior.sample_size
    row.users = prior.users
    db.session.commit()
    with _prior_lock:
        _prior_cache.update(at=0.0, prior=None)
    return {
        "users": prior.users,
        "sample_size": prior.sample_size,
        "fitted": prior.sample_size > 0,
        "means": payload["means"],
    }


followthrough_bp = create_adjust_blueprint(
    AdjustDeps(
        identity=_identity,
        feature_enabled=followthrough_enabled,
        now=_now,
        load_plan=_load_plan,
        build_service=_build_service,
        finalize=_finalize,
        save_plan=_save_plan,
        session_reality=_session_reality,
        record_outcomes=_record_outcomes,
        record_version=_record_version,
        emit_signal=_emit_signal,
        refit_prior=refit_population_prior,
        cron_guard=_cron_guard,
        build_rows=_build_rows,
        finalize_autopilot=_finalize_autopilot,
        notify=_notify,
        active_owners=_active_owners,
    )
)


# ── in-process scheduler ─────────────────────────────────────────────
#
# Autopilot and the prior refit both have HTTP cron endpoints, but an
# endpoint nobody calls does nothing — the notification outbox sat unswept
# for exactly that reason. So, like the notification ticker, these run on
# their own inside the web process. A lease row per job means one gunicorn
# worker runs each job, and because the lease expiry *is* the next due time,
# a restart neither re-runs a job early nor skips it.
#
# Env:
#   FOLLOWTHROUGH_INPROCESS_CRON=0      hand both jobs to an external scheduler
#   AUTOPILOT_INTERVAL_HOURS=4          how often autopilot sweeps every plan
#   PRIOR_REFIT_INTERVAL_HOURS=24       how often the population prior is refit

SCHEDULER_TICK_SECONDS = 300
_scheduler_started = False


def _interval(env: str, default_hours: float) -> timedelta:
    try:
        hours = float(os.getenv(env, default_hours))
    except (TypeError, ValueError):
        hours = default_hours
    return timedelta(hours=max(0.25, hours))


def _jobs() -> list[tuple[str, timedelta, Any]]:
    return [
        ("followthrough-autopilot", _interval("AUTOPILOT_INTERVAL_HOURS", 4), _autopilot_job),
        ("followthrough-prior", _interval("PRIOR_REFIT_INTERVAL_HOURS", 24), _prior_job),
    ]


def _autopilot_job(app) -> dict:
    # The service and finalizer read request settings when there are any;
    # an empty request context gives them the defaults, exactly as the cron
    # endpoint would with an empty body.
    with app.test_request_context("/cron/autopilot", method="POST"):
        return followthrough_bp.sweep_autopilot()


def _prior_job(app) -> dict:
    return refit_population_prior()


def _claim(name: str, every: timedelta) -> bool:
    """Take the job if it is due. Atomic across workers and restarts."""
    from App import db

    from intelliplan.notifications.models import register_lease

    CronLease = register_lease(db)
    now = utcnow()
    try:
        if db.session.get(CronLease, name) is None:
            # First boot ever: due now.
            db.session.add(CronLease(name=name, holder="", expires_at=now))
            db.session.commit()
    except Exception:
        db.session.rollback()
    try:
        claimed = (
            db.session.query(CronLease)
            .filter(CronLease.name == name, CronLease.expires_at <= now)
            .update(
                {"holder": str(os.getpid()), "expires_at": now + every, "last_run_at": now},
                synchronize_session=False,
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("%s lease acquire failed: %s", name, exc)
        return False
    return bool(claimed)


def run_due_jobs(app) -> dict[str, Any]:
    """Run every job that is due and that this process wins. Returns results."""
    results: dict[str, Any] = {}
    for name, every, job in _jobs():
        with app.app_context():
            if not _claim(name, every):
                continue
            try:
                results[name] = job(app)
                logger.info("%s ran: %s", name, results[name])
            except Exception as exc:
                # The lease stays taken until its interval passes; a job that
                # fails does not retry in a tight loop and hammer the DB.
                logger.warning("%s failed: %s", name, exc)
                results[name] = {"error": str(exc)[:200]}
    return results


def start_scheduler(app) -> bool:
    """Start the background scheduler. Safe to call more than once."""
    global _scheduler_started
    import sys

    if _scheduler_started:
        return False
    if os.getenv("FOLLOWTHROUGH_INPROCESS_CRON", "1") == "0":
        logger.info("follow-through scheduler disabled by env")
        return False
    if "pytest" in sys.modules:
        # A sweep rewriting saved plans in the middle of a test run would
        # make every plan-related test flaky.
        return False
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False

    def _loop() -> None:
        import random

        # Staggered, so every worker booting at once does not hit the DB together.
        time.sleep(random.uniform(30, 120))
        while True:
            try:
                run_due_jobs(app)
            except Exception as exc:
                logger.warning("follow-through scheduler tick failed: %s", exc)
            time.sleep(SCHEDULER_TICK_SECONDS)

    threading.Thread(target=_loop, name="ip-followthrough", daemon=True).start()
    _scheduler_started = True
    logger.info("follow-through scheduler started")
    return True
