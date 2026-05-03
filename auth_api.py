# """
# auth_api.py — Token-based auth API for the Chrome extension.

# This blueprint is ONLY for the extension API (Bearer token auth).
# Web login/logout/register for the browser are handled entirely in App.py
# via Flask-Login + session cookies. Do NOT add /login, /register, or /logout
# routes here — they conflict with App.py's endpoint names and break Flask.
# """

# import os
# import sqlite3
# from datetime import datetime
# from functools import wraps

# from flask import Blueprint, jsonify, request, current_app
# from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
# from flask_bcrypt import Bcrypt
# from sqlalchemy import text
# from werkzeug.security import check_password_hash, generate_password_hash

# # Renamed blueprint: 'auth_api_bp' to avoid endpoint name collisions with App.py
# auth_bp = Blueprint("auth_api_bp", __name__)

# DB_PATH = os.getenv(
#     "INTELLIPLAN_AUTH_DB",
#     os.path.join(os.path.dirname(__file__), "intelliplan_auth.db")
# )
# TOKEN_MAX_AGE_SECONDS = int(os.getenv("INTELLIPLAN_TOKEN_AGE", str(60 * 60 * 24 * 30)))

# bcrypt = Bcrypt()


# def _get_app_sa():
#     # Flask-SQLAlchemy extension is registered by App.py as app.extensions["sqlalchemy"]
#     return current_app.extensions.get("sqlalchemy")


# def _row_to_dict(row):
#     if row is None:
#         return None
#     return dict(row)


# def _get_app_user_by_email(email):
#     sa = _get_app_sa()
#     if not sa:
#         return None
#     row = (
#         sa.session.execute(
#             text(
#                 "SELECT id, email, password_hash, created_at "
#                 "FROM users WHERE lower(email) = :email LIMIT 1"
#             ),
#             {"email": email.lower().strip()},
#         )
#         .mappings()
#         .first()
#     )
#     return _row_to_dict(row)


# def _get_app_user_by_id(user_id):
#     sa = _get_app_sa()
#     if not sa:
#         return None
#     row = (
#         sa.session.execute(
#             text(
#                 "SELECT id, email, password_hash, created_at "
#                 "FROM users WHERE id = :id LIMIT 1"
#             ),
#             {"id": int(user_id)},
#         )
#         .mappings()
#         .first()
#     )
#     return _row_to_dict(row)


# def _create_app_user(email, password):
#     sa = _get_app_sa()
#     if not sa:
#         return None

#     pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
#     now = datetime.utcnow()

#     try:
#         sa.session.execute(
#             text("INSERT INTO users (email, password_hash, created_at) VALUES (:email, :pw, :created_at)"),
#             {"email": email.lower().strip(), "pw": pw_hash, "created_at": now},
#         )
#         sa.session.commit()
#     except Exception:
#         sa.session.rollback()
#         raise

#     return _get_app_user_by_email(email)


# def get_db():
#     conn = sqlite3.connect(DB_PATH)
#     conn.row_factory = sqlite3.Row
#     return conn


# def init_db():
#     conn = get_db()
#     conn.execute(
#         """
#         CREATE TABLE IF NOT EXISTS users (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             name TEXT NOT NULL,
#             email TEXT NOT NULL UNIQUE,
#             password_hash TEXT NOT NULL,
#             created_at TEXT NOT NULL
#         )
#         """
#     )
#     conn.commit()
#     conn.close()


# def serializer():
#     secret_key = current_app.config.get("SECRET_KEY") or os.getenv("SECRET_KEY")
#     if not secret_key:
#         raise RuntimeError("SECRET_KEY is required for auth tokens.")
#     return URLSafeTimedSerializer(secret_key=secret_key, salt="intelliplan-auth")


# def make_token(user_row):
#     return serializer().dumps(
#         {
#             "user_id": int(user_row["id"]),
#             "email": user_row["email"],
#         }
#     )


# def verify_token(token):
#     try:
#         payload = serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
#         return payload
#     except (SignatureExpired, BadSignature):
#         return None


