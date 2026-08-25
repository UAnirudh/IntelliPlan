"""
intelliplan_api.py — Public REST API for IntelliPlan

Exposes IntelliPlan functionality (assignments, tests, schedule generation,
streak data, identity) to third-party clients and to the IntelliPlan MCP
server. Everything lives under /api/v1/.

Two ways to authenticate, and they are not interchangeable:

  * `X-API-Key: ip_live_…` — the credential a developer applies for at
    /developers. Scoped, revocable, rate-limited per key. This is what
    third-party integrations use, and what the docs tell people to use.
  * `Authorization: Bearer …` — a signed session token from
    POST /api/v1/auth/token. It carries every scope and cannot be revoked
    without changing SECRET_KEY, so it is for first-party clients only (our
    MCP server, the browser extension) where the user is handing their own
    password to their own tool.

Every response — success or failure — carries an `X-Request-Id`, and every
failure uses one envelope: {"status", "error", "message", "request_id"}.
That means a developer can quote one id at us and we can find the request.

Blueprint name: 'intelliplan_api_bp' (kept unique to avoid endpoint
collisions with App.py's web routes).
"""

import json
import os
import uuid
from datetime import datetime
from time_utils import utcnow
from functools import wraps

from flask import Blueprint, jsonify, request, g, current_app

from auth_api import make_token, verify_token, get_bearer_token
from api_keys import SCOPES, resolve_api_key, touch_key, looks_like_api_key

api_bp = Blueprint("intelliplan_api_bp", __name__, url_prefix="/api/v1")
API_VERSION = "v1"

# A Bearer token means "this is the user, on their own behalf" — there is no
# third party to constrain, so it gets everything.
ALL_SCOPES = list(SCOPES.keys())


# ── Resolve App.py's models/helpers via current_app ──────────────────
# We attach references on App.py to the live Flask `app` so we don't have
# to `from App import ...` (which creates a SECOND SQLAlchemy instance
# when `python App.py` loads the file as both `__main__` and `App`).
def _models():
    a = current_app
    return a.intelliplan_db, a.intelliplan_user_model, a.intelliplan_bcrypt


def _app():
    return current_app


# ── Error envelope ────────────────────────────────────────────────────
def _request_id():
    rid = getattr(g, "api_request_id", None)
    if not rid:
        rid = uuid.uuid4().hex[:16]
        g.api_request_id = rid
    return rid


def _fail(error, message, code=400, **extra):
    payload = {
        "status": "error",
        "error": error,
        "message": message,
        "request_id": _request_id(),
    }
    payload.update(extra)
    return jsonify(payload), code


def _err(msg, code=400):
    """Back-compat shorthand for plain validation failures."""
    return _fail("invalid_request", msg, code)


@api_bp.before_request
def _assign_request_id():
    _request_id()


@api_bp.after_request
def _stamp_response(response):
    response.headers["X-Request-Id"] = _request_id()
    response.headers["X-IntelliPlan-API-Version"] = API_VERSION
    key = getattr(g, "api_key_row", None)
    if key is not None:
        response.headers["X-RateLimit-Limit"] = str(key.rate_limit_per_min or 60)
    # Nothing under /api/v1 is safe to cache: it is all per-credential.
    response.headers.setdefault("Cache-Control", "no-store")
    return response


# Handlers for a bad path or verb inside the API, so it returns the same
# JSON shape as everything else instead of the site's HTML 404.
#
# These have to be `app_errorhandler`, not `errorhandler`: a request for a
# URL that matches no route never enters a blueprint, so a blueprint-scoped
# handler would never see the very case this exists for.
#
# The catch is that app_errorhandler registers app-*wide* and replaces the
# site's own handler for that code. The original code dealt with a
# non-API path by re-raising, which does not work: re-raising inside an
# error handler does not fall through to another handler, it propagates to
# the catch-all Exception handler. So every 404 anywhere on the site —
# every mistyped URL, every stale inbound link, every crawler probe —
# rendered as a 500. Delegate to the site's handler instead of re-raising.


def _site_handler(name):
    """The App-level error handler this one displaced, if it exists."""
    import App  # lazy: App imports this module, so this cannot be top-level

    return getattr(App, name, None)


def _delegate(e, site_handler_name):
    """Hand a non-API error back to the page the site would have shown."""
    handler = _site_handler(site_handler_name)
    if handler is not None:
        return handler(e)
    # No site handler for this code — Werkzeug's own page is correct and
    # is still infinitely better than a 500.
    return e.get_response()


