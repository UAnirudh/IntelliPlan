"""
api_keys.py — the process a developer goes through to get an IntelliPlan API
key, and the code that checks the key on every request afterwards.

Why an application step at all: the public API hands out a student's
assignments, grades context, and schedule. Anyone can create an IntelliPlan
account, so "logged in" is not a meaningful bar for that data. A developer
applies with an app name, a URL, a written use case, and the scopes they
want; read-only applications clear automatically, anything that can write
into a student's account waits for a human.

Two credential types reach the API:

  * `X-API-Key: ip_live_…`  — issued here, scoped, revocable, per-key rate
    limits. This is what third-party apps should use.
  * `Authorization: Bearer …` — the signed session token from
    /api/v1/auth/token. First-party only (our own MCP server, the browser
    extension); it carries every scope and can't be revoked individually,
    which is exactly why it isn't what we hand to other people.

The secret is shown once, at approval, and stored only as a SHA-256. If a
developer loses it they roll the key; we cannot recover it.

Blueprint: 'developer_api_bp', mounted at /developers.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user, login_required

from time_utils import utcnow

developer_api_bp = Blueprint("developer_api_bp", __name__, url_prefix="/developers")

KEY_PUBLIC_PREFIX = "ip_live_"
_PREFIX_DISPLAY_LEN = len(KEY_PUBLIC_PREFIX) + 6   # ip_live_ + 6 chars

# ── Scopes ────────────────────────────────────────────────────────────
# `write` is the flag that decides whether an application can be approved
# without a human looking at it.
SCOPES = {
    "read:profile":     {"label": "Read profile",      "write": False,
                         "desc": "Name, email, and account creation date."},
    "read:assignments": {"label": "Read assignments",  "write": False,
                         "desc": "The unified assignment list from every connected source."},
    "read:tests":       {"label": "Read tests",        "write": False,
                         "desc": "Assignments the student has marked as tests."},
    "read:schedule":    {"label": "Read schedule",     "write": False,
                         "desc": "Saved study plans and their progress."},
    "read:streak":      {"label": "Read streak",       "write": False,
                         "desc": "Sparks, streak length, level, and quest state."},
    "read:identity":    {"label": "Read learning profile", "write": False,
                         "desc": "Grade level, focus areas, goals, availability."},
    "write:tasks":      {"label": "Create tasks",      "write": True,
                         "desc": "Add manual tasks, dismiss and restore assignments."},
    "write:tests":      {"label": "Mark tests",        "write": True,
                         "desc": "Mark and unmark assignments as tests."},
    "write:schedule":   {"label": "Generate schedules", "write": True,
                         "desc": "Run the scheduler and save the resulting plan."},
    "write:identity":   {"label": "Update learning profile", "write": True,
                         "desc": "Change grade level, focus areas, goals, availability."},
}

READ_SCOPES = [s for s, m in SCOPES.items() if not m["write"]]

# Rate limit ceiling by declared volume. A developer who says "high" still
# has to be approved by a human, because `expected_volume` is self-reported.
VOLUME_LIMITS = {"low": 60, "medium": 300, "high": 1000}

# An account has to exist for this long before it can hold a key. It costs a
# legitimate developer nothing and it stops a throwaway signup from getting
# an auto-approved credential in the same minute.
MIN_ACCOUNT_AGE = timedelta(hours=1)

MAX_KEYS_PER_USER = 5


# ── Model access ──────────────────────────────────────────────────────
def _db():
    return current_app.intelliplan_db


def _ApiKey():
    return current_app.intelliplan_api_key_model


# ── Secret generation ─────────────────────────────────────────────────
def generate_key():
    """Return (full_secret, display_prefix, sha256_hex).

    32 bytes of `secrets.token_urlsafe` is ~43 characters of URL-safe
    entropy — far past guessable, and it survives being pasted into a
    shell, a .env file, or a header without escaping.
    """
    body = secrets.token_urlsafe(32)
    full = KEY_PUBLIC_PREFIX + body
    return full, full[:_PREFIX_DISPLAY_LEN], hash_key(full)


def hash_key(raw):
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def looks_like_api_key(raw):
    return bool(raw) and raw.startswith(KEY_PUBLIC_PREFIX)


# ── Verification (called on every API request) ────────────────────────
def resolve_api_key(raw):
    """Return the active ApiKey row for `raw`, or None.

    Looks the row up by hash, so a wrong key is a miss rather than a
    comparison against a stored secret. Revoked, pending, and denied keys
    all resolve to None — a revoked key must stop working immediately, not
    at the next cache expiry.
    """
    if not looks_like_api_key(raw):
        return None
    try:
        db, ApiKey = _db(), _ApiKey()
    except AttributeError:
        return None
    row = db.session.query(ApiKey).filter_by(key_hash=hash_key(raw)).first()
    if not row or row.status != "active":
        return None
    return row


def touch_key(row):
    """Record that a key was used. Best-effort: a failure here must never
    turn a working API call into a 500."""
    try:
        db = _db()
        row.last_used_at = utcnow()
        row.request_count = (row.request_count or 0) + 1
        db.session.commit()
    except Exception:
        try:
            _db().session.rollback()
        except Exception:
            pass


# ── Application review ────────────────────────────────────────────────
def _account_old_enough(user):
    created = getattr(user, "created_at", None)
    if not created:
        return False
    now = datetime.utcnow() if created.tzinfo is None else utcnow()
    return (now - created) >= MIN_ACCOUNT_AGE


def auto_approvable(user, scopes):
    """True when the application can be approved without a human.

    Read-only, from an account that has existed for at least an hour.
    Anything that can write into a student's account goes to review, no
    matter who is asking.
    """
    if any(SCOPES.get(s, {}).get("write") for s in scopes):
        return False
    return _account_old_enough(user)


def approve(row, note="", by_admin=False):
    """Mint the secret for an approved application. Returns the full secret,
    which is the only time it exists in plaintext."""
    db = _db()
    full, prefix, digest = generate_key()
    row.key_prefix = prefix
    row.key_hash = digest
    row.status = "active"
    row.approved_at = utcnow()
    row.revoked_at = None
    row.review_note = (note or "")[:500]
    row.rate_limit_per_min = VOLUME_LIMITS.get(
        row.expected_volume or "low", VOLUME_LIMITS["low"]
    ) if by_admin else VOLUME_LIMITS["low"]
    db.session.commit()
    return full


# ── Routes: the developer's side ──────────────────────────────────────
def _err(msg, code=400, **extra):
    payload = {"status": "error", "message": msg}
    payload.update(extra)
    return jsonify(payload), code


@developer_api_bp.route("/scopes", methods=["GET"])
def list_scopes():
    """Public: what a developer can ask for. Rendered by the apply form."""
    return jsonify({
        "scopes": [
            {"name": name, "label": meta["label"], "description": meta["desc"],
             "write": meta["write"], "requires_review": meta["write"]}
            for name, meta in SCOPES.items()
        ],
        "auto_approved": READ_SCOPES,
        "max_keys_per_user": MAX_KEYS_PER_USER,
        "volume_limits": VOLUME_LIMITS,
    })


@developer_api_bp.route("/applications", methods=["GET"])
@login_required
def my_applications():
    db, ApiKey = _db(), _ApiKey()
    rows = (db.session.query(ApiKey)
            .filter_by(user_id=current_user.id)
            .order_by(ApiKey.created_at.desc())
            .all())
    return jsonify({
        "status": "ok",
        "applications": [r.to_dict() for r in rows],
        "slots_remaining": max(0, MAX_KEYS_PER_USER - len([
            r for r in rows if r.status in ("pending", "active")
        ])),
    })


@developer_api_bp.route("/apply", methods=["POST"])
@login_required
def apply_for_key():
    """Submit an application. Read-only ones come back with the secret in the
    same response; the rest come back as 'pending'."""
    db, ApiKey = _db(), _ApiKey()
    body = request.get_json(silent=True) or {}

    app_name = (body.get("app_name") or "").strip()[:120]
    if len(app_name) < 2:
        return _err("Tell us what the app is called.", 400, field="app_name")

    use_case = (body.get("use_case") or "").strip()[:2000]
    if len(use_case) < 40:
        return _err(
            "Describe what you're building in at least 40 characters — "
            "this is what a reviewer reads.", 400, field="use_case")

    if not body.get("accepted_terms"):
        return _err("You need to accept the API terms to continue.",
                    400, field="accepted_terms")

    requested = body.get("scopes") or []
    if not isinstance(requested, list):
        return _err("scopes must be a list.", 400, field="scopes")
    scopes = [s for s in dict.fromkeys(str(x).strip() for x in requested) if s in SCOPES]
    if not scopes:
        return _err("Pick at least one valid scope.", 400, field="scopes")

    volume = (body.get("expected_volume") or "low").lower()
    if volume not in VOLUME_LIMITS:
        volume = "low"

    open_rows = (db.session.query(ApiKey)
                 .filter(ApiKey.user_id == current_user.id,
                         ApiKey.status.in_(("pending", "active")))
                 .count())
    if open_rows >= MAX_KEYS_PER_USER:
        return _err(
            f"You already have {MAX_KEYS_PER_USER} open applications or keys. "
            "Revoke one before applying again.", 409)

    row = ApiKey(
        user_id=current_user.id,
        app_name=app_name,
        app_url=(body.get("app_url") or "").strip()[:512],
        use_case=use_case,
        expected_volume=volume,
        contact_email=(body.get("contact_email") or current_user.email or "").strip()[:255],
        accepted_terms_at=utcnow(),
        scopes=" ".join(scopes),
        status="pending",
        rate_limit_per_min=VOLUME_LIMITS["low"],
    )
    db.session.add(row)
    db.session.commit()

    if auto_approvable(current_user, scopes):
        secret = approve(row, note="Auto-approved: read-only scopes.")
        _notify_applicant(row, approved=True)
        return jsonify({
            "status": "approved",
            "message": "Approved. Copy the key now — this is the only time we show it.",
            "key": secret,
            "application": row.to_dict(),
        }), 201

    _notify_reviewers(row)
    return jsonify({
        "status": "pending",
        "message": ("Submitted. Write access is reviewed by a person, usually "
                    "within two business days. We'll email you either way."),
        "application": row.to_dict(),
    }), 202


@developer_api_bp.route("/keys/<int:key_id>/revoke", methods=["POST"])
@login_required
def revoke_key(key_id):
    db, ApiKey = _db(), _ApiKey()
    row = db.session.get(ApiKey, key_id)
    if not row or row.user_id != current_user.id:
        return _err("No such key.", 404)
    if row.status == "revoked":
        return jsonify({"status": "ok", "application": row.to_dict()})
    row.status = "revoked"
    row.revoked_at = utcnow()
    row.key_hash = ""          # the secret can never match again
    db.session.commit()
    return jsonify({"status": "ok", "application": row.to_dict()})


@developer_api_bp.route("/keys/<int:key_id>/roll", methods=["POST"])
@login_required
def roll_key(key_id):
    """Replace the secret on an already-approved key. The old one stops
    working the instant this returns — that is the point of rolling."""
    db, ApiKey = _db(), _ApiKey()
    row = db.session.get(ApiKey, key_id)
    if not row or row.user_id != current_user.id:
        return _err("No such key.", 404)
    if row.status != "active":
        return _err("Only an active key can be rolled.", 409)
    full, prefix, digest = generate_key()
    row.key_prefix, row.key_hash = prefix, digest
    db.session.commit()
    return jsonify({
        "status": "ok",
        "message": "Rolled. The previous key stopped working just now.",
        "key": full,
        "application": row.to_dict(),
    })


# ── Routes: the reviewer's side ───────────────────────────────────────
def _admin_only(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        from App import is_admin
        if not current_user.is_authenticated or not is_admin(current_user):
            return _err("Admins only.", 403)
        return fn(*a, **kw)
    return wrapper


@developer_api_bp.route("/admin/applications", methods=["GET"])
@login_required
@_admin_only
def admin_list_applications():
    db, ApiKey = _db(), _ApiKey()
    status = (request.args.get("status") or "pending").lower()
    q = db.session.query(ApiKey)
    if status != "all":
        q = q.filter_by(status=status)
    rows = q.order_by(ApiKey.created_at.desc()).limit(200).all()
    User = current_app.intelliplan_user_model
    out = []
    for r in rows:
        d = r.to_dict()
        owner = db.session.get(User, r.user_id)
        d["owner"] = {"id": r.user_id,
                      "email": getattr(owner, "email", "") or "",
                      "name": getattr(owner, "name", "") or ""}
        out.append(d)
    return jsonify({"status": "ok", "applications": out})


@developer_api_bp.route("/admin/applications/<int:key_id>/decide", methods=["POST"])
@login_required
@_admin_only
def admin_decide(key_id):
    db, ApiKey = _db(), _ApiKey()
    row = db.session.get(ApiKey, key_id)
    if not row:
        return _err("No such application.", 404)
    body = request.get_json(silent=True) or {}
    decision = (body.get("decision") or "").lower()
    note = (body.get("note") or "").strip()

    if decision == "approve":
        secret = approve(row, note=note, by_admin=True)
        _notify_applicant(row, approved=True, secret=secret)
        # The secret goes to the applicant by email, not back to the admin —
        # a reviewer has no reason to hold another developer's credential.
        return jsonify({"status": "ok", "application": row.to_dict(),
                        "emailed_secret": True})
    if decision == "deny":
        row.status = "denied"
        row.review_note = note[:500]
        db.session.commit()
        _notify_applicant(row, approved=False)
        return jsonify({"status": "ok", "application": row.to_dict()})
    return _err("decision must be 'approve' or 'deny'.", 400)


# ── Notifications ─────────────────────────────────────────────────────
def _notify_reviewers(row):
    try:
        from App import _send_email, ADMIN_EMAILS
        for addr in list(ADMIN_EMAILS)[:5]:
            _send_email(
                addr,
                f"IntelliPlan API application: {row.app_name}",
                f"{row.contact_email} applied for {row.scopes}.\n\n"
                f"Use case:\n{row.use_case}\n\nReview at /developers (admin).",
            )
    except Exception as e:
        print(f"[api-keys] reviewer notify failed: {e}")


def _notify_applicant(row, approved, secret=None):
    try:
        from App import _send_email
        if not row.contact_email:
            return
        if approved:
            body = (f"Your IntelliPlan API application for \"{row.app_name}\" is approved.\n\n"
                    f"Scopes: {row.scopes}\n"
                    f"Rate limit: {row.rate_limit_per_min} requests/minute\n\n")
            body += (f"Your key:\n{secret}\n\nStore it now — we can't show it again.\n"
                     if secret else
                     "Your key is on the /developers page.\n")
            subject = "Your IntelliPlan API key"
        else:
            body = (f"Your IntelliPlan API application for \"{row.app_name}\" was not approved.\n\n"
                    f"{row.review_note or 'No further detail was given.'}\n\n"
                    "You're welcome to apply again with a narrower scope.")
            subject = "About your IntelliPlan API application"
        _send_email(row.contact_email, subject, body)
    except Exception as e:
        print(f"[api-keys] applicant notify failed: {e}")