# def user_to_dict(user_row):
#     if not user_row:
#         return None

#     # app-db users don't include "name"; legacy extension users do.
#     if isinstance(user_row, dict):
#         email = user_row.get("email") or ""
#         created_at = user_row.get("created_at")
#         name = user_row.get("name") or (email.split("@")[0] if email else "")
#     else:
#         email = user_row["email"] or ""
#         created_at = user_row["created_at"]
#         name = user_row["name"]

#     if isinstance(created_at, datetime):
#         created_at_str = created_at.isoformat() + "Z"
#     else:
#         created_at_str = created_at or ""

#     return {
#         "id": int(user_row["id"]),
#         "name": name,
#         "email": email,
#         "created_at": created_at_str,
#     }


# def get_user_by_email(email):
#     # Prefer the main app DB so extension auth persists and matches web users.
#     app_user = _get_app_user_by_email(email)
#     if app_user:
#         return app_user

#     # Legacy fallback (extension-only sqlite file)
#     init_db()
#     conn = get_db()
#     user = conn.execute(
#         "SELECT id, name, email, password_hash, created_at FROM users WHERE email = ?",
#         (email.lower().strip(),)
#     ).fetchone()
#     conn.close()
#     return user


# def get_user_by_id(user_id):
#     app_user = _get_app_user_by_id(user_id)
#     if app_user:
#         return app_user

#     init_db()
#     conn = get_db()
#     user = conn.execute(
#         "SELECT id, name, email, password_hash, created_at FROM users WHERE id = ?",
#         (user_id,)
#     ).fetchone()
#     conn.close()
#     return user


# def get_bearer_token():
#     auth_header = request.headers.get("Authorization", "")
#     if auth_header.startswith("Bearer "):
#         return auth_header.split(" ", 1)[1].strip()
#     return None


# def get_current_user():
#     token = get_bearer_token()
#     if not token:
#         return None
#     payload = verify_token(token)
#     if not payload:
#         return None
#     return get_user_by_id(payload.get("user_id"))


# def _corsify(response):
#     origin = request.headers.get("Origin", "")
#     allowed = (
#         origin.startswith("chrome-extension://")
#         or origin.startswith("http://localhost")
#         or origin.startswith("http://127.0.0.1")
#         or origin.startswith("https://intelli-plan.up.railway.app")
#     )
#     if allowed and origin:
#         response.headers["Access-Control-Allow-Origin"] = origin
#         response.headers["Vary"] = "Origin"
#         response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
#         response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
#         response.headers["Access-Control-Allow-Credentials"] = "true"
#     return response


# # IMPORTANT: Use after_request (blueprint-scoped), NOT after_app_request.
# # after_app_request would intercept Flask-Login redirects globally and break
# # browser login/logout flows.
# @auth_bp.after_request
# def add_cors_headers(response):
#     return _corsify(response)


# def json_error(message, status=400):
#     return jsonify({"status": "error", "message": message}), status


# def json_ok(data=None, status=200):
#     payload = {"status": "ok"}
#     if data:
#         payload.update(data)
#     return jsonify(payload), status


# def require_json_fields(*fields):
#     data = request.get_json(silent=True) or {}
#     missing = [f for f in fields if not data.get(f)]
#     if missing:
#         return None, json_error(f"Missing required field(s): {', '.join(missing)}", 400)
#     return data, None


# def handle_options():
#     resp = current_app.make_response(("", 204))
#     return _corsify(resp)


# # ── EXTENSION-ONLY API ROUTES ─────────────────────────────────
# # These are prefixed with /api/auth/ and /api/ext/ to avoid any
# # collision with the browser-facing routes in App.py.

# @auth_bp.route("/api/auth/register", methods=["POST", "OPTIONS"])
# @auth_bp.route("/api/ext/register", methods=["POST", "OPTIONS"])
# def api_register():
#     if request.method == "OPTIONS":
#         return handle_options()

#     data, err = require_json_fields("email", "password")
#     if err:
#         return err

