"""Active study blueprint factory.

Routes
------
``GET  /active``                     — the study page
``POST /api/active/start``           — open a session
``POST /api/active/<id>/heartbeat``  — timer tick, pause/resume, focus buckets
``POST /api/active/<id>/finish``     — close as completed or abandoned
``GET  /api/active/current``         — resume after a reload or a device swap
``GET  /api/active/stats``           — what the model has learned so far
``GET  /api/active/next``            — the next session the plan wants done

Design notes
------------
*The timer is authoritative on the server.* A client that reloads, sleeps,
or loses its connection must not lose its accumulated effort, and a client
that lies must not be able to inflate it — so heartbeats carry a monotonic
elapsed count that the repository merges with ``max()``, and the server
independently caps growth against wall time.

*Everything is injected.* Dependencies arrive through
:class:`ActiveDeps` so this module imports neither ``App`` nor the ORM,
and the whole surface is testable with fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from flask import Blueprint, abort, jsonify, render_template, request

from time_utils import utcnow

logger = logging.getLogger(__name__)

FLAG_KEY = "active_study"

#: Hard ceiling on a single session. Past this the student has walked away
#: with the tab open, and counting it as effort would poison the model.
MAX_SESSION_SECONDS = 6 * 60 * 60

#: The largest jump in recorded effort a single heartbeat may claim. Clients
#: send one roughly every 15 seconds; allowing several minutes tolerates a
#: sleeping laptop or a slow network without letting a broken (or hostile)
#: client fabricate hours in one call.
MAX_HEARTBEAT_JUMP_SECONDS = 5 * 60


@dataclass(frozen=True)
class ActiveDeps:
    """Everything the blueprint needs, injected by the glue layer."""

    get_repository: Callable[[], Any]
    current_user_id: Callable[[], int | None]
    guest_session_id: Callable[[], str | None]
    feature_enabled: Callable[[str], bool] = field(default=lambda key: True)
    #: Returns the student's current plan as ``schedule_data``, or None.
    get_plan: Callable[[], dict[str, Any] | None] = field(default=lambda: None)
    #: Called after a session closes so the scheduler can react.
    on_session_finished: Callable[[Any], None] = field(default=lambda row: None)
    emit_signal: Callable[..., Any] = field(default=lambda *a, **k: None)


def create_active_blueprint(deps: ActiveDeps) -> Blueprint:
    bp = Blueprint("active", __name__)

    # ── guards ────────────────────────────────────────────────────────

    def _identity() -> tuple[int | None, str | None]:
        """Who is studying — a logged-in user or a guest browser session.

        Guests get the full feature. A planner that only learns from
        registered users learns from the smaller half of the audience.
        """
        uid = deps.current_user_id()
        if uid is not None:
            return uid, None
        gid = deps.guest_session_id()
        if not gid:
            abort(401, description="No session. Enable cookies or sign in.")
        return None, gid

    def _require_flag() -> None:
        if not deps.feature_enabled(FLAG_KEY):
            abort(404)

    def _payload() -> dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _session_or_404(session_id: int) -> Any:
        uid, gid = _identity()
        row = deps.get_repository().get(session_id, user_id=uid, guest_id=gid)
        if row is None:
            abort(404, description="Session not found.")
        return row

    def _clamp_elapsed(row: Any, claimed: Any) -> int | None:
        """Bound a client's claimed elapsed time by what time allows.

        Two independent ceilings, because they fail differently: a bad
        clock produces a huge jump in one heartbeat, and a tab left open
        overnight produces a slow creep to an absurd total.
        """
        try:
            value = int(claimed)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        current = int(row.active_seconds or 0)
        value = min(value, current + MAX_HEARTBEAT_JUMP_SECONDS)
        if row.started_at:
            wall = int((utcnow() - row.started_at).total_seconds())
            value = min(value, max(0, wall))
        return min(value, MAX_SESSION_SECONDS)

    # ── page ──────────────────────────────────────────────────────────

    @bp.route("/active")
    def active_page():
        """Serve the layout that matches the device.

        Two templates, not one responsive template, because a phone changes
        *where controls live* — primary actions move into a fixed thumb-zone
        bar and the supporting detail collapses behind disclosures. That is a
        different document, not a different width, and expressing it purely
        in media queries means shipping both layouts to both devices and
        hiding one.

        Both render the same element ids, so one controller
        (``static/js/ip-active.js``) drives both and cannot drift.
        """
        _require_flag()
        # No ``active_page``: this page deliberately renders without the app
        # sidebar. Its whole job is holding attention on one task, and the
        # blueprint is also mounted standalone in tests, where the chrome's
        # ``current_user`` is not available. The slim top nav still carries
        # every app link, so nothing is stranded here.
        return render_template(
            "active_mobile.html" if _wants_phone_layout() else "active.html"
        )

    # ── lifecycle ─────────────────────────────────────────────────────

    @bp.route("/api/active/start", methods=["POST"])
    def start_session():
        _require_flag()
        uid, gid = _identity()
        body = _payload()

        title = str(body.get("title") or "").strip()
        if not title:
            return jsonify({"error": "A session needs a title."}), 400
        try:
            planned = int(body.get("planned_minutes") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "planned_minutes must be a number."}), 400
        if not 1 <= planned <= 300:
            return jsonify({"error": "planned_minutes must be between 1 and 300."}), 400

        due = _parse_date(body.get("due_date"))
        row = deps.get_repository().start(
            user_id=uid,
            guest_id=gid,
            title=title,
            planned_minutes=planned,
            block_id=_short(body.get("block_id"), 64),
            task_id=_short(body.get("task_id"), 128),
            course=_short(body.get("course"), 256) or "",
            kind=_short(body.get("kind"), 32) or "homework",
            difficulty=_short(body.get("difficulty"), 16) or "medium",
            due_date=due,
            focus_enabled=bool(body.get("focus_enabled")),
        )
        deps.emit_signal(kind="active_session_started", subject_ref=str(row.id))
        return jsonify({"session": row.to_dict()}), 201

    @bp.route("/api/active/current", methods=["GET"])
    def current_session():
        _require_flag()
        uid, gid = _identity()
        repo = deps.get_repository()
        # Sweep first: a session the student abandoned yesterday must not
        # come back as "resume?" today.
        repo.expire_stale(user_id=uid, guest_id=gid)
        row = repo.active(user_id=uid, guest_id=gid)
        return jsonify({"session": row.to_dict() if row else None})

    @bp.route("/api/active/<int:session_id>/heartbeat", methods=["POST"])
    def heartbeat(session_id: int):
        _require_flag()
        row = _session_or_404(session_id)
        if row.is_terminal:
            return jsonify({"error": "Session already ended.", "session": row.to_dict()}), 409

        body = _payload()
        repo = deps.get_repository()

        state = body.get("state")
        repo.update_progress(
            row,
            active_seconds=_clamp_elapsed(row, body.get("active_seconds")),
            paused_seconds=_int_or_none(body.get("paused_seconds")),
            pause_count=_int_or_none(body.get("pause_count")),
            progress_percent=_int_or_none(body.get("progress_percent")),
            state=state if state in ("running", "paused") else None,
        )

        focus = body.get("focus")
        if isinstance(focus, dict) and row.focus_enabled:
            _record_focus(repo, row, focus)

        return jsonify({"session": row.to_dict()})

    @bp.route("/api/active/<int:session_id>/forfeit", methods=["POST"])
    def forfeit(session_id: int):
        """Record sparks this session has given up to focus enforcement.

        The number is a running total for the session, not a delta, so a
        dropped request costs nothing: the next one carries the true figure.
        That matters because these arrive once a second while a student is
        away from the desk, which is exactly when the network is least
        likely to be someone's priority.

        Clamped to what the session could plausibly have earned. The client
        is the one counting, and a client-supplied number that reaches the
        spark ledger unbounded is a way to zero somebody's balance.
        """
        _require_flag()
        row = _session_or_404(session_id)
        if row.is_terminal:
            return jsonify({"error": "Session already ended."}), 409

        body = _payload()
        try:
            claimed = int(body.get("sparks") or 0)
        except (TypeError, ValueError):
            claimed = 0

        # A session cannot forfeit more than it could have earned, and it
        # can never go negative.
        ceiling = max(0, int((row.active_seconds or 0) / 60))
        deps.get_repository().record_forfeit(row, min(claimed, ceiling))
        return jsonify({
            "session_id": row.id,
            "sparks_forfeited": row.sparks_forfeited,
        })

    @bp.route("/api/active/<int:session_id>/finish", methods=["POST"])
    def finish(session_id: int):
        _require_flag()
        row = _session_or_404(session_id)
        if row.is_terminal:
            return jsonify({"session": row.to_dict()})

        body = _payload()
        repo = deps.get_repository()
        completed = bool(body.get("completed"))

        focus = body.get("focus")
        if isinstance(focus, dict) and row.focus_enabled:
            _record_focus(repo, row, focus)

        repo.finish(
            row,
            completed=completed,
            active_seconds=_clamp_elapsed(row, body.get("active_seconds")),
            progress_percent=_int_or_none(body.get("progress_percent")),
            perceived_difficulty=_int_or_none(body.get("perceived_difficulty")),
            notes=_short(body.get("notes"), 2000),
        )
        # The scheduler wants to know immediately — this is the whole point
        # of the feedback loop, and a nightly batch would make the plan
        # stale for a day after every session.
        try:
            deps.on_session_finished(row)
        except Exception as exc:
            logger.warning("post-session hook failed (session=%s): %s", row.id, exc)

        deps.emit_signal(
            kind="active_session_finished",
            subject_ref=str(row.id),
            value={"completed": completed, "minutes": row.active_minutes},
        )
        return jsonify({"session": row.to_dict(), "learned": _learned(repo, row)})

    # ── introspection ─────────────────────────────────────────────────

    @bp.route("/api/active/stats", methods=["GET"])
    def stats():
        _require_flag()
        uid, gid = _identity()
        repo = deps.get_repository()
        return jsonify(
            {
                "stats": repo.stats(user_id=uid, guest_id=gid),
                "recent": [r.to_dict() for r in repo.recent(user_id=uid, guest_id=gid, limit=10)],
            }
        )

    @bp.route("/api/active/next", methods=["GET"])
    def next_session():
        """The block the plan wants done next, ready to start.

        Returning the plan's own next block — rather than making the
        student find it — is what keeps Active and the scheduler one
        feature instead of two.
        """
        _require_flag()
        plan = deps.get_plan() or {}
        # "No next block" has three causes and they need different words:
        # no plan at all, a plan whose blocks are all finished, or a plan
        # holding nothing but breaks. Reporting only ``next: null`` made the
        # client tell a student who had finished their plan to go and make
        # one, which reads as the app having lost their schedule.
        days = plan.get("schedule", []) or []
        has_plan = bool(days)
        saw_workable_block = False

        for day in days:
            for block in day.get("blocks", []) or []:
                if block.get("is_break"):
                    continue
                saw_workable_block = True
                if block.get("done"):
                    continue
                return jsonify(
                    {
                        "has_plan": True,
                        "all_done": False,
                        "next": {
                            "block_id": block.get("id"),
                            "task_id": block.get("task_id"),
                            "title": block.get("parent_title") or block.get("assignment"),
                            "label": block.get("assignment"),
                            "course": block.get("course"),
                            "kind": block.get("kind"),
                            "difficulty": block.get("difficulty"),
                            "planned_minutes": block.get("duration_minutes"),
                            "due_date": block.get("due_date"),
                            "reasons": block.get("reasons") or [],
                            "time_slot": block.get("time_slot"),
                            "date": day.get("date"),
                        }
                    }
                )
        return jsonify({
            "next": None,
            "has_plan": has_plan,
            "all_done": has_plan and saw_workable_block,
        })

    return bp


# ── device detection ──────────────────────────────────────────────────

#: Tokens that identify a *phone*, not merely a touch device. Tablets are
#: deliberately excluded: an iPad has the screen height the desktop layout
#: assumes, and giving it the phone layout wastes most of the display.
_PHONE_TOKENS = (
    "iphone", "ipod", "android", "windows phone", "blackberry",
    "bb10", "opera mini", "opera mobi", "iemobile", "webos", "fennec",
)
#: Present on Android *tablets* too, so it only counts alongside "mobile".
_ANDROID_PHONE_MARKER = "mobile"


def _wants_phone_layout() -> bool:
    """Whether this request should get the phone layout.

    An explicit ``?view=`` always wins. User-agent sniffing is a heuristic
    that ages badly, and a student on a device we guessed wrong about needs
    a way out that does not involve waiting for a deploy. The override is
    also what makes this testable without faking user agents.
    """
    override = (request.args.get("view") or "").strip().lower()
    if override in ("phone", "mobile"):
        return True
    if override in ("desktop", "full"):
        return False

    ua = (request.headers.get("User-Agent") or "").lower()
    if not ua:
        return False
    if "ipad" in ua or ("android" in ua and _ANDROID_PHONE_MARKER not in ua):
        return False
    return any(token in ua for token in _PHONE_TOKENS)


# ── helpers ───────────────────────────────────────────────────────────


def _record_focus(repo: Any, row: Any, focus: dict[str, Any]) -> None:
    """Persist an attention bucket and the running summary.

    Best-effort by contract: see
    :meth:`ActiveSessionRepository.record_focus_bucket`.
    """
    try:
        repo.record_focus_bucket(
            row.id,
            offset_seconds=int(focus.get("offset_seconds") or 0),
            present=int(focus.get("present") or 0),
            away=int(focus.get("away") or 0),
            absent=int(focus.get("absent") or 0),
            confidence=float(focus.get("confidence") or 0.0),
        )
        summary = focus.get("summary")
        if isinstance(summary, dict):
            repo.apply_focus_summary(
                row,
                samples=int(summary.get("samples") or 0),
                present=int(summary.get("present") or 0),
                away=int(summary.get("away") or 0),
                absent=int(summary.get("absent") or 0),
                distraction_events=int(summary.get("distraction_events") or 0),
                longest_streak_seconds=int(summary.get("longest_streak_seconds") or 0),
            )
    except (TypeError, ValueError) as exc:
        logger.warning("malformed focus payload on session %s: %s", row.id, exc)


def _learned(repo: Any, row: Any) -> dict[str, Any]:
    """What this session changed, in the student's terms.

    Shown right after finishing. A feedback loop the student cannot see is
    indistinguishable from no feedback loop.
    """
    planned = int(row.planned_minutes or 0)
    actual = row.active_minutes
    if not planned or not actual:
        return {}
    delta = actual - planned
    if abs(delta) < max(3, planned * 0.1):
        message = f"Right on your estimate ({actual} min planned {planned})."
    elif delta < 0:
        message = f"Finished {abs(delta)} min faster than planned — future estimates will come down."
    else:
        message = f"Took {delta} min longer than planned — future estimates will allow for it."
    return {"planned_minutes": planned, "actual_minutes": actual, "message": message}


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _short(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None
