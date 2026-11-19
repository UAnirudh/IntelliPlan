"""Read-only, project-scoped Todoist tasks for the planner."""
from datetime import date, timedelta
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode

from flask import Blueprint, jsonify, redirect, request, session
from flask_login import current_user
import requests

from child_privacy import hold_reason
from intelliplan.intelligence.deadlines import local_deadline
from intelliplan.learning import policy
from intelliplan.persistence.lms_grants import lms_grants
from time_utils import utcnow

bp = Blueprint("todoist", __name__)
REDIRECT = "https://intelliplan.tech/oauth/todoist/callback"
API = "https://api.todoist.com"
MAX_PROJECTS = 4
ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


def configured():
    return bool(os.environ.get("TODOIST_CLIENT_ID") and os.environ.get("TODOIST_CLIENT_SECRET"))


def allowed(user):
    if not getattr(user, "is_authenticated", False) or hold_reason(user, require_known_age=True):
        return False
    try:
        return policy.decision(user, "todoist")[0] and not policy.organizations(user)
    except Exception:
        return False


def _token(**fields):
    response = requests.post(API + "/oauth/access_token", data={
        **fields, "client_id": os.environ["TODOIST_CLIENT_ID"],
        "client_secret": os.environ["TODOIST_CLIENT_SECRET"],
    }, timeout=(3, 8), allow_redirects=False)
    if response.status_code != 200:
        raise ValueError("Todoist rejected the connection.")
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Todoist returned an invalid token response.")
    access = data.get("access_token")
    scope = data.get("scope", "data:read")
    if scope != "data:read" or not isinstance(access, str) or not access or len(access) > 4096:
        raise ValueError("Todoist did not grant read-only access.")
    refresh = data.get("refresh_token")
    if refresh is not None and (not isinstance(refresh, str) or not refresh or len(refresh) > 4096):
        raise ValueError("Todoist supplied an invalid refresh token.")
    expires = int(data.get("expires_in") or 315360000)
    if not 0 < expires <= 315360000:
        raise ValueError("Todoist supplied an invalid token expiry.")
    return dict(access_token=access, refresh_token=refresh,
                token_expires_at=utcnow() + timedelta(seconds=expires))


def _grant(uid):
    repo = lms_grants("todoist")
    grant = repo.get(uid)
    if grant and grant.token_expires_at and grant.token_expires_at <= utcnow() + timedelta(seconds=60):
        if not grant.refresh_token:
            raise ValueError("Reconnect Todoist to refresh tasks.")
        try:
            updated = _token(grant_type="refresh_token", refresh_token=grant.refresh_token)
        except (requests.RequestException, ValueError):
            latest = repo.get(uid)
            if latest and latest.access_token != grant.access_token and latest.token_expires_at > utcnow():
                return latest
            raise
        updated["refresh_token"] = updated["refresh_token"] or grant.refresh_token
        grant = repo.refresh(uid, grant, **updated) or repo.get(uid)
    return grant


