"""Adaptive-scheduler blueprint.

Endpoints:

* ``GET  /api/next-action``            — what to do now, and why
* ``POST /api/next-action/respond``    — the student took it, or did not
* ``POST /api/schedule/override``      — preview an override's consequences
* ``POST /api/schedule/override/apply``— accept the previewed override
* ``GET  /api/schedule/feasibility``   — is the current plan actually possible
* ``GET  /api/schedule/versions``      — the plan's history and why it changed

Everything arrives injected via :class:`NextActionDeps` so this module never
imports ``App`` and stays unit-testable with fakes.

Feature gating
--------------
``adaptive_scheduler`` is a **percentage rollout**, not a kill switch: the
surface is new and changes what students are told to do, so it opens to a
cohort rather than to everyone. Outside the cohort every route 404s — the
same answer as a route that does not exist, which is what an un-rolled-out
feature should look like from the client's side.

Nothing here is on the critical path of an existing surface. If the whole
blueprint were removed, the scheduler, the Command Center and the recovery
flow would all behave exactly as they do today.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from flask import Blueprint, abort, jsonify, request

logger = logging.getLogger(__name__)

FLAG_KEY = "adaptive_scheduler"

#: Overrides are cheap to compute and easy to spam from a stuck UI loop.
#: Bounded so one client cannot pin a worker re-measuring the same plan.
MAX_OVERRIDE_MINUTES = 600

#: How far ahead an override may push work. The planner's horizon is 14 days;
#: beyond that there is no plan to move into, and accepting an arbitrary date
#: would let a client mint calendar days years out that every later read has
#: to cope with. Rejected loudly rather than clamped: silently retargeting
#: someone's move to a date they did not choose is worse than refusing it.
MAX_OVERRIDE_HORIZON_DAYS = 30


@dataclass(frozen=True)
class NextActionDeps:
    """Everything the blueprint needs, injected by the glue layer."""

    get_service: Callable[[], Any]                     # → NextActionService
    feature_enabled_for_user: Callable[[str, int], bool]
    current_user_id: Callable[[], int | None]
    now: Callable[[], datetime]
    get_active_schedule: Callable[[int], dict | None] = field(
        default=lambda uid: None
    )
    save_schedule: Callable[[int, dict], bool] = field(
        default=lambda uid, data: False
    )
    windows_for: Callable[[int], Any] = field(default=lambda uid: None)
    busy_by_date: Callable[[int], dict] = field(default=lambda uid: {})
    record_decisions: Callable[..., Any] = field(default=lambda *a, **k: None)
    record_override: Callable[..., Any] = field(default=lambda *a, **k: None)
    record_version: Callable[..., Any] = field(default=lambda *a, **k: None)
    resolve_decision: Callable[..., Any] = field(default=lambda *a, **k: False)
    versions_for: Callable[[int, int], list] = field(default=lambda uid, n: [])
    emit_signal: Callable[..., Any] = field(default=lambda *a, **k: None)
    #: ``(name, personalization_opted_in)`` for the reasoning layer. The
    #: opt-in is load-bearing: it is what decides whether anything about this
    #: student is sent to an external model at all.
    student_profile: Callable[[int], tuple[str, bool]] = field(
        default=lambda uid: ("", False)
    )


def create_next_action_blueprint(deps: NextActionDeps) -> Blueprint:
    bp = Blueprint("adaptive_scheduler", __name__)

    # ── guards ───────────────────────────────────────────────────────

    def _require_user() -> int:
        uid = deps.current_user_id()
        if uid is None:
            abort(401, description="Authentication required.")
        if not deps.feature_enabled_for_user(FLAG_KEY, uid):
            abort(404)
        return uid

    # ── read: what to do now ─────────────────────────────────────────

    @bp.route("/api/next-action", methods=["GET"])
    def next_action():
        uid = _require_user()
        try:
            limit = max(1, min(6, int(request.args.get("limit", 4))))
        except (TypeError, ValueError):
            limit = 4

        from intelliplan.services.next_action import decision_to_dict

        decision = deps.get_service().decide(uid, deps.now(), limit=limit)
        payload = decision_to_dict(decision)

        # Log the whole ranked list, not just the winner. Which option a
        # student picked out of the four they saw is the signal; recording
        # only the top one throws it away.
        try:
            shown = [decision.headline, *decision.upcoming]
            deps.record_decisions(uid, [a for a in shown if a is not None])
        except Exception as exc:
            logger.warning("decision logging failed for user %s: %s", uid, exc)

        return jsonify({"status": "ok", **payload})

    @bp.route("/api/next-action/respond", methods=["POST"])
    def respond():
        """Record whether the student took the recommendation.

        The rejection case is the valuable one and the easy one to omit —
        a model trained only on accepted recommendations learns that every
        recommendation is good.
        """
        uid = _require_user()
        body = request.get_json(silent=True) or {}
        task_id = str(body.get("task_id") or "").strip()
        accepted = bool(body.get("accepted"))
        if not task_id:
            return jsonify({"status": "error", "message": "task_id is required."}), 400
        ok = deps.resolve_decision(uid, task_id, accepted)
        deps.emit_signal(
            uid,
            "nba_accepted" if accepted else "nba_rejected",
            subject_type="task",
            subject_ref=task_id,
        )
        return jsonify({"status": "ok", "recorded": bool(ok)})

    @bp.route("/api/next-action/explain", methods=["GET"])
    def explain_next_action():
        """The narrated version of the current recommendation.

        Deliberately a *separate* endpoint from ``/api/next-action``. The
        recommendation itself is deterministic and must render instantly;
        putting an AI round trip on that path would make the main surface as
        slow and as unreliable as the slowest provider. This one is fetched
        after the card is already on screen, and if it never returns the
        student still has the grounded reason list.
        """
        uid = _require_user()

        from intelliplan.intelligence import reasoning

        service = deps.get_service()
        decision = service.decide(uid, deps.now(), limit=4)
        if decision.headline is None:
            return jsonify({
                "status": "ok",
                "explanation": None,
                "reason": decision.reason,
            })

        name, opted_in = deps.student_profile(uid)
        explanation = reasoning.explain(
            decision.headline,
            minutes_available=decision.minutes_remaining_today,
            today=deps.now().date(),
            alternatives=decision.upcoming,
            behavior=decision.behavior,
            student_first_name=name,
            personalization_enabled=bool(opted_in),
        )
        return jsonify({
            "status": "ok",
            "task_id": decision.headline.candidate.task_id,
            "explanation": explanation.to_dict(),
        })

    # ── overrides ────────────────────────────────────────────────────

    def _parse_override(body: dict) -> tuple[str, str, date, date | None, int]:
        action = str(body.get("action") or "move").strip().lower()
        if action not in ("move", "skip", "shorten"):
            abort(400, description="action must be move, skip, or shorten.")
        task_id = str(body.get("task_id") or "").strip()
        if not task_id:
            abort(400, description="task_id is required.")
        today = deps.now().date()
        day = _as_date(body.get("day")) or today
        to_day = _as_date(body.get("to_day"))
        if to_day is not None:
            offset = (to_day - today).days
            if offset < 0 or offset > MAX_OVERRIDE_HORIZON_DAYS:
                abort(400, description="to_day is outside the planning horizon.")
            if to_day <= day:
                abort(400, description="to_day must be after the day being moved from.")
        try:
            minutes = int(body.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes < 0 or minutes > MAX_OVERRIDE_MINUTES:
            abort(400, description="minutes is out of range.")
        return action, task_id, day, to_day, minutes

    @bp.route("/api/schedule/override", methods=["POST"])
    def override_preview():
        """Show the consequences without committing to them."""
        uid = _require_user()
        action, task_id, day, to_day, minutes = _parse_override(
            request.get_json(silent=True) or {}
        )

        from intelliplan.services.next_action import preview_override

        service = deps.get_service()
        snapshot = service.snapshot(uid, deps.now())
        outcome = preview_override(
            deps.get_active_schedule(uid),
            action=action,
            task_id=task_id,
            day=day,
            to_day=to_day,
            minutes=minutes,
            today=deps.now().date(),
            behavior=snapshot.behavior,
        )
        if outcome is None:
            return jsonify({
                "status": "ok",
                "changed": False,
                "summary": "Nothing in your plan would change.",
            })
        return jsonify({"status": "ok", "changed": True, **outcome.to_dict()})

    @bp.route("/api/schedule/override/apply", methods=["POST"])
    def override_apply():
        """Commit an override the student has seen the price of.

        The consequences are recomputed here rather than trusted from the
        client. A preview is a claim the browser made; the write has to be
        based on the plan as it is now, which may have changed since.
        """
        uid = _require_user()
        action, task_id, day, to_day, minutes = _parse_override(
            request.get_json(silent=True) or {}
        )

        from intelliplan.services.next_action import preview_override

        service = deps.get_service()
        snapshot = service.snapshot(uid, deps.now())
        schedule = deps.get_active_schedule(uid)
        outcome = preview_override(
            schedule,
            action=action,
            task_id=task_id,
            day=day,
            to_day=to_day,
            minutes=minutes,
            today=deps.now().date(),
            behavior=snapshot.behavior,
        )
        if outcome is None:
            return jsonify({
                "status": "ok",
                "changed": False,
                "summary": "Nothing in your plan would change.",
            })

        updated = _apply_to_schedule_data(
            schedule, action=action, task_id=task_id, day=day, to_day=outcome.target_day,
            minutes=minutes,
        )
        saved = False
        try:
            saved = bool(deps.save_schedule(uid, updated))
        except Exception as exc:
            logger.warning("override save failed for user %s: %s", uid, exc)

        version = None
        try:
            version = deps.record_version(
                uid,
                trigger="override",
                objective_key="student_override",
                candidate_count=1,
                scheduled_minutes=outcome.plan.total_minutes,
                deferred_minutes=sum(d.minutes for d in outcome.plan.deferred),
                changes=[{
                    "task_id": task_id,
                    "kind": action,
                    "from": day.isoformat(),
                    "to": outcome.target_day.isoformat() if outcome.target_day else None,
                    "minutes": outcome.moved_minutes,
                }],
            )
            deps.record_override(
                uid,
                action_kind=action,
                task_id=task_id,
                schedule_version_id=getattr(version, "id", None),
            )
        except Exception as exc:
            logger.warning("override audit failed for user %s: %s", uid, exc)

        return jsonify({
            "status": "ok",
            "changed": True,
            "saved": saved,
            "version": getattr(version, "version", None),
            "data": updated,
            **outcome.to_dict(),
        })

    # ── feasibility and history ──────────────────────────────────────

    @bp.route("/api/schedule/feasibility", methods=["GET"])
    def feasibility():
        uid = _require_user()
        report = deps.get_service().check(
            uid,
            now=deps.now(),
            windows_for=deps.windows_for(uid),
            busy_by_date=deps.busy_by_date(uid),
        )
        return jsonify({"status": "ok", **report.to_dict()})

    @bp.route("/api/schedule/versions", methods=["GET"])
    def versions():
        uid = _require_user()
        try:
            limit = max(1, min(50, int(request.args.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20
        return jsonify({"status": "ok", "versions": deps.versions_for(uid, limit)})

    return bp


# ── schedule_data surgery ────────────────────────────────────────────


def _apply_to_schedule_data(
    schedule_data: dict | None,
    *,
    action: str,
    task_id: str,
    day: date,
    to_day: date | None,
    minutes: int,
) -> dict:
    """Mirror the override onto the stored blob.

    The override engine works on ``Plan`` objects; what gets saved and
    rendered is ``schedule_data``. Rather than regenerating the blob from the
    mutated plan — which would drop every field the planner does not know
    about, including anything the student edited by hand — the same edit is
    replayed against the blob directly.

    Moved blocks lose their clock placement (``start_iso`` / ``time_slot``).
    That is correct and deliberate: 7 PM on Tuesday is not 7 PM on Wednesday,
    and carrying the old times over would produce a block that looks placed
    and is not. The next placement pass re-times them.

    **Work never leaves the blob silently.** Anything this removes and cannot
    re-place — a move with nowhere to go, a skip, the trimmed tail of a
    shorten — is appended to ``deferred`` and flips ``overloaded``, the same
    two fields ``plan_to_schedule_data`` uses for the same purpose. A block
    that disappears from the calendar and is recorded only in a ``Plan``
    object the student never sees is work they have been told about in a way
    they will never see.
    """
    data = dict(schedule_data or {})
    days = [dict(d) for d in (data.get("schedule") or []) if isinstance(d, dict)]

    src = next((d for d in days if str(d.get("date") or "")[:10] == day.isoformat()), None)
    if src is None:
        return data

    blocks = [dict(b) for b in (src.get("blocks") or []) if isinstance(b, dict)]
    matched = [b for b in blocks if _block_task(b) == task_id]
    if not matched:
        return data
    kept = [b for b in blocks if _block_task(b) != task_id]
    dropped: list[dict] = []

    if action == "shorten":
        remaining = max(0, minutes)
        trimmed = []
        for b in matched:
            full = int(b.get("duration_minutes") or 0)
            take = min(full, remaining)
            remaining -= take
            if take > 0:
                trimmed.append({**b, "duration_minutes": take})
            if full - take > 0:
                dropped.append(_deferral(b, full - take, "You shortened this sitting."))
        src["blocks"] = _reorder(kept + trimmed)
    elif action == "skip":
        src["blocks"] = _reorder(kept)
        dropped = [
            _deferral(b, int(b.get("duration_minutes") or 0), "You chose to skip this.")
            for b in matched
        ]
    else:  # move
        src["blocks"] = _reorder(kept)
        if to_day is None:
            dropped = [
                _deferral(
                    b,
                    int(b.get("duration_minutes") or 0),
                    "You moved this out of the last day in the plan.",
                )
                for b in matched
            ]
        else:
            dst = next(
                (d for d in days if str(d.get("date") or "")[:10] == to_day.isoformat()),
                None,
            )
            relocated = [_unplace(b) for b in matched]
            if dst is None:
                days.append({
                    "date": to_day.isoformat(),
                    "day_name": to_day.strftime("%A"),
                    "blocks": relocated,
                    "total_minutes": sum(int(b.get("duration_minutes") or 0) for b in relocated),
                    "capacity_minutes": 0,
                })
            else:
                dst["blocks"] = _reorder(
                    [dict(b) for b in (dst.get("blocks") or []) if isinstance(b, dict)]
                    + relocated
                )

    for d in days:
        d["total_minutes"] = sum(
            int(b.get("duration_minutes") or 0) for b in (d.get("blocks") or [])
        )
    days.sort(key=lambda d: str(d.get("date") or ""))
    data["schedule"] = days

    if dropped:
        existing = [x for x in (data.get("deferred") or []) if isinstance(x, dict)]
        data["deferred"] = existing + dropped
        data["overloaded"] = True

    return data


def _deferral(block: dict, minutes: int, reason: str) -> dict:
    """One entry in ``schedule_data["deferred"]``.

    Same shape ``plan_to_schedule_data`` writes, so the surfaces that already
    render deferrals pick these up with no change.
    """
    return {
        "task_id": block.get("task_id"),
        "title": block.get("parent_title") or block.get("assignment") or "Task",
        "minutes": int(minutes),
        "due_date": block.get("due_date") or None,
        "reason": reason,
    }


def _block_task(block: dict) -> str:
    return str(block.get("task_id") or block.get("id") or "")


def _unplace(block: dict) -> dict:
    out = dict(block)
    for key in ("start_iso", "end_iso", "time_slot", "window_slot"):
        out.pop(key, None)
    return out


def _reorder(blocks: list) -> list:
    """Placed blocks first, in clock order; unplaced ones after.

    Sorting on a missing key would raise, and sorting unplaced blocks to the
    top would show a student an un-timed block above their 8 AM one.
    """
    placed = [b for b in blocks if b.get("start_iso")]
    unplaced = [b for b in blocks if not b.get("start_iso")]
    placed.sort(key=lambda b: str(b.get("start_iso")))
    return placed + unplaced


def _as_date(value: Any) -> date | None:
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