@api_bp.app_errorhandler(404)
def _api_404(e):
    if not request.path.startswith("/api/v1"):
        return _delegate(e, "error_404")
    return _fail("not_found", "No such endpoint. See GET /api/v1/docs.", 404)


@api_bp.app_errorhandler(405)
def _api_405(e):
    if not request.path.startswith("/api/v1"):
        return _delegate(e, "error_405")
    return _fail("method_not_allowed",
                 f"{request.method} is not allowed here.", 405,
                 allowed=sorted(getattr(e, "valid_methods", []) or []))


@api_bp.app_errorhandler(429)
def _api_429(e):
    if not request.path.startswith("/api/v1"):
        return _delegate(e, "error_429")
    return _fail("rate_limited",
                 "Too many requests. Slow down and retry shortly.", 429,
                 retry_after=getattr(e, "retry_after", None))


# ── Authentication ────────────────────────────────────────────────────
def _resolve_credential():
    """Return (user, api_key_row_or_None, scopes) or (None, None, []).

    Checks the API key first: if a caller sends both, the narrower, revocable
    credential is the one that should govern the request.
    """
    raw_key = (request.headers.get("X-API-Key") or "").strip()
    if not raw_key:
        bearer = get_bearer_token()
        if looks_like_api_key(bearer):
            # A developer pasting their key into the Authorization header is
            # the single most common integration mistake. Accept it rather
            # than 401-ing someone who holds a valid credential.
            raw_key = bearer
    if raw_key:
        row = resolve_api_key(raw_key)
        if not row:
            return None, None, []
        db, User, _ = _models()
        user = db.session.get(User, row.user_id)
        if not user:
            return None, None, []
        return user, row, row.scope_list()

    token = get_bearer_token()
    if not token:
        return None, None, []
    payload = verify_token(token)
    if not payload:
        return None, None, []
    db, User, _ = _models()
    user = db.session.get(User, payload.get("user_id"))
    if not user:
        return None, None, []
    return user, None, ALL_SCOPES


def require_auth(*required_scopes):
    """401 without a valid credential, 403 when it lacks a required scope.

    Used as `@require_auth("read:assignments")`. Bare `@require_auth` (no
    parentheses) is also accepted so older call sites keep working.
    """
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user, key_row, scopes = _resolve_credential()
            if not user:
                return _fail(
                    "unauthorized",
                    "Send a valid `X-API-Key` header, or a Bearer token from "
                    "POST /api/v1/auth/token. Apply for a key at /developers.",
                    401)
            missing = [s for s in required_scopes if s not in scopes]
            if missing:
                return _fail(
                    "insufficient_scope",
                    "This key is not authorized for that. "
                    f"Missing scope(s): {', '.join(missing)}.",
                    403, required=list(required_scopes), granted=scopes)
            g.api_user = user
            g.api_key_row = key_row
            g.api_scopes = scopes
            if key_row is not None:
                touch_key(key_row)
            return fn(*args, **kwargs)
        return wrapper

    # `@require_auth` with no arguments: the single positional is the view.
    if len(required_scopes) == 1 and callable(required_scopes[0]):
        fn, required_scopes = required_scopes[0], ()
        return decorate(fn)
    return decorate


# Kept so anything still importing the old name keeps working.
require_token = require_auth


def api_rate_limit_key():
    """flask-limiter bucket for /api/v1.

    A key gets its own bucket so one noisy integration can't spend another
    one's budget, and so a shared office IP isn't one bucket for everybody.
    """
    raw = (request.headers.get("X-API-Key") or "").strip()
    if not raw:
        raw = get_bearer_token() or ""
    if looks_like_api_key(raw):
        row = resolve_api_key(raw)
        if row:
            return f"apikey:{row.id}"
        return f"badkey:{request.remote_addr}"
    if raw:
        payload = verify_token(raw)
        if payload and payload.get("user_id"):
            return f"user:{payload['user_id']}"
    return f"ip:{request.remote_addr}"


def api_rate_limit_value():
    """Per-request limit string. Resolves the key's own ceiling so an
    approved high-volume integration isn't stuck on the default."""
    raw = (request.headers.get("X-API-Key") or "").strip() or (get_bearer_token() or "")
    if looks_like_api_key(raw):
        row = resolve_api_key(raw)
        if row:
            return f"{row.rate_limit_per_min or 60} per minute"
    return "60 per minute"