def _list(path, token, parameters=None):
    rows = []
    cursor = None
    deadline = time.monotonic() + 10
    for _ in range(5):
        if time.monotonic() > deadline:
            break
        response = requests.get(API + "/api/v1/" + path,
            headers={"Authorization": "Bearer " + token},
            params={"limit": 100, **(parameters or {}), **({"cursor": cursor} if cursor else {})},
            timeout=(2, 4), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Todoist did not finish syncing.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Todoist returned an incomplete list.")
        page = payload.get("results")
        if not isinstance(page, list):
            raise ValueError("Todoist returned an incomplete list.")
        rows.extend(page)
        cursor = payload.get("next_cursor")
        if not cursor:
            return rows
    raise ValueError("That Todoist list is too large to sync in one request.")


def _projects(grant):
    return {row["id"]: row["name"][:200] for row in _list("projects", grant.access_token)
            if isinstance(row, dict) and isinstance(row.get("id"), str) and ID.fullmatch(row["id"])
            and isinstance(row.get("name"), str) and not row.get("is_archived")}


def _selected(grant):
    selected = json.loads(grant.projects_json or "{}") if grant else {}
    return selected if isinstance(selected, dict) else {}


def assignments(user):
    """Return only selected projects, with no local copy of Todoist task text."""
    if not configured() or not allowed(user):
        return []
    uid = int(user.id)
    grant = _grant(uid)
    projects = _selected(grant)
    rows = []
    for project, name in projects.items():
        for task in _list("tasks", grant.access_token, {"project_id": project}):
            if (not isinstance(task, dict) or task.get("project_id") != project
                    or task.get("checked") or task.get("is_deleted")):
                continue
            key, title = task.get("id"), task.get("content")
            if not isinstance(key, str) or not ID.fullmatch(key) or not isinstance(title, str) or not title.strip():
                continue
            due = task.get("due")
            due = due.get("date") if isinstance(due, dict) else None
            day = ""
            if isinstance(due, str):
                try:
                    day = date.fromisoformat(due[:10]).isoformat()
                except ValueError:
                    pass
            time_fields = {"due_at": due} if day and local_deadline(due) is not None else {}
            duration = task.get("duration")
            if isinstance(duration, dict) and duration.get("unit") == "minute":
                amount = duration.get("amount")
                if type(amount) is int and amount > 0:
                    time_fields["estimated_time"] = min(amount, 1440)
            rows.append(dict(id="todoist:" + key, title=title.strip()[:512], due_date=day,
                course=name, source="todoist", priority={4: "High", 3: "High", 2: "Medium"}.get(task.get("priority"), "Low"),
                todoist_project=project, todoist_connection=grant.connected_at.isoformat(),
                url="https://app.todoist.com/app/task/" + key, **time_fields))
    return filter_assignments(user, rows)


def filter_assignments(user, rows):
    if not any(row.get("source") == "todoist" for row in rows):
        return rows
    grant = lms_grants("todoist").get(user.id) if configured() and allowed(user) else None
    selected = _selected(grant)
    version = grant.connected_at.isoformat() if grant else None
    return [row for row in rows if row.get("source") != "todoist"
            or (row.get("todoist_connection") == version and row.get("todoist_project") in selected)]


@bp.get("/oauth/todoist/start")
def start():
    if not current_user.is_authenticated:
        return redirect("/login")
    if not configured() or not allowed(current_user):
        return redirect("/scheduler?capture=1&todoist=unavailable")
    state = secrets.token_urlsafe(32)
    session["todoist_oauth"] = [int(current_user.id), state, int(time.time())]
    return redirect("https://app.todoist.com/oauth/authorize?" + urlencode({
        "client_id": os.environ["TODOIST_CLIENT_ID"], "scope": "data:read",
        "state": state, "redirect_uri": REDIRECT, "response_type": "code"}))


@bp.get("/oauth/todoist/callback")
def callback():
    proof = session.pop("todoist_oauth", None)
    code, state = request.args.get("code", ""), request.args.get("state", "")
    if not (configured() and allowed(current_user) and proof and proof[0] == int(current_user.id)
            and 0 <= time.time() - proof[2] <= 600 and state.isascii()
            and secrets.compare_digest(state, proof[1]) and 0 < len(code) <= 2048):
        return redirect("/scheduler?capture=1&todoist=failed")
    try:
        grant = _token(code=code, redirect_uri=REDIRECT)
        lms_grants("todoist").connect(current_user.id, projects_json="{}", **grant)
        from App import invalidate_lms_cache_for_user
        invalidate_lms_cache_for_user(current_user.id)
    except (requests.RequestException, ValueError, LookupError):
        return redirect("/scheduler?capture=1&todoist=failed")
    return redirect("/scheduler?capture=1&todoist=connected")


@bp.route("/api/todoist/connection", methods=["GET", "POST", "DELETE"])
def connection():
    from App import _no_store, invalidate_lms_cache_for_user
    from flask import make_response
    if not current_user.is_authenticated:
        return jsonify(message="Sign in to connect Todoist."), 401
    uid = int(current_user.id)
    repo = lms_grants("todoist")
    if request.method == "DELETE":
        repo.disconnect(uid)
        invalidate_lms_cache_for_user(uid)
        return _no_store(jsonify(connected=False))
    if not configured() or not allowed(current_user):
        return _no_store(jsonify(available=False, connected=False))
    try:
        grant = _grant(uid)
        if not grant:
            return _no_store(jsonify(available=True, connected=False))
        projects = _projects(grant)
        if request.method == "POST":
            body = request.get_json(silent=True)
            ids = body.get("projects") if isinstance(body, dict) else None
            if not isinstance(ids, list) or len(ids) > MAX_PROJECTS or any(
                    not isinstance(key, str) or key not in projects for key in ids):
                return jsonify(message="Choose up to four of your Todoist projects."), 400
            selected = {key: projects[key] for key in ids}
            if not repo.refresh(uid, grant, access_token=grant.access_token, projects_json=json.dumps(selected)):
                return jsonify(message="Your Todoist connection changed. Refresh and try again."), 409
            invalidate_lms_cache_for_user(uid)
        else:
            selected = _selected(grant)
        return _no_store(jsonify(available=True, connected=True,
            projects=[{"id": key, "name": name} for key, name in projects.items()], selected=list(selected)))
    except (requests.RequestException, ValueError, LookupError):
        return _no_store(make_response(jsonify(connected=True,
            message="Todoist could not be reached. Try again or reconnect."), 502))
