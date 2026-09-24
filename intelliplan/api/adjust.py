"""Follow-Through engine blueprint — one-tap rescheduling and on-time forecasts.

Endpoints:

* ``POST /api/schedule/adjust``            — apply an intent ("can't study
  today", "only 30 minutes", "push this", "keep it where I put it", …) to
  the saved plan. ``preview: true`` returns the consequence without saving.
* ``GET  /api/schedule/forecast``          — on-time probability per
  assignment for the plan as it stands, plus what the Follow-Through model
  has learned about this student.
* ``POST /api/schedule/autopilot``          — let the plan re-plan itself:
  missed work gets new days, newly posted assignments are absorbed, an
  at-risk deadline is protected. Explained, and undoable.
* ``POST /api/schedule/autopilot/undo``     — put the previous plan back.
* ``POST /api/schedule/autopilot/settings`` — ``{"enabled": bool}``.
* ``POST /cron/refit-followthrough-prior`` — refit the population prior new
  students start from. ``CRON_SECRET``-guarded like every other cron.
* ``POST /cron/autopilot``                  — run autopilot for every student
  with a live plan, so it works even for someone who never opens the page.

Works for signed-in students and for guests with a saved plan — rescheduling
is the thing a student reaches for on a bad day, and a bad day is not the
moment to ask them to make an account.

Everything arrives injected via :class:`AdjustDeps`, so this module never
imports ``App`` and is unit-testable with fakes.

Feature gating
--------------
``followthrough_engine`` is a **kill switch** (default on). Off, every route
404s and the scheduler behaves exactly as it did before this engine existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Mapping

from flask import Blueprint, abort, jsonify, request

logger = logging.getLogger(__name__)

FLAG_KEY = "followthrough_engine"


@dataclass(frozen=True)
class AdjustDeps:
    """Everything the blueprint needs, injected by ``followthrough_glue``."""

    #: ``(user_id, guest_session_id)`` — at most one is set; both ``None``
    #: means nobody we can scope a plan to.
    identity: Callable[[], tuple[int | None, str | None]]
    feature_enabled: Callable[[int | None], bool]
    now: Callable[[], datetime]
    #: ``(schedule_data | None, progress)`` for the active saved plan.
    load_plan: Callable[[int | None, str | None], tuple[dict | None, dict]]
    #: A ``SchedulingService`` configured for this student. Receives the
    #: request payload for ``preferred_time`` / ``hours_per_day``.
    build_service: Callable[[int | None, str | None, Mapping[str, Any]], Any]
    #: ``(new_data, old_data, old_progress, today) -> (final_data, progress)``
    #: — stamps block ids, carries today's finished blocks across.
    finalize: Callable[[dict, dict, dict, date], tuple[dict, dict]] = field(
        default=lambda data, old, progress, today: (data, {})
    )
    save_plan: Callable[[int | None, str | None, dict, dict], bool] = field(
        default=lambda uid, gid, data, progress: False
    )
    #: Active-study sittings per task: ``{"abandoned": {...}, "finished": {...}}``.
    session_reality: Callable[[set, int | None, str | None], dict] = field(
        default=lambda ids, uid, gid: {}
    )
    #: Log the outgoing plan's past blocks as training outcomes.
    record_outcomes: Callable[[int | None, str | None, dict, dict, date], Any] = field(
        default=lambda uid, gid, data, progress, today: None
    )
    record_version: Callable[[int, Any, dict], Any] = field(
        default=lambda uid, result, data: None
    )
    emit_signal: Callable[[int, str, dict], Any] = field(
        default=lambda uid, kind, value: None
    )
    refit_prior: Callable[[], dict] = field(default=lambda: {})
    #: Returns a Flask response to reject the cron call, or ``None`` to allow.
    cron_guard: Callable[[], Any] = field(default=lambda: None)
    #: Client assignment rows → planner rows (sizing, priority, kind).
    build_rows: Callable[[list], list] = field(default=lambda rows: [])
    #: ``(uid, gid, run, old_data, old_progress, today) -> (data, progress)``:
    #: stamp ids, attach autopilot state and the undo copy.
    finalize_autopilot: Callable[..., tuple[dict, dict]] = field(
        default=lambda uid, gid, run, old, progress, today: (run.data, {})
    )
    notify: Callable[[int, Any], Any] = field(default=lambda uid, run: None)
    #: ``[(user_id, None)]`` for every student with a live plan (cron).
    active_owners: Callable[[], list] = field(default=lambda: [])


def _task_ids(schedule_data: Mapping[str, Any] | None) -> set[str]:
    return {
        str(b.get("task_id"))
        for d in (schedule_data or {}).get("schedule") or []
        if isinstance(d, Mapping)
        for b in d.get("blocks") or []
        if isinstance(b, Mapping) and b.get("task_id")
    }


def create_adjust_blueprint(deps: AdjustDeps) -> Blueprint:
    bp = Blueprint("followthrough", __name__)

    def _require_owner() -> tuple[int | None, str | None]:
        uid, gid = deps.identity()
        if uid is None and not gid:
            abort(401, description="Sign in, or save a plan first.")
        if not deps.feature_enabled(uid):
            abort(404)
        return uid, gid

    # ── write: apply an intent ───────────────────────────────────────

    @bp.route("/api/schedule/adjust", methods=["POST"])
    def adjust():
        uid, gid = _require_owner()
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"status": "error", "message": "Send a JSON object."}), 400
        preview = bool(payload.get("preview"))

        schedule_data, progress = deps.load_plan(uid, gid)
        if not schedule_data or not schedule_data.get("schedule"):
            return jsonify({
                "status": "none",
                "message": "There's no saved plan to adjust yet — make one first.",
            }), 404

        now = deps.now()
        today = now.date()
        try:
            reality = deps.session_reality(_task_ids(schedule_data), uid, gid) or {}
        except Exception as exc:
            logger.warning("session reality failed: %s", exc)
            reality = {}

        try:
            service = deps.build_service(uid, gid, payload)
            result, data = service.adjust(
                schedule_data,
                progress,
                payload,
                now=now,
                credit=reality.get("abandoned") or {},
                finished=tuple(reality.get("finished") or ()),
            )
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except Exception as exc:
            logger.exception("adjust failed: %s", exc)
            return jsonify({
                "status": "error",
                "message": "Couldn't rework the plan just now. Your plan is unchanged.",
                "retryable": True,
            }), 500

        try:
            data, new_progress = deps.finalize(data, schedule_data, progress, today)
        except Exception as exc:
            logger.warning("finalize failed (non-fatal): %s", exc)
            new_progress = {}

        saved = False
        if not preview:
            try:
                # Before the old plan is overwritten: its past blocks are
                # exactly the done / not-done evidence the model learns from.
                deps.record_outcomes(uid, gid, schedule_data, progress, today)
            except Exception as exc:
                logger.warning("outcome logging failed (non-fatal): %s", exc)
            saved = bool(deps.save_plan(uid, gid, data, new_progress))
            if uid is not None and saved:
                try:
                    deps.record_version(uid, result, data)
                except Exception as exc:
                    logger.warning("version audit failed (non-fatal): %s", exc)
                try:
                    deps.emit_signal(uid, "schedule_adjusted", {
                        "intent": result.disruption.kind,
                        "strategy": result.strategy,
                        "moved": result.moved_sittings,
                        "kept": result.kept_sittings,
                    })
                except Exception:
                    pass

        return jsonify({
            "status": "ok",
            "preview": preview,
            "saved": saved,
            "data": data,
            "progress": new_progress,
            **result.summary(),
        })

    # ── autopilot ────────────────────────────────────────────────────

    def _run_autopilot(uid, gid, rows, *, force=False):
        """Returns ``(body, status)``. Shared by the page and the cron."""
        schedule_data, progress = deps.load_plan(uid, gid)
        if not schedule_data or not schedule_data.get("schedule"):
            return {"status": "none", "acted": False}, 200
        now = deps.now()
        today = now.date()
        try:
            reality = deps.session_reality(_task_ids(schedule_data), uid, gid) or {}
        except Exception:
            reality = {}
        finished_titles = [
            str(r.get("title") or "") for r in rows or []
            if isinstance(r, dict) and str(r.get("status") or "").lower()
            in ("submitted", "graded", "completed", "complete", "done", "excused")
        ]
        try:
            planner_rows = deps.build_rows([
                r for r in rows or [] if isinstance(r, dict)
                and str(r.get("title") or "") not in finished_titles
            ]) if rows else []
            service = deps.build_service(uid, gid, request.get_json(silent=True) or {})
            run = service.autopilot(
                schedule_data, progress, planner_rows, now=now,
                credit=reality.get("abandoned") or {},
                finished=tuple(reality.get("finished") or ()),
                finished_titles=finished_titles, force=force,
            )
        except Exception as exc:
            logger.exception("autopilot failed: %s", exc)
            return {"status": "error", "acted": False}, 500

        from intelliplan.intelligence.autopilot import AutopilotState

        state = AutopilotState.from_json(schedule_data.get("autopilot"))
        if run is None:
            return {"status": "ok", "acted": False, "enabled": state.enabled}, 200

        try:
            deps.record_outcomes(uid, gid, schedule_data, progress, today)
        except Exception as exc:
            logger.warning("outcome logging failed (non-fatal): %s", exc)
        try:
            data, new_progress = deps.finalize_autopilot(uid, gid, run, schedule_data, progress, today)
        except Exception as exc:
            logger.exception("autopilot finalize failed: %s", exc)
            return {"status": "error", "acted": False}, 500
        if not deps.save_plan(uid, gid, data, new_progress):
            return {"status": "error", "acted": False}, 500
        if uid is not None:
            for hook in (
                lambda: deps.record_version(uid, run.replan, data),
                lambda: deps.emit_signal(uid, "autopilot_acted", {
                    "triggers": list(run.triggers), "moved": run.replan.moved_sittings,
                    "strategy": run.replan.strategy,
                }),
            ):
                try:
                    hook()
                except Exception:
                    pass
        # The undo copy stays on the server; the page only needs to know it
        # exists. Shipping it would double every payload for a button most
        # students never press.
        client_data = dict(data)
        if isinstance(client_data.get("autopilot"), dict):
            client_data["autopilot"] = {
                k: v for k, v in client_data["autopilot"].items() if k != "undo"
            }
        return {
            "status": "ok",
            "acted": True,
            "enabled": True,
            "data": client_data,
            "progress": new_progress,
            "undo_available": True,
            **run.summary(),
        }, 200

    @bp.route("/api/schedule/autopilot", methods=["POST"])
    def autopilot():
        uid, gid = _require_owner()
        payload = request.get_json(silent=True) or {}
        rows = payload.get("assignments") if isinstance(payload, dict) else None
        body, status = _run_autopilot(uid, gid, rows if isinstance(rows, list) else [])
        return jsonify(body), status

    @bp.route("/api/schedule/autopilot/undo", methods=["POST"])
    def autopilot_undo():
        uid, gid = _require_owner()
        schedule_data, _progress = deps.load_plan(uid, gid)
        auto = (schedule_data or {}).get("autopilot") or {}
        undo = auto.get("undo") if isinstance(auto, dict) else None
        if not isinstance(undo, dict) or not isinstance(undo.get("data"), dict):
            return jsonify({"status": "error", "message": "There's nothing to undo."}), 409

        from intelliplan.intelligence.autopilot import AutopilotState

        state = AutopilotState.from_json(auto)
        now = deps.now()
        # An undo is the student saying "not like that". Stand down for
        # the rest of today rather than redoing it on the next page load.
        state = AutopilotState(
            enabled=state.enabled, last_run=state.last_run,
            suppressed_until=now.date(),
            log=state.log,
        ).with_entry({"at": now.isoformat(timespec="seconds"), "headline": "You undid autopilot's change.", "undone": True})
        data = dict(undo["data"])
        data["autopilot"] = state.to_json()
        progress = undo.get("progress") if isinstance(undo.get("progress"), dict) else {}
        if not deps.save_plan(uid, gid, data, progress):
            return jsonify({"status": "error", "message": "Couldn't restore the plan."}), 500
        if uid is not None:
            try:
                deps.emit_signal(uid, "autopilot_undone", {"at": now.isoformat(timespec="seconds")})
            except Exception:
                pass
        return jsonify({"status": "ok", "data": data, "progress": progress})

    @bp.route("/api/schedule/autopilot/settings", methods=["POST"])
    def autopilot_settings():
        uid, gid = _require_owner()
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
            return jsonify({"status": "error", "message": "Send {\"enabled\": true|false}."}), 400
        schedule_data, progress = deps.load_plan(uid, gid)
        if not schedule_data:
            return jsonify({"status": "none"}), 404

        from intelliplan.intelligence.autopilot import AutopilotState

        auto = schedule_data.get("autopilot") if isinstance(schedule_data.get("autopilot"), dict) else {}
        state = AutopilotState.from_json(auto)
        state = AutopilotState(enabled=payload["enabled"], last_run=state.last_run,
                               suppressed_until=None, log=state.log)
        schedule_data["autopilot"] = {**state.to_json(), **({"undo": auto["undo"]} if auto.get("undo") else {})}
        if not deps.save_plan(uid, gid, schedule_data, progress):
            return jsonify({"status": "error"}), 500
        return jsonify({"status": "ok", "enabled": state.enabled})

    @bp.route("/cron/autopilot", methods=["GET", "POST"])
    def cron_autopilot():
        rejected = deps.cron_guard()
        if rejected is not None:
            return rejected
        acted = checked = failed = 0
        for uid, gid in deps.active_owners():
            checked += 1
            try:
                body, _status = _run_autopilot(uid, gid, [])
            except Exception:
                failed += 1
                continue
            if body.get("acted"):
                acted += 1
                try:
                    deps.notify(uid, body)
                except Exception:
                    pass
        return jsonify({"status": "ok", "checked": checked, "acted": acted, "failed": failed})

    # ── read: will I make it? ────────────────────────────────────────

    @bp.route("/api/schedule/forecast", methods=["GET"])
    def forecast():
        uid, gid = _require_owner()
        schedule_data, progress = deps.load_plan(uid, gid)
        if not schedule_data or not schedule_data.get("schedule"):
            return jsonify({"status": "none"})
        try:
            service = deps.build_service(uid, gid, dict(request.args))
            body = service.forecast(schedule_data, progress, now=deps.now())
        except Exception as exc:
            logger.exception("forecast failed: %s", exc)
            return jsonify({"status": "error", "message": "Forecast unavailable."}), 500
        return jsonify({"status": "ok", **body})

    # ── cron: refit the population prior ────────────────────────────

    @bp.route("/cron/refit-followthrough-prior", methods=["GET", "POST"])
    def refit_prior():
        rejected = deps.cron_guard()
        if rejected is not None:
            return rejected
        try:
            summary = deps.refit_prior() or {}
        except Exception as exc:
            logger.exception("prior refit failed: %s", exc)
            return jsonify({"status": "error", "message": "refit failed"}), 500
        return jsonify({"status": "ok", **summary})

    return bp