def _impersonate_payload(user):
    """Return Flask-Login session payload that authenticates `user`.

    Used by `_call_internal()` so we can re-enter our own Flask views with
    `current_user` populated to the API caller, without touching the
    actual web session cookie.
    """
    return {
        "_user_id": str(user.id),
        "_id": "",
        "_fresh": True,
    }


def _call_internal(method, path, json_body=None):
    """Invoke a Flask view in-process as the current API user.

    Returns (status_code, json_data_or_text).
    """
    client = current_app.test_client()
    with client.session_transaction() as sess:
        for k, v in _impersonate_payload(g.api_user).items():
            sess[k] = v
    fn = getattr(client, method.lower())
    resp = fn(path, json=json_body) if json_body is not None else fn(path)
    body = resp.get_data(as_text=True)
    try:
        return resp.status_code, json.loads(body)
    except ValueError:
        return resp.status_code, body


# ── Discovery / docs ──────────────────────────────────────────────────
@api_bp.route("/", methods=["GET"])
@api_bp.route("/docs", methods=["GET"])
def api_index():
    return jsonify({
        "api": "IntelliPlan",
        "version": API_VERSION,
        "auth": {
            "preferred": {
                "scheme": "X-API-Key",
                "obtain": "Apply at https://intelliplan.tech/developers",
                "note": "Scoped and revocable. Use this for third-party apps.",
            },
            "first_party": {
                "scheme": "Bearer",
                "obtain_token": "POST /api/v1/auth/token",
                "note": "Full scope, not revocable per-client. Our MCP server "
                        "and browser extension only.",
            },
        },
        "scopes": {name: meta["desc"] for name, meta in SCOPES.items()},
        "endpoints": {
            "GET  /api/v1/health": "Liveness probe. No credential needed.",
            "POST /api/v1/auth/token": "Exchange email+password for a Bearer token.",
            "GET  /api/v1/me": "Current authenticated user. [read:profile]",
            "GET  /api/v1/assignments": "Unified assignment list from all sources. [read:assignments]",
            "POST /api/v1/tasks": "Create a manual task. [write:tasks]",
            "POST /api/v1/assignments/dismiss": "Mark an assignment done. [write:tasks]",
            "POST /api/v1/assignments/restore": "Restore a dismissed assignment. [write:tasks]",
            "GET  /api/v1/tests": "All assignments marked as tests. [read:tests]",
            "POST /api/v1/tests": "Mark an assignment as a test. [write:tests]",
            "DELETE /api/v1/tests": "Unmark a test. [write:tests]",
            "POST /api/v1/schedule/generate": "Build a study plan. [write:schedule]",
            "GET  /api/v1/streak": "Streak, sparks, freezes, level, quests. [read:streak]",
            "GET  /api/v1/identity": "Student learning profile. [read:identity]",
            "PATCH /api/v1/identity": "Update learning profile fields. [write:identity]",
        },
        "errors": {
            "shape": {"status": "error", "error": "<code>",
                      "message": "<human readable>", "request_id": "<hex>"},
            "codes": ["invalid_request", "unauthorized", "insufficient_scope",
                      "not_found", "method_not_allowed", "rate_limited",
                      "upstream_failed", "server_error"],
        },
        "rate_limit": "Per key. Default 60 requests/minute; see X-RateLimit-Limit.",
        "mcp": "An official Model Context Protocol server is available at intelliplan_mcp.py.",
    })


@api_bp.route("/health", methods=["GET"])
def health():
    """Unauthenticated liveness probe, so a client can tell "the API is down"
    apart from "my key is wrong" without burning its rate limit."""
    return jsonify({"status": "ok", "version": API_VERSION,
                    "time": utcnow().isoformat()})


# ── Auth ──────────────────────────────────────────────────────────────
@api_bp.route("/auth/token", methods=["POST"])
def auth_token():
    db, User, bcrypt = _models()
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = (payload.get("password") or "").strip()
    if not email or not password:
        return _err("email and password required.", 400)
    user = db.session.query(User).filter_by(email=email).first()
    if not user or not user.password_hash:
        return _fail("unauthorized", "Invalid credentials.", 401)
    if not bcrypt.check_password_hash(user.password_hash, password):
        return _fail("unauthorized", "Invalid credentials.", 401)
    return jsonify({
        "token": make_token(user),
        "token_type": "Bearer",
        "scopes": ALL_SCOPES,
        "user": {"id": user.id, "email": user.email, "name": user.name},
    })


