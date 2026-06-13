"""Command Center blueprint factory.

``create_command_center_blueprint(deps)`` returns a Flask blueprint with:

* ``GET  /command-center``        — the page (template rendered with payload)
* ``GET  /api/today``             — full payload, cached briefing
* ``POST /api/today/refresh``     — force-rebuild the briefing
* ``GET  /api/today/explain``     — rationale for one score
* ``POST /cron/refresh-briefings``— cron sweeper (X-Cron-Token gated)

All dependencies arrive injected via :class:`CommandCenterDeps` so this
module never imports ``App`` and stays unit-testable with fakes.

Feature gating: the ``command_center`` flag is a KILL SWITCH — the
surface is on by default (matching ``feature_enabled``'s default-True
convention and the product decision that this is the main page). When
the flag is off, API routes return 404 and the page falls back to the
legacy dashboard redirect.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from flask import Blueprint, abort, jsonify, redirect, render_template, request

from intelliplan.api.serialize import (
    forecast_to_dict,
    health_to_dict,
    planned_task_to_dict,
    today_to_dict,
)

logger = logging.getLogger(__name__)

FLAG_KEY = "command_center"


@dataclass(frozen=True)
class CommandCenterDeps:
    """Everything the blueprint needs, injected by the glue layer."""

    get_service: Callable[[], Any]                  # → TodayService
    feature_enabled: Callable[[str], bool]
    current_user_id: Callable[[], int | None]       # None when not authenticated
    emit_signal: Callable[..., Any] = field(default=lambda *a, **k: None)
    stale_briefing_user_ids: Callable[[], list[int]] = field(default=lambda: [])
    cron_token: Callable[[], str] = field(default=lambda: os.getenv("CRON_TOKEN", ""))


def create_command_center_blueprint(deps: CommandCenterDeps) -> Blueprint:
    bp = Blueprint("command_center", __name__)

    # ── guards ───────────────────────────────────────────────────────

    def _require_user() -> int:
        uid = deps.current_user_id()
        if uid is None:
            abort(401, description="Authentication required.")
        return uid

    def _require_flag() -> None:
        if not deps.feature_enabled(FLAG_KEY):
            abort(404)

    # ── page ─────────────────────────────────────────────────────────

    @bp.route("/command-center")
    def command_center_page():
        if not deps.feature_enabled(FLAG_KEY):
            return redirect("/dashboard?classic=1")
        uid = deps.current_user_id()
        if uid is None:
            return redirect("/login")
        try:
            payload = deps.get_service().build(uid)
            data = today_to_dict(payload)
            error = None
        except Exception as exc:
            logger.exception("command-center build failed: %s", exc)
            data, error = None, "We couldn't load your day. Pull to refresh or try again."
        deps.emit_signal(uid, "command_center_opened", subject_type="briefing")
        return render_template("command_center.html", today=data,
                               load_error=error, active_page="command_center")

    # ── API ──────────────────────────────────────────────────────────

    @bp.route("/api/today", methods=["GET"])
    def api_today():
        _require_flag()
        uid = _require_user()
        payload = deps.get_service().build(uid)
        return jsonify(today_to_dict(payload))

    @bp.route("/api/today/refresh", methods=["POST"])
    def api_today_refresh():
        _require_flag()
        uid = _require_user()
        payload = deps.get_service().build(uid, force_brief=True)
        deps.emit_signal(uid, "briefing_refreshed", subject_type="briefing")
        return jsonify(today_to_dict(payload))

    @bp.route("/api/today/explain", methods=["GET"])
    def api_today_explain():
        _require_flag()
        uid = _require_user()
        component = (request.args.get("component") or "").strip()
        if component not in ("priority", "health", "forecast"):
            abort(400, description="component must be priority | health | forecast")

        payload = deps.get_service().build(uid)

        if component == "priority":
            task_id = (request.args.get("task_id") or "").strip()
            match = next((t for t in payload.plan if t.assignment.id == task_id), None)
            if match is None:
                abort(404, description="Unknown task_id.")
            body = planned_task_to_dict(match)
            return jsonify({
                "component": "priority",
                "score": body["priority"]["score"],
                "rationale": body["priority"]["rationale"],
                "narrative": match.why_now,
            })
        if component == "health":
            body = health_to_dict(payload.health)
            return jsonify({
                "component": "health",
                "score": body["score"],
                "rationale": body["components"],
                "narrative": body["summary"],
            })
        body = forecast_to_dict(payload.forecast)
        return jsonify({
            "component": "forecast",
            "score": None,
            "rationale": body["days"],
            "narrative": body["summary"],
        })

    # ── cron ─────────────────────────────────────────────────────────

    @bp.route("/cron/refresh-briefings", methods=["POST", "GET"])
    def cron_refresh_briefings():
        token = request.headers.get("X-Cron-Token") or request.args.get("token") or ""
        expected = deps.cron_token()
        if not expected or token != expected:
            abort(403)
        refreshed, failed = 0, 0
        for uid in deps.stale_briefing_user_ids():
            try:
                deps.get_service().build(uid, force_brief=True)
                refreshed += 1
            except Exception as exc:
                failed += 1
                logger.warning("cron briefing refresh failed for user %s: %s", uid, exc)
        return jsonify({"refreshed": refreshed, "failed": failed})

    # ── errors (JSON for API paths) ──────────────────────────────────

    @bp.errorhandler(401)
    def _unauthorized(e):
        return jsonify({"error": "auth_required"}), 401

    @bp.errorhandler(400)
    def _bad_request(e):
        return jsonify({"error": "bad_request", "detail": str(e.description)}), 400

    return bp


# ── availability parsing (used by the glue layer) ────────────────────


def time_range_to_minutes(raw: str) -> int | None:
    """Parse "16:00-19:00" → 180. Returns None when unparseable."""
    try:
        text = (raw or "").strip()
        if not text or "-" not in text:
            return None
        start_s, end_s = (p.strip() for p in text.split("-", 1))

        def _mins(t: str) -> int:
            parts = t.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return h * 60 + m

        start, end = _mins(start_s), _mins(end_s)
        if end < start:  # crosses midnight
            end += 24 * 60
        return max(0, end - start)
    except (ValueError, IndexError):
        return None


def availability_to_minutes(avail: dict[str, str] | None) -> dict[str, int] | None:
    """UserIdentity.availability (day → "HH:MM-HH:MM") → day → minutes.

    Days that fail to parse are skipped (the forecaster fills defaults).
    Returns None when nothing parses, so the forecaster's default
    profile applies wholesale.
    """
    if not avail:
        return None
    out: dict[str, int] = {}
    for day, rng in avail.items():
        minutes = time_range_to_minutes(str(rng))
        if minutes is not None:
            out[(day or "").strip().lower()] = minutes
    return out or None