#     email = data["email"].lower().strip()
#     password = data["password"]

#     if len(password) < 8:
#         return json_error("Password must be at least 8 characters long.", 400)

#     if _get_app_user_by_email(email):
#         return json_error("An account with that email already exists.", 409)

#     try:
#         user = _create_app_user(email, password)
#     except Exception:
#         return json_error("Could not create account.", 500)

#     token = make_token(user)
#     return json_ok({"token": token, "user": user_to_dict(user)}, 201)


# @auth_bp.route("/api/auth/login", methods=["POST", "OPTIONS"])
# @auth_bp.route("/api/ext/login", methods=["POST", "OPTIONS"])
# def api_login():
#     if request.method == "OPTIONS":
#         return handle_options()

#     data, err = require_json_fields("email", "password")
#     if err:
#         return err

#     email = data["email"].lower().strip()
#     password = data["password"]

#     # Prefer main app DB (bcrypt hashes)
#     user = _get_app_user_by_email(email)
#     if user:
#         if not bcrypt.check_password_hash(user["password_hash"], password):
#             return json_error("Invalid email or password.", 401)
#     else:
#         # Legacy fallback: check old extension-only DB, then migrate into app DB
#         legacy_user = get_user_by_email(email)
#         if not legacy_user or not check_password_hash(legacy_user["password_hash"], password):
#             return json_error("Invalid email or password.", 401)
#         try:
#             user = _create_app_user(email, password)
#         except Exception:
#             return json_error("Login succeeded, but account migration failed.", 500)

#     token = make_token(user)
#     return json_ok({"token": token, "user": user_to_dict(user)})


# @auth_bp.route("/api/auth/me", methods=["GET", "OPTIONS"])
# @auth_bp.route("/api/ext/me", methods=["GET", "OPTIONS"])
# def api_me():
#     if request.method == "OPTIONS":
#         return handle_options()

#     user = get_current_user()
#     if not user:
#         return json_error("Unauthorized.", 401)

#     return json_ok({"user": user_to_dict(user)})


# @auth_bp.route("/api/auth/logout", methods=["POST", "OPTIONS"])
# @auth_bp.route("/api/ext/logout", methods=["POST", "OPTIONS"])
# def api_logout():
#     if request.method == "OPTIONS":
#         return handle_options()
#     # Stateless — the extension clears its token locally.
#     return json_ok({"message": "Logged out."})


# @auth_bp.route("/api/auth/debug", methods=["GET"])
# def api_debug():
#     # Show counts for both backends to aid debugging during migration.
#     legacy_count = 0
#     try:
#         init_db()
#         conn = get_db()
#         legacy_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
#         conn.close()
#     except Exception:
#         legacy_count = 0

#     app_count = 0
#     try:
#         sa = _get_app_sa()
#         if sa:
#             app_count = (
#                 sa.session.execute(text("SELECT COUNT(*) AS c FROM users"))
#                 .mappings()
#                 .first()
#                 .get("c", 0)
#             )
#     except Exception:
#         app_count = 0

#     return json_ok({"extension_users_legacy": legacy_count, "users_app_db": app_count})


"""
auth_api.py — Token-based auth API for the Chrome extension.

Uses the SAME SQLAlchemy User model as App.py so extension accounts
and web accounts are unified in a single database.

Blueprint name is 'auth_api_bp' to avoid Flask endpoint-name collisions
with App.py's web routes (/login, /register, /logout).
"""

import os
from flask import Blueprint, jsonify, request, current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

auth_bp = Blueprint("auth_api_bp", __name__)

TOKEN_MAX_AGE_SECONDS = int(os.getenv("INTELLIPLAN_TOKEN_AGE", str(60 * 60 * 24 * 30)))


# ── Lazy imports (avoids circular import with App.py) ─────────
def _models():
    from App import db, User, bcrypt
    return db, User, bcrypt


# ── Token helpers ─────────────────────────────────────────────
def _serializer():
    secret = current_app.config.get("SECRET_KEY") or os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY is required for auth tokens.")
    return URLSafeTimedSerializer(secret_key=secret, salt="intelliplan-auth")