@api_bp.route("/me", methods=["GET"])
@require_auth("read:profile")
def me():
    u = g.api_user
    key = getattr(g, "api_key_row", None)
    return jsonify({
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "credential": {
            "type": "api_key" if key else "bearer",
            "app_name": key.app_name if key else None,
            "key_prefix": key.key_prefix if key else None,
            "scopes": getattr(g, "api_scopes", []),
        },
    })


# ── Assignments ───────────────────────────────────────────────────────
@api_bp.route("/assignments", methods=["GET"])
@require_auth("read:assignments")
def list_assignments():
    """Return the same unified payload the dashboard renders."""
    status, data = _call_internal("GET", "/live")
    if status >= 400:
        return _fail("upstream_failed",
                     "Could not load assignments from the connected sources.",
                     502, detail=data)
    return jsonify({"assignments": data if isinstance(data, list) else []})


@api_bp.route("/tasks", methods=["POST"])
@require_auth("write:tasks")
def create_task():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title required.", 400)
    payload = {
        "title": title,
        "due_date": body.get("due_date"),
        "priority": body.get("priority") or "Medium",
        "course": body.get("course") or "Personal",
        "estimated_time": body.get("estimated_time") or 60,
        "notes": body.get("notes") or "",
        "sync_notion": False,
    }
    status, data = _call_internal("POST", "/tasks/manual/create", payload)
    return jsonify(data), status


@api_bp.route("/assignments/dismiss", methods=["POST"])
@require_auth("write:tasks")
def dismiss_assignment():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/dismiss", body)
    return jsonify(data), status


@api_bp.route("/assignments/restore", methods=["POST"])
@require_auth("write:tasks")
def restore_assignment():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/restore", body)
    return jsonify(data), status


# ── Push registration ─────────────────────────────────────────────────
@api_bp.route("/push/register", methods=["POST"])
@require_auth("read:profile")
def register_push_token():
    """Record this device's Expo push token.

    Stored as a PushSubscription row, the same table browser subscriptions
    use — one row per device install, which is what that table already
    means. Everything that already sends a reminder then reaches the phone
    with no change at the call site.

    read:profile rather than a write scope: this writes a delivery address
    for the account that already authenticated, not any user content. It is
    the same trust level as knowing your own email.
    """
    db, User, _ = _models()
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()

    from intelliplan.notifications import expo_push
    if not expo_push.is_expo_token(token):
        return _err("A valid Expo push token is required.", 400)

    PushSubscription = current_app.intelliplan_push_subscription_model
    # Upsert on the token: reopening the app hands back the same token, and
    # a fresh row per launch would multiply notifications by the number of
    # times the student ever opened it.
    row = db.session.query(PushSubscription).filter_by(endpoint=token).first()
    if row is None:
        row = PushSubscription(
            user_id=g.api_user.id,
            endpoint=token,
            subscription_json=json.dumps({"type": "expo", "token": token}),
        )
        db.session.add(row)
    else:
        # A shared device: the token follows the install, so the last
        # student to sign in owns it. Leaving it pointed at the previous
        # account would send them someone else's deadlines.
        row.user_id = g.api_user.id
        row.subscription_json = json.dumps({"type": "expo", "token": token})
    db.session.commit()
    return jsonify({"status": "ok", "registered": True})


@api_bp.route("/push/unregister", methods=["POST"])
@require_auth("read:profile")
def unregister_push_token():
    """Stop sending to this device. Called on sign-out, so signing out of a
    borrowed phone does not keep pushing your deadlines to it."""
    db, _User, _ = _models()
    token = ((request.get_json(silent=True) or {}).get("token") or "").strip()
    if not token:
        return _err("token required.", 400)
    PushSubscription = current_app.intelliplan_push_subscription_model
    row = db.session.query(PushSubscription).filter_by(
        endpoint=token, user_id=g.api_user.id).first()
    if row is not None:
        db.session.delete(row)
        db.session.commit()
    # Idempotent: unregistering a token that is already gone is success.
    return jsonify({"status": "ok"})


