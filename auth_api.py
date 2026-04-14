import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

auth_bp = Blueprint("auth_bp", __name__)

DB_PATH = os.getenv(
    "INTELLIPLAN_AUTH_DB",
    os.path.join(os.path.dirname(__file__), "intelliplan_auth.db")
)
TOKEN_MAX_AGE_SECONDS = int(os.getenv("INTELLIPLAN_TOKEN_AGE", str(60 * 60 * 24 * 30)))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def serializer():
    secret_key = current_app.config.get("SECRET_KEY") or os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY is required for auth tokens.")
    return URLSafeTimedSerializer(secret_key=secret_key, salt="intelliplan-auth")


def make_token(user_row):
    return serializer().dumps(
        {
            "user_id": int(user_row["id"]),
            "email": user_row["email"],
        }
    )


def verify_token(token):
    try:
        payload = serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
        return payload
    except SignatureExpired:
        return None
    except BadSignature:
        return None


def user_to_dict(user_row):
    return {
        "id": int(user_row["id"]),
        "name": user_row["name"],
        "email": user_row["email"],
        "created_at": user_row["created_at"],
    }


def get_user_by_email(email):
    conn = get_db()
    user = conn.execute(
        "SELECT id, name, email, password_hash, created_at FROM users WHERE email = ?",
        (email.lower().strip(),)
    ).fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute(
        "SELECT id, name, email, password_hash, created_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return user


def get_bearer_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def get_current_user():
    token = get_bearer_token()
    if not token:
        return None

    payload = verify_token(token)
    if not payload:
        return None

    return get_user_by_id(payload.get("user_id"))


def corsify(response):
    origin = request.headers.get("Origin", "")
    allowed = (
        origin.startswith("chrome-extension://")
        or origin.startswith("http://localhost")
        or origin.startswith("http://127.0.0.1")
        or origin.startswith("https://intelliplan.up.railway.app")
    )

    if allowed and origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@auth_bp.after_app_request
def add_cors_headers(response):
    return corsify(response)


def json_error(message, status=400):
    return corsify(jsonify({"status": "error", "message": message})), status


def json_ok(data=None, status=200):
    payload = {"status": "ok"}
    if data:
        payload.update(data)
    return corsify(jsonify(payload)), status


def require_json_fields(*fields):
    data = request.get_json(silent=True) or {}
    missing = [field for field in fields if not data.get(field)]
    if missing:
        return None, json_error(f"Missing required field(s): {', '.join(missing)}", 400)
    return data, None


def handle_options():
    return corsify(("", 204))


@auth_bp.route("/api/auth/register", methods=["POST", "OPTIONS"])
@auth_bp.route("/auth/register", methods=["POST", "OPTIONS"])
@auth_bp.route("/signup", methods=["POST", "OPTIONS"])
@auth_bp.route("/register", methods=["POST", "OPTIONS"])
@auth_bp.route("/api/signup", methods=["POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS":
        return handle_options()

    init_db()
    data, err = require_json_fields("email", "password")
    if err:
        return err

    email = data["email"].lower().strip()
    password = data["password"]
    name = (data.get("name") or email.split("@")[0]).strip()

    if len(password) < 6:
        return json_error("Password must be at least 6 characters long.", 400)

    existing = get_user_by_email(email)
    if existing:
        return json_error("An account with that email already exists.", 409)

    conn = get_db()
    conn.execute(
        """
        INSERT INTO users (name, email, password_hash, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            name,
            email,
            generate_password_hash(password),
            datetime.utcnow().isoformat() + "Z"
        )
    )
    conn.commit()

    user = conn.execute(
        "SELECT id, name, email, password_hash, created_at FROM users WHERE email = ?",
        (email,)
    ).fetchone()
    conn.close()

    token = make_token(user)
    return json_ok(
        {
            "token": token,
            "user": user_to_dict(user)
        },
        201
    )


@auth_bp.route("/api/auth/login", methods=["POST", "OPTIONS"])
@auth_bp.route("/auth/login", methods=["POST", "OPTIONS"])
@auth_bp.route("/login", methods=["POST", "OPTIONS"])
@auth_bp.route("/api/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return handle_options()

    init_db()
    data, err = require_json_fields("email", "password")
    if err:
        return err

    email = data["email"].lower().strip()
    password = data["password"]

    user = get_user_by_email(email)
    if not user:
        return json_error("Invalid email or password.", 401)

    if not check_password_hash(user["password_hash"], password):
        return json_error("Invalid email or password.", 401)

    token = make_token(user)
    return json_ok(
        {
            "token": token,
            "user": user_to_dict(user)
        }
    )


@auth_bp.route("/api/auth/me", methods=["GET", "OPTIONS"])
@auth_bp.route("/auth/me", methods=["GET", "OPTIONS"])
@auth_bp.route("/me", methods=["GET", "OPTIONS"])
@auth_bp.route("/api/user", methods=["GET", "OPTIONS"])
@auth_bp.route("/api/profile", methods=["GET", "OPTIONS"])
def me():
    if request.method == "OPTIONS":
        return handle_options()

    user = get_current_user()
    if not user:
        return json_error("Unauthorized.", 401)

    return json_ok({"user": user_to_dict(user)})


@auth_bp.route("/api/auth/logout", methods=["POST", "OPTIONS"])
@auth_bp.route("/auth/logout", methods=["POST", "OPTIONS"])
@auth_bp.route("/logout", methods=["POST", "OPTIONS"])
@auth_bp.route("/api/logout", methods=["POST", "OPTIONS"])
def logout():
    if request.method == "OPTIONS":
        return handle_options()

    # Stateless token auth: the extension clears the token locally.
    return json_ok({"message": "Logged out."})


@auth_bp.route("/api/auth/debug", methods=["GET"])
def debug():
    init_db()
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    conn.close()
    return json_ok({"users": count})