def make_token(user):
    return _serializer().dumps({"user_id": user.id, "email": user.email})


def verify_token(token):
    try:
        return _serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (SignatureExpired, BadSignature):
        return None


def get_bearer_token():
    h = request.headers.get("Authorization", "")
    return h.split(" ", 1)[1].strip() if h.startswith("Bearer ") else None


def get_token_user():
    """Resolve Bearer token → User row. Used by App.py data routes."""
    token = get_bearer_token()
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        return None
    _, User, _ = _models()
    return User.query.get(payload.get("user_id"))


def user_to_dict(user):
    name = getattr(user, "name", None) or user.email.split("@")[0]
    created = user.created_at.isoformat() + "Z" if getattr(user, "created_at", None) else ""
    return {"id": user.id, "name": name, "email": user.email, "created_at": created}


# ── CORS ──────────────────────────────────────────────────────
def _corsify(response):
    origin = request.headers.get("Origin", "")
    if any(origin.startswith(p) for p in (
        "chrome-extension://", "http://localhost", "http://127.0.0.1",
        "https://intelli-plan.up.railway.app"
    )):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@auth_bp.after_request          # blueprint-scoped only, won't touch Flask-Login redirects
def _add_cors(response):
    return _corsify(response)


def _options():
    return _corsify(current_app.make_response(("", 204)))


def _ok(data=None, status=200):
    p = {"status": "ok"}
    if data:
        p.update(data)
    return jsonify(p), status


def _err(msg, status=400):
    return jsonify({"status": "error", "message": msg}), status


# ── Routes ────────────────────────────────────────────────────

@auth_bp.route("/api/auth/register", methods=["POST", "OPTIONS"])
@auth_bp.route("/api/ext/register",  methods=["POST", "OPTIONS"])
def api_register():
    if request.method == "OPTIONS":
        return _options()

    db, User, bcrypt = _models()
    data = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""
    name     = (data.get("name") or email.split("@")[0]).strip()

    if not email or not password:
        return _err("Email and password are required.", 400)
    if len(password) < 6:
        return _err("Password must be at least 6 characters.", 400)
    if User.query.filter_by(email=email).first():
        return _err("An account with that email already exists.", 409)

    pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
    try:
        user = User(email=email, password_hash=pw_hash, name=name)
    except TypeError:
        user = User(email=email, password_hash=pw_hash)
    db.session.add(user)
    db.session.commit()

    return _ok({"token": make_token(user), "user": user_to_dict(user)}, 201)


@auth_bp.route("/api/auth/login", methods=["POST", "OPTIONS"])
@auth_bp.route("/api/ext/login",  methods=["POST", "OPTIONS"])
def api_login():
    if request.method == "OPTIONS":
        return _options()

    _, User, bcrypt = _models()
    data = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""

    if not email or not password:
        return _err("Email and password are required.", 400)

    user = User.query.filter_by(email=email).first()
    if not user or not bcrypt.check_password_hash(user.password_hash, password):
        return _err("Invalid email or password.", 401)

    return _ok({"token": make_token(user), "user": user_to_dict(user)})


@auth_bp.route("/api/auth/me", methods=["GET", "OPTIONS"])
@auth_bp.route("/api/ext/me",  methods=["GET", "OPTIONS"])
def api_me():
    if request.method == "OPTIONS":
        return _options()
    user = get_token_user()
    if not user:
        return _err("Unauthorized.", 401)
    return _ok({"user": user_to_dict(user)})


@auth_bp.route("/api/auth/logout", methods=["POST", "OPTIONS"])
@auth_bp.route("/api/ext/logout",  methods=["POST", "OPTIONS"])
def api_logout():
    if request.method == "OPTIONS":
        return _options()
    return _ok({"message": "Logged out."})   # stateless; extension clears token locally


@auth_bp.route("/api/auth/debug", methods=["GET"])
def api_debug():
    _, User, _ = _models()
    return _ok({"total_users": User.query.count()})