@api_bp.route("/tasks", methods=["GET"])
@require_auth("read:assignments")
def list_tasks():
    """Everything the student has to do, from every source.

    Distinct from /assignments, which proxies /live and therefore returns
    only what the connected school platforms report. That leaves out manual
    tasks and anything synced from Notion — so a task the student created a
    minute ago was absent from the list they created it in, which reads as
    the app having dropped it.

    /assignments keeps its existing meaning rather than being widened,
    because third-party keys already depend on it being the platform feed.
    """
    status, data = _call_internal("GET", "/tasks/unified")
    if status >= 400:
        return _fail("upstream_failed", "Could not load tasks.", 502, detail=data)

    # /tasks/unified answers in three buckets — overdue, today, upcoming —
    # because that is how the dashboard's columns are laid out. Flattened
    # here in that order, so a client that just renders the list still
    # shows the late work first, and each item keeps a `bucket` telling it
    # which column it came from.
    if isinstance(data, dict):
        tasks = []
        for bucket in ("overdue", "today", "upcoming"):
            for item in (data.get(bucket) or []):
                if isinstance(item, dict):
                    tasks.append({**item, "bucket": bucket})
    else:
        tasks = data if isinstance(data, list) else []
    return jsonify({"tasks": tasks})


@api_bp.route("/courses", methods=["GET"])
@require_auth("read:assignments")
def list_courses():
    """The courses behind the assignment list.

    Lets a client group or filter by course without inferring the set from
    whatever happens to be due this week — a course with nothing outstanding
    would otherwise vanish.
    """
    status, data = _call_internal("GET", "/courses")
    if status >= 400:
        return _fail("upstream_failed",
                     "Could not load courses from the connected sources.",
                     502, detail=data)
    return jsonify({"courses": data if isinstance(data, list) else []})


# ── Grades ────────────────────────────────────────────────────────────
@api_bp.route("/grades", methods=["GET"])
@require_auth("read:grades")
def list_grades():
    """Course grades and GPA, straight from the connected school platform.

    Its own scope rather than read:assignments: knowing what is due and
    knowing what you scored are different levels of trust, and this is the
    most sensitive thing the app holds.
    """
    status, data = _call_internal("GET", "/grades/data")
    if status >= 400:
        return _fail("upstream_failed",
                     "Could not load grades from the connected sources.",
                     502, detail=data)
    # /grades/data returns whatever the connected provider hands back: a
    # list of courses for the imported/StudentVue path, an object for
    # others. Normalising to one shape here means a client writes one
    # renderer instead of branching on which platform the student happens
    # to use — the per-course fields are passed through untouched.
    if isinstance(data, list):
        courses = data
    elif isinstance(data, dict):
        courses = next(
            (data[k] for k in ("grades", "courses", "classes") if isinstance(data.get(k), list)),
            [],
        )
    else:
        courses = []
    return jsonify({"grades": courses})


# ── Schedule (read) ───────────────────────────────────────────────────
@api_bp.route("/schedule", methods=["GET"])
@require_auth("read:schedule")
def get_saved_schedule():
    """The plan the student already has.

    Distinct from POST /schedule/generate, which builds a new one. A client
    opening to "today" wants the saved plan; regenerating on every launch
    would quietly discard the progress ticked off against it.
    """
    status, data = _call_internal("GET", "/schedule/saved")
    if status >= 400:
        return _fail("upstream_failed", "Could not load the saved schedule.",
                     502, detail=data)
    return jsonify(data if isinstance(data, dict) else {"schedule": data})


# ── Tests ─────────────────────────────────────────────────────────────
@api_bp.route("/tests", methods=["GET"])
@require_auth("read:tests")
def list_tests():
    status, data = _call_internal("GET", "/api/tests")
    return jsonify({"tests": data if isinstance(data, list) else []}), status


@api_bp.route("/tests", methods=["POST"])
@require_auth("write:tests")
def mark_test():
    body = request.get_json(silent=True) or {}
    if not (body.get("title") or "").strip():
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/test/mark", body)
    return jsonify(data), status


@api_bp.route("/tests", methods=["DELETE"])
@require_auth("write:tests")
def unmark_test():
    body = request.get_json(silent=True) or {}
    if not (body.get("title") or "").strip():
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/test/unmark", body)
    return jsonify(data), status


# ── Schedule ──────────────────────────────────────────────────────────
@api_bp.route("/schedule/generate", methods=["POST"])
@require_auth("write:schedule")
def generate_schedule():
    """Build a study plan. Pulls assignments from /live if none supplied."""
    body = request.get_json(silent=True) or {}
    assignments = body.get("assignments")
    if assignments is None:
        s, live = _call_internal("GET", "/live")
        assignments = live if isinstance(live, list) else []
    payload = {
        "assignments": assignments,
        "hours_per_day": float(body.get("hours_per_day") or 2),
        "preferred_time": (body.get("preferred_time") or "evening").lower(),
        "custom_tasks": body.get("custom_tasks") or [],
    }
    status, data = _call_internal("POST", "/generate_schedule", payload)
    return jsonify(data), status


# ── Streak / sparks ───────────────────────────────────────────────────
@api_bp.route("/streak", methods=["GET"])
@require_auth("read:streak")
def streak_info():
    status, data = _call_internal("GET", "/study/points")
    return jsonify(data), status


# ── Browser hand-off ──────────────────────────────────────────────────
@api_bp.route("/link/session", methods=["POST"])
@require_auth("read:profile")
def mint_link_session():
    """A one-time URL that opens the browser already signed in as this user.

    Connecting Canvas or Google cannot happen inside the app: both flows
    start at a Flask route that reads the session cookie rather than the
    bearer token, and Google refuses to run OAuth in an embedded web view
    at all. So the app opens the system browser — which arrives with no
    session, and would otherwise attach the connection to nobody.

    Body: {"provider": "canvas" | "google" | "notion"}
    Returns: {"url": "https://…/link/<code>", "expires_in": 90}

    read:profile rather than a write scope: this proves who the caller is
    and hands that identity to a browser. It grants nothing the caller does
    not already hold — the bearer token it was minted from can reach every
    one of these routes directly.
    """
    import app_link

    db, User, _ = _models()
    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").strip().lower()

    next_path = app_link.resolve_next(provider)
    if next_path is None:
        return _err(
            "provider must be one of: " + ", ".join(app_link.LINKABLE) + ".", 400)

    AppLinkCode = current_app.intelliplan_app_link_code_model
    code = app_link.new_code()
    db.session.add(AppLinkCode(
        code_hash=app_link.hash_code(code),
        user_id=g.api_user.id,
        next_path=next_path,
    ))

    # Codes this account already minted and never spent are burned here.
    # A student who taps Connect, backs out, and taps again should not
    # leave a live credential behind for every attempt.
    (db.session.query(AppLinkCode)
        .filter(AppLinkCode.user_id == g.api_user.id,
                AppLinkCode.used_at.is_(None),
                AppLinkCode.code_hash != app_link.hash_code(code))
        .update({"used_at": utcnow()}, synchronize_session=False))
    db.session.commit()

    base = (current_app.config.get("APP_BASE_URL")
            or os.getenv("APP_BASE_URL")
            or "https://intelliplan.tech").rstrip("/")
    return jsonify({
        "url": f"{base}/link/{code}",
        "expires_in": app_link.CODE_TTL_SECONDS,
        "provider": provider,
        # What the browser redirects to when the flow finishes, so the app
        # knows which URL means "done" without hard-coding the scheme.
        "return_url": app_link.deep_link("connected", provider=provider),
    })


# ── Identity / profile ────────────────────────────────────────────────
@api_bp.route("/identity", methods=["GET"])
@require_auth("read:identity")
def get_identity():
    try:
        identity = current_app.intelliplan_get_identity(g.api_user.id)
        return jsonify(identity.to_dict())
    except Exception as e:
        current_app.logger.exception("identity read failed")
        return _fail("server_error", "Could not load the learning profile.", 500)


@api_bp.route("/identity", methods=["PATCH"])
@require_auth("write:identity")
def patch_identity():
    db, _, _ = _models()
    body = request.get_json(silent=True) or {}
    try:
        identity = current_app.intelliplan_get_identity(g.api_user.id)
        if "grade_level" in body:
            identity.grade_level = (str(body.get("grade_level") or "").strip()[:32]) or None
        if "focus_areas" in body and isinstance(body["focus_areas"], list):
            cleaned = [str(x).strip()[:48] for x in body["focus_areas"] if str(x).strip()][:12]
            identity.focus_areas = json.dumps(cleaned)
        if "goals" in body:
            identity.goals = str(body.get("goals") or "").strip()[:1000]
        if "weekly_commitments" in body:
            identity.weekly_commitments = str(body.get("weekly_commitments") or "").strip()[:500]
        if "availability" in body and isinstance(body["availability"], dict):
            identity.availability = json.dumps(body["availability"])
        identity.updated_at = utcnow()
        db.session.commit()
        return jsonify({"status": "ok", "identity": identity.to_dict()})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("identity write failed")
        return _fail("server_error", "Could not update the learning profile.", 500)
