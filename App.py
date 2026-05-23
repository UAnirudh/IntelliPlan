import flask
import sys as _sys

# ── Dev-mode fix: when this file is run via `python App.py` it loads as
# `__main__`. Any subsequent `from App import ...` (e.g. from auth_api's
# lazy helpers) would re-execute this whole file as a SECOND `App`
# module, creating duplicate `db`/`User`/Flask-app instances and
# producing "current Flask app is not registered with this 'SQLAlchemy'
# instance" errors when blueprints touch the DB. Aliasing __main__ as
# `App` makes both import paths resolve to the same module object.
if __name__ == "__main__":
    _sys.modules.setdefault("App", _sys.modules[__name__])
from flask import render_template, request, redirect, session, url_for
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from studentvue_helper import (
    test_login,
    get_assignments as get_sv_assignments,
    get_missing_assignments,
    normalize_district_url,
    _compute_priority as compute_priority,
)
from groq import Groq
import re
import json
import uuid
import base64
import io
import functools
import random
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from auth_api import auth_bp, verify_token
from chatbot_api import chatbot_bp
from werkzeug.utils import secure_filename
import secrets as secrets_module
import urllib.parse
from flask import jsonify, send_from_directory
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user
)
from flask_bcrypt import Bcrypt
from werkzeug.middleware.proxy_fix import ProxyFix

# ── FIX: Use database-backed sessions so Railway container restarts
#         don't wipe the OAuth state between redirect hops.
#         flask-session with "sqlalchemy" keeps state in the DB itself.
from flask_session import Session

try:
    from google_calendar_helper import (
        get_auth_url, exchange_code_for_token,
        get_upcoming_events, add_schedule_to_calendar, find_free_slots,
        compute_free_hours, merge_token_data, has_calendar_scope
    )
    GCAL_AVAILABLE = True
except Exception as e:
    print(f"Google Calendar not available: {e}")
    GCAL_AVAILABLE = False

try:
    from notion_helper import (
        test_notion_token, test_notion_token_detail, get_notion_databases,
        get_shared_pages, create_intelliplan_database,
        get_notion_tasks, create_notion_task,
        update_notion_task, complete_notion_task,
        get_notion_auth_url, exchange_notion_code,
        get_upcoming_notion_tasks, add_schedule_to_notion,
    )
    NOTION_AVAILABLE = True
except Exception as e:
    print(f"Notion not available: {e}")
    NOTION_AVAILABLE = False

try:
    from canvas_oauth import (
        get_canvas_auth_url, exchange_canvas_code,
        refresh_canvas_token, revoke_canvas_token,
        oauth_is_configured as canvas_oauth_configured,
        DEFAULT_CANVAS_BASE,
    )
    CANVAS_OAUTH_AVAILABLE = True
except Exception as e:
    print(f"Canvas OAuth not available: {e}")
    CANVAS_OAUTH_AVAILABLE = False

if os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1
    )

limiter = Limiter(key_func=get_remote_address)

load_dotenv()

app = flask.Flask(
    __name__,
    template_folder="Main_Project/templates",
)

app.secret_key = os.getenv("SECRET_KEY", "intelliplan-dev-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://intelliplan.tech").rstrip("/")
APP_DOMAIN = APP_BASE_URL.replace("https://", "").replace("http://", "").split("/", 1)[0]
LEGACY_ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("LEGACY_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
ALLOWED_WEB_ORIGINS = [
    APP_BASE_URL,
    f"https://www.{APP_DOMAIN}" if not APP_DOMAIN.startswith("www.") else APP_BASE_URL,
    *LEGACY_ALLOWED_ORIGINS,
]

# ── FIX: Switch SESSION_TYPE from "filesystem" to "sqlalchemy".
#   Filesystem sessions are stored in /tmp on Railway — ephemeral containers
#   can spin up a NEW instance to serve the OAuth callback, which has an empty
#   /tmp and therefore loses oauth_state, causing the IPE-XXXXXXXX 500 error.
#   Storing sessions in the same Postgres/SQLite DB as the app makes them
#   survive across instances and restarts.
#   We configure this BEFORE db = SQLAlchemy(app) because flask-session
#   needs the app config ready, and we wire it up after db is created below.
app.config["SESSION_TYPE"] = "sqlalchemy"
app.config["SESSION_PERMANENT"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["REMEMBER_COOKIE_SECURE"] = True
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "None"
app.config["PREFERRED_URL_SCHEME"] = "https" if APP_BASE_URL.startswith("https://") else "http"
app.permanent_session_lifetime = timedelta(days=7)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///intelliplan.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["NOTES_UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads", "course_notes")
os.makedirs(app.config["NOTES_UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ── FIX: Point flask-session at the SQLAlchemy db so sessions are durable.
app.config["SESSION_SQLALCHEMY"] = db
Session(app)

app.register_blueprint(auth_bp)
app.register_blueprint(chatbot_bp)

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    is_extension = origin.startswith("chrome-extension://")
    is_local = origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1")
    if origin and (origin.rstrip("/") in ALLOWED_WEB_ORIGINS or is_extension or is_local):
        # Echo origin back for our own domains/extension so credentials work
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    else:
        # Allow any other origin (including Lotus and other embedders)
        response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Extension-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    # Allow embedding in iframes on any domain
    response.headers.pop("X-Frame-Options", None)
    response.headers["Content-Security-Policy"] = "frame-ancestors * https://lotus-72e3e.web.app https://intelliplan.tech http://localhost:5000"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "all"
    return response

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# ── MODELS ────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False, default="")
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # ── Referral + tier columns (added together so we only schema-migrate once) ──
    referral_code = db.Column(db.String(16), unique=True, nullable=True)
    referred_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    tier = db.Column(db.String(16), default="free")            # "free" | "pro"
    pro_until = db.Column(db.DateTime, nullable=True)          # Pro expiry timestamp
    # ── Phone + reminder opt-in ──
    phone = db.Column(db.String(32), nullable=True)            # E.164 (+15551234567) or blank
    sms_reminders_opt_in = db.Column(db.Boolean, default=False)
    push_reminders_opt_in = db.Column(db.Boolean, default=False)
    reminder_lead_minutes = db.Column(db.Integer, default=60)  # how far ahead to remind
    sms_carrier = db.Column(db.String(32), default="tmobile")  # SMS-over-email gateway key
    # ── COPPA: under-13 gating ──
    birth_year = db.Column(db.Integer, nullable=True)          # collected at signup
    parent_email = db.Column(db.String(255), nullable=True)    # for under-13 accounts
    parent_consent_granted = db.Column(db.Boolean, default=False)
    parent_consent_token = db.Column(db.String(64), nullable=True)  # signed verification token
    # ── Stripe customer linkage ──
    stripe_customer_id = db.Column(db.String(64), nullable=True)
    linked_accounts = db.relationship("LinkedAccount", backref="user", lazy=True, cascade="all, delete-orphan")
    dismissed = db.relationship("DismissedAssignment", backref="user", lazy=True, cascade="all, delete-orphan")
    descriptions = db.relationship("CustomDescription", backref="user", lazy=True, cascade="all, delete-orphan")

class UserIdentity(db.Model):
    """Student identity profile collected at onboarding and editable in settings.

    Drives chatbot/tutor personalization — grade level, academic focus areas, and
    goals/priorities are injected into the Plani/Tutor system prompt so replies
    match the student's curriculum and ambitions.
    """
    __tablename__ = "user_identities"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    grade_level = db.Column(db.String(32), nullable=True)        # e.g. "11th grade"
    focus_areas = db.Column(db.Text, default="[]")                # JSON list of subjects
    goals = db.Column(db.Text, default="")                        # free-text priorities
    completed = db.Column(db.Boolean, default=False)              # questionnaire finished
    availability = db.Column(db.Text, default="{}")               # JSON: day -> time range
    weekly_commitments = db.Column(db.Text, default="")           # free-text extracurriculars
    class_schedule = db.Column(db.Text, default="[]")             # JSON list of class slots
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def focus_list(self):
        try:
            value = json.loads(self.focus_areas or "[]")
            return value if isinstance(value, list) else []
        except (TypeError, json.JSONDecodeError):
            return []

    def avail_dict(self):
        try:
            v = json.loads(self.availability or "{}")
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    def class_list(self):
        try:
            v = json.loads(self.class_schedule or "[]")
            return v if isinstance(v, list) else []
        except Exception:
            return []

    def to_dict(self):
        return {
            "grade_level": self.grade_level or "",
            "focus_areas": self.focus_list(),
            "goals": self.goals or "",
            "completed": bool(self.completed),
            "availability": self.avail_dict(),
            "weekly_commitments": self.weekly_commitments or "",
            "class_schedule": self.class_list(),
        }


class LinkedAccount(db.Model):
    __tablename__ = "linked_accounts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    profile_id = db.Column(db.String(16), unique=True, default=lambda: str(uuid.uuid4())[:8])
    name = db.Column(db.String(255), default="My Account")
    login_type = db.Column(db.String(32), nullable=False)
    credentials = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_credentials(self):
        return json.loads(self.credentials)

    def set_credentials(self, creds_dict):
        self.credentials = json.dumps(creds_dict)

class DismissedAssignment(db.Model):
    __tablename__ = "dismissed_assignments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(512), nullable=False)
    data = db.Column(db.Text, default="{}")

class TestMark(db.Model):
    __tablename__ = "test_marks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(512), nullable=False)
    data = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CustomDescription(db.Model):
    __tablename__ = "custom_descriptions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    assignment_title = db.Column(db.String(512), nullable=False)
    description = db.Column(db.Text, nullable=False)

class GoogleIntegration(db.Model):
    __tablename__ = "google_integrations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    token_data = db.Column(db.Text, nullable=False)
    # Multi-account support — each row is one connected Google Account.
    # `is_active=True` marks the account currently used for calendar sync.
    account_email = db.Column(db.String(255), nullable=True)
    account_name = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    connected_at = db.Column(db.DateTime, default=datetime.utcnow)

class NotionIntegration(db.Model):
    __tablename__ = "notion_integrations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    token = db.Column(db.String(512), nullable=False)
    database_id = db.Column(db.String(256), nullable=True)
    # New OAuth metadata — populated by /oauth/notion/callback. Older
    # rows from the manual-token flow leave these as NULL.
    auth_type = db.Column(db.String(16), default="manual")  # "oauth" | "manual"
    workspace_id = db.Column(db.String(64), nullable=True)
    workspace_name = db.Column(db.String(256), nullable=True)
    workspace_icon = db.Column(db.String(512), nullable=True)
    bot_id = db.Column(db.String(64), nullable=True)
    connected_at = db.Column(db.DateTime, default=datetime.utcnow)


class CanvasIntegration(db.Model):
    """Canvas LMS OAuth tokens. Separate from the legacy token-paste flow,
    which still writes to LinkedAccount.credentials for backwards compat."""
    __tablename__ = "canvas_integrations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    canvas_base = db.Column(db.String(256), nullable=False)
    access_token = db.Column(db.String(2048), nullable=False)
    refresh_token = db.Column(db.String(2048), nullable=True)
    token_expires_at = db.Column(db.DateTime, nullable=True)
    canvas_user_id = db.Column(db.String(64), nullable=True)
    canvas_user_name = db.Column(db.String(256), nullable=True)
    connected_at = db.Column(db.DateTime, default=datetime.utcnow)

class ManualTask(db.Model):
    __tablename__ = "manual_tasks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(512), nullable=False)
    due_date = db.Column(db.String(32), default="")
    priority = db.Column(db.String(16), default="Medium")
    course = db.Column(db.String(256), default="Personal")
    estimated_time = db.Column(db.Integer, default=60)
    notes = db.Column(db.Text, default="")
    done = db.Column(db.Boolean, default=False)
    notion_page_id = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SavedSchedule(db.Model):
    __tablename__ = "saved_schedules"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    name = db.Column(db.String(256), default="My Schedule")
    schedule_data = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

class Task:
    def __init__(self, name, deadline, duration, priority_weight=1, difficulty=1):
        self.name = name
        self.deadline = deadline
        self.duration = duration
        self.priority_weight = priority_weight
        self.difficulty = difficulty

class TaskFeedback(db.Model):
    __tablename__ = "task_feedback"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(512), nullable=False)
    course = db.Column(db.String(256), default="")
    estimated_time = db.Column(db.Integer, default=60)
    actual_time = db.Column(db.Integer, nullable=True)
    difficulty = db.Column(db.String(16), default="Medium")
    priority = db.Column(db.String(16), default="Medium")
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)
    day_of_week = db.Column(db.String(16), default="")
    time_of_day = db.Column(db.String(16), default="")

class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    subscription_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ReminderSent(db.Model):
    """Dedupe row — one per (user, task, channel) so a reminder is sent
    at most once per task, regardless of how often the cron runs."""
    __tablename__ = "reminders_sent"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    task_key = db.Column(db.String(256), nullable=False)  # "manual:<id>" or "canvas:<id>"
    channel = db.Column(db.String(16), nullable=False)    # "sms" or "push"
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

class CourseNote(db.Model):
    __tablename__ = "course_notes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    course_name = db.Column(db.String(255), nullable=False)
    course_id = db.Column(db.String(128), nullable=True)
    course_source = db.Column(db.String(32), nullable=True)
    note_date = db.Column(db.String(32), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=True)
    stored_filename = db.Column(db.String(255), nullable=True)
    text_content = db.Column(db.Text, default="")
    summary_cache = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ExtensionToken(db.Model):
    __tablename__ = "extension_tokens"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Lesson(db.Model):
    """Uploaded lesson recording (audio or video) + AI-generated summary."""
    __tablename__ = "lessons"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    course = db.Column(db.String(128), default="")
    tags = db.Column(db.Text, default="[]")            # JSON list of tag strings
    media_kind = db.Column(db.String(16), default="video")  # video | audio
    original_filename = db.Column(db.String(255), default="")
    stored_filename = db.Column(db.String(255), default="")
    mime_type = db.Column(db.String(64), default="")
    duration_seconds = db.Column(db.Integer, default=0)
    transcript = db.Column(db.Text, default="")
    summary = db.Column(db.Text, default="")
    summary_status = db.Column(db.String(16), default="pending")  # pending | ready | failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def tag_list(self):
        try:
            v = json.loads(self.tags or "[]")
            return v if isinstance(v, list) else []
        except Exception:
            return []

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "course": self.course or "",
            "tags": self.tag_list(),
            "media_kind": self.media_kind,
            "stream_url": f"/lessons/{self.id}/stream",
            "original_filename": self.original_filename,
            "duration_seconds": int(self.duration_seconds or 0),
            "summary": self.summary or "",
            "summary_status": self.summary_status or "pending",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class StudyGroup(db.Model):
    """A small study group: shared notes, schedule, video room link."""
    __tablename__ = "study_groups"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    topic = db.Column(db.String(120), default="")           # e.g. "AP Calc BC", "SAT Math"
    level = db.Column(db.String(32), default="any")         # beginner | intermediate | advanced | any
    style = db.Column(db.String(32), default="any")         # focused | discussion | quizzing | flashcards | any
    visibility = db.Column(db.String(16), default="public") # public | private
    description = db.Column(db.Text, default="")
    shared_notes = db.Column(db.Text, default="")
    meeting_url = db.Column(db.String(512), default="")     # generated Jitsi room
    next_meeting_at = db.Column(db.DateTime, nullable=True)
    next_meeting_topic = db.Column(db.String(255), default="")
    suggested_plan = db.Column(db.Text, default="")          # AI-generated study plan
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class StudyGroupMember(db.Model):
    __tablename__ = "study_group_members"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("study_groups.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    role = db.Column(db.String(16), default="member")  # owner | member
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)


class FeatureFlag(db.Model):
    """Admin-controlled kill switches. Default behaviour for an unknown
    flag is "enabled" — so apps still work if a flag row is missing or
    the table fails to load. Set `enabled=False` to disable a feature."""
    __tablename__ = "feature_flags"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    description = db.Column(db.String(255), default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LiveSession(db.Model):
    """A real-time study room — Jitsi-backed. Anyone with the link can
    join. Owner can toggle audio/video defaults and pin materials."""
    __tablename__ = "live_sessions"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    title = db.Column(db.String(160), nullable=False, default="Study session")
    topic = db.Column(db.String(160), default="")
    room_slug = db.Column(db.String(48), unique=True, nullable=False)
    audio_only = db.Column(db.Boolean, default=False)
    video_enabled = db.Column(db.Boolean, default=True)
    audio_enabled = db.Column(db.Boolean, default=True)
    materials = db.Column(db.Text, default="")
    is_open = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StudySession(db.Model):
    __tablename__ = "study_sessions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    mode = db.Column(db.String(16), default="casual")
    questions_total = db.Column(db.Integer, default=0)
    questions_correct = db.Column(db.Integer, default=0)
    points_earned = db.Column(db.Integer, default=0)
    duration_seconds = db.Column(db.Integer, default=0)
    completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StudyPoints(db.Model):
    __tablename__ = "study_points"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    total_points = db.Column(db.Integer, default=0)
    spark_balance = db.Column(db.Integer, default=0)
    sparks_earned_total = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    streak_count = db.Column(db.Integer, default=0)
    streak_freeze_count = db.Column(db.Integer, default=0)
    freeze_capacity = db.Column(db.Integer, default=2)
    last_active_date = db.Column(db.String(16), default="")
    repair_last_used = db.Column(db.String(16), default="")
    repair_eligible_until = db.Column(db.DateTime, nullable=True)
    streak_history = db.Column(db.Text, default="[]")
    session_history = db.Column(db.Text, default="[]")
    badges = db.Column(db.Text, default="[]")
    active_booster = db.Column(db.Text, default="null")
    active_cosmetics = db.Column(db.Text, default="{}")
    weekly_quests = db.Column(db.Text, default="{}")
    shop_purchases = db.Column(db.Text, default="[]")
    longest_streak = db.Column(db.Integer, default=0)
    total_sessions = db.Column(db.Integer, default=0)
    last_daily_claim = db.Column(db.String(16), default="")   # YYYY-MM-DD of last daily-chest claim
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class StudyMastery(db.Model):
    __tablename__ = "study_mastery"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_session_id = db.Column(db.String(64), nullable=True)
    question_key = db.Column(db.String(512), nullable=False)
    question_text = db.Column(db.Text, default="")
    answer_text = db.Column(db.Text, default="")
    topic = db.Column(db.String(256), default="")
    mastery_level = db.Column(db.Integer, default=0)
    times_seen = db.Column(db.Integer, default=0)
    times_correct = db.Column(db.Integer, default=0)
    times_partial = db.Column(db.Integer, default=0)
    last_seen = db.Column(db.String(16), default="")
    next_review = db.Column(db.String(16), default="")
    easiness_factor = db.Column(db.Float, default=2.5)
    interval_days = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

STREAK_TIERS = [
    {"id": "spark", "name": "Spark", "min": 1, "max": 6, "bonus": 5, "freeze_cap": 2, "color": "#ef4444"},
    {"id": "flame", "name": "Flame", "min": 7, "max": 13, "bonus": 10, "freeze_cap": 3, "color": "#f97316"},
    {"id": "blaze", "name": "Blaze", "min": 14, "max": 20, "bonus": 15, "freeze_cap": 3, "color": "#f59e0b"},
    {"id": "inferno", "name": "Inferno", "min": 21, "max": 29, "bonus": 20, "freeze_cap": 5, "color": "#dc2626"},
    {"id": "wildfire", "name": "Wildfire", "min": 30, "max": 59, "bonus": 30, "freeze_cap": 5, "color": "#f97316"},
    {"id": "firestorm", "name": "Firestorm", "min": 60, "max": 99, "bonus": 50, "freeze_cap": 5, "color": "#06b6d4"},
    {"id": "legendary", "name": "Legendary", "min": 100, "max": 364, "bonus": 75, "freeze_cap": 5, "color": "#8b5cf6"},
    {"id": "eternal", "name": "Eternal", "min": 365, "max": 99999, "bonus": 150, "freeze_cap": 5, "color": "#22c55e"},
]

LEVEL_TITLE_TIERS = [
    (1, "Learner"),
    (5, "Student"),
    (10, "Scholar"),
    (15, "Researcher"),
    (20, "Expert"),
    (25, "Veteran"),
    (30, "Mentor"),
    (40, "Master"),
    (50, "Legend"),
]

def level_title_for(level):
    title = LEVEL_TITLE_TIERS[0][1]
    for threshold, candidate in LEVEL_TITLE_TIERS:
        if level >= threshold:
            title = candidate
    return title

LEVELS = [
    (level, level_title_for(level), 0 if level == 1 else int(round((35 * ((level - 1) ** 2)) / 25) * 25))
    for level in range(1, 51)
]

STREAK_MILESTONES = {
    3: {"sparks": 25, "freezes": 0, "badge": "first_flame", "title": None},
    7: {"sparks": 75, "freezes": 1, "badge": "week_warrior", "title": None},
    14: {"sparks": 100, "freezes": 1, "badge": "fortnight_fighter", "title": None},
    21: {"sparks": 150, "freezes": 0, "badge": "inferno_initiate", "title": "Grinder"},
    30: {"sparks": 250, "freezes": 2, "badge": "monthly_master", "title": "Monthly Master"},
    60: {"sparks": 500, "freezes": 2, "badge": "sixty_strong", "title": None},
    100: {"sparks": 1000, "freezes": 3, "badge": "century_club", "title": "Legendary"},
    365: {"sparks": 2000, "freezes": 3, "badge": "year_of_fire", "title": "Eternal"},
}

BADGE_CATALOG = {
    "first_flame": {"name": "First Flame", "kind": "streak"},
    "week_warrior": {"name": "Week Warrior", "kind": "streak"},
    "fortnight_fighter": {"name": "Fortnight Fighter", "kind": "streak"},
    "inferno_initiate": {"name": "Inferno Initiate", "kind": "streak"},
    "monthly_master": {"name": "Monthly Master", "kind": "streak"},
    "sixty_strong": {"name": "Sixty Strong", "kind": "streak"},
    "century_club": {"name": "Century Club", "kind": "streak"},
    "year_of_fire": {"name": "Year of Fire", "kind": "streak"},
    "first_session": {"name": "First Session", "kind": "session"},
    "getting_serious": {"name": "Getting Serious", "kind": "session"},
    "dedicated": {"name": "Dedicated", "kind": "session"},
    "committed": {"name": "Committed", "kind": "session"},
    "unstoppable": {"name": "Unstoppable", "kind": "session"},
    "sharp": {"name": "Sharp", "kind": "accuracy"},
    "precise": {"name": "Precise", "kind": "accuracy"},
    "flawless": {"name": "Flawless", "kind": "accuracy"},
    "perfect_week": {"name": "Perfect Week", "kind": "special"},
    "speed_demon": {"name": "Speed Demon", "kind": "special"},
    "night_owl": {"name": "Night Owl", "kind": "special"},
    "early_bird": {"name": "Early Bird", "kind": "special"},
    "comeback_kid": {"name": "Comeback Kid", "kind": "special"},
    "quest_starter": {"name": "Quest Starter", "kind": "quest"},
    "quest_finisher": {"name": "Quest Finisher", "kind": "quest"},
    "weekly_champion": {"name": "Weekly Champion", "kind": "quest"},
    "shopper": {"name": "Spark Shopper", "kind": "shop"},
    "deal_hunter": {"name": "Deal Hunter", "kind": "shop"},
    "freeze_ready": {"name": "Freeze Ready", "kind": "protection"},
    "booster_pilot": {"name": "Booster Pilot", "kind": "shop"},
    "spark_saver": {"name": "Spark Saver", "kind": "currency"},
    "style_setter": {"name": "Style Setter", "kind": "cosmetic"},
}

SHOP_ITEMS = {
    "streak_freeze": {"name": "Streak Freeze", "price": 200, "kind": "protection", "value": 1, "description": "Blocks one missed day."},
    "freeze_pack": {"name": "Freeze Pack", "price": 500, "kind": "protection", "value": 3, "description": "Three freezes at a discount."},
    "weekend_shield": {"name": "Weekend Shield", "price": 350, "kind": "protection", "value": 2, "description": "Adds two freezes for busy weekends or travel days."},
    "repair_token": {"name": "Repair Token", "price": 900, "kind": "inventory", "field": "repair_credits", "value": 1, "description": "Cuts the next streak repair cost in half."},
    "booster_2x": {"name": "2x Sparks", "price": 100, "kind": "booster", "multiplier": 2, "uses": 1, "description": "Doubles Sparks in the next session."},
    "booster_3x": {"name": "3x Sparks", "price": 250, "kind": "booster", "multiplier": 3, "uses": 1, "description": "Triples Sparks in the next session."},
    "daily_booster": {"name": "Daily Booster", "price": 350, "kind": "booster", "multiplier": 1.5, "hours": 24, "description": "+50% Sparks for 24 hours."},
    "focus_booster": {"name": "Focus Booster", "price": 180, "kind": "booster", "multiplier": 1.25, "hours": 72, "description": "+25% Sparks for the next three days."},
    "skip_pack": {"name": "Question Skip Pack", "price": 75, "kind": "inventory", "field": "skips", "value": 5, "description": "Skip five questions without losing momentum."},
    "hint_pack": {"name": "Hint Token Pack", "price": 120, "kind": "inventory", "field": "hints", "value": 10, "description": "Use AI hints on hard questions."},
    "color_gold": {"name": "Streak Color: Gold", "price": 400, "kind": "cosmetic", "slot": "streak_color", "value": "gold", "description": "Gold streak glow."},
    "color_neon": {"name": "Streak Color: Neon", "price": 400, "kind": "cosmetic", "slot": "streak_color", "value": "neon", "description": "Neon streak glow."},
    "color_forest": {"name": "Streak Color: Forest", "price": 400, "kind": "cosmetic", "slot": "streak_color", "value": "forest", "description": "Calm green streak glow."},
    "title_scholar": {"name": "Profile Title: Scholar", "price": 300, "kind": "cosmetic", "slot": "title", "value": "Scholar", "description": "Display Scholar by your name."},
    "title_grinder": {"name": "Profile Title: Grinder", "price": 300, "kind": "cosmetic", "slot": "title", "value": "Grinder", "description": "Display Grinder by your name."},
    "title_comeback": {"name": "Profile Title: Comeback Kid", "price": 450, "kind": "cosmetic", "slot": "title", "value": "Comeback Kid", "description": "Display Comeback Kid by your name."},
    "calendar_dark": {"name": "Calendar Theme: Dark", "price": 500, "kind": "cosmetic", "slot": "calendar_theme", "value": "dark", "description": "Dark calendar grid."},
    "frame_ember": {"name": "Streak Frame: Ember", "price": 600, "kind": "cosmetic", "slot": "streak_frame", "value": "ember", "description": "Animated ember frame."},
    "frame_aurora": {"name": "Streak Frame: Aurora", "price": 800, "kind": "cosmetic", "slot": "streak_frame", "value": "aurora", "description": "Rare aurora frame."},
    "frame_cosmic": {"name": "Streak Frame: Cosmic", "price": 950, "kind": "cosmetic", "slot": "streak_frame", "value": "cosmic", "description": "Premium cosmic streak frame."},
}

QUEST_POOL = [
    {"id": "study_5_days", "title": "Study 5 days this week", "metric": "study_days", "target": 5, "reward": 80},
    {"id": "answer_75_correct", "title": "Answer 75 questions correctly", "metric": "correct_answers", "target": 75, "reward": 60},
    {"id": "serious_3_sessions", "title": "Complete 3 Serious or Extreme sessions", "metric": "focus_sessions", "target": 3, "reward": 70},
    {"id": "maintain_7_days", "title": "Maintain your streak all 7 days", "metric": "study_days", "target": 7, "reward": 120},
    {"id": "master_3_concepts", "title": "Master 3 new concepts", "metric": "mastered_concepts", "target": 3, "reward": 60},
    {"id": "earn_200_session", "title": "Earn 200 Sparks in a single session", "metric": "single_session_sparks", "target": 200, "reward": 50},
    {"id": "perfect_session", "title": "Complete a perfect session", "metric": "perfect_sessions", "target": 1, "reward": 90},
    {"id": "study_30_minutes", "title": "Spend 30+ minutes studying this week", "metric": "study_minutes", "target": 30, "reward": 70},
]

def safe_json_load(raw, fallback):
    try:
        if raw in (None, ""):
            return fallback
        return json.loads(raw)
    except Exception:
        return fallback

def current_study_tier(streak_count):
    streak_count = int(streak_count or 0)
    for tier in STREAK_TIERS:
        if tier["min"] <= streak_count <= tier["max"]:
            return tier
    return STREAK_TIERS[0]

def repair_cost_for(streak_count):
    streak_count = int(streak_count or 0)
    if streak_count <= 6:
        return 150
    if streak_count <= 13:
        return 300
    if streak_count <= 29:
        return 500
    if streak_count <= 59:
        return 800
    return 1200

def level_for_sparks(sparks_total):
    sparks_total = int(sparks_total or 0)
    current = LEVELS[0]
    for level in LEVELS:
        if sparks_total >= level[2]:
            current = level
    return {"level": current[0], "title": current[1], "next": next(({"level": l, "title": t, "required": req} for l, t, req in LEVELS if req > sparks_total), None)}

def add_badges(p, badge_ids):
    badges = safe_json_load(p.badges, [])
    changed = []
    for badge_id in badge_ids:
        if badge_id and badge_id not in badges:
            badges.append(badge_id)
            changed.append(badge_id)
    if changed:
        p.badges = json.dumps(badges)
    return changed

def grant_sparks(p, amount, reason="", apply_booster=True):
    amount = max(0, int(round(float(amount or 0))))
    if amount <= 0:
        return {"awarded": 0, "base": 0, "multiplier": 1, "level_up": None}
    multiplier = 1
    booster = safe_json_load(p.active_booster, None)
    now = datetime.utcnow()
    if apply_booster and isinstance(booster, dict):
        expires_at = booster.get("expires_at")
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) < now:
                    booster = None
            except Exception:
                booster = None
        if booster:
            multiplier = float(booster.get("multiplier", 1) or 1)
            if booster.get("uses") is not None:
                booster["uses"] = max(0, int(booster.get("uses", 0)) - 1)
                p.active_booster = json.dumps(booster) if booster["uses"] > 0 else "null"
    awarded = int(round(amount * multiplier))
    old_level = int(p.level or 1)
    p.spark_balance = int(p.spark_balance or 0) + awarded
    p.sparks_earned_total = int(p.sparks_earned_total or p.total_points or 0) + awarded
    p.total_points = int(p.sparks_earned_total or 0)
    new_level = level_for_sparks(p.sparks_earned_total)["level"]
    level_up = None
    if new_level > old_level:
        p.level = new_level
        p.spark_balance += 50
        p.sparks_earned_total += 50
        p.total_points = p.sparks_earned_total
        level_up = {"from": old_level, "to": new_level, "bonus": 50}
    p.updated_at = datetime.utcnow()
    return {"awarded": awarded, "base": amount, "multiplier": multiplier, "level_up": level_up}

def active_week_id(dt=None):
    dt = dt or datetime.now()
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"

def ensure_weekly_quests(p):
    week_id = active_week_id()
    data = safe_json_load(p.weekly_quests, {})
    if data.get("week_id") == week_id and data.get("quests"):
        return data
    seed = f"{week_id}:{p.user_id or p.guest_session_id or p.id or 'study'}"
    rng = random.Random(seed)
    quests = [dict(q) for q in rng.sample(QUEST_POOL, 3)]
    data = {
        "week_id": week_id,
        "quests": quests,
        "progress": {q["id"]: 0 for q in quests},
        "completed": [],
        "completion_bonus_claimed": False,
        "study_dates": [],
    }
    p.weekly_quests = json.dumps(data)
    return data

def update_quest_progress(p, session_data):
    quests = ensure_weekly_quests(p)
    progress = quests.setdefault("progress", {})
    completed = set(quests.get("completed", []))
    study_date = session_data.get("date") or datetime.now().strftime("%Y-%m-%d")
    study_dates = set(quests.get("study_dates", []))
    study_dates.add(study_date)
    quests["study_dates"] = sorted(study_dates)
    metrics = {
        "study_days": len(study_dates),
        "correct_answers": int(session_data.get("correct", 0) or 0),
        "focus_sessions": 1 if session_data.get("mode") in ("serious", "extreme") else 0,
        "mastered_concepts": int(session_data.get("mastered_concepts", 0) or 0),
        "single_session_sparks": int(session_data.get("sparks", 0) or 0),
        "perfect_sessions": 1 if int(session_data.get("questions", 0) or 0) > 0 and int(session_data.get("correct", 0) or 0) >= int(session_data.get("questions", 0) or 0) else 0,
        "study_minutes": int(round((int(session_data.get("duration", 0) or 0)) / 60)),
    }
    rewards = []
    quest_badges = []
    for q in quests.get("quests", []):
        qid = q["id"]
        metric = q["metric"]
        if metric == "study_days":
            progress[qid] = len(study_dates)
        elif metric == "single_session_sparks":
            progress[qid] = max(int(progress.get(qid, 0) or 0), metrics[metric])
        else:
            progress[qid] = int(progress.get(qid, 0) or 0) + metrics.get(metric, 0)
        if progress[qid] >= q["target"] and qid not in completed:
            completed.add(qid)
            rewards.append({"quest_id": qid, "title": q["title"], "sparks": q["reward"]})
            grant_sparks(p, q["reward"], f"quest:{qid}", apply_booster=False)
            quest_badges.append("quest_starter")
    quests["completed"] = sorted(completed)
    if len(completed) >= 3 and not quests.get("completion_bonus_claimed"):
        quests["completion_bonus_claimed"] = True
        rewards.append({"quest_id": "weekly_completion", "title": "Weekly Completion Bonus", "sparks": 150, "freezes": 1})
        grant_sparks(p, 150, "weekly_completion", apply_booster=False)
        p.streak_freeze_count = min(int(p.freeze_capacity or 2), int(p.streak_freeze_count or 0) + 1)
        quest_badges.extend(["quest_finisher", "weekly_champion"])
    if quest_badges:
        add_badges(p, quest_badges)
    p.weekly_quests = json.dumps(quests)
    return rewards

def passive_freezes_due(p, today=None):
    today = today or datetime.now()
    if int(p.streak_count or 0) < 30:
        return 0
    history = safe_json_load(p.shop_purchases, [])
    week_id = active_week_id(today)
    passive_key = f"passive_freeze:{week_id}"
    if any(item.get("id") == passive_key for item in history if isinstance(item, dict)):
        return 0
    amount = 2 if int(p.streak_count or 0) >= 100 else 1
    history.append({"id": passive_key, "item_id": "passive_freeze", "qty": amount, "created_at": datetime.utcnow().isoformat()})
    p.shop_purchases = json.dumps(history[-200:])
    return amount

def reconcile_missed_streak(p):
    if not p.last_active_date or int(p.streak_count or 0) <= 0:
        return None
    today = datetime.now().date()
    try:
        last = datetime.strptime(p.last_active_date[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    if last >= today - timedelta(days=1):
        return None
    history = safe_json_load(p.streak_history, [])
    cosmetics = safe_json_load(p.active_cosmetics, {})
    missed_day = (last + timedelta(days=1)).strftime("%Y-%m-%d")
    tier = current_study_tier(p.streak_count)
    p.freeze_capacity = tier["freeze_cap"]
    if int(p.streak_freeze_count or 0) > 0:
        p.streak_freeze_count = max(0, int(p.streak_freeze_count or 0) - 1)
        p.last_active_date = missed_day
        if missed_day not in history:
            history.append(missed_day)
            p.streak_history = json.dumps(sorted(history)[-90:])
        return {"type": "freeze_consumed", "message": "Streak Freeze used - your streak is safe. Back tomorrow."}
    if not p.repair_eligible_until or p.repair_eligible_until < datetime.utcnow():
        p.repair_eligible_until = datetime.utcnow() + timedelta(hours=48)
        cosmetics["broken_streak_count"] = int(p.streak_count or 0)
        cosmetics["broken_last_active_date"] = p.last_active_date
        p.active_cosmetics = json.dumps(cosmetics)
        p.streak_count = 0
        return {"type": "repair_window", "message": "Your streak broke, but you can still save it."}
    return {"type": "repair_window", "message": "Your streak broke, but you can still save it."}

def apply_study_schema_migrations():
    inspector = inspect(db.engine)
    if "study_points" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("study_points")}
    dialect = db.engine.dialect.name
    text_type = "TEXT"
    dt_type = "TIMESTAMP" if dialect != "sqlite" else "DATETIME"
    columns = {
        "spark_balance": "INTEGER DEFAULT 0",
        "sparks_earned_total": "INTEGER DEFAULT 0",
        "level": "INTEGER DEFAULT 1",
        "freeze_capacity": "INTEGER DEFAULT 2",
        "repair_last_used": f"{text_type} DEFAULT ''",
        "repair_eligible_until": dt_type,
        "badges": f"{text_type} DEFAULT '[]'",
        "active_booster": f"{text_type} DEFAULT 'null'",
        "active_cosmetics": f"{text_type} DEFAULT '{{}}'",
        "weekly_quests": f"{text_type} DEFAULT '{{}}'",
        "shop_purchases": f"{text_type} DEFAULT '[]'",
        "longest_streak": "INTEGER DEFAULT 0",
        "total_sessions": "INTEGER DEFAULT 0",
    }
    for name, ddl in columns.items():
        if name not in existing:
            db.session.execute(text(f"ALTER TABLE study_points ADD COLUMN {name} {ddl}"))
    db.session.execute(text("UPDATE study_points SET spark_balance = COALESCE(spark_balance, total_points, 0)"))
    db.session.execute(text("UPDATE study_points SET sparks_earned_total = COALESCE(sparks_earned_total, total_points, 0)"))
    db.session.execute(text("UPDATE study_points SET level = COALESCE(level, 1), freeze_capacity = COALESCE(freeze_capacity, 2), longest_streak = COALESCE(longest_streak, streak_count, 0), total_sessions = COALESCE(total_sessions, 0)"))
    db.session.execute(text("UPDATE study_points SET badges = COALESCE(badges, '[]'), active_booster = COALESCE(active_booster, 'null'), active_cosmetics = COALESCE(active_cosmetics, '{}'), weekly_quests = COALESCE(weekly_quests, '{}'), shop_purchases = COALESCE(shop_purchases, '[]')"))
    db.session.commit()

with app.app_context():
    db.create_all()
    apply_study_schema_migrations()

print([str(r) for r in app.url_map.iter_rules() if 'tutor' in str(r)])

@login_manager.user_loader
def load_user(user_id):
    # Defensive: if the SELECT for User fails (e.g. a column from a new
    # migration doesn't exist yet on a freshly-deployed DB), DO NOT
    # propagate the exception. Flask-Login bubbles it up into every
    # template render, and the error page itself extends base.html
    # which calls is_logged_in() → load_user → the same exception, so
    # users see only the raw "Server Error" fallback with no way out.
    try:
        return db.session.get(User, int(user_id))
    except Exception as _e:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[load_user] failed for {user_id}: {_e}")
        return None

@login_manager.request_loader
def load_user_from_request(req):
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        return None
    user_id = payload.get("user_id")
    if not user_id:
        return None
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None

# ── CONSTANTS ─────────────────────────────────────────────────
PRIORITY_COLORS = {
    "High": "#ef4444",
    "Medium": "#f59e0b",
    "Low": "#22c55e",
}

DIFFICULTY_COLORS = {
    "Easy": "#86efac",
    "Medium": "#60a5fa",
    "Hard": "#8b5cf6",
}

WORKLOAD_COLORS = {
    "light": "#dcfce7",
    "moderate": "#fef3c7",
    "heavy": "#fee2e2",
}

API_ERROR_MESSAGES = {
    "groq": "AI scheduling is temporarily unavailable. Please try again in a moment.",
    "canvas": "Canvas connection failed. Check your API token in Settings.",
    "studentvue": "StudentVue connection failed. Check your credentials in Settings.",
    "google_calendar": "Google Calendar sync is temporarily unavailable.",
    "notion": "Notion connection failed. Try reconnecting in Integrations.",
    "generic": "Service temporarily unavailable. Please try again later."
}

# ── ERROR HELPERS ─────────────────────────────────────────────
def make_error_id():
    return "IPE-" + str(uuid.uuid4())[:8].upper()

# ── HELPERS ───────────────────────────────────────────────────
def get_guest_session_id():
    if "guest_id" not in session:
        session["guest_id"] = str(uuid.uuid4())
    return session["guest_id"]

def is_logged_in():
    if current_user.is_authenticated:
        return True
    return "login_type" in session

def get_active_account():
    if current_user.is_authenticated:
        acct = LinkedAccount.query.filter_by(user_id=current_user.id, is_active=True).first()
        if acct:
            creds = acct.get_credentials()
            creds["login_type"] = acct.login_type
            return creds
        return None
    login_type = session.get("login_type")
    if not login_type:
        return None
    if login_type == "canvas":
        return {
            "login_type": "canvas",
            "canvas_token": session.get("canvas_token"),
            "canvas_url": session.get("canvas_url"),
        }
    if login_type == "studentvue":
        return {
            "login_type": "studentvue",
            "sv_username": session.get("sv_username"),
            "sv_password": session.get("sv_password"),
            "sv_district_url": session.get("sv_district_url"),
        }
    return None

def get_dismissed_titles():
    if current_user.is_authenticated:
        rows = DismissedAssignment.query.filter_by(user_id=current_user.id).all()
    else:
        gid = get_guest_session_id()
        rows = DismissedAssignment.query.filter_by(guest_session_id=gid).all()
    return {r.title for r in rows}

def get_dismissed_rows():
    if current_user.is_authenticated:
        return DismissedAssignment.query.filter_by(user_id=current_user.id).all()
    gid = get_guest_session_id()
    return DismissedAssignment.query.filter_by(guest_session_id=gid).all()

def save_dismissed(title, data_dict):
    if current_user.is_authenticated:
        existing = DismissedAssignment.query.filter_by(user_id=current_user.id, title=title).first()
        if not existing:
            db.session.add(DismissedAssignment(user_id=current_user.id, title=title, data=json.dumps(data_dict)))
    else:
        gid = get_guest_session_id()
        existing = DismissedAssignment.query.filter_by(guest_session_id=gid, title=title).first()
        if not existing:
            db.session.add(DismissedAssignment(guest_session_id=gid, title=title, data=json.dumps(data_dict)))
    db.session.commit()

def delete_dismissed(title):
    if current_user.is_authenticated:
        DismissedAssignment.query.filter_by(user_id=current_user.id, title=title).delete()
    else:
        gid = get_guest_session_id()
        DismissedAssignment.query.filter_by(guest_session_id=gid, title=title).delete()
    db.session.commit()

def get_test_titles():
    if current_user.is_authenticated:
        rows = TestMark.query.filter_by(user_id=current_user.id).all()
    else:
        gid = get_guest_session_id()
        rows = TestMark.query.filter_by(guest_session_id=gid).all()
    return {r.title for r in rows}

def get_test_marks():
    if current_user.is_authenticated:
        return TestMark.query.filter_by(user_id=current_user.id).order_by(TestMark.created_at.desc()).all()
    gid = get_guest_session_id()
    return TestMark.query.filter_by(guest_session_id=gid).order_by(TestMark.created_at.desc()).all()

def save_test_mark(title, data_dict):
    if current_user.is_authenticated:
        existing = TestMark.query.filter_by(user_id=current_user.id, title=title).first()
        if not existing:
            db.session.add(TestMark(user_id=current_user.id, title=title, data=json.dumps(data_dict)))
    else:
        gid = get_guest_session_id()
        existing = TestMark.query.filter_by(guest_session_id=gid, title=title).first()
        if not existing:
            db.session.add(TestMark(guest_session_id=gid, title=title, data=json.dumps(data_dict)))
    db.session.commit()

def delete_test_mark(title):
    if current_user.is_authenticated:
        TestMark.query.filter_by(user_id=current_user.id, title=title).delete()
    else:
        gid = get_guest_session_id()
        TestMark.query.filter_by(guest_session_id=gid, title=title).delete()
    db.session.commit()

def get_custom_description(assignment_title):
    if current_user.is_authenticated:
        row = CustomDescription.query.filter_by(user_id=current_user.id, assignment_title=assignment_title).first()
    else:
        gid = get_guest_session_id()
        row = CustomDescription.query.filter_by(guest_session_id=gid, assignment_title=assignment_title).first()
    return row.description if row else None

def save_custom_description(assignment_title, description):
    if current_user.is_authenticated:
        row = CustomDescription.query.filter_by(user_id=current_user.id, assignment_title=assignment_title).first()
        if row:
            row.description = description
        else:
            db.session.add(CustomDescription(user_id=current_user.id, assignment_title=assignment_title, description=description))
    else:
        gid = get_guest_session_id()
        row = CustomDescription.query.filter_by(guest_session_id=gid, assignment_title=assignment_title).first()
        if row:
            row.description = description
        else:
            db.session.add(CustomDescription(guest_session_id=gid, assignment_title=assignment_title, description=description))
    db.session.commit()

def get_google_token():
    if current_user.is_authenticated:
        # Prefer the active row; fall back to the most recent if none flagged.
        gi = (
            GoogleIntegration.query
            .filter_by(user_id=current_user.id, is_active=True)
            .first()
            or GoogleIntegration.query
            .filter_by(user_id=current_user.id)
            .order_by(GoogleIntegration.id.desc())
            .first()
        )
        if gi:
            try:
                return json.loads(gi.token_data)
            except (TypeError, json.JSONDecodeError):
                db.session.delete(gi)
                db.session.commit()
                return None
    return session.get("google_token")

def get_notion_token_and_db():
    if current_user.is_authenticated:
        ni = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if ni and ni.token:
            return ni.token, ni.database_id
    return session.get("notion_token"), session.get("notion_database_id")

def get_study_profile(user_id=None, guest_id=None):
    if user_id:
        p = StudyPoints.query.filter_by(user_id=user_id).first()
        if not p:
            p = StudyPoints(user_id=user_id)
            db.session.add(p)
            db.session.commit()
    else:
        p = StudyPoints.query.filter_by(guest_session_id=guest_id).first()
        if not p:
            p = StudyPoints(guest_session_id=guest_id)
            db.session.add(p)
            db.session.commit()
    if p.spark_balance is None:
        p.spark_balance = int(p.total_points or 0)
    if p.sparks_earned_total is None or int(p.sparks_earned_total or 0) < int(p.total_points or 0):
        p.sparks_earned_total = int(p.total_points or 0)
    if not p.level:
        p.level = level_for_sparks(p.sparks_earned_total)["level"]
    if not p.freeze_capacity:
        p.freeze_capacity = current_study_tier(p.streak_count)["freeze_cap"]
    if p.badges in (None, ""):
        p.badges = "[]"
    if p.active_booster in (None, ""):
        p.active_booster = "null"
    if p.active_cosmetics in (None, ""):
        p.active_cosmetics = "{}"
    if p.weekly_quests in (None, ""):
        p.weekly_quests = "{}"
    if p.shop_purchases in (None, ""):
        p.shop_purchases = "[]"
    return p

@app.context_processor
def inject_auth():
    # Defensive: same reasoning as inject_pro. If load_user blows up
    # (e.g. mid-migration DB schema), we still need every template
    # render — including error.html — to succeed.
    try:
        return dict(logged_in=is_logged_in())
    except Exception:
        return dict(logged_in=False)

# ── SCHEDULE LOGIC ────────────────────────────────────────────
def infer_task_difficulty(points_possible, priority, due_date_str):
    score = float(points_possible or 0)
    try:
        due_date = datetime.fromisoformat(str(due_date_str)[:10])
        days_until_due = (due_date.date() - datetime.now().date()).days
    except ValueError:
        days_until_due = 7
    if priority == "High":
        score += 35
    elif priority == "Medium":
        score += 15
    if days_until_due <= 2:
        score += 20
    elif days_until_due <= 5:
        score += 10
    if score >= 110:
        return "Hard"
    if score >= 55:
        return "Medium"
    return "Easy"

def get_energy_profile(preferred_time):
    preference = (preferred_time or "evening").lower()
    profiles = {
        "morning": {"label": "morning", "summary": "Front-load harder work first.", "recommended_start_hour": 7, "hard_task_window": "7:00 AM - 11:00 AM", "light_task_window": "11:00 AM - 1:00 PM"},
        "afternoon": {"label": "afternoon", "summary": "Place demanding work first.", "recommended_start_hour": 1, "hard_task_window": "1:00 PM - 4:00 PM", "light_task_window": "4:00 PM - 6:00 PM"},
        "evening": {"label": "evening", "summary": "Begin with highest-focus work in early evening.", "recommended_start_hour": 6, "hard_task_window": "6:00 PM - 8:30 PM", "light_task_window": "8:30 PM - 10:30 PM"},
    }
    return profiles.get(preference, profiles["evening"])

def parse_time_slot_start(time_slot):
    if not time_slot or " - " not in time_slot:
        return None
    start_text = time_slot.split(" - ", 1)[0].strip()
    for fmt in ("%I:%M %p", "%I %p", "%H:%M", "%H"):
        try:
            return datetime.strptime(start_text, fmt)
        except ValueError:
            continue
    return None

def infer_block_energy_level(time_slot, preferred_time, difficulty):
    start_dt = parse_time_slot_start(time_slot)
    if start_dt is None:
        return "steady"
    hour = start_dt.hour
    preference = (preferred_time or "evening").lower()
    if preference == "morning":
        if hour < 10: return "peak"
        if hour < 13: return "steady"
        return "wind-down"
    if preference == "afternoon":
        if 13 <= hour < 16: return "peak"
        if 11 <= hour < 18: return "steady"
        return "wind-down"
    if 18 <= hour < 21: return "peak"
    if 16 <= hour < 22: return "steady"
    if difficulty == "Hard": return "steady"
    return "wind-down"

def build_daily_tip(workload_level, preferred_time, high_priority_count, hard_task_count):
    preference = (preferred_time or "evening").lower()
    if workload_level == "heavy":
        return f"Today is a heavier {preference} workload — protect your focus for the first block."
    if hard_task_count >= 2:
        return "Multiple demanding tasks today — clean starts and no distractions before each block."
    if high_priority_count >= 1:
        return "Knock out the urgent task first while your attention is strongest."
    return "Balanced day — finish each block fully and keep your momentum steady."

def enrich_schedule_data(schedule_data, assignments, preferred_time, hours_per_day):
    assignment_lookup = {item["title"]: item for item in assignments if isinstance(item, dict) and item.get("title")}
    schedule = schedule_data.get("schedule", [])
    total_study_minutes = 0
    for day in schedule:
        study_minutes = sum(block.get("duration_minutes", 0) for block in day.get("blocks", []) if not block.get("is_break"))
        break_minutes = sum(block.get("duration_minutes", 0) for block in day.get("blocks", []) if block.get("is_break"))
        total_minutes = study_minutes + break_minutes
        total_study_minutes += study_minutes
        if study_minutes >= max(int(hours_per_day * 60 * 0.85), 150):
            workload_level = "heavy"
        elif study_minutes >= max(int(hours_per_day * 60 * 0.55), 90):
            workload_level = "moderate"
        else:
            workload_level = "light"
        high_priority_count = 0
        hard_task_count = 0
        for block in day.get("blocks", []):
            if block.get("is_break"):
                block["color"] = "#cbd5e1"
                block["energy_level"] = "reset"
                block["difficulty"] = "Break"
                continue
            assignment_meta = assignment_lookup.get(block.get("assignment", ""), {})
            priority = assignment_meta.get("priority", "Medium")
            difficulty = assignment_meta.get("difficulty") or infer_task_difficulty(assignment_meta.get("points_possible"), priority, assignment_meta.get("due_date"))
            energy_level = infer_block_energy_level(block.get("time_slot"), preferred_time, difficulty)
            if priority == "High": high_priority_count += 1
            if difficulty == "Hard": hard_task_count += 1
            block["priority"] = priority
            block["difficulty"] = difficulty
            block["energy_level"] = energy_level
            block["color"] = PRIORITY_COLORS.get(priority, "#60a5fa")
            block["accent_color"] = DIFFICULTY_COLORS.get(difficulty, "#60a5fa")
        day["workload_level"] = workload_level
        day["study_minutes"] = study_minutes
        day["break_minutes"] = break_minutes
        day["total_minutes"] = total_minutes
        day["color_theme"] = WORKLOAD_COLORS[workload_level]
        day["daily_tip"] = build_daily_tip(workload_level, preferred_time, high_priority_count, hard_task_count)
        if not day.get("total_hours"):
            day["total_hours"] = round(total_minutes / 60, 1)
    schedule_data["energy_profile"] = get_energy_profile(preferred_time)
    schedule_data["total_study_time"] = f"{total_study_minutes // 60} hours {total_study_minutes % 60} minutes"
    return schedule_data

# ── PAGE ROUTES ───────────────────────────────────────────────

@app.route("/api/stats")
def public_stats():
    """Returns live site stats for the landing page. No auth required."""
    try:
        total_users = User.query.count()
        total_assignments = DismissedAssignment.query.count()  # assignments completed
        total_study_sessions = StudySession.query.filter_by(completed=True).count()
        
        # Estimate hours saved: avg 8 min saved per assignment (not having to manually sort)
        hours_saved = round((total_assignments * 8) / 60)
        
        # Pad slightly for display (early traction looks better rounded up)
        display_users = max(total_users, 50)         # show at least 50
        display_assignments = max(total_assignments, 200)
        display_hours = max(hours_saved, 120)
        
        return jsonify({
            "students": display_users,
            "assignments_tracked": display_assignments,
            "hours_saved": display_hours,
            "study_sessions": total_study_sessions,
        })
    except Exception as e:
        print(f"Stats error: {e}")
        return jsonify({
            "students": 50,
            "assignments_tracked": 200,
            "hours_saved": 120,
            "study_sessions": 0,
        })

@app.route("/")
def landing():
    return render_template("landing.html", active_page="landing")

# ── SEO / AI-crawler static files ─────────────────────────────
@app.route("/robots.txt")
def robots_txt():
    return send_from_directory(app.static_folder, "robots.txt")

@app.route("/sitemap.xml")
def sitemap_xml():
    return send_from_directory(app.static_folder, "sitemap.xml", mimetype="application/xml")

@app.route("/llms.txt")
def llms_txt():
    return send_from_directory(app.static_folder, "llms.txt", mimetype="text/plain")

@app.route("/tutor")
def tutor():
    logged_in = bool(session.get('logged_in') or (current_user.is_authenticated if hasattr(current_user, 'is_authenticated') else False))
    return render_template("tutor.html", active_page="tutor", logged_in=logged_in)

# ── Public info pages ─────────────────────────────────────────
@app.route("/faq")
def faq():
    return render_template("faq.html", active_page="faq")

@app.route("/compare")
def compare():
    return render_template("compare.html", active_page="compare")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html", active_page="pricing")

# ── Blog / guides ──────────────────────────────────────────────
@app.route("/blog/how-to-use-canvas-with-a-study-planner")
def blog_canvas():
    return render_template("blog_canvas.html", active_page="blog")

@app.route("/blog/studentvue-study-planner")
def blog_studentvue():
    return render_template("blog_studentvue.html", active_page="blog")

@app.route("/blog/how-to-prioritize-assignments")
def blog_prioritize():
    return render_template("blog_prioritize.html", active_page="blog")

@app.route("/about")
def about():
    return render_template("about.html", active_page="about")

@app.route("/api-docs")
@app.route("/developers")
def api_docs_page():
    return render_template("api_docs.html", active_page="api_docs")

@app.route("/schedule")
def home():
    if not is_logged_in():
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))

@app.route("/priority")
def priority():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("priority.html", active_page="priority")

@app.route("/classes")
def classes():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("classes.html", active_page="classes")

@app.route("/grades")
def grades():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("grades.html", active_page="grades")

@app.route("/scheduler")
def scheduler():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("scheduler.html", active_page="scheduler", load_saved=False)

@app.route("/scheduler/saved")
def scheduler_saved():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("scheduler.html", active_page="scheduler", load_saved=True)

@app.route("/grademodel")
def grademodel():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("grademodel.html", active_page="grademodel")

@app.route("/gradebook")
def gradebook():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("gradebook.html", active_page="gradebook")

@app.route("/dismissed")
def dismissed_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("dismissed.html", active_page="dismissed")

@app.route("/profiles")
def profiles():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("profiles.html", active_page="profiles")

@app.route("/settings")
def settings():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    try:
        identity = _get_or_create_identity(current_user.id)
        identity_dict = identity.to_dict()
    except Exception as e:
        print(f"Settings identity error: {e}")
        identity_dict = {
            "grade_level": "", "focus_areas": [], "goals": "",
            "completed": False, "availability": {},
            "weekly_commitments": "", "class_schedule": [],
        }
    return render_template(
        "settings.html",
        active_page="settings",
        identity=identity_dict,
        grade_choices=GRADE_LEVEL_CHOICES,
        focus_choices=FOCUS_AREA_CHOICES,
    )

@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))
    # First-run questionnaire: open the modal if the student hasn't
    # completed their profile yet. Guests skip this (no identity row).
    needs_onboarding = False
    grade_choices = []
    focus_choices = []
    identity_dict = None
    if current_user.is_authenticated:
        identity = None
        try:
            identity = _get_or_create_identity(current_user.id)
        except Exception as _e:
            print(f"[dashboard] identity load failed: {_e}")
            try: db.session.rollback()
            except Exception: pass
            try:
                _run_boot_migration_once()
                identity = _get_or_create_identity(current_user.id)
            except Exception as _e2:
                print(f"[dashboard] identity retry failed: {_e2}")
                identity = None
        if identity is not None:
            needs_onboarding = not bool(identity.completed)
            try:
                identity_dict = identity.to_dict()
            except Exception:
                identity_dict = None
        grade_choices = GRADE_LEVEL_CHOICES
        focus_choices = FOCUS_AREA_CHOICES
    return render_template(
        "dashboard.html",
        active_page="dashboard",
        needs_onboarding=needs_onboarding,
        identity=identity_dict,
        grade_choices=grade_choices,
        focus_choices=focus_choices,
        phone=(current_user.phone if current_user.is_authenticated else "") or "",
        sms_opt_in=bool(getattr(current_user, "sms_reminders_opt_in", False)) if current_user.is_authenticated else False,
    )

@app.route("/study")
def study():
    """New Deep Study Pipeline — the 3-step setup → encoding → database workflow."""
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("deep_study.html", active_page="study")


@app.route("/learn")
def learn():
    """Classic Study & Learn experience — quiz generation, flashcards, mastery, etc."""
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("study.html", active_page="learn")

@app.route("/streak")
def streak():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("streak.html", active_page="streak")

@app.route("/focus")
def focus():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("focus.html", active_page="focus")


@app.route("/library")
def library():
    """AP & Exam content library — curated outlines, FRQs, and ready-made flashcard sets."""
    return render_template("library.html", active_page="library")


# ════════════════════════════════════════════════════════════════
# DEEP STUDY PIPELINE — Step 0 (ingest) / Step 2 (voice coach feedback) /
# Step 3 (fact → active-recall card transform). All three endpoints share
# the Groq Llama backend used by the rest of the AI surface.
# ════════════════════════════════════════════════════════════════
def _deepstudy_extract_text_from_file(f):
    """Best-effort text extraction for the ingest endpoint. PDF/DOCX use
    optional packages with ASCII fallback; .txt and .md read directly."""
    name = (f.filename or "").lower()
    raw = f.read()
    if name.endswith((".txt", ".md")):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return raw.decode("latin-1", errors="replace")
    if name.endswith(".pdf"):
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages[:25])
        except Exception:
            t = raw.decode("latin-1", errors="replace")
            return re.sub(r"[^\x20-\x7E\n\t]", " ", t)
    if name.endswith(".docx"):
        try:
            import docx, io
            d = docx.Document(io.BytesIO(raw))
            return "\n".join(p.text for p in d.paragraphs)
        except Exception:
            t = raw.decode("latin-1", errors="replace")
            return re.sub(r"[^\x20-\x7E\n\t]", " ", t)
    return raw.decode("utf-8", errors="replace")


@app.route("/api/deepstudy/ingest", methods=["POST"])
def api_deepstudy_ingest():
    """Step 0: extract source text and produce a topic + factual outline.

    Accepts multipart with optional `file` and `text` fields. Runs the
    extracted material through Groq to produce a short topic label plus
    a list of atomic factual statements that drive Steps 1-3."""
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401

    text_in = (request.form.get("text") or "").strip()
    source_text = text_in
    if "file" in request.files and request.files["file"].filename:
        file_text = _deepstudy_extract_text_from_file(request.files["file"]).strip()
        if file_text:
            source_text = (source_text + "\n\n" + file_text).strip() if source_text else file_text

    if not source_text:
        return jsonify({"status": "error", "message": "Provide a file or describe your topic."}), 400

    truncated = source_text[:8000]
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        # Graceful fallback: split on sentences so the pipeline keeps working.
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", truncated) if 12 < len(s.strip()) < 280][:12]
        return jsonify({
            "status": "ok",
            "topic": (text_in or "Study Session")[:60],
            "source_text": source_text,
            "facts": sentences or [truncated[:240]],
        })

    prompt = (
        "You are a study coach. Given the material below, return ONLY valid JSON with two keys:\n"
        "  \"topic\": a short 2-6 word topic label\n"
        "  \"facts\": an array of 8-12 atomic factual statements (each ≤ 35 words) drawn from the material\n\n"
        f"MATERIAL:\n---\n{truncated}\n---\nJSON:"
    )
    try:
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1400,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content)
        topic = (parsed.get("topic") or text_in or "Study Session")[:80]
        facts = parsed.get("facts") or []
        if not isinstance(facts, list):
            facts = []
        facts = [str(f).strip() for f in facts if str(f).strip()][:14]
    except Exception as e:
        print(f"[deepstudy ingest] groq error: {e}")
        topic = (text_in or "Study Session")[:60]
        facts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", truncated) if 12 < len(s.strip()) < 280][:12]

    return jsonify({
        "status": "ok",
        "topic": topic,
        "source_text": source_text,
        "facts": facts,
    })


@app.route("/api/deepstudy/feedback", methods=["POST"])
def api_deepstudy_feedback():
    """Step 2: voice coach reply. Reads the user's utterance, the mode
    (feynman vs blurting), and the ingested source text; returns a short
    spoken-style reply that pushes them back into the technique."""
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401

    data = request.get_json() or {}
    mode = (data.get("mode") or "feynman").lower()
    topic = (data.get("topic") or "this topic").strip()
    src   = (data.get("source_text") or "")[:3500]
    user_text = (data.get("user_text") or "").strip()
    if not user_text:
        return jsonify({"reply": "Keep going — I'm listening."})

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return jsonify({"reply": "Got it — keep explaining. Try to simplify any jargon."})

    if mode == "feynman":
        system_msg = (
            f"You are Plani, a Feynman-technique voice coach. The student is studying '{topic}'. "
            "Your job is to make sure they explain concepts in PLAIN LANGUAGE, as if to a 10-year-old. "
            "If their utterance quotes textbook jargon or hides behind technical terms from the source, "
            "INTERRUPT gently and ask them to re-explain that exact phrase in everyday words. "
            "If they explain well, give one short specific encouragement and ask the next probing question. "
            "Always reply in 1-3 short sentences — this is a spoken voice call, not a lecture."
        )
    else:
        system_msg = (
            f"You are Plani, a Blurting-technique voice coach. The student is studying '{topic}' and just "
            "brain-dumped what they remember. Cross-reference against the SOURCE MATERIAL below. "
            "Reply in 2-4 short spoken-style sentences: name 1-2 specific things they nailed, then name 1-2 "
            "specific concepts FROM THE SOURCE they did not mention. Be encouraging but factual. "
            f"\n\nSOURCE MATERIAL:\n---\n{src}\n---"
        )

    try:
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_text},
            ],
            temperature=0.6,
            max_tokens=180,
        )
        reply = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[deepstudy feedback] groq error: {e}")
        reply = "Good — keep going. Try to put that in your own words."

    return jsonify({"reply": reply})


@app.route("/api/deepstudy/transform", methods=["POST"])
def api_deepstudy_transform():
    """Step 3: convert raw source facts into active-recall question cards."""
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401

    data = request.get_json() or {}
    facts = data.get("facts") or []
    topic = (data.get("topic") or "this topic").strip()
    if not facts or not isinstance(facts, list):
        return jsonify({"status": "error", "message": "No facts to transform."}), 400

    facts_clean = [str(f).strip() for f in facts if str(f).strip()][:14]
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        # Fallback: heuristic question for each fact
        cards = [{
            "question": f"In your own words, explain: {f[:120]}",
            "answer": f,
        } for f in facts_clean]
        return jsonify({"status": "ok", "cards": cards})

    numbered = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts_clean))
    prompt = (
        f"You are an active-recall coach. Topic: {topic}.\n"
        "For each numbered passive fact below, write a SINGLE active-testing question that forces a student to "
        "retrieve the underlying mechanism or definition. The question must NOT contain the answer.\n"
        "Return ONLY valid JSON with key \"cards\": an array of objects with \"question\" and \"answer\" fields, "
        "in the same order as the input.\n\n"
        f"FACTS:\n{numbered}\n\nJSON:"
    )
    try:
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content)
        cards = parsed.get("cards") or []
        if not isinstance(cards, list):
            cards = []
        cards = [{
            "question": str(c.get("question", "")).strip(),
            "answer":   str(c.get("answer",   "")).strip(),
        } for c in cards if c.get("question")][:14]
    except Exception as e:
        print(f"[deepstudy transform] groq error: {e}")
        cards = [{"question": f"Explain in your own words: {f[:120]}", "answer": f} for f in facts_clean]

    return jsonify({"status": "ok", "cards": cards})


@app.route("/api/lms/connect/<provider>", methods=["POST"])
def api_lms_connect(provider):
    """Start an OAuth flow for an LMS provider (google_classroom, brightspace, moodle).

    Returns the auth URL if the provider's client credentials are configured,
    otherwise reports the provider as not-yet-enabled so the UI can show a
    coming-soon state instead of a hard error."""
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401

    provider = (provider or "").strip().lower()
    if provider not in ("google_classroom", "brightspace", "moodle"):
        return jsonify({"status": "error", "message": f"Unknown LMS provider: {provider}"}), 400

    # Provider config — only Google Classroom has a hosted OAuth flow today.
    # The remaining providers return a `pending` status so the UI can show
    # a waitlist signup instead of a broken connect button.
    config = {
        "google_classroom": {
            "client_id_env": "GOOGLE_CLASSROOM_CLIENT_ID",
            "scope": "https://www.googleapis.com/auth/classroom.courses.readonly "
                     "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
            "auth_base": "https://accounts.google.com/o/oauth2/v2/auth",
        },
        "brightspace": {"client_id_env": "BRIGHTSPACE_CLIENT_ID", "auth_base": None},
        "moodle":      {"client_id_env": "MOODLE_CLIENT_ID",      "auth_base": None},
    }[provider]

    client_id = os.getenv(config["client_id_env"])
    if not client_id or not config.get("auth_base"):
        # Record a waitlist signup so we can notify the user once the
        # integration is live (table reuses email_subscribers from the
        # marketing waitlist if available; otherwise we just log).
        print(f"[lms waitlist] {current_user.email} → {provider}")
        return jsonify({
            "status": "pending",
            "provider": provider,
            "message": f"{provider.replace('_', ' ').title()} support is launching soon. We'll email you when it's ready."
        })

    redirect_uri = APP_BASE_URL + f"/api/lms/callback/{provider}"
    state = secrets_module.token_urlsafe(24)
    session["lms_oauth_state"] = state
    session["lms_oauth_provider"] = provider
    auth_url = (
        f"{config['auth_base']}?client_id={urllib.parse.quote(client_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code&access_type=offline&prompt=consent"
        f"&scope={urllib.parse.quote(config['scope'])}"
        f"&state={urllib.parse.quote(state)}"
    )
    return jsonify({"status": "ok", "url": auth_url})


@app.route("/api/lms/callback/<provider>")
def api_lms_callback(provider):
    """OAuth callback for LMS providers. Stores the access token on the user
    record so subsequent syncs can pull courses and assignments."""
    if not current_user.is_authenticated:
        return redirect(url_for("login"))

    state = request.args.get("state", "")
    expected = session.pop("lms_oauth_state", None)
    if not state or state != expected:
        return render_template("error.html", error_code=400, error_id="LMS-STATE-MISMATCH",
                               message="OAuth state mismatch — please retry the connection."), 400
    code = request.args.get("code", "")
    if not code:
        return redirect("/connect?lms_error=1")

    # Token exchange happens here; for now we redirect back to /connect with success
    # since the storage schema for LMS tokens (separate table per provider) will be
    # added with the full integration rollout.
    print(f"[lms callback] {current_user.email} authorized {provider} with code length {len(code)}")
    return redirect(f"/connect?lms_connected={provider}")

@app.route("/legal")
def legal():
    return render_template("legal.html", active_page="legal")

@app.route("/install")
def install():
    return render_template("install.html")

@app.route("/install/ios")
def install_ios():
    return render_template("install_ios.html")

# ── AUTH ROUTES ───────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        return redirect(url_for("login_account"), 307)
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return render_template("login.html", active_page="login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()
        if not email or not password:
            error = "Please fill in all fields."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            # Same defensive lookup as login — re-run the migration if
            # the SELECT trips on a missing column.
            try:
                _existing = User.query.filter_by(email=email).first()
            except Exception as _e:
                print(f"[register] User lookup failed: {_e}")
                try: db.session.rollback()
                except Exception: pass
                try:
                    _run_boot_migration_once()
                    _existing = User.query.filter_by(email=email).first()
                except Exception:
                    _existing = None
            if _existing:
                error = "An account with that email already exists."
        # ── COPPA: capture birth year and (if needed) parent email ──
        birth_year_raw = request.form.get("birth_year", "").strip()
        parent_email_raw = request.form.get("parent_email", "").strip().lower()
        birth_year_val = None
        age = None
        if not error and birth_year_raw:
            try:
                birth_year_val = int(birth_year_raw)
                current_year = datetime.utcnow().year
                if birth_year_val < 1900 or birth_year_val > current_year:
                    error = "Please enter a valid birth year."
                else:
                    age = current_year - birth_year_val
            except ValueError:
                error = "Please enter a valid birth year."
        elif not error:
            error = "Please tell us your birth year so we can comply with COPPA."
        if not error and age is not None and age < 13:
            if not parent_email_raw or "@" not in parent_email_raw:
                error = ("Because you're under 13, a parent or guardian's email is required. "
                         "We'll send them a one-time consent link before activating your account.")
            elif parent_email_raw == email:
                error = "Your parent or guardian's email must be different from your own."

        if not error:
            try:
                pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
                # Optional phone + SMS opt-in from the register form
                phone_raw = request.form.get("phone", "").strip()
                phone_norm = _normalise_phone(phone_raw) if phone_raw else None
                sms_optin = bool(request.form.get("sms_reminders_opt_in"))
                if not phone_norm:
                    sms_optin = False  # can't reminder without a number
                under_13 = (age is not None and age < 13)
                consent_token = secrets_module.token_urlsafe(24) if under_13 else None
                user = User(
                    email=email, password_hash=pw_hash,
                    phone=phone_norm, sms_reminders_opt_in=sms_optin,
                    birth_year=birth_year_val,
                    parent_email=parent_email_raw if under_13 else None,
                    parent_consent_granted=not under_13,  # adults skip the gate
                    parent_consent_token=consent_token,
                )
                db.session.add(user)
                db.session.commit()
                # Fire the parental consent email out-of-band.
                if under_13 and parent_email_raw:
                    try:
                        consent_url = f"{APP_BASE_URL}/parent/consent?token={consent_token}"
                        body = (
                            f"Hi,\n\nYour child ({email}) signed up for IntelliPlan, a free study "
                            f"planner. Because they're under 13, COPPA requires your consent before "
                            f"their account becomes active.\n\nApprove the account:\n{consent_url}\n\n"
                            f"If you didn't expect this email, you can safely ignore it — the account "
                            f"stays locked until you click the link.\n\n— IntelliPlan"
                        )
                        # Send via SMTP if SMTP_HOST is set; otherwise log
                        # the link so it can still be delivered manually.
                        _send_email(parent_email_raw, "Consent needed: your child's IntelliPlan account", body)
                        print(f"[coppa] consent link emailed to {parent_email_raw}: {consent_url}")
                    except Exception as _e:
                        print(f"[coppa] consent email failed: {_e}")
                # Apply any pending referral bonus (sets referred_by_id and
                # grants both accounts Pro days). Safe no-op if there's none.
                try:
                    _grant_referral_bonus(user)
                except Exception as _ref_e:
                    print(f"[referral] grant failed: {_ref_e}")
                # Under-13 accounts stay logged-out until the parent clicks
                # the consent link. Show a friendly "waiting for parent" page.
                if under_13:
                    return render_template(
                        "register.html",
                        active_page="login",
                        error=None,
                        info=(f"Account created for {email}. We've emailed a consent link to "
                              f"{parent_email_raw}. Your account will activate as soon as they approve it."),
                    )
                login_user(user, remember=True)
                # Best-effort welcome SMS so the user sees the integration
                # work the moment they opt in.
                if phone_norm and sms_optin:
                    # Best-effort welcome text via the email-to-SMS gateway.
                    try:
                        _sms_send_email_gateway(
                            phone_norm,
                            "Welcome to IntelliPlan! Open the app to customise your reminder times.",
                            carrier=(user.sms_carrier or "tmobile"),
                        )  # return value intentionally ignored here
                    except Exception: pass
                # Skip the legacy /onboarding page entirely — the dashboard's
                # built-in modal handles the questionnaire and never 500s on
                # a partial migration.
                return redirect(url_for("dashboard"))
            except Exception as _e:
                print(f"[register] user create failed: {_e}")
                try: db.session.rollback()
                except Exception: pass
                error = "Could not create that account right now — please try again."
    return render_template("register.html", active_page="login", error=error)


GRADE_LEVEL_CHOICES = [
    "6th grade", "7th grade", "8th grade",
    "9th grade", "10th grade", "11th grade", "12th grade",
    "Undergraduate", "Graduate", "Other",
]

FOCUS_AREA_CHOICES = [
    "Math", "Science", "Biology", "Chemistry", "Physics",
    "English / Literature", "History", "Foreign language",
    "Computer Science", "Economics", "Arts",
    "Test prep (SAT / ACT / AP)",
]


def _get_or_create_identity(user_id):
    identity = UserIdentity.query.filter_by(user_id=user_id).first()
    if not identity:
        identity = UserIdentity(user_id=user_id)
        db.session.add(identity)
        db.session.commit()
    return identity


@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    if not is_logged_in():
        return redirect(url_for("login"))
    try:
        identity = _get_or_create_identity(current_user.id)
    except Exception as _e:
        # The dashboard modal handles the questionnaire too — if the
        # legacy /onboarding page can't load (e.g. column missing on a
        # not-yet-fully-migrated DB), forward to dashboard and let the
        # modal handle it from there. Don't 500.
        print(f"[onboarding] identity load failed: {_e}")
        try: db.session.rollback()
        except Exception: pass
        try:
            _run_boot_migration_once()
            identity = _get_or_create_identity(current_user.id)
        except Exception as _e2:
            print(f"[onboarding] retry failed: {_e2}")
            return redirect(url_for("dashboard"))
    if request.method == "POST":
        grade = (request.form.get("grade_level") or "").strip()[:32]
        focus = request.form.getlist("focus_areas")
        focus = [f.strip()[:48] for f in focus if f.strip()][:12]
        goals = (request.form.get("goals") or "").strip()[:1000]
        identity.grade_level = grade or None
        identity.focus_areas = json.dumps(focus)
        identity.goals = goals
        identity.completed = True
        db.session.commit()
        next_url = request.args.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("connect_account"))
    return render_template(
        "onboarding.html",
        active_page="onboarding",
        identity=identity.to_dict(),
        grade_choices=GRADE_LEVEL_CHOICES,
        focus_choices=FOCUS_AREA_CHOICES,
    )


@app.route("/identity", methods=["POST"])
def update_identity():
    if not is_logged_in():
        return jsonify({"error": "auth required"}), 401
    payload = request.get_json(silent=True) or {}
    identity = _get_or_create_identity(current_user.id)
    if "grade_level" in payload:
        identity.grade_level = str(payload.get("grade_level") or "").strip()[:32] or None
    if "focus_areas" in payload:
        raw = payload.get("focus_areas") or []
        if isinstance(raw, list):
            identity.focus_areas = json.dumps([str(x).strip()[:48] for x in raw if str(x).strip()][:12])
    if "goals" in payload:
        identity.goals = str(payload.get("goals") or "").strip()[:1000]
    if "availability" in payload:
        av = payload.get("availability") or {}
        if isinstance(av, dict):
            identity.availability = json.dumps(av)
    if "weekly_commitments" in payload:
        identity.weekly_commitments = str(payload.get("weekly_commitments") or "").strip()[:500]
    if "class_schedule" in payload:
        cs = payload.get("class_schedule") or []
        if isinstance(cs, list):
            identity.class_schedule = json.dumps(cs[:50])
    if payload.get("completed"):
        identity.completed = True
    else:
        identity.completed = True
    db.session.commit()
    return jsonify({"ok": True, "identity": identity.to_dict()})


@app.route("/api/identity", methods=["GET"])
def get_identity():
    if not is_logged_in():
        return jsonify({"error": "auth required"}), 401
    identity = _get_or_create_identity(current_user.id)
    return jsonify({"identity": identity.to_dict()})

@app.route("/login/google")
def login_google():
    if not GCAL_AVAILABLE:
        return redirect(url_for("login"))
    state = secrets_module.token_urlsafe(32)
    session["oauth_state"] = state
    session["oauth_purpose"] = "login"
    session.permanent = True
    session.modified = True
    auth_url, code_verifier = get_auth_url(state, purpose="login")
    session["oauth_code_verifier"] = code_verifier
    session.modified = True
    print(f"[GOOGLE LOGIN] state={state[:8]}..., session_keys={list(session.keys())}")
    return redirect(auth_url)

@app.route("/login/account", methods=["GET", "POST"])
def login_account():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        try:
            user = User.query.filter_by(email=email).first()
        except Exception as _e:
            # If the SELECT itself blows up (e.g. a new column hasn't
            # been migrated yet on production), retry the migration and
            # try ONCE more before surfacing a friendly error.
            print(f"[login] User lookup failed: {_e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            try:
                _run_boot_migration_once()
                user = User.query.filter_by(email=email).first()
            except Exception as _e2:
                print(f"[login] retry also failed: {_e2}")
                user = None
                error = "Login is briefly unavailable. Please try again in a moment."
        if not error:
            if user and user.password_hash and bcrypt.check_password_hash(user.password_hash, password):
                # COPPA: block sign-in for under-13 accounts that haven't been approved yet.
                if hasattr(user, "parent_consent_granted") and user.parent_consent_granted is False and (user.parent_email or ""):
                    error = ("This account is waiting for parental consent. We emailed "
                             f"{user.parent_email} a consent link — once they click it, "
                             "you'll be able to sign in.")
                    return render_template("login_account.html", active_page="login", error=error)
                login_user(user, remember=True)
                # Auto-join any group whose invite link was clicked pre-login.
                joined_gid = _apply_pending_group_join()
                if joined_gid:
                    return redirect(url_for("groups_page") + f"?open={joined_gid}")
                try:
                    acct = LinkedAccount.query.filter_by(user_id=user.id, is_active=True).first()
                except Exception:
                    acct = None
                if not acct:
                    return redirect(url_for("connect_account"))
                return redirect(url_for("dashboard"))
            else:
                error = "Invalid email or password."
    return render_template("login_account.html", active_page="login", error=error)

@app.route("/connect", methods=["GET"])
def connect_account():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("connect.html", active_page="login")

@app.route("/login/canvas", methods=["GET", "POST"])
def login_canvas():
    error = None
    if request.method == "POST":
        token = request.form.get("canvas_token", "").strip()
        canvas_url = request.form.get("canvas_url", "").strip().rstrip("/")
        profile_name = request.form.get("profile_name", "").strip() or "Canvas Account"
        if canvas_url and not canvas_url.startswith(("http://", "https://")):
            canvas_url = "https://" + canvas_url
        if not token or not canvas_url:
            error = "Please fill in both fields."
        else:
            test = requests.get(f"{canvas_url}/api/v1/courses", headers={"Authorization": f"Bearer {token}"}, timeout=20)
            if test.status_code == 200:
                creds = {"canvas_token": token, "canvas_url": canvas_url}
                if current_user.is_authenticated:
                    LinkedAccount.query.filter_by(user_id=current_user.id).update({"is_active": False})
                    acct = LinkedAccount(user_id=current_user.id, name=profile_name, login_type="canvas", is_active=True)
                    acct.set_credentials(creds)
                    db.session.add(acct)
                    db.session.commit()
                else:
                    session.permanent = True
                    session["canvas_token"] = token
                    session["canvas_url"] = canvas_url
                    session["login_type"] = "canvas"
                return redirect(url_for("dashboard"))
            else:
                error = "Invalid token or Canvas URL."
    return render_template(
        "login_canvas.html",
        active_page="login",
        error=error,
        canvas_oauth_available=(CANVAS_OAUTH_AVAILABLE and canvas_oauth_configured()),
    )

# ── CANVAS OAUTH ──────────────────────────────────────────────
# Lets students connect Canvas with one click instead of finding and
# pasting a personal access token. Backed by canvas_oauth.py.
@app.route("/oauth/canvas/check")
def oauth_canvas_check():
    """Lightweight probe used by the login page to decide whether to show
    the "Continue with Canvas" button for a given canvas_base."""
    base = (request.args.get("canvas_base") or DEFAULT_CANVAS_BASE).strip()
    if not CANVAS_OAUTH_AVAILABLE:
        return flask.jsonify({"available": False, "reason": "library_missing"})
    return flask.jsonify({"available": bool(canvas_oauth_configured(base)), "canvas_base": base})


@app.route("/oauth/canvas")
def oauth_canvas_start():
    canvas_base = request.args.get("canvas_base") or DEFAULT_CANVAS_BASE
    if not CANVAS_OAUTH_AVAILABLE or not canvas_oauth_configured(canvas_base):
        return redirect(url_for("login_canvas") + "?reason=oauth_unavailable&canvas_base=" + urllib.parse.quote(canvas_base))
    profile_name = request.args.get("profile_name") or "Canvas Account"
    state = secrets_module.token_urlsafe(24)
    session["canvas_oauth_state"] = state
    session["canvas_oauth_base"] = canvas_base
    session["canvas_oauth_profile_name"] = profile_name
    session.modified = True
    redirect_uri = os.getenv("CANVAS_REDIRECT_URI") or (
        APP_BASE_URL.rstrip("/") + "/oauth/canvas/callback"
    )
    try:
        url = get_canvas_auth_url(state, canvas_base=canvas_base, redirect_uri=redirect_uri)
    except Exception as e:
        return render_template("error.html", active_page="error", error_code=500,
                               error_id=f"CANVAS-OAUTH-{make_error_id()}",
                               message=str(e)), 500
    return redirect(url)


@app.route("/oauth/canvas/callback")
def oauth_canvas_callback():
    if not CANVAS_OAUTH_AVAILABLE:
        return redirect(url_for("login_canvas"))
    code = request.args.get("code")
    state = request.args.get("state")
    err = request.args.get("error")
    err_desc = request.args.get("error_description") or ""
    if err:
        # Canvas returns ?error=invalid_client when the institution hasn't
        # registered IntelliPlan as a Developer Key. Redirect to the token
        # paste page with a clear explanation instead of a generic error.
        if "invalid_client" in err or "unauthorized" in err:
            return redirect(
                url_for("login_canvas")
                + "?reason=oauth_not_registered"
                + ("&desc=" + urllib.parse.quote(err_desc) if err_desc else "")
            )
        return redirect(url_for("login_canvas") + f"?error={err}")
    expected_state = session.pop("canvas_oauth_state", None)
    canvas_base = session.pop("canvas_oauth_base", None) or DEFAULT_CANVAS_BASE
    profile_name = session.pop("canvas_oauth_profile_name", None) or "Canvas Account"
    if not code or not state or state != expected_state:
        return render_template("error.html", active_page="error", error_code=400,
                               error_id="CANVAS-OAUTH-STATE",
                               message="Canvas OAuth state did not match. Try again."), 400
    redirect_uri = os.getenv("CANVAS_REDIRECT_URI") or (
        APP_BASE_URL.rstrip("/") + "/oauth/canvas/callback"
    )
    try:
        tokens = exchange_canvas_code(code, canvas_base, redirect_uri=redirect_uri)
    except Exception as e:
        msg = str(e)
        if "invalid_client" in msg or "unauthorized_client" in msg or "redirect_uri" in msg:
            return redirect(url_for("login_canvas") + "?reason=oauth_not_registered")
        return render_template("error.html", active_page="error", error_code=500,
                               error_id=f"CANVAS-OAUTH-{make_error_id()}",
                               message=msg), 500

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in")
    expires_at = (datetime.utcnow() + timedelta(seconds=int(expires_in))) if expires_in else None
    user_info = tokens.get("user") or {}
    canvas_user_id = str(user_info.get("id") or "") or None
    canvas_user_name = user_info.get("name") or profile_name

    creds = {
        "canvas_token": access_token,
        "canvas_url": tokens.get("canvas_base") or canvas_base,
        "canvas_refresh_token": refresh_token,
        "canvas_token_expires_at": expires_at.isoformat() if expires_at else None,
        "canvas_oauth": True,
    }

    if current_user.is_authenticated:
        # Persist long-lived OAuth tokens in their own table for refresh,
        # AND mirror short-lived access_token onto LinkedAccount so the
        # existing /live, /grades, /classes paths keep working unchanged.
        existing_oauth = CanvasIntegration.query.filter_by(user_id=current_user.id).first()
        if existing_oauth:
            existing_oauth.canvas_base = tokens.get("canvas_base") or canvas_base
            existing_oauth.access_token = access_token
            existing_oauth.refresh_token = refresh_token or existing_oauth.refresh_token
            existing_oauth.token_expires_at = expires_at
            existing_oauth.canvas_user_id = canvas_user_id or existing_oauth.canvas_user_id
            existing_oauth.canvas_user_name = canvas_user_name or existing_oauth.canvas_user_name
            existing_oauth.connected_at = datetime.utcnow()
        else:
            db.session.add(CanvasIntegration(
                user_id=current_user.id,
                canvas_base=tokens.get("canvas_base") or canvas_base,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=expires_at,
                canvas_user_id=canvas_user_id,
                canvas_user_name=canvas_user_name,
            ))
        LinkedAccount.query.filter_by(user_id=current_user.id).update({"is_active": False})
        acct = LinkedAccount(
            user_id=current_user.id,
            name=canvas_user_name or profile_name,
            login_type="canvas",
            is_active=True,
        )
        acct.set_credentials(creds)
        db.session.add(acct)
        db.session.commit()
    else:
        session.permanent = True
        session["canvas_token"] = access_token
        session["canvas_url"] = tokens.get("canvas_base") or canvas_base
        session["canvas_refresh_token"] = refresh_token
        session["canvas_oauth"] = True
        session["login_type"] = "canvas"
    return redirect(url_for("dashboard"))


@app.route("/oauth/canvas/disconnect", methods=["POST"])
def oauth_canvas_disconnect():
    if current_user.is_authenticated:
        ci = CanvasIntegration.query.filter_by(user_id=current_user.id).first()
        if ci and ci.access_token:
            try:
                revoke_canvas_token(ci.access_token, ci.canvas_base)
            except Exception:
                pass
            db.session.delete(ci)
            db.session.commit()
    session.pop("canvas_token", None)
    session.pop("canvas_url", None)
    session.pop("canvas_refresh_token", None)
    session.pop("canvas_oauth", None)
    return flask.jsonify({"status": "ok"})


@app.route("/login/studentvue", methods=["GET", "POST"])
def login_studentvue():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        district_url = normalize_district_url(request.form.get("district_url", ""))
        profile_name = request.form.get("profile_name", "").strip() or "StudentVue Account"
        if not username or not password or not district_url:
            error = "Please fill in all fields."
        else:
            if test_login(district_url, username, password):
                creds = {"sv_username": username, "sv_password": password, "sv_district_url": district_url}
                if current_user.is_authenticated:
                    LinkedAccount.query.filter_by(user_id=current_user.id).update({"is_active": False})
                    acct = LinkedAccount(user_id=current_user.id, name=profile_name, login_type="studentvue", is_active=True)
                    acct.set_credentials(creds)
                    db.session.add(acct)
                    db.session.commit()
                else:
                    session.permanent = True
                    session["sv_username"] = username
                    session["sv_password"] = password
                    session["sv_district_url"] = district_url
                    session["login_type"] = "studentvue"
                return redirect(url_for("dashboard"))
            else:
                error = "Invalid StudentVUE credentials or district URL. Try your district's StudentVUE web address, like https://district-psv.edupoint.com."
    return render_template("login_studentvue.html", active_page="login", error=error)


@app.route("/oauth/studentvue")
def oauth_studentvue_start():
    return redirect(url_for("login_studentvue") + "?oauth=studentvue")

@app.route("/login/schoology", methods=["GET", "POST"])
def login_schoology():
    error = None
    if request.method == "POST":
        key = request.form.get("api_key", "").strip()
        secret = request.form.get("api_secret", "").strip()
        profile_name = request.form.get("profile_name", "").strip() or "Schoology Account"
        if not key or not secret:
            error = "Please fill in both fields."
        else:
            try:
                from schoology_helper import test_schoology_login
                if test_schoology_login(key, secret):
                    creds = {"schoology_key": key, "schoology_secret": secret}
                    if current_user.is_authenticated:
                        LinkedAccount.query.filter_by(user_id=current_user.id).update({"is_active": False})
                        acct = LinkedAccount(user_id=current_user.id, name=profile_name, login_type="schoology", is_active=True)
                        acct.set_credentials(creds)
                        db.session.add(acct)
                        db.session.commit()
                    else:
                        session["schoology_key"] = key
                        session["schoology_secret"] = secret
                        session["login_type"] = "schoology"
                    return redirect(url_for("dashboard"))
                else:
                    error = "Invalid Schoology credentials."
            except Exception as e:
                error = f"Schoology error: {str(e)}"
    return render_template("login_schoology.html", active_page="login", error=error)

@app.route("/logout", methods=["POST", "GET"])
def logout():
    logout_user()
    session.clear()
    response = redirect(url_for("login"))
    response.delete_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
    response.delete_cookie("remember_token")
    return response

# ── GOOGLE OAUTH ──────────────────────────────────────────────
# ── FIX: Single consolidated OAuth callback that handles both
#         "login" (create/find account + log in) and "calendar"
#         (link calendar to existing logged-in user).
#
#   Both /oauth/google/callback and /oauth2callback point here so
#   either redirect URI registered in Google Cloud Console works.
# ─────────────────────────────────────────────────────────────

def _handle_google_callback():
    import traceback

    print(f"[GOOGLE CALLBACK] args={dict(request.args)}")
    print(f"[GOOGLE CALLBACK] session_keys={list(session.keys())}")

    # Google returned an error (user denied, etc.)
    error_msg = request.args.get("error")
    if error_msg:
        print(f"[GOOGLE CALLBACK] Google error param: {error_msg}")
        return redirect(url_for("login"))

    returned_state = request.args.get("state")
    stored_state = session.pop("oauth_state", None)
    purpose = session.pop("oauth_purpose", "calendar")

    # ── State check — primary guard against CSRF and session loss ──
    if not stored_state:
        # This is the exact cause of IPE-XXXXXXXX errors:
        # session was empty because a different container handled the callback.
        # Switching to sqlalchemy sessions (above) prevents this permanently.
        print("[GOOGLE CALLBACK] FATAL: oauth_state missing from session (session was lost)")
        return redirect(url_for("login"))

    if returned_state != stored_state:
        print(f"[GOOGLE CALLBACK] FATAL: state mismatch returned={returned_state!r} stored={stored_state!r}")
        return redirect(url_for("login"))

    code = request.args.get("code")
    if not code:
        print("[GOOGLE CALLBACK] FATAL: no code param")
        return redirect(url_for("login"))

    # ── Exchange code for tokens ──
    try:
        code_verifier = session.pop("oauth_code_verifier", None)
        token_dict = exchange_code_for_token(code, code_verifier=code_verifier)
        print(f"[GOOGLE CALLBACK] token_dict keys={list(token_dict.keys())}")
    except Exception:
        print(f"[GOOGLE CALLBACK] token exchange failed:\n{traceback.format_exc()}")
        return redirect(url_for("login"))

    # exchange_code_for_token returns {"token": ..., "refresh_token": ...}
    # guard against either key name
    access_token = token_dict.get("access_token") or token_dict.get("token")
    if not access_token:
        print(f"[GOOGLE CALLBACK] FATAL: no access_token in token_dict keys={list(token_dict.keys())}")
        return redirect(url_for("login"))

    # ── Fetch Google user profile ──
    try:
        ui_resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        ui_resp.raise_for_status()
        userinfo = ui_resp.json()
    except Exception:
        print(f"[GOOGLE CALLBACK] userinfo fetch failed:\n{traceback.format_exc()}")
        return redirect(url_for("login"))

    google_id = userinfo.get("sub")
    email = (userinfo.get("email") or "").lower().strip()
    name = userinfo.get("name") or email.split("@")[0]
    print(f"[GOOGLE CALLBACK] google_id={google_id} email={email} purpose={purpose}")

    if not google_id or not email:
        print("[GOOGLE CALLBACK] FATAL: missing sub or email in userinfo")
        return redirect(url_for("login"))

    if purpose == "calendar":
        if not current_user.is_authenticated:
            print("[GOOGLE CALLBACK] calendar link attempted without an authenticated app user")
            return redirect(url_for("login"))
        user = current_user
        if user.email.lower() == email and not user.google_id:
            user.google_id = google_id
        if not user.name:
            user.name = name
        db.session.commit()
        if has_calendar_scope(token_dict):
            # Multi-account: match the row by account_email so a second
            # Google sign-in adds a new row instead of overwriting the first.
            gi = (
                GoogleIntegration.query
                .filter_by(user_id=user.id, account_email=email)
                .first()
            )
            existing_token = json.loads(gi.token_data) if gi else {}
            token_dict = merge_token_data(existing_token, token_dict)
            # Newly-linked account becomes the active one.
            GoogleIntegration.query.filter_by(user_id=user.id).update({"is_active": False})
            if gi:
                gi.token_data = json.dumps(token_dict)
                gi.account_name = name or gi.account_name
                gi.is_active = True
            else:
                db.session.add(GoogleIntegration(
                    user_id=user.id,
                    token_data=json.dumps(token_dict),
                    account_email=email,
                    account_name=name,
                    is_active=True,
                ))
            db.session.commit()
            session["google_token"] = token_dict
            session.permanent = True
            session.modified = True
        return redirect(url_for("settings") if session.pop("oauth_return_to_settings", False) else url_for("dashboard"))

    # ── Find or create User (defensive — see login_account for rationale) ──
    try:
        user = User.query.filter_by(google_id=google_id).first()
    except Exception as _e:
        print(f"[GOOGLE CALLBACK] User.query failed, retrying after migration: {_e}")
        try: db.session.rollback()
        except Exception: pass
        try:
            _run_boot_migration_once()
            user = User.query.filter_by(google_id=google_id).first()
        except Exception as _e2:
            print(f"[GOOGLE CALLBACK] User.query still failing after migration: {_e2}")
            return redirect(url_for("login") + "?err=oauth_db")
    if not user:
        try:
            user = User.query.filter_by(email=email).first()
        except Exception:
            user = None
        if user:
            # Existing email-based account — link the Google ID
            user.google_id = google_id
            if not user.name:
                user.name = name
            print(f"[GOOGLE CALLBACK] linked google_id to existing user id={user.id}")
        else:
            # Brand-new user — create the account
            user = User(
                email=email,
                google_id=google_id,
                name=name,
                password_hash="",
            )
            db.session.add(user)
            print(f"[GOOGLE CALLBACK] created new user email={email}")
    else:
        print(f"[GOOGLE CALLBACK] found existing google user id={user.id}")

    db.session.commit()

    # ── Persist the Google token for calendar use ──
    if has_calendar_scope(token_dict):
        gi = (
            GoogleIntegration.query
            .filter_by(user_id=user.id, account_email=email)
            .first()
        )
        existing_token = json.loads(gi.token_data) if gi else {}
        token_dict = merge_token_data(existing_token, token_dict)
        GoogleIntegration.query.filter_by(user_id=user.id).update({"is_active": False})
        if gi:
            gi.token_data = json.dumps(token_dict)
            gi.account_name = name or gi.account_name
            gi.is_active = True
        else:
            db.session.add(GoogleIntegration(
                user_id=user.id,
                token_data=json.dumps(token_dict),
                account_email=email,
                account_name=name,
                is_active=True,
            ))
        db.session.commit()
        session["google_token"] = token_dict
        session.permanent = True
        session.modified = True

    # ── Log the user in ──
    login_user(user, remember=True)
    print(f"[GOOGLE CALLBACK] logged in user id={user.id}")

    # ── Redirect ──
    if purpose == "login":
        return redirect(url_for("dashboard"))
    return redirect(url_for("dashboard"))


@app.route("/oauth/google")
def google_oauth_start():
    """Start the OAuth flow from the calendar-linking UI.
    Accepts ?add=1 to force the Google account chooser (so the user can
    add a second account) and ?return=settings to come back to /settings."""
    if not is_logged_in():
        return redirect(url_for("login"))
    if not GCAL_AVAILABLE:
        return "Google Calendar not configured", 500
    state = secrets_module.token_urlsafe(32)
    session["oauth_state"] = state
    session["oauth_purpose"] = "calendar"
    if request.args.get("return") == "settings":
        session["oauth_return_to_settings"] = True
    session.permanent = True
    session.modified = True
    auth_url, code_verifier = get_auth_url(state, purpose="calendar")
    # When the user clicks "Add another account", force Google to show
    # the account picker even if they're already signed in.
    if request.args.get("add") == "1":
        sep = "&" if "?" in auth_url else "?"
        auth_url = f"{auth_url}{sep}prompt=select_account"
    session["oauth_code_verifier"] = code_verifier
    session.modified = True
    return redirect(auth_url)


@app.route("/gcal/status")
def gcal_status():
    """List the user's connected Google accounts and which one is active."""
    if not current_user.is_authenticated:
        return flask.jsonify({"connected": False, "accounts": [], "active_id": None})
    rows = (
        GoogleIntegration.query
        .filter_by(user_id=current_user.id)
        .order_by(GoogleIntegration.id.asc())
        .all()
    )
    accounts = [{
        "id": r.id,
        "email": r.account_email or "",
        "name": r.account_name or "",
        "is_active": bool(r.is_active),
    } for r in rows]
    active = next((a for a in accounts if a["is_active"]), accounts[0] if accounts else None)
    return flask.jsonify({
        "connected": bool(accounts),
        "accounts": accounts,
        "active_id": active["id"] if active else None,
        "active_email": active["email"] if active else "",
    })


@app.route("/gcal/activate", methods=["POST"])
def gcal_activate():
    """Mark a specific GoogleIntegration row as the active account."""
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"}), 401
    body = request.get_json(silent=True) or {}
    row_id = body.get("id")
    if not row_id:
        return flask.jsonify({"status": "error", "message": "id required"}), 400
    target = GoogleIntegration.query.filter_by(user_id=current_user.id, id=row_id).first()
    if not target:
        return flask.jsonify({"status": "error", "message": "not found"}), 404
    GoogleIntegration.query.filter_by(user_id=current_user.id).update({"is_active": False})
    target.is_active = True
    db.session.commit()
    try:
        session["google_token"] = json.loads(target.token_data)
        session.modified = True
    except Exception:
        pass
    return flask.jsonify({"status": "ok"})


@app.route("/gcal/remove", methods=["POST"])
def gcal_remove():
    """Disconnect a single Google account by row id (multi-account aware)."""
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"}), 401
    body = request.get_json(silent=True) or {}
    row_id = body.get("id")
    if not row_id:
        return flask.jsonify({"status": "error", "message": "id required"}), 400
    target = GoogleIntegration.query.filter_by(user_id=current_user.id, id=row_id).first()
    if not target:
        return flask.jsonify({"status": "error", "message": "not found"}), 404
    was_active = bool(target.is_active)
    db.session.delete(target)
    db.session.commit()
    if was_active:
        # Promote the next-most-recent account to active.
        nxt = GoogleIntegration.query.filter_by(user_id=current_user.id).order_by(GoogleIntegration.id.desc()).first()
        if nxt:
            nxt.is_active = True
            db.session.commit()
            try: session["google_token"] = json.loads(nxt.token_data)
            except Exception: pass
        else:
            session.pop("google_token", None)
        session.modified = True
    return flask.jsonify({"status": "ok"})


# ── FIX: Both route paths registered so either redirect URI works.
#   Set GOOGLE_REDIRECT_URI=https://intelliplan.tech/oauth2callback
#   in Railway and add that URI in Google Cloud Console.
#   Keep /oauth/google/callback as a fallback alias.
@app.route("/oauth2callback")
@app.route("/oauth/google/callback")
def google_oauth_callback():
    return _handle_google_callback()


@app.route("/oauth/google/disconnect", methods=["POST"])
def google_disconnect():
    if current_user.is_authenticated:
        GoogleIntegration.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
    else:
        session.pop("google_token", None)
    return flask.jsonify({"status": "ok"})

@app.route("/calendar/events")
def calendar_events():
    if not GCAL_AVAILABLE:
        return flask.jsonify({"connected": False, "events": []})
    token = get_google_token()
    if not token:
        return flask.jsonify({"connected": False, "events": []})
    try:
        events = get_upcoming_events(token)
        session["google_token"] = token
        session.modified = True
        return flask.jsonify({"connected": True, "events": events})
    except Exception as e:
        print(f"Calendar events error: {e}")
        session.pop("google_token", None)
        if current_user.is_authenticated:
            GoogleIntegration.query.filter_by(user_id=current_user.id).delete()
            db.session.commit()
        return flask.jsonify({"connected": False, "error": str(e), "events": []})

@app.route("/calendar/free-slot")
def calendar_free_slot():
    if not GCAL_AVAILABLE:
        return flask.jsonify({"slot": "7:00 PM", "connected": False})
    token = get_google_token()
    date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    if not token:
        return flask.jsonify({"slot": "7:00 PM", "connected": False})
    try:
        slot = find_free_slots(token, date_str)
        free_hours = compute_free_hours(token, date_str)
        return flask.jsonify({"slot": slot, "connected": True, "free_hours": free_hours})
    except Exception as e:
        return flask.jsonify({"slot": "7:00 PM", "connected": False, "error": str(e)})

@app.route("/calendar/export", methods=["POST"])
def calendar_export():
    if not GCAL_AVAILABLE:
        return flask.jsonify({"status": "error", "message": "Google Calendar not configured"})
    token = get_google_token()
    if not token:
        return flask.jsonify({"status": "error", "message": "Google Calendar not connected"})
    data = request.get_json(silent=True) or {}
    schedule_data = data.get("schedule_data")
    if not schedule_data:
        return flask.jsonify({"status": "error", "message": "No schedule data supplied"}), 400
    skip_overlaps = data.get("skip_overlaps", False)
    try:
        existing_events = []
        if skip_overlaps:
            try:
                existing_events = get_upcoming_events(token)
            except Exception:
                existing_events = []
        ids, new_token, skipped = add_schedule_to_calendar(token, schedule_data, existing_events if skip_overlaps else [])
        if new_token:
            session["google_token"] = {**token, "token": new_token}
            session.modified = True
            if current_user.is_authenticated:
                gi = GoogleIntegration.query.filter_by(user_id=current_user.id).first()
                if gi:
                    td = json.loads(gi.token_data)
                    td["token"] = new_token
                    gi.token_data = json.dumps(td)
                    db.session.commit()
        return flask.jsonify({"status": "ok", "created": len(ids), "skipped": skipped})
    except Exception as e:
        print(f"Calendar export error: {e}")
        return flask.jsonify({"status": "error", "message": "Google Calendar export failed. Please try again."})

# ── PROFILE MANAGEMENT ────────────────────────────────────────
@app.route("/profiles/list")
def profiles_list():
    if not current_user.is_authenticated:
        login_type = session.get("login_type")
        if login_type:
            return flask.jsonify({"is_guest": True, "profiles": [{"id": "guest", "name": "Guest Session", "login_type": login_type, "is_active": True}], "active": "guest"})
        return flask.jsonify({"is_guest": True, "profiles": [], "active": None})
    accounts = LinkedAccount.query.filter_by(user_id=current_user.id).all()
    active = next((a for a in accounts if a.is_active), None)
    return flask.jsonify({
        "is_guest": False,
        "email": current_user.email,
        "profiles": [{"id": a.profile_id, "name": a.name, "login_type": a.login_type, "is_active": a.is_active} for a in accounts],
        "active": active.profile_id if active else None
    })

@app.route("/profiles/switch", methods=["POST"])
def profiles_switch():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    profile_id = (request.json or {}).get("id")
    acct = LinkedAccount.query.filter_by(user_id=current_user.id, profile_id=profile_id).first()
    if not acct:
        return flask.jsonify({"status": "error"})
    LinkedAccount.query.filter_by(user_id=current_user.id).update({"is_active": False})
    acct.is_active = True
    db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/profiles/delete", methods=["POST"])
def profiles_delete():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    profile_id = (request.json or {}).get("id")
    acct = LinkedAccount.query.filter_by(user_id=current_user.id, profile_id=profile_id).first()
    if acct:
        db.session.delete(acct)
        db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/profiles/rename", methods=["POST"])
def profiles_rename():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    profile_id = (request.json or {}).get("id")
    name = (request.json or {}).get("name", "").strip()
    acct = LinkedAccount.query.filter_by(user_id=current_user.id, profile_id=profile_id).first()
    if acct and name:
        acct.name = name
        db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/account/delete", methods=["POST"])
def account_delete():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    user = current_user
    logout_user()
    db.session.delete(user)
    db.session.commit()
    session.clear()
    return flask.jsonify({"status": "ok"})

# ── DATA ROUTES ───────────────────────────────────────────────
@app.route("/live")
@limiter.limit("30 per minute")
def get_live_schedule():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    dismissed = get_dismissed_titles()
    test_titles = get_test_titles()
    excluded = dismissed | test_titles
    login_type = acct["login_type"]
    if login_type == "studentvue":
        try:
            print(f"Fetching StudentVue data for {acct['sv_username']}...")
            result = get_sv_assignments(acct["sv_district_url"], acct["sv_username"], acct["sv_password"])
            if not isinstance(result, list):
                print("StudentVue returned non-list data")
                return flask.jsonify([])
            filtered = [a for a in result if isinstance(a, dict) and a.get("title") not in excluded]
            return flask.jsonify(filtered)
        except Exception as e:
            print(f"StudentVue Live Error: {str(e)}")
            return flask.jsonify([]), 500
    if login_type == "schoology":
        try:
            from schoology_helper import get_schoology_assignments
            result = get_schoology_assignments(acct["schoology_key"], acct["schoology_secret"])
            return flask.jsonify([a for a in result if a.get("title", "") not in excluded])
        except Exception as e:
            print(f"Schoology Error: {e}")
            return flask.jsonify([])
    try:
        token = acct["canvas_token"]
        canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
        base = f"{canvas_url}/api/v1"
        headers = {"Authorization": f"Bearer {token}"}
        course_response = requests.get(f"{base}/courses", headers=headers, timeout=20)
        courses = course_response.json()
        course_map = {}
        for c in courses:
            if isinstance(c, dict) and "id" in c:
                course_map[c["id"]] = c.get("name", "Unknown")
        assignments = []
        for course_id in course_map:
            response = requests.get(f"{base}/courses/{course_id}/assignments", headers=headers, timeout=20)
            data = response.json()
            if isinstance(data, list):
                assignments += data
        schedule = []
        today = datetime.now(timezone.utc)
        for a in assignments:
            if not isinstance(a, dict): continue
            if a.get("due_at") is None: continue
            if a.get("points_possible") is None:
                a["points_possible"] = 60
            due_str = a["due_at"]
            due_date = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
            days = (due_date - today).days
            if days < -14: continue
            priority = compute_priority(days, a["points_possible"], a.get("name", ""))
            raw_minutes = a["points_possible"] * 1.5
            rounded_minutes = max(30, round(raw_minutes / 30) * 30)
            difficulty = infer_task_difficulty(a["points_possible"], priority, due_str[:10])
            title = a["name"]
            if title in excluded: continue
            schedule.append({
                "id": str(a["id"]),
                "course_id": str(a["course_id"]),
                "title": title,
                "course": course_map.get(a["course_id"], "Unknown Course"),
                "due_date": due_str[:10],
                "points_possible": a["points_possible"],
                "priority": priority,
                "difficulty": difficulty,
                "estimated_time": rounded_minutes,
                "color": PRIORITY_COLORS.get(priority, "#60a5fa"),
            })
        return flask.jsonify(sorted(schedule, key=lambda x: x["due_date"]))
    except Exception as e:
        print(f"Canvas live error: {e}")
        return flask.jsonify([])

@app.route("/courses")
def get_courses():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    login_type = acct["login_type"]
    if login_type == "studentvue":
        from studentvue_helper import get_courses as get_sv_courses
        return flask.jsonify(get_sv_courses(acct["sv_district_url"], acct["sv_username"], acct["sv_password"]))
    if login_type == "schoology":
        try:
            from schoology_helper import get_schoology_courses
            return flask.jsonify(get_schoology_courses(acct["schoology_key"], acct["schoology_secret"]))
        except Exception:
            return flask.jsonify([])
    token = acct["canvas_token"]
    canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
    headers = {"Authorization": f"Bearer {token}"}
    course_response = requests.get(f"{canvas_url}/api/v1/courses", headers=headers, timeout=20)
    courses = course_response.json()
    return flask.jsonify([{"name": c.get("name", "Unknown")} for c in courses if isinstance(c, dict) and "id" in c])

@app.route("/grades/data")
def grades_data():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    login_type = acct["login_type"]
    if login_type == "studentvue":
        from studentvue_helper import get_grades as get_sv_grades
        return flask.jsonify(get_sv_grades(acct["sv_district_url"], acct["sv_username"], acct["sv_password"]))
    if login_type == "schoology":
        try:
            from schoology_helper import get_schoology_grades
            return flask.jsonify(get_schoology_grades(acct["schoology_key"], acct["schoology_secret"]))
        except Exception:
            return flask.jsonify([])
    if login_type == "canvas":
        try:
            from canvas_helper import get_grades as get_canvas_grades
            return flask.jsonify(get_canvas_grades(
                acct.get("canvas_url", "https://canvas.instructure.com"),
                acct["canvas_token"]
            ))
        except Exception as e:
            print(f"Canvas grades error: {e}")
            return flask.jsonify([])
    return flask.jsonify([])

@app.route("/gradebook/detail")
def gradebook_detail():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    if acct["login_type"] == "studentvue":
        from studentvue_helper import get_gradebook_detail
        return flask.jsonify(get_gradebook_detail(acct["sv_district_url"], acct["sv_username"], acct["sv_password"]))
    if acct["login_type"] == "canvas":
        try:
            from canvas_helper import get_gradebook_detail as get_canvas_gradebook
            return flask.jsonify(get_canvas_gradebook(
                acct.get("canvas_url", "https://canvas.instructure.com"),
                acct["canvas_token"]
            ))
        except Exception as e:
            print(f"Canvas gradebook error: {e}")
            return flask.jsonify([])
    return flask.jsonify([])

@app.route("/dismissed/data")
def dismissed_data():
    rows = get_dismissed_rows()
    result = []
    for r in rows:
        try:
            result.append(json.loads(r.data))
        except Exception:
            result.append({"title": r.title})
    return flask.jsonify(result)

@app.route("/notes/list")
def notes_list():
    course_name = request.args.get("course_name", "").strip()
    course_id = request.args.get("course_id", "").strip()
    course_source = request.args.get("course_source", "").strip()
    q = get_notes_owner_query()
    if course_name:
        q = q.filter(db.func.lower(CourseNote.course_name) == course_name.lower())
    if course_id:
        q = q.filter(CourseNote.course_id == course_id)
    if course_source:
        q = q.filter(CourseNote.course_source == course_source)
    notes = q.order_by(CourseNote.note_date.desc(), CourseNote.created_at.desc()).all()
    return flask.jsonify({"status": "ok", "notes": [course_note_payload(n) for n in notes]})

@app.route("/notes/upload", methods=["POST"])
def upload_note():
    course_name = request.form.get("course_name", "").strip()
    course_id = request.form.get("course_id", "").strip()
    course_source = request.form.get("course_source", "").strip()
    note_date = request.form.get("note_date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    title = request.form.get("title", "").strip() or f"{course_name} Notes"
    if not course_name:
        return flask.jsonify({"status": "error", "message": "Course name is required"}), 400
    file = request.files.get("file")
    text_content = request.form.get("text_content", "").strip()
    original_filename = None
    stored_filename = None
    if file and file.filename:
        original_filename = file.filename
        ext = os.path.splitext(original_filename)[1].lower()
        if ext not in NOTE_ALLOWED_EXTENSIONS:
            return flask.jsonify({"status": "error", "message": "Only TXT, MD, CSV, PDF, and DOCX files are supported."}), 400
        owner_folder = get_notes_owner_folder()
        owner_dir = os.path.join(app.config["NOTES_UPLOAD_FOLDER"], owner_folder)
        os.makedirs(owner_dir, exist_ok=True)
        stored_filename = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(owner_dir, stored_filename)
        file.save(file_path)
        extracted = extract_text_from_note_file(file_path)
        if extracted:
            text_content = extracted
    if not text_content and not stored_filename:
        return flask.jsonify({"status": "error", "message": "Upload a note file or paste note text."}), 400
    note = CourseNote(
        user_id=current_user.id if current_user.is_authenticated else None,
        guest_session_id=None if current_user.is_authenticated else get_guest_session_id(),
        course_name=course_name,
        course_id=course_id or None,
        course_source=course_source or None,
        note_date=note_date,
        title=title,
        original_filename=original_filename,
        stored_filename=stored_filename,
        text_content=text_content or "",
    )
    db.session.add(note)
    db.session.commit()
    return flask.jsonify({"status": "ok", "note": course_note_payload(note)})

@app.route("/notes/<int:note_id>")
def get_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404
    return flask.jsonify({"status": "ok", "note": course_note_payload(note, include_text=True)})

@app.route("/notes/<int:note_id>/download")
def download_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note.stored_filename or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "File not found"}), 404
    owner_dir = os.path.join(app.config["NOTES_UPLOAD_FOLDER"], f"user_{note.user_id}" if note.user_id else f"guest_{note.guest_session_id}")
    return flask.send_from_directory(owner_dir, note.stored_filename, as_attachment=True, download_name=note.original_filename or note.stored_filename)

@app.route("/notes/<int:note_id>/summarize", methods=["POST"])
def summarize_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404
    if not (note.text_content or "").strip():
        return flask.jsonify({"status": "error", "message": "No extracted text is available for this note."}), 400
    if not os.getenv("GROQ_API_KEY"):
        return flask.jsonify({"status": "error", "message": "GROQ_API_KEY is not set."}), 500
    client = _groq_client()
    text = (note.text_content or "")[:12000]
    prompt = f"""Summarize these class notes for a student.\n\nReturn:\n- 5 to 8 bullet points\n- a short "Key takeaways" section\n- keep it clear, practical, and concise\n\nNotes:\n{text}"""
    try:
        response = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=900)
        summary = response.choices[0].message.content.strip()
        note.summary_cache = summary
        db.session.commit()
        return flask.jsonify({"status": "ok", "summary": summary})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": "AI summarization is temporarily unavailable. Please try again."}), 500

@app.route("/notes/<int:note_id>/study", methods=["POST"])
def study_note_route(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404
    if not (note.text_content or "").strip():
        return flask.jsonify({"status": "error", "message": "No extracted text is available for this note."}), 400
    client = _groq_client()
    text = (note.text_content or "")[:12000]
    prompt = f"""Turn these notes into study material.\n\nReturn ONLY valid JSON:\n{{\n  "title": "Study Guide",\n  "summary": "short summary",\n  "cards": [{{"question": "Q1", "answer": "A1"}}],\n  "quiz": [{{"question": "Q1", "answer": "A1"}}]\n}}\n\nNotes:\n{text}"""
    try:
        response = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=1200)
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```", "", raw).strip()
        try:
            study_data = json.loads(raw)
        except Exception:
            study_data = {"title": "Study Guide", "summary": raw, "cards": [], "quiz": []}
        return flask.jsonify({"status": "ok", "study": study_data})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": "Study generation is temporarily unavailable."}), 500

@app.route("/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404
    if note.stored_filename:
        owner_dir = os.path.join(app.config["NOTES_UPLOAD_FOLDER"], f"user_{note.user_id}" if note.user_id else f"guest_{note.guest_session_id}")
        file_path = os.path.join(owner_dir, note.stored_filename)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Could not remove note file: {e}")
    db.session.delete(note)
    db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/notes/<int:note_id>/quiz", methods=["POST"])
def notes_quiz(note_id):
    note = None
    if current_user.is_authenticated:
        note = CourseNote.query.filter_by(id=note_id, user_id=current_user.id).first()
    else:
        note = CourseNote.query.filter_by(id=note_id, guest_session_id=get_guest_session_id()).first()
    if not note:
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404
    note_text = (note.text_content or "").strip()
    if not note_text:
        return flask.jsonify({"status": "error", "message": "No note text available"}), 400
    history = (request.json or {}).get("history", []) if request.is_json else []
    history_text = json.dumps(history[-8:], ensure_ascii=False)
    client = _groq_client()
    prompt = f"""Generate one study question from the note below.\n\nPrior questions:\n{history_text}\n\nNote:\n{note_text[:12000]}\n\nReturn JSON:\n{{\n  "question": "one question",\n  "answer": "one correct answer",\n  "key_points": ["point 1", "point 2"]\n}}"""
    try:
        response = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}], temperature=0.5, max_tokens=900)
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```", "", raw)
        quiz = json.loads(raw)
        return flask.jsonify({"status": "ok", "quiz": quiz})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/notes/<int:note_id>/file", methods=["GET"])
def notes_file(note_id):
    note = None
    if current_user.is_authenticated:
        note = CourseNote.query.filter_by(id=note_id, user_id=current_user.id).first()
    else:
        note = CourseNote.query.filter_by(id=note_id, guest_session_id=get_guest_session_id()).first()
    if not note:
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404
    return flask.jsonify({"status": "ok", "view_url": getattr(note, "download_url", None), "filename": getattr(note, "original_filename", None), "text_content": getattr(note, "text_content", "")})

@app.route("/dismiss", methods=["POST"])
def dismiss():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "Missing title"}), 400
    try:
        save_dismissed(title, data)
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/restore", methods=["POST"])
def restore():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "Missing title"}), 400
    try:
        delete_dismissed(title)
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/tests")
def tests_page():
    return flask.render_template("tests.html", active_page="tests",
                                 logged_in=current_user.is_authenticated)

@app.route("/api/tests", methods=["GET"])
def api_get_tests():
    rows = get_test_marks()
    result = []
    for r in rows:
        try:
            d = json.loads(r.data) if r.data else {}
        except Exception:
            d = {}
        d["title"] = r.title
        d["marked_at"] = r.created_at.isoformat() if r.created_at else ""
        result.append(d)
    return flask.jsonify(result)

@app.route("/test/mark", methods=["POST"])
def mark_as_test():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "Missing title"}), 400
    try:
        save_test_mark(title, data)
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/test/unmark", methods=["POST"])
def unmark_as_test():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "Missing title"}), 400
    try:
        delete_test_mark(title)
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/assignment/description", methods=["GET"])
def get_description():
    assignment_id = request.args.get("id")
    course_id = request.args.get("course_id")
    title = request.args.get("title", "")
    custom = get_custom_description(title)
    if custom:
        return flask.jsonify({"description": custom, "source": "custom"})
    acct = get_active_account()
    if acct and acct["login_type"] == "canvas" and assignment_id and course_id:
        token = acct["canvas_token"]
        canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
        resp = requests.get(f"{canvas_url}/api/v1/courses/{course_id}/assignments/{assignment_id}", headers={"Authorization": f"Bearer {token}"}, timeout=20)
        if resp.status_code == 200:
            raw = resp.json().get("description") or ""
            clean = re.sub(r"<[^>]+>", " ", raw).strip()
            clean = re.sub(r"\s+", " ", clean)
            if clean:
                return flask.jsonify({"description": clean, "source": "canvas"})
    return flask.jsonify({"description": "", "source": "none"})

@app.route("/assignment/description", methods=["POST"])
def save_description():
    data = request.json or {}
    title = data.get("title")
    description = data.get("description", "").strip()
    if title and description:
        save_custom_description(title, description)
    return flask.jsonify({"status": "ok"})


@app.route("/api/assignment/notes", methods=["GET"])
def api_get_assignment_notes():
    title = request.args.get("title", "").strip()
    if not title:
        return flask.jsonify({"notes": "", "due_date": ""})
    raw = get_custom_description(title)
    if raw:
        try:
            data = json.loads(raw)
            return flask.jsonify({"notes": data.get("notes", ""), "due_date": data.get("due_date", "")})
        except Exception:
            return flask.jsonify({"notes": raw, "due_date": ""})
    return flask.jsonify({"notes": "", "due_date": ""})


@app.route("/api/assignment/notes", methods=["POST"])
def api_save_assignment_notes():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    notes = (body.get("notes") or "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "title required"}), 400
    raw = get_custom_description(title)
    try:
        existing = json.loads(raw) if raw else {}
    except Exception:
        existing = {"notes": raw} if raw else {}
    existing["notes"] = notes
    save_custom_description(title, json.dumps(existing))
    return flask.jsonify({"status": "ok"})


@app.route("/api/assignment/due-date", methods=["POST"])
def api_save_assignment_due_date():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    due_date = (body.get("due_date") or "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "title required"}), 400
    raw = get_custom_description(title)
    try:
        existing = json.loads(raw) if raw else {}
    except Exception:
        existing = {"notes": raw} if raw else {}
    existing["due_date"] = due_date
    save_custom_description(title, json.dumps(existing))
    return flask.jsonify({"status": "ok"})

@app.route("/generate_schedule", methods=["POST"])
@limiter.limit("10 per hour")
def generate_schedule():
    data = request.json or {}
    assignments = data.get("assignments", [])
    hours_per_day = data.get("hours_per_day", 2)
    preferred_time = data.get("preferred_time", "evening")
    custom_tasks = data.get("custom_tasks", [])
    if not assignments and not custom_tasks:
        return flask.jsonify({"status": "error", "message": "No assignments to schedule."})
    normalized_assignments = []
    for assignment in assignments:
        difficulty = assignment.get("difficulty") or infer_task_difficulty(
            assignment.get("points_possible"),
            assignment.get("priority", "Medium"),
            assignment.get("due_date"),
        )
        normalized_assignments.append({
            **assignment,
            "difficulty": difficulty,
            "color": assignment.get("color") or PRIORITY_COLORS.get(assignment.get("priority", "Medium"), "#60a5fa"),
        })
    today_str = datetime.now().strftime("%Y-%m-%d")
    overdue = [a for a in normalized_assignments if a.get("due_date", "9999") < today_str]
    upcoming = [a for a in normalized_assignments if a.get("due_date", "9999") >= today_str]
    upcoming.sort(key=lambda x: x.get("due_date", "9999"))
    try:
        client = _groq_client()
    except Exception:
        return flask.jsonify({"status": "error", "message": API_ERROR_MESSAGES["groq"], "retryable": True}), 503
    overdue_text = ""
    if overdue:
        overdue_text = f"\nOVERDUE — MUST BE SCHEDULED TODAY ({len(overdue)} assignments):\n" + "\n".join([
            f"  ⚠ {a['title']} ({a['course']}) — was due {a['due_date']}, Priority: HIGH, Est: {a['estimated_time']}min"
            for a in overdue
        ])
    upcoming_text = ""
    if upcoming:
        upcoming_text = f"\nUPCOMING ({len(upcoming)} assignments):\n" + "\n".join([
            f"  - {a['title']} ({a['course']}) — Due: {a['due_date']}, Priority: {a['priority']}, Difficulty: {a['difficulty']}, Est: {a['estimated_time']}min"
            for a in upcoming
        ])
    custom_text = ""
    if custom_tasks:
        custom_text = f"\nCUSTOM TASKS ADDED BY STUDENT — use EXACT names as written ({len(custom_tasks)}):\n" + "\n".join([f"  - {t}" for t in custom_tasks])
    today = datetime.now().strftime("%Y-%m-%d")
    total = len(normalized_assignments) + len(custom_tasks)
    # Build study profile context if user is logged in
    profile_context = ""
    if is_logged_in():
        try:
            identity = _get_or_create_identity(current_user.id)
            p = identity.to_dict()
            parts = []
            if p.get("grade_level"):
                parts.append(f"Grade: {p['grade_level']}")
            if p.get("focus_areas"):
                parts.append(f"Subjects: {', '.join(p['focus_areas'])}")
            if p.get("goals"):
                parts.append(f"Goals: {p['goals'][:200]}")
            if p.get("weekly_commitments"):
                parts.append(f"Weekly commitments: {p['weekly_commitments'][:150]}")
            if p.get("availability"):
                av_str = "; ".join(f"{d}: {t}" for d, t in p["availability"].items())
                if av_str:
                    parts.append(f"Availability: {av_str}")
            if parts:
                profile_context = "\nSTUDENT PROFILE:\n" + "\n".join(f"  - {x}" for x in parts) + "\n"
        except Exception:
            pass
    prompt = f"""You are IntelliPlan — an adaptive academic study-planning system. Today is {today}.

You must schedule ALL {total} items below. Every single one must appear in the schedule.
{profile_context}{overdue_text}
{upcoming_text}
{custom_text}

Student availability: {hours_per_day} hours/day, prefers {preferred_time}.

RULES:
1. ALL {total} items must appear in the schedule — no exceptions
2. Overdue items go on Day 1 as first priority blocks
3. Custom task names must be copied EXACTLY as written — do not rename them
4. Spread assignments across multiple days — max 3 assignments per day unless unavoidable
5. Split long assignments (>90min) across multiple days
6. Add a 10min break after every 45min work block
7. Never put the same assignment twice in one day
8. Schedule must end before the latest due date

Return ONLY valid JSON:
{{
  "schedule": [
    {{
      "date": "YYYY-MM-DD",
      "day_name": "Monday",
      "total_hours": {hours_per_day},
      "blocks": [
        {{
          "assignment": "Exact title here",
          "course": "Course name",
          "duration_minutes": 45,
          "time_slot": "7:00 PM - 7:45 PM",
          "notes": "What to focus on",
          "is_break": false
        }}
      ],
      "daily_tip": "Actionable tip"
    }}
  ],
  "overview": "Plan covering all {total} items",
  "total_study_time": "X hours Y minutes"
}}"""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=4000,
        )
        result = response.choices[0].message.content.strip()
        result = re.sub(r"```json\n?", "", result)
        result = re.sub(r"```\n?", "", result)
        schedule_data = json.loads(result)
        schedule_data = enrich_schedule_data(schedule_data, normalized_assignments, preferred_time, hours_per_day)
        return flask.jsonify({"status": "ok", "data": schedule_data})
    except json.JSONDecodeError:
        return flask.jsonify({"status": "error", "message": "The AI returned an invalid schedule. Please try again.", "retryable": True})
    except Exception as e:
        err_str = str(e).lower()
        if "rate" in err_str or "429" in err_str:
            return flask.jsonify({"status": "error", "message": "AI usage limit reached. Please wait a minute and try again.", "retryable": True}), 429
        if "timeout" in err_str:
            return flask.jsonify({"status": "error", "message": "The AI took too long to respond. Please try again.", "retryable": True}), 504
        print(f"Schedule generation error: {e}")
        return flask.jsonify({"status": "error", "message": API_ERROR_MESSAGES["groq"], "retryable": True}), 503

@app.route("/static/sw.js")
def service_worker():
    response = flask.make_response(flask.send_from_directory("static", "sw.js"))
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Service-Worker-Allowed"] = "/"
    return response

# ── NOTION OAUTH ──────────────────────────────────────────────
@app.route("/oauth/notion")
def oauth_notion_start():
    """Begin the Notion OAuth flow. Requires NOTION_CLIENT_ID."""
    if not NOTION_AVAILABLE:
        return redirect(url_for("settings"))
    if not os.getenv("NOTION_CLIENT_ID"):
        return render_template(
            "error.html",
            active_page="error",
            error_code=503,
            error_id="NOTION-OAUTH-NOT-CONFIGURED",
            message="Notion OAuth isn't set up on this deployment yet. "
                    "Use the integration token form instead.",
        ), 503
    state = secrets_module.token_urlsafe(24)
    session["notion_oauth_state"] = state
    session.modified = True
    redirect_uri = os.getenv("NOTION_REDIRECT_URI") or (
        APP_BASE_URL.rstrip("/") + "/oauth/notion/callback"
    )
    try:
        url = get_notion_auth_url(state, redirect_uri=redirect_uri)
    except Exception as e:
        return render_template("error.html", active_page="error", error_code=500,
                               error_id=f"NOTION-OAUTH-{make_error_id()}",
                               message=str(e)), 500
    return redirect(url)


@app.route("/oauth/notion/callback")
def oauth_notion_callback():
    if not NOTION_AVAILABLE:
        return redirect(url_for("settings"))
    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.pop("notion_oauth_state", None)
    if not code or not state or state != expected_state:
        return render_template("error.html", active_page="error", error_code=400,
                               error_id="NOTION-OAUTH-STATE",
                               message="Notion OAuth state did not match. Try again."), 400
    redirect_uri = os.getenv("NOTION_REDIRECT_URI") or (
        APP_BASE_URL.rstrip("/") + "/oauth/notion/callback"
    )
    try:
        tokens = exchange_notion_code(code, redirect_uri=redirect_uri)
    except Exception as e:
        return render_template("error.html", active_page="error", error_code=500,
                               error_id=f"NOTION-OAUTH-{make_error_id()}",
                               message=str(e)), 500

    access_token = tokens.get("access_token")
    if not access_token:
        return render_template("error.html", active_page="error", error_code=500,
                               error_id="NOTION-OAUTH-NO-TOKEN",
                               message="Notion did not return an access token."), 500

    # Persist on the user (or in session for guests).
    session["notion_token"] = access_token
    session.modified = True
    if current_user.is_authenticated:
        existing = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if existing:
            existing.token = access_token
            existing.auth_type = "oauth"
            existing.workspace_id = tokens.get("workspace_id")
            existing.workspace_name = tokens.get("workspace_name")
            existing.workspace_icon = tokens.get("workspace_icon")
            existing.bot_id = tokens.get("bot_id")
            existing.connected_at = datetime.utcnow()
            # Don't clobber database_id if the user already chose one.
        else:
            db.session.add(NotionIntegration(
                user_id=current_user.id,
                token=access_token,
                auth_type="oauth",
                workspace_id=tokens.get("workspace_id"),
                workspace_name=tokens.get("workspace_name"),
                workspace_icon=tokens.get("workspace_icon"),
                bot_id=tokens.get("bot_id"),
            ))
        db.session.commit()
    return redirect(url_for("settings") + "?notion=connected")


@app.route("/notion/status")
def notion_status():
    """Mirror of /calendar/events — does the dashboard widget have data?"""
    if not NOTION_AVAILABLE:
        return flask.jsonify({"connected": False, "configured": False})
    token, db_id = get_notion_token_and_db()
    if not token:
        return flask.jsonify({
            "connected": False,
            "configured": True,
            "oauth_available": bool(os.getenv("NOTION_CLIENT_ID")),
        })
    workspace = None
    if current_user.is_authenticated:
        ni = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if ni and ni.workspace_name:
            workspace = {
                "id": ni.workspace_id,
                "name": ni.workspace_name,
                "icon": ni.workspace_icon,
                "auth_type": ni.auth_type or "manual",
                "connected_at": ni.connected_at.isoformat() if ni.connected_at else None,
            }
    return flask.jsonify({
        "connected": True,
        "configured": True,
        "has_database": bool(db_id),
        "workspace": workspace,
        "oauth_available": bool(os.getenv("NOTION_CLIENT_ID")),
    })


@app.route("/notion/upcoming")
def notion_upcoming():
    """Notion analog of /calendar/events — used by the dashboard widget."""
    if not NOTION_AVAILABLE:
        return flask.jsonify({"connected": False, "tasks": []})
    token, db_id = get_notion_token_and_db()
    if not token or not db_id:
        return flask.jsonify({"connected": False, "tasks": []})
    try:
        days = int(request.args.get("days", 7))
        tasks = get_upcoming_notion_tasks(token, db_id, days=days)
        return flask.jsonify({"connected": True, "tasks": tasks})
    except Exception as e:
        return flask.jsonify({"connected": False, "error": str(e), "tasks": []})


@app.route("/notion/export", methods=["POST"])
def notion_export_schedule():
    """Push a generated schedule to Notion — mirror of /calendar/export."""
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error", "message": "Notion not configured"}), 503
    token, db_id = get_notion_token_and_db()
    if not token or not db_id:
        return flask.jsonify({"status": "error", "message": "Notion not connected"}), 400
    schedule = (request.get_json(silent=True) or {}).get("schedule") or {}
    try:
        created, skipped = add_schedule_to_notion(token, db_id, schedule)
        return flask.jsonify({"status": "ok", "created": len(created), "skipped": skipped, "ids": created})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500


# ── NOTION ────────────────────────────────────────────────────
@app.route("/notion/connect", methods=["POST"])
def notion_connect():
    if not NOTION_AVAILABLE:
        return flask.jsonify({
            "status": "error",
            "message": "Notion library is not installed on this server. The admin needs to add notion-client to requirements.txt and redeploy."
        }), 503
    token = (request.json or {}).get("token", "").strip()
    if not token:
        return flask.jsonify({"status": "error", "message": "No token provided"}), 400
    ok, info = test_notion_token_detail(token)
    if not ok:
        return flask.jsonify({"status": "error", "message": f"Notion rejected the token: {info}"}), 400
    # Populate workspace metadata from the bot's user record so the UI can
    # show "Connected to <workspace>" even for manual-token connections.
    bot_info = info if isinstance(info, dict) else {}
    bot = (bot_info.get("bot") or {}) if bot_info.get("type") == "bot" else {}
    workspace_name = bot.get("workspace_name") or bot_info.get("name") or "Notion workspace"
    bot_id = bot_info.get("id")
    session["notion_token"] = token
    session.modified = True
    if current_user.is_authenticated:
        existing = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if existing:
            existing.token = token
            existing.database_id = None
            existing.auth_type = "manual"
            existing.workspace_name = workspace_name
            existing.bot_id = bot_id
            existing.connected_at = datetime.utcnow()
        else:
            db.session.add(NotionIntegration(
                user_id=current_user.id,
                token=token,
                auth_type="manual",
                workspace_name=workspace_name,
                bot_id=bot_id,
            ))
        db.session.commit()
    try:
        dbs = get_notion_databases(token)
    except Exception as e:
        return flask.jsonify({
            "status": "ok",
            "databases": [],
            "warning": f"Connected, but could not list databases yet: {e}. Share a database with your integration in Notion, then refresh."
        })
    return flask.jsonify({"status": "ok", "databases": dbs})

@app.route("/notion/disconnect", methods=["POST"])
def notion_disconnect():
    if current_user.is_authenticated:
        NotionIntegration.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
    else:
        session.pop("notion_token", None)
        session.pop("notion_database_id", None)
    return flask.jsonify({"status": "ok"})

@app.route("/notion/set-database", methods=["POST"])
def notion_set_database():
    db_id = (request.json or {}).get("database_id")
    if not db_id:
        return flask.jsonify({"status": "error"})
    if current_user.is_authenticated:
        ni = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if ni:
            ni.database_id = db_id
            db.session.commit()
        else:
            token = session.get("notion_token")
            if token:
                db.session.add(NotionIntegration(user_id=current_user.id, token=token, database_id=db_id))
                db.session.commit()
    session["notion_database_id"] = db_id
    session.modified = True
    return flask.jsonify({"status": "ok"})

@app.route("/notion/databases")
def notion_databases_route():
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error", "databases": []}), 503
    token, db_id = get_notion_token_and_db()
    if not token:
        return flask.jsonify({"status": "error", "message": "Notion not connected", "databases": []}), 400
    try:
        return flask.jsonify({
            "status": "ok",
            "selected": db_id,
            "databases": get_notion_databases(token),
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e), "databases": []}), 500


@app.route("/notion/pages")
def notion_pages_route():
    """Pages the integration is allowed to see — used as parent options
    when creating a new IntelliPlan database in Notion."""
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error", "pages": []}), 503
    token, _ = get_notion_token_and_db()
    if not token:
        return flask.jsonify({"status": "error", "message": "Notion not connected", "pages": []}), 400
    try:
        return flask.jsonify({"status": "ok", "pages": get_shared_pages(token)})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e), "pages": []}), 500


@app.route("/notion/create-database", methods=["POST"])
def notion_create_database_route():
    """One-click "set up Notion for me" — create a tasks database with
    the right schema under a page the user has shared with us."""
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error", "message": "Notion library not installed"}), 503
    token, _ = get_notion_token_and_db()
    if not token:
        return flask.jsonify({"status": "error", "message": "Notion not connected"}), 400
    body = request.json or {}
    parent_id = body.get("parent_page_id")
    name = (body.get("name") or "IntelliPlan Tasks").strip()
    if not parent_id:
        return flask.jsonify({"status": "error", "message": "Pick a parent page first"}), 400
    try:
        new_id = create_intelliplan_database(token, parent_id, name=name)
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500
    # Auto-select the new database as the active target.
    if current_user.is_authenticated:
        ni = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if ni:
            ni.database_id = new_id
            db.session.commit()
    session["notion_database_id"] = new_id
    session.modified = True
    return flask.jsonify({"status": "ok", "database_id": new_id, "name": name})

@app.route("/notion/tasks")
def notion_tasks_route():
    if not NOTION_AVAILABLE:
        return flask.jsonify({"connected": False, "tasks": []})
    token, db_id = get_notion_token_and_db()
    if not token or not db_id:
        return flask.jsonify({"connected": False, "tasks": []})
    try:
        tasks = get_notion_tasks(token, db_id)
        return flask.jsonify({"connected": True, "tasks": tasks})
    except Exception as e:
        return flask.jsonify({"connected": False, "error": str(e), "tasks": []})

@app.route("/notion/tasks/create", methods=["POST"])
def notion_create_task():
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error"})
    token, db_id = get_notion_token_and_db()
    if not token or not db_id:
        return flask.jsonify({"status": "error", "message": "Notion not connected"})
    data = request.json or {}
    try:
        page_id = create_notion_task(token, db_id, data.get("title", ""), data.get("due_date"), data.get("priority", "Medium"))
        return flask.jsonify({"status": "ok", "page_id": page_id})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/notion/tasks/update", methods=["POST"])
def notion_update_task():
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error"})
    token, _ = get_notion_token_and_db()
    if not token:
        return flask.jsonify({"status": "error"})
    data = request.json or {}
    try:
        update_notion_task(token, data["page_id"], data.get("updates", {}))
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/notion/tasks/complete", methods=["POST"])
def notion_complete_task():
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error"})
    token, _ = get_notion_token_and_db()
    if not token:
        return flask.jsonify({"status": "error"})
    page_id = (request.json or {}).get("page_id")
    try:
        complete_notion_task(token, page_id)
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

# ── UNIFIED TASKS ─────────────────────────────────────────────
@app.route("/tasks/unified")
def unified_tasks():
    from datetime import date as date_type

    def dedupe_tasks(task_list):
        seen = set()
        unique = []
        for t in task_list:
            title = (t.get("title") or "").strip().lower()
            course = (t.get("course") or "").strip().lower()
            due_date = (t.get("due_date") or "").strip()
            key = (title, course, due_date)
            if key in seen:
                continue
            seen.add(key)
            unique.append(t)
        return unique

    tasks = []
    dismissed = get_dismissed_titles()
    today = date_type.today()
    priority_order = {"High": 0, "Medium": 1, "Low": 2}
    acct = get_active_account()
    if acct:
        login_type = acct["login_type"]
        if login_type == "studentvue":
            try:
                raw = get_sv_assignments(acct["sv_district_url"], acct["sv_username"], acct["sv_password"])
                if isinstance(raw, list):
                    for a in raw:
                        if isinstance(a, dict) and a.get("title") not in dismissed:
                            a["source"] = "studentvue"
                            if "priority" not in a:
                                a["priority"] = "Medium"
                            a.setdefault("color", PRIORITY_COLORS.get(a.get("priority", "Medium"), "#f59e0b"))
                            tasks.append(a)
            except Exception as e:
                print(f"SV assignments error: {e}")
            try:
                missing_raw = get_missing_assignments(acct["sv_district_url"], acct["sv_username"], acct["sv_password"])
                if isinstance(missing_raw, list):
                    for a in missing_raw:
                        if isinstance(a, dict) and a.get("title") not in dismissed:
                            a["source"] = "studentvue_missing"
                            if "priority" not in a:
                                a["priority"] = "High"
                            a.setdefault("color", PRIORITY_COLORS.get(a.get("priority", "High"), "#ef4444"))
                            tasks.append(a)
            except Exception as e:
                print(f"Missing assignments error: {e}")
        elif login_type == "canvas":
            try:
                token = acct["canvas_token"]
                canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
                base = f"{canvas_url}/api/v1"
                headers = {"Authorization": f"Bearer {token}"}
                course_response = requests.get(f"{base}/courses", headers=headers, timeout=20)
                courses = course_response.json()
                course_map = {c["id"]: c.get("name", "Unknown") for c in courses if isinstance(c, dict) and "id" in c}
                for course_id in course_map:
                    resp = requests.get(f"{base}/courses/{course_id}/assignments", headers=headers, timeout=20)
                    data = resp.json()
                    if not isinstance(data, list):
                        continue
                    for a in data:
                        if not isinstance(a, dict) or not a.get("due_at"):
                            continue
                        due_str = a["due_at"][:10]
                        try:
                            due = datetime.strptime(due_str, "%Y-%m-%d").date()
                        except Exception:
                            continue
                        days = (due - today).days
                        if days < -14:
                            continue
                        title = a["name"]
                        priority = compute_priority(days, a.get("points_possible") or 0, title)
                        if title in dismissed:
                            continue
                        tasks.append({
                            "id": str(a["id"]),
                            "course_id": str(a["course_id"]),
                            "title": title,
                            "course": course_map.get(a["course_id"], "Unknown"),
                            "due_date": due_str,
                            "priority": priority,
                            "source": "canvas",
                            "estimated_time": max(30, round(float(a.get("points_possible", 60) or 60) * 1.5 / 30) * 30),
                            "difficulty": "Medium",
                            "color": PRIORITY_COLORS.get(priority, "#f59e0b")
                        })
            except Exception as e:
                print(f"Canvas unified error: {e}")
    if NOTION_AVAILABLE:
        try:
            notion_token, notion_db_id = get_notion_token_and_db()
            if notion_token and notion_db_id:
                notion_raw = get_notion_tasks(notion_token, notion_db_id)
                for t in notion_raw:
                    if t.get("title") not in dismissed:
                        tasks.append(t)
        except Exception as e:
            print(f"Notion tasks error: {e}")
    try:
        if current_user.is_authenticated:
            manual = ManualTask.query.filter_by(user_id=current_user.id, done=False).all()
        else:
            gid = get_guest_session_id()
            manual = ManualTask.query.filter_by(guest_session_id=gid, done=False).all()
        for t in manual:
            if t.title not in dismissed:
                tasks.append({
                    "id": t.id,
                    "title": t.title,
                    "due_date": t.due_date or "",
                    "priority": t.priority,
                    "course": t.course,
                    "estimated_time": t.estimated_time,
                    "notes": t.notes,
                    "source": "manual",
                    "notion_page_id": t.notion_page_id,
                    "color": PRIORITY_COLORS.get(t.priority, "#f59e0b")
                })
    except Exception as e:
        print(f"Manual tasks error: {e}")
    tasks = dedupe_tasks(tasks)
    result = {"today": [], "upcoming": [], "overdue": []}
    for t in tasks:
        due = t.get("due_date", "")
        if not due:
            result["upcoming"].append(t)
            continue
        try:
            due_date = datetime.strptime(due, "%Y-%m-%d").date()
            if due_date < today:
                result["overdue"].append(t)
            elif due_date == today:
                result["today"].append(t)
            else:
                result["upcoming"].append(t)
        except Exception:
            result["upcoming"].append(t)
    for key in result:
        result[key].sort(key=lambda x: (x.get("due_date", "9999-12-31"), priority_order.get(x.get("priority", "Low"), 2)))
    return flask.jsonify(result)

@app.route("/missing/data")
def missing_data():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    login_type = acct["login_type"]
    try:
        if login_type == "studentvue":
            return flask.jsonify(get_missing_assignments(
                acct["sv_district_url"], acct["sv_username"], acct["sv_password"]
            ))
        if login_type == "canvas":
            from canvas_helper import get_missing_assignments as get_canvas_missing
            return flask.jsonify(get_canvas_missing(
                acct.get("canvas_url", "https://canvas.instructure.com"),
                acct["canvas_token"]
            ))
    except Exception as e:
        print(f"Missing data error ({login_type}): {e}")
        return flask.jsonify([])
    return flask.jsonify([])

# ── MANUAL TASKS ──────────────────────────────────────────────
@app.route("/tasks/manual/create", methods=["POST"])
def manual_create_task():
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "Title required"})
    task = ManualTask(
        user_id=current_user.id if current_user.is_authenticated else None,
        guest_session_id=None if current_user.is_authenticated else get_guest_session_id(),
        title=title,
        due_date=data.get("due_date", ""),
        priority=data.get("priority", "Medium"),
        course=data.get("course", "Personal"),
        estimated_time=int(data.get("estimated_time", 60)),
        notes=data.get("notes", "")
    )
    db.session.add(task)
    db.session.commit()
    if NOTION_AVAILABLE and data.get("sync_notion"):
        notion_token, notion_db_id = get_notion_token_and_db()
        if notion_token and notion_db_id:
            try:
                page_id = create_notion_task(notion_token, notion_db_id, title, data.get("due_date"), data.get("priority", "Medium"))
                task.notion_page_id = page_id
                db.session.commit()
            except Exception:
                pass
    return flask.jsonify({"status": "ok", "id": task.id})

@app.route("/tasks/manual/update", methods=["POST"])
def manual_update_task():
    data = request.json or {}
    task_id = data.get("id")
    task = db.session.get(ManualTask, task_id)
    if not task:
        return flask.jsonify({"status": "error", "message": "Not found"})
    if "title" in data: task.title = data["title"]
    if "due_date" in data: task.due_date = data["due_date"]
    if "priority" in data: task.priority = data["priority"]
    if "course" in data: task.course = data["course"]
    if "estimated_time" in data: task.estimated_time = int(data["estimated_time"])
    if "notes" in data: task.notes = data["notes"]
    if "done" in data: task.done = data["done"]
    db.session.commit()
    if NOTION_AVAILABLE and task.notion_page_id:
        notion_token, _ = get_notion_token_and_db()
        if notion_token:
            try:
                update_notion_task(notion_token, task.notion_page_id, data)
            except Exception:
                pass
    return flask.jsonify({"status": "ok"})

@app.route("/tasks/manual/delete", methods=["POST"])
def manual_delete_task():
    task_id = (request.json or {}).get("id")
    task = db.session.get(ManualTask, task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/tasks/manual/list")
def manual_list_tasks():
    if current_user.is_authenticated:
        tasks = ManualTask.query.filter_by(user_id=current_user.id, done=False).all()
    else:
        gid = get_guest_session_id()
        tasks = ManualTask.query.filter_by(guest_session_id=gid, done=False).all()
    return flask.jsonify([{
        "id": t.id, "title": t.title, "due_date": t.due_date,
        "priority": t.priority, "course": t.course,
        "estimated_time": t.estimated_time, "notes": t.notes,
        "source": "manual", "color": PRIORITY_COLORS.get(t.priority, "#f59e0b")
    } for t in tasks])

# ── SAVED SCHEDULE ────────────────────────────────────────────
@app.route("/schedule/save", methods=["POST"])
def save_schedule():
    data = request.json or {}
    schedule_data = data.get("schedule_data")
    name = data.get("name", f"Schedule {datetime.now().strftime('%b %d')}")
    if not schedule_data:
        return flask.jsonify({"status": "error", "message": "No schedule data"})
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if current_user.is_authenticated else get_guest_session_id()
    if uid:
        SavedSchedule.query.filter_by(user_id=uid).update({"is_active": False})
    else:
        SavedSchedule.query.filter_by(guest_session_id=gid).update({"is_active": False})
    s = SavedSchedule(user_id=uid, guest_session_id=gid, name=name, schedule_data=json.dumps(schedule_data), is_active=True)
    db.session.add(s)
    db.session.commit()
    return flask.jsonify({"status": "ok", "id": s.id})

@app.route("/schedule/saved")
def get_saved_schedule():
    if current_user.is_authenticated:
        s = SavedSchedule.query.filter_by(user_id=current_user.id, is_active=True).order_by(SavedSchedule.created_at.desc()).first()
    else:
        gid = get_guest_session_id()
        s = SavedSchedule.query.filter_by(guest_session_id=gid, is_active=True).order_by(SavedSchedule.created_at.desc()).first()
    if not s:
        return flask.jsonify({"status": "none"})
    return flask.jsonify({"status": "ok", "name": s.name, "created_at": s.created_at.strftime("%b %d, %Y"), "data": json.loads(s.schedule_data)})

@app.route("/schedule/delete", methods=["POST"])
def delete_saved_schedule():
    if current_user.is_authenticated:
        SavedSchedule.query.filter_by(user_id=current_user.id).delete()
    else:
        SavedSchedule.query.filter_by(guest_session_id=get_guest_session_id()).delete()
    db.session.commit()
    return flask.jsonify({"status": "ok"})

# ── FEEDBACK ──────────────────────────────────────────────────
@app.route("/feedback/predict-time", methods=["GET"])
def feedback_predict_time():
    """Predict how long this task will take based on the user's past
    TaskFeedback records. Falls back to the original estimate if there's
    no signal yet.

    Lookup strategy (most → least specific):
      1. Median actual_time of past completions of THIS exact title.
      2. Weighted blend of:
         - course average actual_time
         - priority-bucket average actual_time
         (weights = 0.6 / 0.4 when both exist; else whichever is present)
      3. Global median actual_time across all the user's completions.
      4. The estimate the caller already had (or 60 min).
    """
    if not is_logged_in():
        return flask.jsonify({"status": "ok", "predicted_minutes": None, "source": "anon"})
    title = (request.args.get("title") or "").strip()
    course = (request.args.get("course") or "").strip()
    priority = (request.args.get("priority") or "Medium").strip()
    fallback = request.args.get("fallback")
    try:
        fallback = int(fallback) if fallback else 60
    except (TypeError, ValueError):
        fallback = 60

    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    q = TaskFeedback.query
    if uid:
        q = q.filter_by(user_id=uid)
    else:
        q = q.filter_by(guest_session_id=gid)
    rows = q.filter(TaskFeedback.actual_time.isnot(None)).order_by(TaskFeedback.id.desc()).limit(500).all()

    def median(xs):
        xs = sorted(int(x) for x in xs if x is not None)
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) // 2

    same_title = [r.actual_time for r in rows if r.title.lower() == title.lower()] if title else []
    if same_title:
        med = median(same_title)
        if med:
            return flask.jsonify({"status": "ok", "predicted_minutes": med, "source": "same_title", "sample_size": len(same_title)})

    same_course = [r.actual_time for r in rows if course and (r.course or "").lower() == course.lower()]
    same_priority = [r.actual_time for r in rows if (r.priority or "").lower() == priority.lower()]

    course_med = median(same_course)
    prio_med = median(same_priority)

    if course_med and prio_med:
        blended = int(round(course_med * 0.6 + prio_med * 0.4))
        return flask.jsonify({"status": "ok", "predicted_minutes": blended, "source": "course+priority",
                              "course_sample": len(same_course), "priority_sample": len(same_priority)})
    if course_med:
        return flask.jsonify({"status": "ok", "predicted_minutes": course_med, "source": "course",
                              "sample_size": len(same_course)})
    if prio_med:
        return flask.jsonify({"status": "ok", "predicted_minutes": prio_med, "source": "priority",
                              "sample_size": len(same_priority)})

    global_med = median([r.actual_time for r in rows])
    if global_med:
        return flask.jsonify({"status": "ok", "predicted_minutes": global_med, "source": "global",
                              "sample_size": len(rows)})

    return flask.jsonify({"status": "ok", "predicted_minutes": fallback, "source": "fallback"})


@app.route("/feedback/complete", methods=["POST"])
def feedback_complete():
    data = request.json or {}
    title = data.get("title", "").strip()
    actual_time = data.get("actual_time")
    if not title:
        return flask.jsonify({"status": "error"})
    now = datetime.now()
    hour = now.hour
    time_of_day = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    feedback = TaskFeedback(
        user_id=current_user.id if current_user.is_authenticated else None,
        guest_session_id=None if current_user.is_authenticated else get_guest_session_id(),
        title=title,
        course=data.get("course", ""),
        estimated_time=data.get("estimated_time", 60),
        actual_time=int(actual_time) if actual_time else None,
        difficulty=data.get("difficulty", "Medium"),
        priority=data.get("priority", "Medium"),
        day_of_week=now.strftime("%a"),
        time_of_day=time_of_day
    )
    db.session.add(feedback)
    if data.get("dismiss"):
        save_dismissed(title, data)
    db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/feedback/export")
def feedback_export():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    rows = TaskFeedback.query.filter_by(user_id=current_user.id).all()
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Subject", "Estimate", "Actual", "Difficulty", "Priority", "DayOfWeek", "TimeOfDay"])
    for r in rows:
        writer.writerow([r.course, r.estimated_time, r.actual_time or "", r.difficulty, r.priority, r.day_of_week, r.time_of_day])
    output.seek(0)
    return flask.Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=intelliplan_data.csv"})

# ── PUSH NOTIFICATIONS ────────────────────────────────────────
@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    data = request.json or {}
    sub_json = json.dumps(data.get("subscription"))
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if current_user.is_authenticated else get_guest_session_id()
    existing = PushSubscription.query.filter_by(user_id=uid, guest_session_id=gid).first()
    if existing:
        existing.subscription_json = sub_json
    else:
        db.session.add(PushSubscription(user_id=uid, guest_session_id=gid, subscription_json=sub_json))
    db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/push/test", methods=["POST"])
def push_test():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if current_user.is_authenticated else get_guest_session_id()
    sub = PushSubscription.query.filter_by(user_id=uid, guest_session_id=gid).first()
    if not sub:
        return flask.jsonify({"status": "error", "message": "No subscription"})
    try:
        from pywebpush import webpush
        webpush(
            subscription_info=json.loads(sub.subscription_json),
            data=json.dumps({"title": "IntelliPlan", "body": "Notifications are working!"}),
            vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
            vapid_claims={"sub": f"mailto:{os.getenv('VAPID_EMAIL', 'hello@intelliplan.app')}"}
        )
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/push/vapid-public")
def vapid_public():
    return flask.jsonify({"key": os.getenv("VAPID_PUBLIC_KEY", "")})

@app.route("/notifications/silence", methods=["POST"])
def silence_notifications():
    data = request.json or {}
    minutes = int(data.get("minutes", 0))
    if minutes <= 0:
        return jsonify({"status": "error", "message": "Invalid duration"})
    silenced_until = datetime.utcnow() + timedelta(minutes=minutes)
    session["notifications_silenced_until"] = silenced_until.isoformat()
    return jsonify({"status": "ok", "silenced_until": silenced_until.isoformat()})

@app.route("/notifications/status")
def notification_status():
    return jsonify({"silenced_until": session.get("notifications_silenced_until")})

# ── DEBUG ─────────────────────────────────────────────────────
@app.route("/debug/auth")
def debug_auth():
    return flask.jsonify({
        "is_authenticated": current_user.is_authenticated,
        "user_id": current_user.id if current_user.is_authenticated else None,
        "session_keys": list(session.keys()),
        "has_google_session": "google_token" in session,
        "has_notion_session": "notion_token" in session,
        "google_db_row": GoogleIntegration.query.filter_by(user_id=current_user.id).first() is not None if current_user.is_authenticated else False,
        "notion_db_row": NotionIntegration.query.filter_by(user_id=current_user.id).first() is not None if current_user.is_authenticated else False,
    })

# ── NOTES HELPERS ─────────────────────────────────────────────
NOTE_ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".pdf", ".docx"}

def get_notes_owner_folder():
    if current_user.is_authenticated:
        return f"user_{current_user.id}"
    return f"guest_{get_guest_session_id()}"

def get_notes_owner_query():
    if current_user.is_authenticated:
        return CourseNote.query.filter_by(user_id=current_user.id)
    return CourseNote.query.filter_by(guest_session_id=get_guest_session_id())

def note_belongs_to_current_user(note):
    if current_user.is_authenticated:
        return note.user_id == current_user.id
    return note.guest_session_id == get_guest_session_id()

def extract_text_from_note_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext in {".txt", ".md", ".csv"}:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read().strip()
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
            except Exception:
                from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            return "\n".join(pages).strip()
        if ext == ".docx":
            from docx import Document
            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception as e:
        print(f"Note extraction error: {e}")
    return ""

def course_note_payload(note, include_text=False):
    payload = {
        "id": note.id,
        "course_name": note.course_name,
        "course_id": note.course_id,
        "course_source": note.course_source,
        "note_date": note.note_date,
        "title": note.title,
        "original_filename": note.original_filename,
        "has_file": bool(note.stored_filename),
        "download_url": f"/notes/{note.id}/download" if note.stored_filename else None,
        "summary_available": bool((note.summary_cache or "").strip()),
        "created_at": note.created_at.strftime("%b %d, %Y %I:%M %p"),
        "preview": (note.text_content or "")[:240],
    }
    if include_text:
        payload["text_content"] = note.text_content or ""
        payload["summary_cache"] = note.summary_cache or ""
    return payload

# ── EXTENSION ROUTES ──────────────────────────────────────────
@app.route("/extension/login", methods=["POST", "OPTIONS"])
def extension_login():
    if request.method == "OPTIONS":
        response = flask.make_response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Extension-Token"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response
    try:
        data = request.get_json(force=True, silent=True) or {}
        email = data.get("email", "").strip().lower()
        password = data.get("password", "").strip()
        if not email or not password:
            return flask.jsonify({"status": "error", "message": "Email and password required"})
        user = User.query.filter_by(email=email).first()
        if not user or not user.password_hash or not bcrypt.check_password_hash(user.password_hash, password):
            return flask.jsonify({"status": "error", "message": "Invalid email or password"})
        token = secrets_module.token_hex(32)
        db.session.add(ExtensionToken(user_id=user.id, token=token))
        db.session.commit()
        resp = flask.jsonify({"status": "ok", "token": token, "email": user.email})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        print(f"Extension login error: {e}")
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/extension/register", methods=["POST", "OPTIONS"])
def extension_register():
    if request.method == "OPTIONS":
        response = flask.make_response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Extension-Token"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response
    try:
        data = request.get_json(force=True, silent=True) or {}
        email = data.get("email", "").strip().lower()
        password = data.get("password", "").strip()
        if not email or not password:
            return flask.jsonify({"status": "error", "message": "Email and password required"})
        if len(password) < 8:
            return flask.jsonify({"status": "error", "message": "Password must be at least 8 characters"})
        if User.query.filter_by(email=email).first():
            return flask.jsonify({"status": "error", "message": "Account already exists"})
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        user = User(email=email, password_hash=pw_hash)
        db.session.add(user)
        db.session.commit()
        token = secrets_module.token_hex(32)
        db.session.add(ExtensionToken(user_id=user.id, token=token))
        db.session.commit()
        resp = flask.jsonify({"status": "ok", "token": token, "email": user.email})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        print(f"Extension register error: {e}")
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/extension/logout", methods=["POST", "OPTIONS"])
def extension_logout():
    if request.method == "OPTIONS":
        response = flask.make_response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Extension-Token"
        return response
    token = request.headers.get("X-Extension-Token")
    if token:
        ExtensionToken.query.filter_by(token=token).delete()
        db.session.commit()
    resp = flask.jsonify({"status": "ok"})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

def get_extension_user(token):
    if not token:
        return None
    try:
        row = ExtensionToken.query.filter_by(token=token).first()
        if not row:
            return None
        return db.session.get(User, row.user_id)
    except Exception:
        return None

def ext_response(data, status=200):
    resp = flask.jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Extension-Token"
    resp.status_code = status
    return resp

@app.route("/extension/tasks")
def extension_tasks():
    token = request.headers.get("X-Extension-Token")
    user = get_extension_user(token)
    if not user:
        return ext_response({"status": "error", "message": "Not authenticated"}, 401)
    try:
        from datetime import date as date_type
        today = date_type.today()
        priority_order = {"High": 0, "Medium": 1, "Low": 2}
        tasks = []
        dismissed_rows = DismissedAssignment.query.filter_by(user_id=user.id).all()
        dismissed = {r.title for r in dismissed_rows}
        acct = LinkedAccount.query.filter_by(user_id=user.id, is_active=True).first()
        if acct:
            creds = acct.get_credentials()
            login_type = acct.login_type
            if login_type == "studentvue":
                try:
                    raw = get_sv_assignments(creds["sv_district_url"], creds["sv_username"], creds["sv_password"])
                    missing = get_missing_assignments(creds["sv_district_url"], creds["sv_username"], creds["sv_password"])
                    for a in raw + missing:
                        if a["title"] not in dismissed:
                            a.setdefault("source", "studentvue")
                            tasks.append(a)
                except Exception as e:
                    print(f"Ext SV error: {e}")
            elif login_type == "canvas":
                try:
                    canvas_token = creds["canvas_token"]
                    canvas_url = creds.get("canvas_url", "https://canvas.instructure.com")
                    headers = {"Authorization": f"Bearer {canvas_token}"}
                    courses = requests.get(f"{canvas_url}/api/v1/courses", headers=headers, timeout=10).json()
                    course_map = {c["id"]: c.get("name", "Unknown") for c in courses if isinstance(c, dict) and "id" in c}
                    for course_id in course_map:
                        resp = requests.get(f"{canvas_url}/api/v1/courses/{course_id}/assignments", headers=headers, timeout=10).json()
                        if not isinstance(resp, list):
                            continue
                        for a in resp:
                            if not isinstance(a, dict) or not a.get("due_at"):
                                continue
                            due_str = a["due_at"][:10]
                            try:
                                due = datetime.strptime(due_str, "%Y-%m-%d").date()
                            except Exception:
                                continue
                            days = (due - today).days
                            if days < -14:
                                continue
                            title = a["name"]
                            priority = compute_priority(days, a.get("points_possible") or 0, title)
                            if title in dismissed:
                                continue
                            tasks.append({
                                "title": title,
                                "course": course_map.get(a["course_id"], "Unknown"),
                                "due_date": due_str,
                                "priority": priority,
                                "source": "canvas",
                                "estimated_time": max(30, round(float(a.get("points_possible", 60) or 60) * 1.5 / 30) * 30),
                                "color": PRIORITY_COLORS.get(priority, "#f59e0b")
                            })
                except Exception as e:
                    print(f"Ext Canvas error: {e}")
        manual = ManualTask.query.filter_by(user_id=user.id, done=False).all()
        for t in manual:
            if t.title not in dismissed:
                tasks.append({"id": t.id, "title": t.title, "due_date": t.due_date or "", "priority": t.priority, "course": t.course, "estimated_time": t.estimated_time, "source": "manual", "color": PRIORITY_COLORS.get(t.priority, "#f59e0b")})
        result = {"today": [], "upcoming": [], "overdue": []}
        for t in tasks:
            due = t.get("due_date", "")
            if not due:
                result["upcoming"].append(t)
                continue
            try:
                due_date = datetime.strptime(due, "%Y-%m-%d").date()
                if due_date < today:
                    result["overdue"].append(t)
                elif due_date == today:
                    result["today"].append(t)
                else:
                    result["upcoming"].append(t)
            except Exception:
                result["upcoming"].append(t)
        for key in result:
            result[key].sort(key=lambda x: (x.get("due_date", "9999"), priority_order.get(x.get("priority", "Low"), 2)))
        return ext_response(result)
    except Exception as e:
        print(f"Extension tasks error: {e}")
        return ext_response({"status": "error", "message": str(e)}, 500)

@app.route("/extension/schedule")
def extension_schedule():
    token = request.headers.get("X-Extension-Token")
    user = get_extension_user(token)
    if not user:
        return ext_response({"status": "error"}, 401)
    try:
        s = SavedSchedule.query.filter_by(user_id=user.id, is_active=True).order_by(SavedSchedule.created_at.desc()).first()
        if not s:
            return ext_response({"status": "none"})
        return ext_response({"status": "ok", "name": s.name, "created_at": s.created_at.strftime("%b %d, %Y"), "data": json.loads(s.schedule_data)})
    except Exception as e:
        print(f"Extension schedule error: {e}")
        return ext_response({"status": "error"}, 500)

@app.route("/extension/grades")
def extension_grades():
    token = request.headers.get("X-Extension-Token")
    user = get_extension_user(token)
    if not user:
        return ext_response([], 401)
    try:
        acct = LinkedAccount.query.filter_by(user_id=user.id, is_active=True).first()
        if not acct:
            return ext_response([])
        creds = acct.get_credentials()
        if acct.login_type == "studentvue":
            from studentvue_helper import get_grades as get_sv_grades
            grades = get_sv_grades(creds["sv_district_url"], creds["sv_username"], creds["sv_password"])
            return ext_response(grades)
        return ext_response([])
    except Exception as e:
        print(f"Extension grades error: {e}")
        return ext_response([], 500)

@app.route("/extension/dismiss", methods=["POST", "OPTIONS"])
def extension_dismiss():
    if request.method == "OPTIONS":
        response = flask.make_response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Extension-Token"
        return response
    token = request.headers.get("X-Extension-Token")
    user = get_extension_user(token)
    if not user:
        return ext_response({"status": "error"}, 401)
    try:
        data = request.get_json(force=True, silent=True) or {}
        title = data.get("title", "")
        if title:
            existing = DismissedAssignment.query.filter_by(user_id=user.id, title=title).first()
            if not existing:
                db.session.add(DismissedAssignment(user_id=user.id, title=title, data=json.dumps(data)))
                db.session.commit()
        return ext_response({"status": "ok"})
    except Exception as e:
        return ext_response({"status": "error"}, 500)

# ── STUDY ROUTES ──────────────────────────────────────────────
@app.route("/study/evaluate", methods=["POST"])
def study_evaluate():
    data = request.json or {}
    question = data.get("question", "").strip()
    correct_answer = data.get("correct_answer", "").strip()
    user_answer = data.get("user_answer", "").strip()
    confidence = data.get("confidence", "medium")
    if not user_answer:
        return flask.jsonify({"status": "error", "message": "No answer provided"})
    prompt = f'''Evaluate this student answer against the correct answer SEMANTICALLY and LENIENTLY.

QUESTION: {question}
CORRECT ANSWER: {correct_answer}
STUDENT'S ANSWER: {user_answer}

Guidelines:
- Focus on meaning, not exact wording.
- If the student captures the main idea, mark at least "partial".
- Minor wording differences or missing detail should NOT be marked incorrect.
- Only mark "incorrect" if the core concept is wrong or missing.
- Reward approximate understanding.

Return ONLY valid JSON:
{{
  "verdict": "correct" | "partial" | "incorrect",
  "score": 0-100,
  "what_was_right": "Encouraging feedback on what was correct",
  "what_was_missing": "Precise gaps or misconceptions",
  "critique": "2-3 sentence constructive critique",
  "memory_anchor": "One vivid way to remember this concept",
  "better_answer": "Ideal concise response"
}}

Scoring guide:
- correct: 70-100 (main idea + mostly accurate)
- partial: 40-69 (some understanding present)
- incorrect: 0-39 (core idea missing or wrong)
'''
    try:
        client = _groq_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=800
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\n?", "", raw)
        raw = re.sub(r"```\n?", "", raw)
        result = json.loads(raw)
        ua = user_answer.lower()
        ca = correct_answer.lower()
        keywords = [w for w in re.findall(r'\w+', ca) if len(w) > 4]
        keyword_hits = sum(1 for w in keywords if w in ua)
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, ua, ca).ratio()
        if result["verdict"] == "incorrect":
            if keyword_hits >= 2 or similarity > 0.5:
                result["verdict"] = "partial"
                result["score"] = max(result.get("score", 0), 45)
        if result["verdict"] == "partial" and similarity > 0.75:
            result["verdict"] = "correct"
            result["score"] = max(result.get("score", 0), 75)
        base = {"correct": 10, "partial": 7, "incorrect": 3}.get(result["verdict"], 3)
        conf_mult = {"high": 1.5, "medium": 1.0, "low": 0.7}.get(confidence, 1.0)
        if result["verdict"] == "incorrect" and confidence == "high":
            conf_mult = 0.6
        result["points_earned"] = max(1, round(base * conf_mult))
        return flask.jsonify({"status": "ok", "evaluation": result})
    except Exception as e:
        print(f"Study evaluate error: {e}")
        return flask.jsonify({
            "status": "error",
            "message": "Evaluation temporarily unavailable. Please try again."
        })

@app.route("/study/analyze-image", methods=["POST"])
def study_analyze_image():
    if "image" not in request.files:
        return flask.jsonify({"status": "error", "message": "No image provided"})
    img_file = request.files["image"]
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if img_file.content_type not in allowed_types:
        return flask.jsonify({"status": "error", "message": "Only JPEG, PNG, WebP, or GIF images are supported"})
    try:
        raw = img_file.read()
        if len(raw) > 10 * 1024 * 1024:
            return flask.jsonify({"status": "error", "message": "Image too large. Max 10MB."})
        b64 = base64.b64encode(raw).decode("utf-8")
        media_type = img_file.content_type
        client = _groq_client()
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                    {"type": "text", "text": "Extract ALL text, formulas, diagrams, tables, and key information from this educational image. Format as clean, readable study material. Preserve all text exactly, describe visual elements, preserve mathematical formulas, and note labels and captions. Output the extracted content directly without any preamble."}
                ]
            }],
            max_tokens=2000,
            temperature=0.1
        )
        extracted = response.choices[0].message.content.strip()
        if not extracted:
            return flask.jsonify({"status": "error", "message": "No content could be extracted from this image"})
        return flask.jsonify({"status": "ok", "text": extracted, "char_count": len(extracted)})
    except Exception as e:
        print(f"Image analysis error: {e}")
        return flask.jsonify({"status": "error", "message": "Image analysis is temporarily unavailable. Try pasting the text manually."})

@app.route("/analyze-image", methods=["POST"])
def analyze_image_general():
    if "image" not in request.files:
        return flask.jsonify({"status": "error", "message": "No image provided"})
    img_file = request.files["image"]
    question = request.form.get("question", "Describe what you see in this image in detail.")
    try:
        raw = img_file.read()
        if len(raw) > 10 * 1024 * 1024:
            return flask.jsonify({"status": "error", "message": "Image too large. Max 10MB."})
        b64 = base64.b64encode(raw).decode("utf-8")
        media_type = img_file.content_type or "image/jpeg"
        client = _groq_client()
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}, {"type": "text", "text": question}]}],
            max_tokens=1500,
            temperature=0.3
        )
        return flask.jsonify({"status": "ok", "response": response.choices[0].message.content.strip()})
    except Exception as e:
        print(f"General image analysis error: {e}")
        return flask.jsonify({"status": "error", "message": "Service temporarily unavailable. Please try again later."})

@app.route("/study/points", methods=["GET"])
@app.route("/study/streak", methods=["GET"])
def study_get_points():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    try:
        p = get_study_profile(uid, gid)
        streak_event = reconcile_missed_streak(p)
        tier = current_study_tier(p.streak_count)
        p.freeze_capacity = tier["freeze_cap"]
        weekly_quests = ensure_weekly_quests(p)
        history = safe_json_load(p.streak_history, [])
        sessions = safe_json_load(p.session_history, [])
        badges = safe_json_load(p.badges, [])
        cosmetics = safe_json_load(p.active_cosmetics, {})
        booster = safe_json_load(p.active_booster, None)
        level_info = level_for_sparks(p.sparks_earned_total)
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        at_risk = bool(p.last_active_date != today_str and now.hour >= 18 and int(p.streak_count or 0) > 0)
        repair_until = p.repair_eligible_until.isoformat() if p.repair_eligible_until else None
        repair_available = bool(p.repair_eligible_until and p.repair_eligible_until > datetime.utcnow())
        weekly_item_ids = sorted(SHOP_ITEMS.keys())
        weekly_deal_id = random.Random(active_week_id()).choice(weekly_item_ids)
        db.session.commit()
        return flask.jsonify({
            "status": "ok",
            "total_points": p.total_points,
            "spark_balance": p.spark_balance or 0,
            "sparks_earned_total": p.sparks_earned_total or p.total_points or 0,
            "level": p.level or level_info["level"],
            "level_title": level_info["title"],
            "next_level": level_info["next"],
            "streak_count": p.streak_count,
            "streak_freeze_count": p.streak_freeze_count,
            "freeze_capacity": p.freeze_capacity or tier["freeze_cap"],
            "last_active_date": p.last_active_date,
            "streak_tier": tier,
            "at_risk": at_risk,
            "repair_eligible_until": repair_until,
            "repair_available": repair_available,
            "repair_cost": repair_cost_for(p.longest_streak or p.streak_count),
            "streak_event": streak_event,
            "streak_history": history,
            "session_history": sessions[-20:],
            "longest_streak": p.longest_streak or p.streak_count,
            "total_sessions": p.total_sessions or 0,
            "badges": [{"id": bid, **BADGE_CATALOG.get(bid, {"name": bid.replace("_", " ").title(), "kind": "earned"})} for bid in badges],
            "active_booster": booster,
            "active_cosmetics": cosmetics,
            "weekly_quests": weekly_quests,
            "shop": {
                "items": SHOP_ITEMS,
                "weekly_deal": {
                    "item_id": weekly_deal_id,
                    "discount_percent": 30,
                    "price": int(round(SHOP_ITEMS[weekly_deal_id]["price"] * 0.7))
                }
            }
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/sparks/update", methods=["POST"])
@app.route("/study/points/update", methods=["POST"])
def study_update_points():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    data = request.json or {}
    delta = int(data.get("delta", data.get("sparks", 0)) or 0)
    try:
        p = get_study_profile(uid, gid)
        result = grant_sparks(p, delta, data.get("reason", "study"), apply_booster=bool(data.get("apply_booster", True)))
        p.updated_at = datetime.utcnow()
        db.session.commit()
        return flask.jsonify({
            "status": "ok",
            "awarded": result["awarded"],
            "base": result["base"],
            "multiplier": result["multiplier"],
            "total_points": p.total_points,
            "spark_balance": p.spark_balance,
            "sparks_earned_total": p.sparks_earned_total,
            "level": p.level,
            "level_title": level_for_sparks(p.sparks_earned_total)["title"],
            "level_up": result["level_up"],
            "active_booster": safe_json_load(p.active_booster, None)
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/streak/update", methods=["POST"])
def study_update_streak():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    data = request.json or {}
    today_str = (data.get("local_date") or datetime.now().strftime("%Y-%m-%d"))[:10]
    questions_answered = int(data.get("questions_answered", data.get("questions_total", 0)) or 0)
    if questions_answered < 5:
        return flask.jsonify({"status": "error", "message": "Complete at least 5 questions to count toward your streak."})
    try:
        p = get_study_profile(uid, gid)
        streak_event = reconcile_missed_streak(p)
        history = safe_json_load(p.streak_history, [])
        last = p.last_active_date
        bonus_points = 0
        milestone = None
        new_badges = []
        freeze_awarded = 0
        if last == today_str:
            return flask.jsonify({"status": "ok", "streak_count": p.streak_count, "bonus_points": 0, "total_points": p.total_points, "spark_balance": p.spark_balance, "streak_history": history, "longest_streak": p.longest_streak or p.streak_count, "streak_event": streak_event})
        yesterday = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        if last == yesterday:
            p.streak_count += 1
        elif last == "":
            p.streak_count = 1
        else:
            if p.streak_freeze_count > 0:
                p.streak_freeze_count -= 1
                p.streak_count += 1
                streak_event = {"type": "freeze_consumed", "message": "Streak Freeze used - your streak is safe. Back tomorrow."}
            else:
                p.streak_count = 1
        p.last_active_date = today_str
        if today_str not in history:
            history.append(today_str)
        history = sorted(history)[-90:]
        p.streak_history = json.dumps(history)
        if p.streak_count > (p.longest_streak or 0):
            p.longest_streak = p.streak_count
        tier = current_study_tier(p.streak_count)
        p.freeze_capacity = tier["freeze_cap"]
        daily = grant_sparks(p, tier["bonus"], "daily_streak", apply_booster=False)
        bonus_points += daily["awarded"]
        if p.streak_count in STREAK_MILESTONES:
            reward = STREAK_MILESTONES[p.streak_count]
            milestone_grant = grant_sparks(p, reward["sparks"], f"milestone:{p.streak_count}", apply_booster=False)
            freeze_awarded += reward["freezes"]
            p.streak_freeze_count = min(int(p.freeze_capacity or tier["freeze_cap"]), int(p.streak_freeze_count or 0) + freeze_awarded)
            new_badges.extend(add_badges(p, [reward.get("badge")]))
            bonus_points += milestone_grant["awarded"]
            milestone = {"day": p.streak_count, "sparks": reward["sparks"], "freezes": reward["freezes"], "badge": reward.get("badge"), "title": reward.get("title")}
        passive = passive_freezes_due(p)
        if passive:
            p.streak_freeze_count = min(int(p.freeze_capacity or tier["freeze_cap"]), int(p.streak_freeze_count or 0) + passive)
            freeze_awarded += passive
        p.repair_eligible_until = None
        db.session.commit()
        return flask.jsonify({
            "status": "ok",
            "streak_count": p.streak_count,
            "streak_freeze_count": p.streak_freeze_count,
            "freeze_capacity": p.freeze_capacity,
            "bonus_points": bonus_points,
            "daily_bonus": tier["bonus"],
            "total_points": p.total_points,
            "spark_balance": p.spark_balance,
            "streak_history": history,
            "longest_streak": p.longest_streak or p.streak_count,
            "streak_tier": tier,
            "milestone": milestone,
            "badges_unlocked": new_badges,
            "freezes_awarded": freeze_awarded,
            "streak_event": streak_event
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/api/activity", methods=["POST"])
def api_activity():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    data = request.json or {}
    today_str = (data.get("local_date") or datetime.now().strftime("%Y-%m-%d"))[:10]
    try:
        p = get_study_profile(uid, gid)
        reconcile_missed_streak(p)
        history = safe_json_load(p.streak_history, [])
        last = p.last_active_date
        if last == today_str:
            return flask.jsonify({"status": "ok", "already_counted": True, "streak_count": p.streak_count, "spark_balance": p.spark_balance, "streak_history": history})
        yesterday = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        if last == yesterday:
            p.streak_count += 1
        elif last == "":
            p.streak_count = 1
        else:
            if p.streak_freeze_count > 0:
                p.streak_freeze_count -= 1
                p.streak_count += 1
            else:
                p.streak_count = 1
        p.last_active_date = today_str
        if today_str not in history:
            history.append(today_str)
        history = sorted(history)[-90:]
        p.streak_history = json.dumps(history)
        if p.streak_count > (p.longest_streak or 0):
            p.longest_streak = p.streak_count
        grant_sparks(p, 5, "daily_activity")
        db.session.commit()
        return flask.jsonify({"status": "ok", "streak_count": p.streak_count, "spark_balance": p.spark_balance, "streak_history": history})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/mastery/update", methods=["POST"])
def study_mastery_update():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    data = request.json or {}
    question_key = data.get("question_key", "")[:512]
    verdict = data.get("verdict", "incorrect")
    score = int(data.get("score", 0))
    if not question_key:
        return flask.jsonify({"status": "error"})
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        q = StudyMastery.query.filter_by(user_id=uid, guest_session_id=gid, question_key=question_key).first()
        if not q:
            q = StudyMastery(user_id=uid, guest_session_id=gid, question_key=question_key, question_text=data.get("question_text", "")[:1000], answer_text=data.get("answer_text", "")[:1000], topic=data.get("topic", "")[:256])
            db.session.add(q)
        q.times_seen += 1
        q.last_seen = today
        if verdict == "correct":
            q.times_correct += 1
            q.easiness_factor = max(1.3, q.easiness_factor + 0.1 - (5 - min(5, score // 20)) * (0.08 + (5 - min(5, score // 20)) * 0.02))
            if q.times_correct == 1:
                q.interval_days = 1
            elif q.times_correct == 2:
                q.interval_days = 6
            else:
                q.interval_days = round(q.interval_days * q.easiness_factor)
            q.mastery_level = min(3, q.mastery_level + 1)
        elif verdict == "partial":
            q.times_partial += 1
            q.interval_days = max(1, q.interval_days // 2)
            q.easiness_factor = max(1.3, q.easiness_factor - 0.15)
        else:
            q.interval_days = 1
            q.easiness_factor = max(1.3, q.easiness_factor - 0.2)
            q.mastery_level = max(0, q.mastery_level - 1)
        q.next_review = (datetime.now() + timedelta(days=q.interval_days)).strftime("%Y-%m-%d")
        db.session.commit()
        mastery_labels = ["Not Learned", "Learning", "Familiar", "Mastered"]
        return flask.jsonify({"status": "ok", "mastery_level": q.mastery_level, "mastery_label": mastery_labels[q.mastery_level], "next_review": q.next_review, "interval_days": q.interval_days})
    except Exception as e:
        print(f"Mastery update error: {e}")
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/mastery/due", methods=["GET"])
def study_mastery_due():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if uid:
            items = StudyMastery.query.filter(StudyMastery.user_id == uid, StudyMastery.next_review <= today, StudyMastery.mastery_level < 3).order_by(StudyMastery.next_review.asc()).limit(20).all()
        else:
            items = StudyMastery.query.filter(StudyMastery.guest_session_id == gid, StudyMastery.next_review <= today, StudyMastery.mastery_level < 3).order_by(StudyMastery.next_review.asc()).limit(20).all()
        mastery_labels = ["Not Learned", "Learning", "Familiar", "Mastered"]
        return flask.jsonify([{"question_key": m.question_key, "question_text": m.question_text, "answer_text": m.answer_text, "topic": m.topic, "mastery_level": m.mastery_level, "mastery_label": mastery_labels[m.mastery_level], "times_seen": m.times_seen, "times_correct": m.times_correct, "next_review": m.next_review} for m in items])
    except Exception as e:
        return flask.jsonify([])

@app.route("/study/mastery/all", methods=["GET"])
def study_mastery_all():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    try:
        if uid:
            items = StudyMastery.query.filter_by(user_id=uid).order_by(StudyMastery.mastery_level.asc()).limit(100).all()
        else:
            items = StudyMastery.query.filter_by(guest_session_id=gid).order_by(StudyMastery.mastery_level.asc()).limit(100).all()
        mastery_labels = ["Not Learned", "Learning", "Familiar", "Mastered"]
        return flask.jsonify([{"question_key": m.question_key, "question_text": m.question_text, "topic": m.topic, "mastery_level": m.mastery_level, "mastery_label": mastery_labels[m.mastery_level], "times_seen": m.times_seen, "times_correct": m.times_correct, "accuracy": round(m.times_correct / m.times_seen * 100) if m.times_seen else 0, "next_review": m.next_review} for m in items])
    except Exception as e:
        return flask.jsonify([])

@app.route("/study/session/complete", methods=["POST"])
def study_session_complete():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    data = request.json or {}
    try:
        p = get_study_profile(uid, gid)
        sessions = safe_json_load(p.session_history, [])
        questions_total = int(data.get("questions_total", 0) or 0)
        questions_correct = int(data.get("questions_correct", 0) or 0)
        questions_partial = int(data.get("questions_partial", 0) or 0)
        points_earned = int(data.get("points_earned", data.get("sparks_earned", 0)) or 0)
        duration_seconds = int(data.get("duration_seconds", 0) or 0)
        rec = {
            "date": (data.get("local_date") or datetime.now().strftime("%Y-%m-%d"))[:10],
            "mode": data.get("mode", "casual"),
            "questions": questions_total,
            "correct": questions_correct,
            "partial": questions_partial,
            "points": points_earned,
            "duration": duration_seconds
        }
        sessions.append(rec)
        p.session_history = json.dumps(sessions[-50:])
        if not data.get("already_awarded"):
            grant_sparks(p, points_earned, "session_complete_import", apply_booster=False)
        p.total_sessions = (p.total_sessions or 0) + 1
        badges_to_add = []
        if p.total_sessions >= 1:
            badges_to_add.append("first_session")
        if p.total_sessions >= 10:
            badges_to_add.append("getting_serious")
        if p.total_sessions >= 25:
            badges_to_add.append("dedicated")
        if p.total_sessions >= 50:
            badges_to_add.append("committed")
        if p.total_sessions >= 100:
            badges_to_add.append("unstoppable")
        total_questions = sum(int(s.get("questions", 0) or 0) for s in sessions)
        total_correct = sum(int(s.get("correct", 0) or 0) for s in sessions)
        accuracy = round(total_correct / total_questions * 100) if total_questions else 0
        if accuracy >= 75 and total_questions >= 20:
            badges_to_add.append("sharp")
        if accuracy >= 85 and total_questions >= 20:
            badges_to_add.append("precise")
        if accuracy >= 95 and len(sessions) >= 20:
            badges_to_add.append("flawless")
        if questions_total > 0 and questions_correct >= questions_total:
            badges_to_add.append("perfect_week")
        if duration_seconds and duration_seconds < 300:
            badges_to_add.append("speed_demon")
        local_hour = int(data.get("local_hour", datetime.now().hour) or 0)
        if local_hour < 7:
            badges_to_add.append("early_bird")
        if local_hour == 0:
            badges_to_add.append("night_owl")
        new_badges = add_badges(p, badges_to_add)
        quest_rewards = update_quest_progress(p, {
            "date": rec["date"],
            "mode": rec["mode"],
            "questions": questions_total,
            "correct": questions_correct,
            "sparks": points_earned,
            "duration": duration_seconds,
            "mastered_concepts": int(data.get("mastered_concepts", 0) or 0)
        })
        db.session.commit()
        return flask.jsonify({
            "status": "ok",
            "total_points": p.total_points,
            "spark_balance": p.spark_balance,
            "total_sessions": p.total_sessions,
            "badges_unlocked": new_badges,
            "quest_rewards": quest_rewards,
            "weekly_quests": safe_json_load(p.weekly_quests, {})
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/quests", methods=["GET"])
def study_quests():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    try:
        p = get_study_profile(uid, gid)
        quests = ensure_weekly_quests(p)
        db.session.commit()
        return flask.jsonify({"status": "ok", "weekly_quests": quests})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/quests/update", methods=["POST"])
def study_quests_update():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    data = request.json or {}
    try:
        p = get_study_profile(uid, gid)
        rewards = update_quest_progress(p, data)
        db.session.commit()
        return flask.jsonify({"status": "ok", "quest_rewards": rewards, "weekly_quests": safe_json_load(p.weekly_quests, {}), "spark_balance": p.spark_balance})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/shop/buy", methods=["POST"])
def study_shop_buy():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    data = request.json or {}
    item_id = data.get("item_id")
    if item_id not in SHOP_ITEMS:
        return flask.jsonify({"status": "error", "message": "Unknown shop item."}), 400
    try:
        p = get_study_profile(uid, gid)
        item = SHOP_ITEMS[item_id]
        weekly_deal_id = random.Random(active_week_id()).choice(sorted(SHOP_ITEMS.keys()))
        price = int(round(item["price"] * 0.7)) if item_id == weekly_deal_id else int(item["price"])
        if int(p.spark_balance or 0) < price:
            return flask.jsonify({"status": "error", "message": "Not enough Sparks.", "spark_balance": p.spark_balance}), 400
        p.spark_balance = int(p.spark_balance or 0) - price
        cosmetics = safe_json_load(p.active_cosmetics, {})
        badges_to_add = ["shopper"]
        if item["kind"] == "protection":
            tier = current_study_tier(p.streak_count)
            p.freeze_capacity = tier["freeze_cap"]
            p.streak_freeze_count = min(int(p.freeze_capacity or 2), int(p.streak_freeze_count or 0) + int(item["value"]))
            badges_to_add.append("freeze_ready")
        elif item["kind"] == "booster":
            booster = {"type": item_id, "multiplier": item["multiplier"], "created_at": datetime.utcnow().isoformat()}
            if item.get("uses"):
                booster["uses"] = item["uses"]
            if item.get("hours"):
                booster["expires_at"] = (datetime.utcnow() + timedelta(hours=item["hours"])).isoformat()
            p.active_booster = json.dumps(booster)
            badges_to_add.append("booster_pilot")
        elif item["kind"] == "inventory":
            field = item["field"]
            cosmetics[field] = int(cosmetics.get(field, 0) or 0) + int(item["value"])
            p.active_cosmetics = json.dumps(cosmetics)
        elif item["kind"] == "cosmetic":
            owned = set(cosmetics.get("owned", []))
            owned.add(item_id)
            cosmetics["owned"] = sorted(owned)
            cosmetics[item["slot"]] = item["value"]
            p.active_cosmetics = json.dumps(cosmetics)
            badges_to_add.append("style_setter")
        if item_id == weekly_deal_id:
            badges_to_add.append("deal_hunter")
        if int(p.spark_balance or 0) >= 1000:
            badges_to_add.append("spark_saver")
        new_badges = add_badges(p, badges_to_add)
        purchases = safe_json_load(p.shop_purchases, [])
        purchases.append({"id": str(uuid.uuid4()), "item_id": item_id, "price": price, "created_at": datetime.utcnow().isoformat()})
        p.shop_purchases = json.dumps(purchases[-200:])
        db.session.commit()
        return flask.jsonify({
            "status": "ok",
            "item": item,
            "item_id": item_id,
            "price": price,
            "spark_balance": p.spark_balance,
            "streak_freeze_count": p.streak_freeze_count,
            "freeze_capacity": p.freeze_capacity,
            "active_booster": safe_json_load(p.active_booster, None),
            "active_cosmetics": safe_json_load(p.active_cosmetics, {}),
            "badges_unlocked": new_badges
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/repair", methods=["POST"])
def study_repair():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else get_guest_session_id()
    try:
        p = get_study_profile(uid, gid)
        cosmetics = safe_json_load(p.active_cosmetics, {})
        broken_streak = int(cosmetics.get("broken_streak_count", p.longest_streak or 0) or 0)
        if not p.repair_eligible_until or p.repair_eligible_until < datetime.utcnow():
            return flask.jsonify({"status": "error", "message": "Repair window expired."}), 400
        if p.repair_last_used:
            try:
                last_repair = datetime.strptime(p.repair_last_used[:10], "%Y-%m-%d")
                if last_repair > datetime.now() - timedelta(days=30):
                    return flask.jsonify({"status": "error", "message": "Streak Repair can only be used once every 30 days."}), 400
            except Exception:
                pass
        cost = repair_cost_for(broken_streak)
        used_repair_credit = False
        if int(cosmetics.get("repair_credits", 0) or 0) > 0:
            cost = max(1, int(round(cost * 0.5)))
            cosmetics["repair_credits"] = max(0, int(cosmetics.get("repair_credits", 0) or 0) - 1)
            used_repair_credit = True
        if int(p.spark_balance or 0) < cost:
            return flask.jsonify({"status": "error", "message": "Not enough Sparks to repair this streak.", "cost": cost, "spark_balance": p.spark_balance}), 400
        p.spark_balance = int(p.spark_balance or 0) - cost
        p.streak_count = max(int(p.streak_count or 0), broken_streak)
        p.longest_streak = max(int(p.longest_streak or 0), p.streak_count)
        p.last_active_date = cosmetics.get("broken_last_active_date") or p.last_active_date
        p.repair_last_used = datetime.now().strftime("%Y-%m-%d")
        p.repair_eligible_until = None
        cosmetics.pop("broken_streak_count", None)
        cosmetics.pop("broken_last_active_date", None)
        p.active_cosmetics = json.dumps(cosmetics)
        new_badges = add_badges(p, ["comeback_kid"])
        db.session.commit()
        return flask.jsonify({"status": "ok", "streak_count": p.streak_count, "spark_balance": p.spark_balance, "badges_unlocked": new_badges, "message": "Streak repaired.", "repair_cost": cost, "used_repair_credit": used_repair_credit})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

# ── STUDY ACCESS LIMITS ───────────────────────────────────────
GUEST_STUDY_LIMITS = {
    "uploads": 1,
    "generations": 1,
    "max_chars": 6000,
    "max_questions": 5
}

def _get_guest_usage():
    if "guest_study_usage" not in session:
        session["guest_study_usage"] = {"uploads": 0, "generations": 0}
    return session["guest_study_usage"]

def _save_guest_usage(usage):
    session["guest_study_usage"] = usage
    session.modified = True

def _guest_limit_response():
    return flask.jsonify({
        "status": "error",
        "code": "login_required",
        "message": "Create an account to continue using Study & Learn.",
        "upgrade_required": True
    }), 403

def _is_guest():
    return not current_user.is_authenticated

@app.route("/study/access", methods=["GET"])
def study_access():
    if current_user.is_authenticated:
        return flask.jsonify({"status": "ok", "logged_in": True, "limits": None})
    usage = _get_guest_usage()
    remaining_uploads = max(0, GUEST_STUDY_LIMITS["uploads"] - usage["uploads"])
    remaining_generations = max(0, GUEST_STUDY_LIMITS["generations"] - usage["generations"])
    return flask.jsonify({
        "status": "ok",
        "logged_in": False,
        "limits": {
            "remaining_uploads": remaining_uploads,
            "remaining_generations": remaining_generations,
            "max_questions": GUEST_STUDY_LIMITS["max_questions"]
        }
    })

@app.route("/study/extract-pdf", methods=["POST"])
def study_extract_pdf():
    if "file" not in request.files:
        return flask.jsonify({"status": "error", "message": "No file"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return flask.jsonify({"status": "error", "message": "Only PDF files"}), 400
    if _is_guest():
        usage = _get_guest_usage()
        if usage["uploads"] >= GUEST_STUDY_LIMITS["uploads"]:
            return _guest_limit_response()
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(f.read()))
        text = " ".join(page.extract_text() or "" for page in reader.pages)
        if _is_guest():
            usage = _get_guest_usage()
            usage["uploads"] += 1
            _save_guest_usage(usage)
        return flask.jsonify({"status": "ok", "text": text[:15000]})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/study/generate", methods=["POST"])
def study_generate():
    data = request.json or {}
    content = data.get("content", "").strip()
    mode = data.get("mode", "casual")
    num_questions = int(data.get("num_questions", 8))
    if not content:
        return flask.jsonify({"status": "error", "message": "No content provided"}), 400
    if _is_guest():
        usage = _get_guest_usage()
        if usage["generations"] >= GUEST_STUDY_LIMITS["generations"]:
            return _guest_limit_response()
        mode = "casual"
        num_questions = min(num_questions, GUEST_STUDY_LIMITS["max_questions"])
        content = content[:GUEST_STUDY_LIMITS["max_chars"]]
    else:
        if len(content) > 20000:
            content = content[:20000]
    prompt = f'''You are an expert study assistant. Analyze the following study material and generate exactly {num_questions} study questions.

STUDY MATERIAL:
{content}

Generate a mix of:
- 3-4 recall/definition questions (straightforward facts)
- 2-3 conceptual questions (understanding why/how)
- 2-3 short-answer questions (application or explanation)

Also extract 5-8 key concepts from the material.

Respond ONLY with valid JSON in this exact format:
{{
  "title": "Brief topic title (5 words max)",
  "key_concepts": [
    {{"term": "Term name", "definition": "Clear definition in 1-2 sentences"}}
  ],
  "questions": [
    {{
      "id": 1,
      "type": "recall",
      "question": "Question text here?",
      "answer": "Complete, detailed answer here. Be thorough.",
      "hint": "Optional one-word hint"
    }}
  ]
}}

Question types: "recall", "conceptual", "short-answer"
Make answers comprehensive (2-4 sentences). Make questions specific to the content.
Be accurate, but keep the tone supportive and student-friendly.'''
    try:
        client = _groq_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1200 if _is_guest() else 3000
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```", "", raw).strip()
        result = json.loads(raw)
        if _is_guest():
            usage = _get_guest_usage()
            usage["generations"] += 1
            _save_guest_usage(usage)
        return flask.jsonify({"status": "ok", "data": result})
    except Exception as e:
        print(f"Study generate error: {e}")
        return flask.jsonify({
            "status": "error",
            "message": "Study generation temporarily unavailable. Please try again later."
        }), 500

# ── ERROR HANDLERS ────────────────────────────────────────────
@app.errorhandler(404)
def error_404(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/"):
        return flask.jsonify({"status": "error", "message": "Not found"}), 404
    try:
        return render_template("error.html", active_page="error", error_code=404, error_id=make_error_id()), 404
    except Exception:
        return flask.Response("<h1>404 Not Found</h1><a href='/'>Home</a>", status=404, mimetype="text/html")

@app.errorhandler(403)
def error_403(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/"):
        return flask.jsonify({"status": "error", "message": "Forbidden"}), 403
    try:
        return render_template("error.html", active_page="error", error_code=403, error_id=make_error_id()), 403
    except Exception:
        return flask.Response("<h1>403 Forbidden</h1><a href='/'>Home</a>", status=403, mimetype="text/html")

# ═════════════════════════════════════════════════════════════════════
# NEW FEATURE MODULES
# (Grade simulator, Lesson Recorder, Study Groups, Writing Assistant,
#  Math Explainer, Task Extractor)
# Each block is self-contained: lazy Groq client, light DB use, JSON
# responses. The web pages each new module needs sit in /Main_Project/
# templates/ — see lessons.html, groups.html, writing.html, math.html,
# extractor.html. The upgraded grade modeller lives in grademodel.html.
# ═════════════════════════════════════════════════════════════════════

GROQ_MODEL = "llama-3.3-70b-versatile"
# Fast/small model for short classification tasks (priority assignment,
# task extraction, simple suggestions) — ~10x faster + cheaper than the
# 70b model with comparable quality for short prompts.
GROQ_FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
# Vision-capable model. The old meta-llama/llama-4-scout-17b-16e-instruct was retired
# by Groq; meta-llama/llama-4-scout-17b-16e-instruct is the current
# multimodal model on Groq with image input support.
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

# Singleton Groq client. The previous code created a new client on every
# request, which re-allocated httpx connection pools and added 50-200ms
# of overhead per call. The client is thread-safe so one global is fine
# under gunicorn workers.
_groq_client_cache = None
def _groq_client():
    global _groq_client_cache
    if _groq_client_cache is None:
        _groq_client_cache = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client_cache


# ── ADMIN / FEATURE FLAGS ───────────────────────────────────────────
# Hidden admin surface for the project owner. Email-gated so even if
# someone discovers the URL, only the configured account can use it.
ADMIN_EMAILS = {
    e.strip().lower()
    for e in (os.getenv("ADMIN_EMAILS") or "uanirudh0811@gmail.com").split(",")
    if e.strip()
}
ADMIN_PATH = os.getenv("ADMIN_PATH", "/admin-x9k2p7")  # not linked anywhere


def is_admin(user):
    try:
        return bool(user and getattr(user, "is_authenticated", False)
                    and (user.email or "").lower() in ADMIN_EMAILS)
    except Exception:
        return False


def require_admin(fn):
    from functools import wraps as _wraps
    @_wraps(fn)
    def w(*a, **kw):
        if not is_admin(current_user):
            # 404 to hide existence from non-admins. Admin gets a fresh
            # /login redirect if simply not yet signed in.
            if not current_user.is_authenticated:
                return redirect(url_for("login"))
            return render_template("error.html", error_code=404,
                                   error_id="ADMIN-403"), 404
        return fn(*a, **kw)
    return w


# Default flag descriptions seeded into the DB on first admin visit so
# the admin page is useful out-of-the-box. Adding new keys here keeps
# them in sync.
DEFAULT_FLAGS = {
    "lessons":       "Lesson Library (uploads + Whisper summary)",
    "groups":        "Study Groups",
    "live_sessions": "Live study sessions (Jitsi)",
    "writing":       "Writing Assistant",
    "math":          "Math Explainer",
    "extractor":     "Task Extractor",
    "stripe":        "Stripe Checkout for Pro upgrades",
    "referral":      "Referral program",
    "onboarding":    "First-run onboarding modal",
    "ai_chat":       "Plani chat assistant",
}


def feature_enabled(key):
    """Cheap per-request check. Default True so an empty / missing
    flag row never disables a feature accidentally."""
    try:
        row = FeatureFlag.query.filter_by(key=key).first()
        return True if row is None else bool(row.enabled)
    except Exception:
        return True


@app.context_processor
def inject_admin():
    try:
        return {
            "is_admin": is_admin(current_user) if current_user.is_authenticated else False,
            "feature_enabled": feature_enabled,
            "admin_path": ADMIN_PATH,
        }
    except Exception:
        return {"is_admin": False, "feature_enabled": lambda k: True, "admin_path": ADMIN_PATH}


def _seed_default_flags():
    try:
        for k, desc in DEFAULT_FLAGS.items():
            row = FeatureFlag.query.filter_by(key=k).first()
            if not row:
                db.session.add(FeatureFlag(key=k, enabled=True, description=desc))
        db.session.commit()
    except Exception:
        try: db.session.rollback()
        except Exception: pass


@app.route(ADMIN_PATH, methods=["GET"])
@require_admin
def admin_panel():
    _seed_default_flags()
    flags = FeatureFlag.query.order_by(FeatureFlag.key.asc()).all()
    user_count = 0
    pro_count = 0
    try:
        user_count = User.query.count()
        pro_count = User.query.filter_by(tier="pro").count()
    except Exception:
        pass
    return render_template(
        "admin.html",
        active_page="admin",
        flags=flags,
        user_count=user_count,
        pro_count=pro_count,
        admin_email=(current_user.email if current_user.is_authenticated else ""),
    )


@app.route("/api/admin/flag", methods=["POST"])
@require_admin
def admin_set_flag():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()[:64]
    enabled = bool(body.get("enabled"))
    if not key:
        return flask.jsonify({"status": "error", "message": "key required"}), 400
    row = FeatureFlag.query.filter_by(key=key).first()
    if not row:
        row = FeatureFlag(key=key, enabled=enabled, description=DEFAULT_FLAGS.get(key, ""))
        db.session.add(row)
    else:
        row.enabled = enabled
    db.session.commit()
    return flask.jsonify({"status": "ok", "key": key, "enabled": enabled})


@app.route("/api/admin/grant-pro", methods=["POST"])
@require_admin
def admin_grant_pro():
    """Admin testing helper — grant N days of Pro to any user by email."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    days = int(body.get("days") or 30)
    user = User.query.filter_by(email=email).first()
    if not user:
        return flask.jsonify({"status": "error", "message": "user not found"}), 404
    base = user.pro_until if (user.pro_until and user.pro_until > datetime.utcnow()) else datetime.utcnow()
    user.pro_until = base + timedelta(days=days)
    user.tier = "pro"
    db.session.commit()
    return flask.jsonify({"status": "ok", "user": user.email, "pro_until": user.pro_until.isoformat()})


@app.route("/api/admin/smtp-status", methods=["GET"])
@require_admin
def admin_smtp_status():
    """Show which SMTP env vars are detected — values masked, just presence/absence."""
    host, port, user, pw, sender = _smtp_config()
    return flask.jsonify({
        "host": host or None,
        "port": port,
        "user_set": bool(user),
        "password_set": bool(pw),
        "sender": sender,
        "ready": bool(host and user and pw),
        "vars_checked": {
            "host": ["SMTP_HOST", "MAIL_HOST", "EMAIL_HOST", "(gmail auto-detect)"],
            "user": ["SMTP_USER", "SMTP_USERNAME", "MAIL_USERNAME", "EMAIL_USERNAME", "USERNAME"],
            "password": ["SMTP_PASSWORD", "MAIL_PASSWORD", "EMAIL_PASSWORD", "PASSWORD"],
            "port": ["SMTP_PORT", "MAIL_PORT", "(default 587)"],
        },
    })


@app.route("/api/admin/sms-blast/preview", methods=["POST"])
@require_admin
def admin_sms_blast_preview():
    """Return recipient count and sample emails for the chosen audience — no SMS sent."""
    body = request.get_json(silent=True) or {}
    audience = (body.get("audience") or "sms_opted_in").strip()
    specific_emails = [e.strip().lower() for e in (body.get("emails") or "").split(",") if e.strip()]

    try:
        q = _admin_sms_audience_query(audience, specific_emails)
        users = q.all()
        return flask.jsonify({
            "status": "ok",
            "count": len(users),
            "sample": [u.email for u in users[:10]],
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/admin/sms-blast/send", methods=["POST"])
@require_admin
def admin_sms_blast_send():
    """Send an SMS to every user in the selected audience.
    Returns per-recipient results. Runs synchronously — keep audience small
    or use the cron pipeline for large sends."""
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    audience = (body.get("audience") or "sms_opted_in").strip()
    specific_emails = [e.strip().lower() for e in (body.get("emails") or "").split(",") if e.strip()]

    if not message:
        return flask.jsonify({"status": "error", "message": "message is required"}), 400
    if len(message) > 160:
        return flask.jsonify({"status": "error", "message": "message must be 160 characters or fewer"}), 400

    try:
        q = _admin_sms_audience_query(audience, specific_emails)
        users = q.all()
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

    sent, failed, skipped = 0, 0, 0
    results = []
    for u in users:
        if not u.phone:
            skipped += 1
            results.append({"email": u.email, "result": "skipped_no_phone"})
            continue
        ok, _err = _sms_send_for_user(u, message)
        if ok:
            sent += 1
            results.append({"email": u.email, "result": "sent"})
        else:
            failed += 1
            results.append({"email": u.email, "result": "failed"})

    print(f"[admin-sms-blast] audience={audience} total={len(users)} sent={sent} failed={failed} skipped={skipped}")
    return flask.jsonify({
        "status": "ok",
        "total": len(users),
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    })


def _admin_sms_audience_query(audience, specific_emails=None):
    """Return a SQLAlchemy query for users matching the given audience key."""
    base = User.query.filter(User.phone.isnot(None), User.phone != "")

    if audience == "specific":
        if not specific_emails:
            raise ValueError("No emails provided for specific audience")
        return base.filter(User.email.in_(specific_emails))
    elif audience == "all_with_phone":
        return base
    elif audience == "pro":
        return base.filter(User.sms_reminders_opt_in.is_(True), User.tier == "pro")
    elif audience == "free":
        return base.filter(User.sms_reminders_opt_in.is_(True), User.tier == "free")
    else:
        # default: sms_opted_in — only users who consented
        return base.filter(User.sms_reminders_opt_in.is_(True))


# ── REFERRAL + PRO TIER ─────────────────────────────────────────────
PRO_TRIAL_DAYS = 30
PRO_REFERRAL_BONUS_DAYS = 14


def _ensure_referral_code(user):
    if user.referral_code:
        return user.referral_code
    # 8-char URL-safe code; retry on the unlikely collision.
    for _ in range(5):
        code = secrets_module.token_urlsafe(6).replace("-", "").replace("_", "")[:8].lower()
        if not User.query.filter_by(referral_code=code).first():
            user.referral_code = code
            db.session.commit()
            return code
    return None


def is_pro(user):
    """True if the user has an active Pro subscription right now."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if (user.tier or "free") != "pro":
        return False
    if user.pro_until and user.pro_until < datetime.utcnow():
        return False
    return True


@app.context_processor
def inject_pro():
    """Templates can read `is_pro` directly: {% if is_pro %} ... {% endif %}.

    Defensive — if the `tier`/`pro_until` columns are missing (e.g. an
    older DB the migration hasn't reached yet) we MUST swallow the
    error here, otherwise every Jinja2 render (including the error
    page itself) blows up and the user sees a raw "Server Error" page
    with a Home link that loops back to the same broken state.
    """
    try:
        if current_user and current_user.is_authenticated:
            return {"is_pro": is_pro(current_user)}
    except Exception:
        pass
    return {"is_pro": False}


def require_pro(fn):
    """Decorator for endpoints that should fall behind the paywall."""
    from functools import wraps as _wraps
    @_wraps(fn)
    def w(*a, **kw):
        if not current_user.is_authenticated:
            return flask.jsonify({"status": "error", "code": "auth_required"}), 401
        if not is_pro(current_user):
            return flask.jsonify({"status": "error", "code": "pro_required",
                                  "message": "IntelliPlan Pro is required for this feature."}), 402
        return fn(*a, **kw)
    return w


@app.route("/ref/<code>")
def referral_landing(code):
    """Public landing — stash the code in session, then redirect to register.

    A new account created during this session gets `referred_by_id` set,
    bumping both accounts' Pro day balance on signup.
    """
    code = (code or "").strip().lower()[:16]
    if code:
        ref_user = User.query.filter_by(referral_code=code).first()
        if ref_user:
            session["pending_referral"] = ref_user.id
            session.modified = True
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("register"))


@app.route("/api/referral", methods=["GET"])
def api_referral():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    code = _ensure_referral_code(current_user)
    invited = User.query.filter_by(referred_by_id=current_user.id).count()
    return flask.jsonify({
        "status": "ok",
        "code": code,
        "invited_count": invited,
        "pro_days_earned": invited * PRO_REFERRAL_BONUS_DAYS,
    })


@app.route("/api/pro/status", methods=["GET"])
def api_pro_status():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "ok", "tier": "free", "pro_until": None})
    return flask.jsonify({
        "status": "ok",
        "tier": "pro" if is_pro(current_user) else "free",
        "pro_until": current_user.pro_until.isoformat() if current_user.pro_until else None,
    })


@app.route("/api/pro/upgrade", methods=["POST"])
def api_pro_upgrade():
    """Lightweight trial-start endpoint. Production deployments would put
    a real Stripe Checkout in front of this; we still record the tier so
    the rest of the gating works end-to-end."""
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    current_user.tier = "pro"
    base = current_user.pro_until if (current_user.pro_until and current_user.pro_until > datetime.utcnow()) else datetime.utcnow()
    current_user.pro_until = base + timedelta(days=PRO_TRIAL_DAYS)
    db.session.commit()
    return flask.jsonify({"status": "ok", "pro_until": current_user.pro_until.isoformat()})


@app.route("/api/pro/cancel", methods=["POST"])
def api_pro_cancel():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"}), 401
    # Keep Pro until the end of the current period.
    if not current_user.pro_until:
        current_user.tier = "free"
        db.session.commit()
    return flask.jsonify({"status": "ok"})


# ── STRIPE CHECKOUT ────────────────────────────────────────────────
# Live billing path: /api/pro/checkout creates a Stripe Checkout session
# and returns the redirect URL. After payment, Stripe POSTs to
# /api/stripe/webhook and we flip the user to Pro.
#
# Required env vars on the deployment:
#   STRIPE_SECRET_KEY        (sk_live_… or sk_test_…)
#   STRIPE_PRICE_ID          (price_… of the Pro recurring product)
#   STRIPE_WEBHOOK_SECRET    (whsec_… from the Stripe dashboard)
def _stripe():
    if not feature_enabled("stripe"):
        return None
    try:
        import stripe as _stripe_lib
    except ImportError:
        print("[stripe] stripe library not installed — `pip install stripe`")
        return None
    sk = os.getenv("STRIPE_SECRET_KEY")
    if not sk:
        return None
    _stripe_lib.api_key = sk
    _stripe_lib.api_version = "2026-04-22.dahlia"
    return _stripe_lib


# Live recurring Pro price created in the IntelliPlan Stripe account
# (acct_1TXoIJDEIZ8BeiXx). Used as a fallback when STRIPE_PRICE_ID env
# var isn't set, so the live deploy can take payments out of the box.
STRIPE_DEFAULT_PRICE_ID = "price_1TXoQtDEIZ8BeiXx5l4kmfJ6"
STRIPE_DEFAULT_PRODUCT_ID = "prod_UWsBXvOf2LiKRR"


@app.route("/api/pro/checkout", methods=["POST"])
def api_pro_checkout():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    stripe = _stripe()
    if not stripe:
        # Graceful fallback: dev / unconfigured deploys still get a trial.
        return api_pro_upgrade()
    price_id = os.getenv("STRIPE_PRICE_ID") or STRIPE_DEFAULT_PRICE_ID
    base = APP_BASE_URL.rstrip("/") if APP_BASE_URL else (request.url_root.rstrip("/"))
    try:
        # Reuse the Stripe Customer if we already have one for this user
        # so repeat purchases / cancellations land on the same record.
        customer_id = current_user.stripe_customer_id
        if not customer_id:
            try:
                cust = stripe.Customer.create(
                    email=current_user.email,
                    name=current_user.name or current_user.email.split("@")[0],
                    metadata={"user_id": str(current_user.id)},
                )
                customer_id = cust.id
                current_user.stripe_customer_id = customer_id
                db.session.commit()
            except Exception as _ce:
                print(f"[stripe] customer create failed: {_ce}")
                customer_id = None

        kwargs = dict(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=str(current_user.id),
            metadata={"user_id": str(current_user.id), "email": current_user.email},
            success_url=base + "/settings?pro=success",
            cancel_url=base + "/settings?pro=canceled",
            allow_promotion_codes=True,
        )
        if customer_id:
            kwargs["customer"] = customer_id
        else:
            kwargs["customer_email"] = current_user.email
        sess = stripe.checkout.Session.create(**kwargs)
    except Exception as e:
        print(f"[stripe] checkout create failed: {e}")
        return flask.jsonify({"status": "error", "message": str(e)}), 500
    return flask.jsonify({"status": "ok", "url": sess.url})


@app.route("/api/pro/billing-portal", methods=["POST"])
def api_pro_billing_portal():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    stripe = _stripe()
    if not stripe or not current_user.stripe_customer_id:
        return flask.jsonify({
            "status": "error",
            "message": "No Stripe customer is attached to this account yet."
        }), 400
    base = APP_BASE_URL.rstrip("/") if APP_BASE_URL else request.url_root.rstrip("/")
    try:
        portal = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=base + "/settings#proCard",
        )
        return flask.jsonify({"status": "ok", "url": portal.url})
    except Exception as e:
        print(f"[stripe] billing portal failed: {e}")
        return flask.jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/stripe/status", methods=["GET"])
def api_stripe_status():
    return flask.jsonify({
        "status": "ok",
        "enabled": feature_enabled("stripe"),
        "library_available": _stripe() is not None,
        "secret_configured": bool(os.getenv("STRIPE_SECRET_KEY")),
        "price_id": os.getenv("STRIPE_PRICE_ID") or STRIPE_DEFAULT_PRICE_ID,
        "webhook_configured": bool(os.getenv("STRIPE_WEBHOOK_SECRET")),
        "portal_available": current_user.is_authenticated and bool(getattr(current_user, "stripe_customer_id", None)),
    })


@app.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Stripe → IntelliPlan webhook. Promotes a user to Pro on
    `checkout.session.completed` and downgrades on subscription
    cancellation. Webhook signature is verified with STRIPE_WEBHOOK_SECRET."""
    stripe = _stripe()
    if not stripe:
        return ("stripe disabled", 200)
    payload = request.get_data(as_text=False)
    sig = request.headers.get("Stripe-Signature", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig, secret)
        else:
            event = json.loads(payload.decode("utf-8"))
    except Exception as e:
        print(f"[stripe webhook] bad payload/signature: {e}")
        return ("invalid", 400)
    etype = event.get("type") if isinstance(event, dict) else event["type"]
    data = (event.get("data") if isinstance(event, dict) else event["data"]).get("object", {})
    try:
        if etype == "checkout.session.completed":
            uid = data.get("client_reference_id") or (data.get("metadata") or {}).get("user_id")
            email = data.get("customer_email") or (data.get("metadata") or {}).get("email")
            user = (User.query.get(int(uid)) if uid and str(uid).isdigit() else None) \
                or (User.query.filter_by(email=(email or "").lower()).first() if email else None)
            if user:
                base_until = user.pro_until if (user.pro_until and user.pro_until > datetime.utcnow()) else datetime.utcnow()
                user.pro_until = base_until + timedelta(days=31)
                user.tier = "pro"
                db.session.commit()
                print(f"[stripe webhook] {user.email} → pro until {user.pro_until}")
        elif etype in ("customer.subscription.deleted", "invoice.payment_failed"):
            customer_id = data.get("customer")
            email = (data.get("customer_email") or "").lower()
            user = None
            if customer_id:
                user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if not user and email:
                user = User.query.filter_by(email=email).first()
            if user:
                # Leave them with whatever pro_until they already had — they
                # get the period they paid for. After expiry is_pro() flips
                # them to free automatically.
                print(f"[stripe webhook] subscription ended for {user.email}")
    except Exception as e:
        print(f"[stripe webhook] handler failure: {e}")
        try: db.session.rollback()
        except Exception: pass
    return ("ok", 200)


# ── PHONE + SMS REMINDERS ─────────────────────────────────────────
import re as _re_phone


def _send_email(to_addr, subject, body):
    """Send a plain-text email via SMTP_*. If SMTP isn't configured, log
    the message (the parental-consent link still gets printed to the
    server log so an admin can deliver it manually)."""
    host, port, user, pw, sender = _smtp_config()
    if not (host and to_addr):
        print(f"[email] SMTP not configured — would send to {to_addr}: {subject}\n{body}")
        return False
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            if user and pw:
                s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[email] send failed: {e}")
        return False


@app.route("/parent/consent")
def parent_consent():
    """Public landing page for the COPPA parental-consent link."""
    token = request.args.get("token", "").strip()
    if not token:
        return "Missing consent token.", 400
    user = User.query.filter_by(parent_consent_token=token).first()
    if not user:
        return render_template(
            "error.html",
            error_title="Link not valid",
            error_message="This consent link is no longer valid. Either the account has already been approved, or the link has expired."
        ) if os.path.exists(os.path.join(app.template_folder, "error.html")) else (
            "<h1>Consent link not valid</h1><p>This consent link is no longer valid.</p>", 404
        )
    if not user.parent_consent_granted:
        user.parent_consent_granted = True
        user.parent_consent_token = None  # one-shot
        db.session.commit()
    # Render a tiny inline confirmation — no template needed.
    return (
        "<!doctype html><meta charset='utf-8'><title>Consent granted</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:560px;margin:60px auto;padding:24px;"
        "background:#f9fafb;color:#111827;line-height:1.6;}a{color:#2563eb;}</style>"
        f"<h1>Thanks!</h1><p>You've approved <strong>{user.email}</strong>'s IntelliPlan account. "
        "They can now sign in and start using the app. You can revoke consent any time by emailing "
        "<a href='mailto:uanirudh0811@gmail.com'>uanirudh0811@gmail.com</a> — we'll delete the "
        "account and all associated data within 30 days.</p>"
        "<p><a href='/'>← Back to IntelliPlan</a></p>"
    )


def _normalise_phone(raw):
    """Return E.164-ish format like +15551234567, or empty string on bad input."""
    if not raw:
        return ""
    s = _re_phone.sub(r"[^0-9+]", "", raw)
    if not s:
        return ""
    # Default to +1 if user typed a bare 10-digit US number.
    if s.startswith("+"):
        return s[:32]
    if len(s) == 10:
        return "+1" + s
    if len(s) == 11 and s.startswith("1"):
        return "+" + s
    return ("+" + s)[:32]


# ── SMS over carrier email-to-SMS gateways ────────────────────────
# IntelliPlan does NOT use Twilio. Instead, we send the reminder body
# as a plain-text email to the recipient's carrier gateway address
# (e.g. 5551234567@tmomail.net for T-Mobile). Each user picks their
# carrier in Settings; T-Mobile is the default.
SMS_CARRIER_GATEWAYS = {
    "tmobile":   "tmomail.net",                # T-Mobile
    "att":       "txt.att.net",                # AT&T
    "verizon":   "vtext.com",                  # Verizon
    "sprint":    "messaging.sprintpcs.com",    # Sprint (legacy)
    "uscellular":"email.uscc.net",             # US Cellular
    "cricket":   "sms.cricketwireless.net",    # Cricket
    "metropcs":  "mymetropcs.com",             # Metro by T-Mobile
    "boost":     "sms.myboostmobile.com",      # Boost Mobile
    "googlefi":  "msg.fi.google.com",          # Google Fi
}


def _digits_only(s):
    """Return the bare digits of a phone string — strips +, spaces, etc."""
    return _re_phone.sub(r"[^0-9]", "", s or "")


def _smtp_config():
    """Resolve SMTP credentials from environment variables.

    Checks several name variants so Railway vars named HOST / USERNAME /
    PASSWORD work alongside the canonical SMTP_* names. Falls back to
    smtp.gmail.com automatically when the username is a Gmail address and
    no explicit host is set.
    """
    host = (os.getenv("SMTP_HOST")
            or os.getenv("MAIL_HOST")
            or os.getenv("EMAIL_HOST"))
    port = int(os.getenv("SMTP_PORT") or os.getenv("MAIL_PORT") or "587")
    user = (os.getenv("SMTP_USER")
            or os.getenv("SMTP_USERNAME")
            or os.getenv("MAIL_USERNAME")
            or os.getenv("EMAIL_USERNAME")
            or os.getenv("USERNAME"))
    pw = (os.getenv("SMTP_PASSWORD")
          or os.getenv("MAIL_PASSWORD")
          or os.getenv("EMAIL_PASSWORD")
          or os.getenv("PASSWORD"))
    sender = os.getenv("SMTP_FROM") or user or "no-reply@intelliplan.tech"

    # Auto-detect Gmail when no host is given but the address is @gmail.com
    if not host and user and "@gmail.com" in user.lower():
        host = "smtp.gmail.com"
        port = 587

    print(f"[smtp-config] host={'set' if host else 'MISSING'} "
          f"user={'set' if user else 'MISSING'} "
          f"pw={'set' if pw else 'MISSING'} port={port}")
    return host, port, user, pw, sender


def _sms_send_email_gateway(to_phone, body, carrier="tmobile"):
    """Send a short SMS by emailing the carrier's gateway address.
    Returns True if SMTP accepted the message. Safe no-op when SMTP
    isn't configured. Intended for personal notifications only.

    Accepted Railway variable names (any of these work):
      SMTP_HOST / MAIL_HOST / EMAIL_HOST
      SMTP_USER / SMTP_USERNAME / MAIL_USERNAME / EMAIL_USERNAME / USERNAME
      SMTP_PASSWORD / MAIL_PASSWORD / EMAIL_PASSWORD / PASSWORD
      SMTP_PORT / MAIL_PORT  (default 587)
    Gmail is auto-detected when USERNAME ends in @gmail.com and no host is set.
    """
    smtp_host, smtp_port, smtp_username, smtp_password, smtp_from = _smtp_config()

    digits = _digits_only(to_phone)
    # US carriers expect a 10-digit number; strip a leading 1 if present.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        print(f"[sms-email] refusing to send — number must be 10 digits, got {digits!r}")
        return False

    gateway = SMS_CARRIER_GATEWAYS.get((carrier or "tmobile").lower())
    if not gateway:
        print(f"[sms-email] unknown carrier {carrier!r} — falling back to T-Mobile")
        gateway = SMS_CARRIER_GATEWAYS["tmobile"]

    sms_recipient = f"{digits}@{gateway}"
    sms_message = (body or "")[:300]  # carriers truncate; keep it tight

    if not smtp_host:
        print(f"[sms-email] SMTP_HOST not set — would email {sms_recipient}: {sms_message}")
        return None, "SMTP host not configured"

    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = smtp_from
        msg["To"] = sms_recipient
        # Many gateways drop or prepend the subject — keep it short or empty.
        msg["Subject"] = ""
        msg.set_content(sms_message)
        clean_pw = smtp_password.replace(" ", "") if smtp_password else ""
        print(f"[sms-email] attempting login as {smtp_username}, pw_len={len(clean_pw)}")
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.starttls()
            if smtp_username and clean_pw:
                s.login(smtp_username, clean_pw)
            s.send_message(msg)
        print(f"[sms-email] sent to {sms_recipient}")
        return True, None
    except Exception as e:
        print(f"[sms-email] send to {sms_recipient} failed: {e}")
        return None, str(e)


def _sms_send_for_user(user, body):
    """Thin wrapper that picks up the user's saved carrier preference.
    Returns (True, None) on success or (None, error_str) on failure."""
    if not user or not user.phone:
        return None, "no phone on account"
    carrier = (getattr(user, "sms_carrier", None) or "tmobile").lower()
    return _sms_send_email_gateway(user.phone, body, carrier=carrier)


def _twilio_send(to_phone, body):
    """Deprecated shim — kept so any older call site still resolves.
    Always uses the email-gateway path (T-Mobile as a safe default)."""
    return _sms_send_email_gateway(to_phone, body, carrier="tmobile")
    # The block below is intentionally unreachable; left for reviewers.
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    src = os.getenv("TWILIO_FROM_NUMBER")
    if not (sid and token and src and to_phone):
        return False
    try:
        from twilio.rest import Client as _TwClient
        _TwClient(sid, token).messages.create(to=to_phone, from_=src, body=body[:600])
        return True
    except Exception as e:
        print(f"[twilio] send failed: {e}")
        return False


def _profile_payload(u):
    return {
        "status": "ok",
        "phone": u.phone or "",
        "sms_reminders_opt_in": bool(getattr(u, "sms_reminders_opt_in", False)),
        "push_reminders_opt_in": bool(getattr(u, "push_reminders_opt_in", False)),
        "reminder_lead_minutes": int(getattr(u, "reminder_lead_minutes", 60) or 60),
        "sms_carrier": (getattr(u, "sms_carrier", "tmobile") or "tmobile"),
        "sms_carrier_choices": list(SMS_CARRIER_GATEWAYS.keys()),
    }


@app.route("/api/profile/phone", methods=["GET", "POST"])
def api_profile_phone():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"}), 401
    if request.method == "GET":
        return flask.jsonify(_profile_payload(current_user))
    body = request.get_json(silent=True) or {}
    if "phone" in body:
        normalised = _normalise_phone(body.get("phone") or "")
        current_user.phone = normalised or None
    if "sms_reminders_opt_in" in body:
        current_user.sms_reminders_opt_in = bool(body.get("sms_reminders_opt_in"))
        # Clearing the phone means you can't be reminded — also clear the flag.
        if not current_user.phone:
            current_user.sms_reminders_opt_in = False
    if "push_reminders_opt_in" in body:
        current_user.push_reminders_opt_in = bool(body.get("push_reminders_opt_in"))
    if "reminder_lead_minutes" in body:
        try:
            lead = int(body.get("reminder_lead_minutes") or 60)
            # Clamp to 5 minutes .. 7 days
            current_user.reminder_lead_minutes = max(5, min(lead, 10080))
        except (TypeError, ValueError):
            pass
    if "sms_carrier" in body:
        carrier = (body.get("sms_carrier") or "tmobile").strip().lower()
        # Silently fall back to T-Mobile for unknown values.
        if carrier not in SMS_CARRIER_GATEWAYS:
            carrier = "tmobile"
        current_user.sms_carrier = carrier
    db.session.commit()
    return flask.jsonify(_profile_payload(current_user))


@app.route("/api/profile/phone/test", methods=["POST"])
def api_profile_phone_test():
    """Admin / opt-in user helper: send a one-line test SMS."""
    if not current_user.is_authenticated or not current_user.phone:
        return flask.jsonify({"status": "error", "message": "phone not set"}), 400
    ok, err = _sms_send_for_user(current_user, "IntelliPlan reminders are active for this number.")
    return flask.jsonify({"status": "ok" if ok else "error",
                          "message": "Sent! Check your phone in a moment." if ok else (err or "Send failed")})


def _send_push_to_user(user_id, payload):
    """Send a webpush payload to every subscription tied to user_id.
    Returns the count of successful deliveries."""
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    if not subs:
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return 0
    private_key = os.getenv("VAPID_PRIVATE_KEY")
    if not private_key:
        return 0
    claims = {"sub": f"mailto:{os.getenv('VAPID_EMAIL', 'hello@intelliplan.tech')}"}
    ok = 0
    for sub in list(subs):
        try:
            webpush(
                subscription_info=json.loads(sub.subscription_json),
                data=json.dumps(payload),
                vapid_private_key=private_key,
                vapid_claims=claims,
            )
            ok += 1
        except Exception as e:
            msg = str(e)
            # 410 Gone / 404 mean the browser unsubscribed — drop the row.
            if "410" in msg or "404" in msg:
                try: db.session.delete(sub); db.session.commit()
                except Exception: pass
            else:
                print(f"[push] webpush failed: {e}")
    return ok


def _upcoming_tasks_for(user, lead_minutes):
    """Return ManualTasks due within the next `lead_minutes`, not yet done."""
    now = datetime.utcnow()
    cutoff = now + timedelta(minutes=int(lead_minutes or 60))
    rows = ManualTask.query.filter_by(user_id=user.id, done=False).all()
    upcoming = []
    for row in rows:
        if not row.due_date:
            continue
        try:
            due = datetime.fromisoformat(str(row.due_date)[:19])
        except (TypeError, ValueError):
            try: due = datetime.fromisoformat(str(row.due_date)[:10])
            except (TypeError, ValueError): continue
        if now <= due <= cutoff:
            upcoming.append((row, due))
    upcoming.sort(key=lambda p: p[1])
    return upcoming


def _send_reminders_for_user(user, mark_sent=True, force=False):
    """Find a user's upcoming tasks and send SMS + push for each, skipping
    any task already reminded.  Returns a dict counting what went out."""
    lead = int(getattr(user, "reminder_lead_minutes", 60) or 60)
    upcoming = _upcoming_tasks_for(user, lead)
    sent = {"sms": 0, "push": 0, "skipped": 0, "tasks": 0}
    if not upcoming:
        return sent
    want_sms  = bool(getattr(user, "sms_reminders_opt_in", False) and user.phone)
    want_push = bool(getattr(user, "push_reminders_opt_in", False))
    for task, due in upcoming:
        sent["tasks"] += 1
        key = f"manual:{task.id}"
        for channel, want in (("sms", want_sms), ("push", want_push)):
            if not want:
                continue
            if not force:
                already = ReminderSent.query.filter_by(
                    user_id=user.id, task_key=key, channel=channel
                ).first()
                if already:
                    sent["skipped"] += 1
                    continue
            mins = max(1, int((due - datetime.utcnow()).total_seconds() // 60))
            body = f"⏰ {task.title} is due in {mins} min ({task.course})."
            ok = False
            if channel == "sms":
                ok, _ = _sms_send_for_user(user, body[:300])
            else:
                ok = _send_push_to_user(user.id, {
                    "title": "Assignment due soon",
                    "body": body,
                    "url": "/dashboard",
                }) > 0
            if ok:
                sent[channel] += 1
                if mark_sent:
                    try:
                        db.session.add(ReminderSent(user_id=user.id, task_key=key, channel=channel))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
    return sent


@app.route("/api/reminders/preview", methods=["POST"])
def api_reminders_preview():
    """User-facing test: ignore the dedupe table and fire any pending
    reminders for the current user right now, so the user can verify
    SMS/push delivery without waiting for the cron to run."""
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"}), 401
    result = _send_reminders_for_user(current_user, mark_sent=False, force=True)
    msg = (
        f"Found {result['tasks']} upcoming task(s). "
        f"Sent {result['sms']} SMS, {result['push']} push notification(s)."
        if result["tasks"] else
        "Nothing due within your reminder window yet."
    )
    return flask.jsonify({"status": "ok", "result": result, "message": msg})


@app.route("/cron/send-reminders", methods=["GET", "POST"])
def cron_send_reminders():
    """Hit by Railway cron / external scheduler. Requires CRON_SECRET in
    a `secret` query param (or X-Cron-Secret header) to prevent abuse."""
    expected = os.getenv("CRON_SECRET", "")
    provided = request.args.get("secret") or request.headers.get("X-Cron-Secret") or ""
    if not expected or provided != expected:
        return flask.jsonify({"status": "error", "message": "unauthorized"}), 401
    total = {"users": 0, "sms": 0, "push": 0, "tasks": 0}
    candidates = User.query.filter(
        (User.sms_reminders_opt_in.is_(True)) | (User.push_reminders_opt_in.is_(True))
    ).all()
    for u in candidates:
        r = _send_reminders_for_user(u, mark_sent=True, force=False)
        if r["tasks"]:
            total["users"] += 1
            total["sms"]  += r["sms"]
            total["push"] += r["push"]
            total["tasks"] += r["tasks"]
    return flask.jsonify({"status": "ok", "summary": total})


# ── DAILY CHECK-IN CHEST (Duolingo-style) ─────────────────────────
@app.route("/api/streak/daily-claim", methods=["GET"])
def api_daily_claim_status():
    """Read-only check used by the dashboard on load. Returns whether the
    chest has been claimed today so we can render it blacked out instead
    of waiting for the user to click and discover it themselves."""
    if not is_logged_in():
        return flask.jsonify({"status": "ok", "claimed": False, "logged_in": False})
    try:
        p = get_study_profile(
            user_id=current_user.id if current_user.is_authenticated else None,
            guest_id=None if current_user.is_authenticated else get_guest_session_id(),
        )
        today = datetime.utcnow().date().isoformat()
        last_claim = (p.last_daily_claim or "") if hasattr(p, "last_daily_claim") else ""
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500
    streak = int(getattr(p, "streak_count", 0) or 0)
    reward = 10 + min(40, streak * 2)
    return flask.jsonify({
        "status": "ok",
        "claimed": last_claim == today,
        "logged_in": True,
        "reward_if_claimed_now": reward,
        "last_claim": last_claim,
    })


@app.route("/api/streak/daily-claim", methods=["POST"])
def api_daily_claim():
    """One-shot daily Sparks claim. Returns the reward + whether it was
    already claimed today. Front-end uses this for the chest animation
    on the dashboard."""
    if not is_logged_in():
        return flask.jsonify({"status": "error"}), 401
    try:
        p = get_study_profile(
            user_id=current_user.id if current_user.is_authenticated else None,
            guest_id=None if current_user.is_authenticated else get_guest_session_id(),
        )
        today = datetime.utcnow().date().isoformat()
        last_claim = (p.last_daily_claim or "") if hasattr(p, "last_daily_claim") else ""
        # last_daily_claim might not exist yet — try/except handles that.
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500
    already = (last_claim == today)
    if already:
        return flask.jsonify({"status": "ok", "claimed": False, "reward": 0,
                              "message": "Already claimed today. Come back tomorrow!"})
    # Reward scales with streak length to reward consistency.
    streak = int(getattr(p, "streak_count", 0) or 0)
    reward = 10 + min(40, streak * 2)
    try:
        p.spark_balance = int(getattr(p, "spark_balance", 0) or 0) + reward
        if hasattr(p, "last_daily_claim"):
            p.last_daily_claim = today
        db.session.commit()
    except Exception:
        try: db.session.rollback()
        except Exception: pass
    return flask.jsonify({"status": "ok", "claimed": True, "reward": reward,
                          "spark_balance": getattr(p, "spark_balance", 0)})


# ── LIVE STUDY SESSIONS (audio/video + materials) ─────────────────
def _live_session_to_dict(s):
    return {
        "id": s.id,
        "title": s.title,
        "topic": s.topic or "",
        "room_url": f"https://meet.jit.si/intelliplan-live-{s.room_slug}",
        "room_slug": s.room_slug,
        "audio_only": bool(s.audio_only),
        "video_enabled": bool(s.video_enabled),
        "audio_enabled": bool(s.audio_enabled),
        "materials": s.materials or "",
        "is_open": bool(s.is_open),
        "owner_id": s.owner_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "is_owner": current_user.is_authenticated and s.owner_id == current_user.id,
        "invite_url": (APP_BASE_URL.rstrip("/") if APP_BASE_URL else "") + f"/live/{s.id}",
    }


@app.route("/live/<int:session_id>")
def live_session_page(session_id):
    """Public-by-link landing for a live session. Anyone with the link
    can join. Used for sharing invites among IntelliPlan accounts."""
    if not feature_enabled("live_sessions"):
        return render_template("error.html", error_code=404, error_id="LIVE-DISABLED"), 404
    s = LiveSession.query.get(session_id)
    if not s:
        return render_template("error.html", error_code=404, error_id="LIVE-NOT-FOUND",
                               message="That study room no longer exists."), 404
    return render_template("live_session.html", active_page="study", session=_live_session_to_dict(s))


@app.route("/api/live", methods=["POST"])
def api_create_live_session():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    if not feature_enabled("live_sessions"):
        return flask.jsonify({"status": "error", "message": "feature disabled"}), 503
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "Study session").strip()[:160]
    topic = (body.get("topic") or "").strip()[:160]
    audio_only = bool(body.get("audio_only"))
    slug = secrets_module.token_urlsafe(8).replace("-", "").replace("_", "")[:12]
    s = LiveSession(
        owner_id=current_user.id, title=title, topic=topic,
        audio_only=audio_only, video_enabled=not audio_only, audio_enabled=True,
        room_slug=slug, materials=(body.get("materials") or "")[:8000],
    )
    db.session.add(s); db.session.commit()
    return flask.jsonify({"status": "ok", "session": _live_session_to_dict(s)})


@app.route("/api/live/<int:session_id>", methods=["GET"])
def api_get_live_session(session_id):
    s = LiveSession.query.get(session_id)
    if not s:
        return flask.jsonify({"status": "error"}), 404
    return flask.jsonify({"status": "ok", "session": _live_session_to_dict(s)})


@app.route("/api/live/<int:session_id>", methods=["PATCH"])
def api_update_live_session(session_id):
    s = LiveSession.query.get(session_id)
    if not s:
        return flask.jsonify({"status": "error"}), 404
    if not current_user.is_authenticated or s.owner_id != current_user.id:
        return flask.jsonify({"status": "error", "message": "not the owner"}), 403
    body = request.get_json(silent=True) or {}
    if "materials" in body:        s.materials = str(body["materials"])[:8000]
    if "title" in body:            s.title = str(body["title"]).strip()[:160] or s.title
    if "topic" in body:            s.topic = str(body["topic"]).strip()[:160]
    if "audio_only" in body:       s.audio_only = bool(body["audio_only"])
    if "video_enabled" in body:    s.video_enabled = bool(body["video_enabled"])
    if "audio_enabled" in body:    s.audio_enabled = bool(body["audio_enabled"])
    if "is_open" in body:          s.is_open = bool(body["is_open"])
    db.session.commit()
    return flask.jsonify({"status": "ok", "session": _live_session_to_dict(s)})


@app.route("/api/live", methods=["GET"])
def api_list_live_sessions():
    """Owner's live rooms (so they can rejoin)."""
    if not current_user.is_authenticated:
        return flask.jsonify({"sessions": []})
    rows = LiveSession.query.filter_by(owner_id=current_user.id, is_open=True) \
        .order_by(LiveSession.created_at.desc()).limit(20).all()
    return flask.jsonify({"sessions": [_live_session_to_dict(s) for s in rows]})


def _grant_referral_bonus(new_user):
    """Apply Pro day bonuses to both the inviter and the new signup."""
    ref_id = session.pop("pending_referral", None) if session else None
    if not ref_id:
        return
    inviter = User.query.get(ref_id)
    if not inviter or inviter.id == new_user.id:
        return
    new_user.referred_by_id = inviter.id
    for u in (inviter, new_user):
        base = u.pro_until if (u.pro_until and u.pro_until > datetime.utcnow()) else datetime.utcnow()
        u.pro_until = base + timedelta(days=PRO_REFERRAL_BONUS_DAYS)
        u.tier = "pro"
    db.session.commit()


def _apply_pending_group_join():
    """Run after login_user to consume a pending /groups/invite/<id> click."""
    try:
        gid = session.pop("pending_group_join", None) if session else None
        if not gid:
            return None
        existing = StudyGroupMember.query.filter_by(group_id=gid, user_id=current_user.id).first()
        if not existing:
            db.session.add(StudyGroupMember(group_id=gid, user_id=current_user.id, role="member"))
            db.session.commit()
        return gid
    except Exception:
        return None





def _groq():
    return _groq_client()


def _owner_filter(model):
    """Build a SQLAlchemy filter that scopes a model query to the current
    user or guest session."""
    if current_user.is_authenticated:
        return model.user_id == current_user.id
    gid = get_guest_session_id()
    return model.guest_session_id == gid


# ── GRADE MODELLER UPGRADES ─────────────────────────────────
# Realistic feasibility + Simulate + Reset are implemented in JS for
# instant feedback; this endpoint is the optional server-side check that
# returns the highest-achievable grade when the user asks for one that
# math forbids.

def _gradebook_compute(courses, simulated=None):
    """Given courses [{name, weight_total, categories:[{name,weight,assignments:[...]}]}],
    return overall course grades and the weighted final."""
    simulated = simulated or {}
    out_courses = []
    overall_total = 0.0
    overall_weight = 0.0
    for course in courses or []:
        cw = float(course.get("weight") or 0)
        cat_grade_total = 0.0
        cat_weight_total = 0.0
        for cat in course.get("categories") or []:
            w = float(cat.get("weight") or 0)
            earned = 0.0
            possible = 0.0
            for a in cat.get("assignments") or []:
                key = str(a.get("id") or a.get("title") or "")
                sim = simulated.get(key)
                if sim is not None:
                    pts = float(sim.get("points_earned", 0))
                    poss = float(sim.get("points_possible", a.get("points_possible") or 0))
                    earned += pts
                    possible += poss
                elif a.get("graded"):
                    earned += float(a.get("points_earned") or 0)
                    possible += float(a.get("points_possible") or 0)
            if possible > 0 and w > 0:
                cat_grade_total += (earned / possible) * w
                cat_weight_total += w
        course_pct = (cat_grade_total / cat_weight_total) * 100 if cat_weight_total > 0 else None
        out_courses.append({"name": course.get("name"), "grade": course_pct, "weight": cw})
        if course_pct is not None and cw > 0:
            overall_total += course_pct * cw
            overall_weight += cw
    overall = (overall_total / overall_weight) if overall_weight > 0 else None
    return {"courses": out_courses, "overall": overall}


@app.route("/api/grademodel/feasibility", methods=["POST"])
@limiter.limit("30 per minute")
def grademodel_feasibility():
    """Decide whether a target grade is mathematically reachable.

    Body:
      {
        "courses": [...],            # full gradebook data (same shape as gradebook page)
        "target": 92,                # desired course or overall percent
        "course_name": "AP Calc BC", # optional — limit to one course
        "max_score_pct": 100         # cap on per-assignment performance (e.g. 100% for perfect)
      }
    """
    body = request.get_json(silent=True) or {}
    courses = body.get("courses") or []
    target = float(body.get("target") or 100)
    course_name = body.get("course_name")
    max_pct = float(body.get("max_score_pct") or 100)

    sims = {}
    for course in courses:
        if course_name and course.get("name") != course_name:
            continue
        for cat in course.get("categories") or []:
            for a in cat.get("assignments") or []:
                if a.get("graded"):
                    continue
                key = str(a.get("id") or a.get("title") or "")
                poss = float(a.get("points_possible") or 0)
                sims[key] = {
                    "points_earned": poss * (max_pct / 100.0),
                    "points_possible": poss,
                    "auto": True,
                }
    best = _gradebook_compute(courses, simulated=sims)
    best_value = None
    if course_name:
        match = next((c for c in best["courses"] if c["name"] == course_name), None)
        best_value = match["grade"] if match else None
    else:
        best_value = best["overall"]
    reachable = best_value is not None and best_value + 1e-6 >= target
    return flask.jsonify({
        "status": "ok",
        "reachable": bool(reachable),
        "best_possible": best_value,
        "target": target,
        "simulated_assignments": sims,
    })


@app.route("/api/grademodel/simulate", methods=["POST"])
@limiter.limit("60 per minute")
def grademodel_simulate():
    """Apply user-supplied simulated scores to UPCOMING assignments only.

    Body: {courses, simulated: {assignment_id_or_title: {points_earned, points_possible}}}
    Existing graded assignments are never modified — the helper just ignores
    sim entries for already-graded items.
    """
    body = request.get_json(silent=True) or {}
    courses = body.get("courses") or []
    simulated_in = body.get("simulated") or {}
    safe_sim = {}
    for course in courses:
        for cat in course.get("categories") or []:
            for a in cat.get("assignments") or []:
                if a.get("graded"):
                    continue
                key = str(a.get("id") or a.get("title") or "")
                if key in simulated_in:
                    s = simulated_in[key]
                    safe_sim[key] = {
                        "points_earned": float(s.get("points_earned", 0) or 0),
                        "points_possible": float(s.get("points_possible") or a.get("points_possible") or 0),
                    }
    result = _gradebook_compute(courses, simulated=safe_sim)
    return flask.jsonify({"status": "ok", "result": result, "applied": safe_sim})


# ── LESSON RECORDER ─────────────────────────────────────────
LESSON_UPLOAD_FOLDER = os.path.join(app.root_path, "uploads", "lessons")
os.makedirs(LESSON_UPLOAD_FOLDER, exist_ok=True)
LESSON_ALLOWED_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".mp4", ".webm", ".mov", ".mkv"}
LESSON_AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg"}


GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"


def _transcribe_lesson(lesson):
    """Real audio/video → text transcription via Groq's Whisper endpoint.

    Groq's audio.transcriptions.create accepts mp3/m4a/wav/ogg/flac and
    will also pull audio off mp4/webm containers. Free-tier accounts have
    a per-minute audio quota, so we cap to ~25 MB which matches our
    Flask MAX_CONTENT_LENGTH.
    """
    if not lesson.stored_filename:
        return ""
    path = os.path.join(LESSON_UPLOAD_FOLDER, lesson.stored_filename)
    if not os.path.exists(path):
        return ""
    try:
        client = _groq()
        with open(path, "rb") as fh:
            resp = client.audio.transcriptions.create(
                model=GROQ_WHISPER_MODEL,
                file=(lesson.original_filename or lesson.stored_filename, fh.read()),
                response_format="text",
            )
        text = resp if isinstance(resp, str) else getattr(resp, "text", "") or ""
        return (text or "").strip()
    except Exception as e:
        print(f"[lesson {lesson.id}] transcription failed: {e}")
        return ""


def _summarize_lesson_async(lesson_id):
    """Two-stage pipeline:

    1. If we don't already have a transcript, run the audio through Groq
       Whisper and persist the text.
    2. Ask the chat model for a clear, detailed study-style summary.
    """
    lesson = Lesson.query.get(lesson_id)
    if not lesson:
        return
    transcript = (lesson.transcript or "").strip()
    if not transcript:
        transcript = _transcribe_lesson(lesson)
        if transcript:
            lesson.transcript = transcript
            db.session.commit()
    seed = transcript
    if not seed:
        seed = (
            f"Title: {lesson.title}\nCourse: {lesson.course or 'general'}\n"
            f"Tags: {', '.join(lesson.tag_list())}\n"
            "No transcript was available — produce a study-friendly placeholder "
            "summary describing what a lesson with this title and tags likely "
            "covers, and list 5 likely sub-topics."
        )
    try:
        client = _groq()
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You write simple, clear, detailed study summaries for student-uploaded lesson recordings. Output plain markdown with sections: TL;DR (1 sentence), Key Points (bullets), Examples / Vocabulary (bullets), Suggested Practice (bullets)."},
                {"role": "user", "content": seed[:14000]},
            ],
            temperature=0.4,
            max_tokens=900,
        )
        summary = resp.choices[0].message.content.strip()
        lesson.summary = summary
        lesson.summary_status = "ready"
    except Exception as e:
        lesson.summary_status = "failed"
        lesson.summary = f"Summary generation failed: {e}"
    db.session.commit()


@app.route("/lessons")
def lessons_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    if not feature_enabled("lessons"):
        return render_template("error.html", error_code=503, error_id="LESSONS-DISABLED",
                               message="Lesson Library is temporarily disabled."), 503
    return render_template("lessons.html", active_page="lessons")


@app.route("/api/lessons", methods=["GET"])
def api_list_lessons():
    rows = Lesson.query.filter(_owner_filter(Lesson)).order_by(Lesson.created_at.desc()).all()
    return flask.jsonify({"lessons": [l.to_dict() for l in rows]})


FREE_LESSON_LIMIT = 3
FREE_GROUP_OWNED_LIMIT = 1


@app.route("/api/lessons", methods=["POST"])
@limiter.limit("12 per minute")
def api_upload_lesson():
    upload = request.files.get("file")
    title = (request.form.get("title") or "").strip()[:255]
    course = (request.form.get("course") or "").strip()[:128]
    tags_raw = (request.form.get("tags") or "").strip()
    transcript = (request.form.get("transcript") or "").strip()
    if not title:
        return flask.jsonify({"status": "error", "message": "title required"}), 400
    if not upload or not upload.filename:
        return flask.jsonify({"status": "error", "message": "file required"}), 400
    # Free-tier cap: 3 lessons total. Pro = unlimited.
    if current_user.is_authenticated and not is_pro(current_user):
        existing = Lesson.query.filter_by(user_id=current_user.id).count()
        if existing >= FREE_LESSON_LIMIT:
            return flask.jsonify({
                "status": "error",
                "code": "pro_required",
                "message": f"Free tier is capped at {FREE_LESSON_LIMIT} lessons. Upgrade to Pro for unlimited uploads."
            }), 402
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in LESSON_ALLOWED_EXT:
        return flask.jsonify({"status": "error", "message": f"unsupported type {ext}"}), 400
    safe_root = secure_filename(os.path.splitext(upload.filename)[0])[:120] or "lesson"
    stored = f"{uuid.uuid4().hex}_{safe_root}{ext}"
    path = os.path.join(LESSON_UPLOAD_FOLDER, stored)
    upload.save(path)
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()][:12]
    lesson = Lesson(
        user_id=current_user.id if current_user.is_authenticated else None,
        guest_session_id=None if current_user.is_authenticated else get_guest_session_id(),
        title=title,
        course=course,
        tags=json.dumps(tags),
        media_kind="audio" if ext in LESSON_AUDIO_EXT else "video",
        original_filename=upload.filename[:255],
        stored_filename=stored,
        mime_type=upload.mimetype or "",
        transcript=transcript[:60000],
        summary_status="pending",
    )
    db.session.add(lesson)
    db.session.commit()
    # Kick the summary in-process (best effort; runs synchronously but
    # cheaply because the prompt is small). For long videos a background
    # worker would be the next upgrade.
    _summarize_lesson_async(lesson.id)
    return flask.jsonify({"status": "ok", "lesson": lesson.to_dict()})


@app.route("/lessons/<int:lesson_id>/stream")
def lesson_stream(lesson_id):
    lesson = Lesson.query.filter(Lesson.id == lesson_id, _owner_filter(Lesson)).first()
    if not lesson or not lesson.stored_filename:
        return flask.jsonify({"status": "error", "message": "not found"}), 404
    return send_from_directory(LESSON_UPLOAD_FOLDER, lesson.stored_filename, as_attachment=False)


@app.route("/api/lessons/<int:lesson_id>", methods=["DELETE"])
def api_delete_lesson(lesson_id):
    lesson = Lesson.query.filter(Lesson.id == lesson_id, _owner_filter(Lesson)).first()
    if not lesson:
        return flask.jsonify({"status": "error"}), 404
    try:
        if lesson.stored_filename:
            os.remove(os.path.join(LESSON_UPLOAD_FOLDER, lesson.stored_filename))
    except Exception:
        pass
    db.session.delete(lesson)
    db.session.commit()
    return flask.jsonify({"status": "ok"})


@app.route("/api/lessons/<int:lesson_id>/resummarize", methods=["POST"])
@limiter.limit("6 per minute")
def api_resummarize_lesson(lesson_id):
    lesson = Lesson.query.filter(Lesson.id == lesson_id, _owner_filter(Lesson)).first()
    if not lesson:
        return flask.jsonify({"status": "error"}), 404
    body = request.get_json(silent=True) or {}
    if body.get("transcript"):
        lesson.transcript = str(body.get("transcript"))[:60000]
        db.session.commit()
    lesson.summary_status = "pending"
    db.session.commit()
    _summarize_lesson_async(lesson.id)
    return flask.jsonify({"status": "ok", "lesson": Lesson.query.get(lesson_id).to_dict()})


# ── STUDY GROUPS ────────────────────────────────────────────
def _group_match_score(group, prefs):
    """Tiny matcher: + for matching topic substring, level, style."""
    score = 0
    topic = (prefs.get("topic") or "").lower().strip()
    if topic and topic in (group.topic or "").lower():
        score += 3
    if prefs.get("level") and (prefs["level"] == group.level or group.level == "any"):
        score += 1
    if prefs.get("style") and (prefs["style"] == group.style or group.style == "any"):
        score += 1
    return score


@app.route("/groups")
def groups_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    if not feature_enabled("groups"):
        return render_template("error.html", error_code=503, error_id="GROUPS-DISABLED",
                               message="Study Groups is temporarily disabled."), 503
    return render_template("groups.html", active_page="groups")


@app.route("/api/groups", methods=["GET"])
def api_list_groups():
    """List public groups + groups the user belongs to."""
    public_q = StudyGroup.query.filter_by(visibility="public").order_by(StudyGroup.created_at.desc()).limit(50).all()
    mine_ids = set()
    if current_user.is_authenticated:
        mine_ids = {m.group_id for m in StudyGroupMember.query.filter_by(user_id=current_user.id).all()}

    def _ser(g):
        return {
            "id": g.id,
            "name": g.name,
            "topic": g.topic,
            "level": g.level,
            "style": g.style,
            "visibility": g.visibility,
            "description": g.description or "",
            "meeting_url": g.meeting_url or "",
            "next_meeting_at": g.next_meeting_at.isoformat() if g.next_meeting_at else None,
            "next_meeting_topic": g.next_meeting_topic or "",
            "member_count": StudyGroupMember.query.filter_by(group_id=g.id).count(),
            "is_member": g.id in mine_ids,
        }

    prefs = {
        "topic": request.args.get("topic", ""),
        "level": request.args.get("level", ""),
        "style": request.args.get("style", ""),
    }
    scored = [(_group_match_score(g, prefs), g) for g in public_q]
    scored.sort(key=lambda x: (-x[0], -(x[1].id or 0)))
    return flask.jsonify({
        "groups": [_ser(g) for _, g in scored],
        "my_group_ids": list(mine_ids),
    })


@app.route("/api/groups", methods=["POST"])
def api_create_group():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()[:120]
    if not name:
        return flask.jsonify({"status": "error", "message": "name required"}), 400
    # Free-tier cap: at most 1 owned group. Pro can create unlimited.
    if not is_pro(current_user):
        owned = StudyGroup.query.filter_by(owner_id=current_user.id).count()
        if owned >= FREE_GROUP_OWNED_LIMIT:
            return flask.jsonify({
                "status": "error",
                "code": "pro_required",
                "message": "Free tier can own one study group. Upgrade to Pro to create more."
            }), 402
    room_slug = secrets_module.token_urlsafe(8).replace("-", "").replace("_", "")
    group = StudyGroup(
        name=name,
        topic=(body.get("topic") or "").strip()[:120],
        level=(body.get("level") or "any")[:32],
        style=(body.get("style") or "any")[:32],
        visibility=(body.get("visibility") or "public")[:16],
        description=(body.get("description") or "").strip()[:2000],
        owner_id=current_user.id,
        meeting_url=f"https://meet.jit.si/intelliplan-{room_slug}",
    )
    db.session.add(group)
    db.session.commit()
    db.session.add(StudyGroupMember(group_id=group.id, user_id=current_user.id, role="owner"))
    db.session.commit()
    # Async-ish AI plan suggestion (synchronous + cheap).
    try:
        prompt = (
            f"You are an academic coach. Suggest a 4-week study plan for a small group studying \"{group.topic or group.name}\".\n"
            f"Level: {group.level}. Study style: {group.style}.\n"
            "Output plain markdown: Week 1 / Week 2 / Week 3 / Week 4 with 3 bullets each."
        )
        resp = _groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=700,
        )
        group.suggested_plan = resp.choices[0].message.content.strip()
        db.session.commit()
    except Exception:
        pass
    return flask.jsonify({"status": "ok", "id": group.id})


@app.route("/api/groups/<int:group_id>", methods=["GET"])
def api_get_group(group_id):
    g = StudyGroup.query.get(group_id)
    if not g:
        return flask.jsonify({"status": "error"}), 404
    members = []
    for m in StudyGroupMember.query.filter_by(group_id=group_id).all():
        u = User.query.get(m.user_id)
        members.append({
            "user_id": m.user_id,
            "role": m.role,
            "name": (u.name if u else None) or (u.email if u else "Member"),
        })
    is_member = current_user.is_authenticated and any(m["user_id"] == current_user.id for m in members)
    invite_url = (APP_BASE_URL.rstrip("/") + url_for("groups_invite", group_id=g.id)) if APP_BASE_URL else url_for("groups_invite", group_id=g.id)
    return flask.jsonify({
        "id": g.id,
        "name": g.name,
        "topic": g.topic,
        "level": g.level,
        "style": g.style,
        "description": g.description or "",
        "shared_notes": g.shared_notes or "",
        "meeting_url": g.meeting_url or "",
        "next_meeting_at": g.next_meeting_at.isoformat() if g.next_meeting_at else None,
        "next_meeting_topic": g.next_meeting_topic or "",
        "suggested_plan": g.suggested_plan or "",
        "members": members,
        "is_member": is_member,
        "is_owner": current_user.is_authenticated and g.owner_id == current_user.id,
        "invite_url": invite_url,
    })


@app.route("/api/groups/<int:group_id>/join", methods=["POST"])
def api_join_group(group_id):
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    g = StudyGroup.query.get(group_id)
    if not g:
        return flask.jsonify({"status": "error"}), 404
    existing = StudyGroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first()
    if not existing:
        db.session.add(StudyGroupMember(group_id=group_id, user_id=current_user.id, role="member"))
        db.session.commit()
    return flask.jsonify({"status": "ok"})


@app.route("/groups/invite/<int:group_id>")
def groups_invite(group_id):
    """Shareable invite link. Anyone with an IntelliPlan account who hits
    this URL is auto-joined and dropped into the group room. Logged-out
    visitors are bounced to /login and joined right after sign-in via the
    `pending_group_join` session flag."""
    g = StudyGroup.query.get(group_id)
    if not g:
        return render_template("error.html", active_page="error", error_code=404,
                               error_id="GROUP-NOT-FOUND",
                               message="That study group invite link no longer works."), 404
    if not current_user.is_authenticated:
        session["pending_group_join"] = group_id
        session.modified = True
        return redirect(url_for("login"))
    existing = StudyGroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first()
    if not existing:
        db.session.add(StudyGroupMember(group_id=group_id, user_id=current_user.id, role="member"))
        db.session.commit()
    return redirect(url_for("groups_page") + f"?open={group_id}")


@app.route("/api/groups/<int:group_id>/leave", methods=["POST"])
def api_leave_group(group_id):
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"}), 401
    StudyGroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).delete()
    db.session.commit()
    return flask.jsonify({"status": "ok"})


@app.route("/api/groups/<int:group_id>/notes", methods=["POST"])
def api_save_group_notes(group_id):
    g = StudyGroup.query.get(group_id)
    if not g:
        return flask.jsonify({"status": "error"}), 404
    if not current_user.is_authenticated or not StudyGroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first():
        return flask.jsonify({"status": "error", "message": "not a member"}), 403
    body = request.get_json(silent=True) or {}
    g.shared_notes = (body.get("notes") or "")[:60000]
    db.session.commit()
    return flask.jsonify({"status": "ok"})


@app.route("/api/groups/<int:group_id>/start-meeting", methods=["POST"])
def api_start_group_meeting(group_id):
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "login required"}), 401
    g = StudyGroup.query.get(group_id)
    if not g:
        return flask.jsonify({"status": "error", "message": "not found"}), 404
    if not StudyGroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first():
        return flask.jsonify({"status": "error", "message": "not a member"}), 403
    if g.meeting_url:
        return flask.jsonify({"status": "ok", "meeting_url": g.meeting_url})
    room_name = f"vpaas-magic-cookie-a0f824ad588d472db70a0847bffc500e/{g.name.replace(' ', '').replace(',', '')[:20]}{g.id}{secrets_module.token_hex(4).upper()}"
    g.meeting_url = f"https://8x8.vc/{room_name}"
    db.session.commit()
    return flask.jsonify({"status": "ok", "meeting_url": g.meeting_url})


@app.route("/api/groups/<int:group_id>/meeting", methods=["POST"])
def api_set_group_meeting(group_id):
    g = StudyGroup.query.get(group_id)
    if not g:
        return flask.jsonify({"status": "error"}), 404
    if not current_user.is_authenticated or not StudyGroupMember.query.filter_by(group_id=group_id, user_id=current_user.id).first():
        return flask.jsonify({"status": "error"}), 403
    body = request.get_json(silent=True) or {}
    when = (body.get("when") or "").strip()
    topic = (body.get("topic") or "").strip()[:255]
    try:
        g.next_meeting_at = datetime.fromisoformat(when) if when else None
    except Exception:
        g.next_meeting_at = None
    g.next_meeting_topic = topic
    db.session.commit()
    return flask.jsonify({"status": "ok"})


# ── WRITING IMPROVEMENT ASSISTANT ───────────────────────────
@app.route("/writing")
def writing_page():
    if not feature_enabled("writing"):
        return render_template("error.html", error_code=503, error_id="WRITING-DISABLED",
                               message="Writing Assistant is temporarily disabled."), 503
    return render_template("writing.html", active_page="writing")


@app.route("/api/writing/analyze", methods=["POST"])
@limiter.limit("20 per minute")
def api_writing_analyze():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    tone = (body.get("tone") or "neutral")[:32]
    purpose = (body.get("purpose") or "essay")[:32]
    if not text:
        return flask.jsonify({"status": "error", "message": "text required"}), 400
    if len(text) > 12000:
        text = text[:12000]
    system = (
        "You are an expert writing coach. Review the user's writing and "
        "respond with STRICT JSON only (no preamble). Schema:\n"
        "{\n"
        "  \"overall\": {\"score\": int 0-100, \"summary\": string},\n"
        "  \"suggestions\": [{\"category\": one of [\"grammar\",\"clarity\",\"tone\",\"structure\",\"argument\"],\n"
        "      \"excerpt\": string (≤120 chars from the original), \"suggestion\": string, \"why\": string}],\n"
        "  \"revised\": string (a clean revised draft)\n"
        "}\n"
        "Return at most 8 suggestions, prioritised by impact. Be specific."
    )
    try:
        resp = _groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Purpose: {purpose}\nTarget tone: {tone}\n\nText:\n{text}"},
            ],
            temperature=0.3,
            max_tokens=1400,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw)
        return flask.jsonify({"status": "ok", **data})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500


# ── MATH EXPLAINER ──────────────────────────────────────────
@app.route("/math")
def math_page():
    if not feature_enabled("math"):
        return render_template("error.html", error_code=503, error_id="MATH-DISABLED",
                               message="Math Explainer is temporarily disabled."), 503
    return render_template("math.html", active_page="math")


@app.route("/api/math/explain", methods=["POST"])
@limiter.limit("30 per minute")
def api_math_explain():
    body = request.get_json(silent=True) or {}
    problem = (body.get("problem") or "").strip()
    level = (body.get("level") or "high_school")[:32]
    if not problem:
        return flask.jsonify({"status": "error", "message": "problem required"}), 400
    system = (
        "You are a patient, rigorous math tutor. Respond with STRICT JSON only (no preamble).\n"
        "Schema:\n"
        "{\n"
        "  \"problem\": string,\n"
        "  \"steps\": [{\"step\": int, \"explanation\": string, \"math\": string}],\n"
        "  \"answer\": string,\n"
        "  \"notes\": string (common pitfalls, intuition)\n"
        "}\n"
        f"Adjust depth for level={level}. Keep each step short and self-contained. Use plain text for math (e.g. x^2 not LaTeX)."
    )
    try:
        resp = _groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": problem[:8000]},
            ],
            temperature=0.2,
            max_tokens=1400,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw)
        return flask.jsonify({"status": "ok", **data})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/math/similar", methods=["POST"])
@limiter.limit("30 per minute")
def api_math_similar():
    body = request.get_json(silent=True) or {}
    problem = (body.get("problem") or "").strip()
    if not problem:
        return flask.jsonify({"status": "error", "message": "problem required"}), 400
    system = (
        "Generate ONE similar practice problem at the same difficulty. Respond with STRICT JSON only:\n"
        "{\"problem\": string, \"answer\": string}"
    )
    try:
        resp = _groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": problem[:6000]},
            ],
            temperature=0.7,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content.strip())
        return flask.jsonify({"status": "ok", **data})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500


# ── TASK EXTRACTOR ──────────────────────────────────────────
@app.route("/extractor")
def extractor_page():
    if not feature_enabled("extractor"):
        return render_template("error.html", error_code=503, error_id="EXTRACTOR-DISABLED",
                               message="Task Extractor is temporarily disabled."), 503
    return render_template("extractor.html", active_page="extractor")


@app.route("/api/tasks/extract", methods=["POST"])
@limiter.limit("30 per minute")
def api_task_extract():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return flask.jsonify({"status": "error", "message": "text required"}), 400
    today_iso = datetime.utcnow().date().isoformat()
    system = (
        "Extract actionable tasks from user-supplied notes / messages / emails. Respond with STRICT JSON only:\n"
        "{\n"
        "  \"tasks\": [{\"title\": string, \"due_date\": null | YYYY-MM-DD, \"priority\": \"High\"|\"Medium\"|\"Low\", \"notes\": string}]\n"
        "}\n"
        f"Today is {today_iso}. Resolve relative dates (e.g. 'next Friday') to absolute YYYY-MM-DD where possible. "
        "Use null for due_date when no date is implied. Use High for urgent or graded items, Low for nice-to-have."
    )
    try:
        resp = _groq().chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text[:12000]},
            ],
            temperature=0.2,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content.strip())
        tasks = data.get("tasks") or []
        # Optionally persist as ManualTask when ?save=1
        saved_ids = []
        if request.args.get("save") == "1" and current_user.is_authenticated:
            for t in tasks[:25]:
                mt = ManualTask(
                    user_id=current_user.id,
                    title=(t.get("title") or "")[:255],
                    due_date=t.get("due_date") or None,
                    priority=(t.get("priority") or "Medium")[:16],
                    course=(t.get("course") or "Personal")[:128],
                    estimated_time=int(t.get("estimated_time") or 60),
                    notes=(t.get("notes") or "")[:2000],
                )
                db.session.add(mt)
                db.session.flush()
                saved_ids.append(mt.id)
            db.session.commit()
        return flask.jsonify({"status": "ok", "tasks": tasks, "saved_ids": saved_ids})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500


@app.errorhandler(429)
def error_429(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/") or request.is_json:
        return flask.jsonify({"status": "error", "message": "Rate limit exceeded. Please wait a moment before trying again."}), 429
    try:
        return render_template("error.html", active_page="error", error_code=429, error_id=make_error_id()), 429
    except Exception:
        return flask.Response("<h1>429 Too Many Requests</h1><a href='/'>Home</a>", status=429, mimetype="text/html")

@app.errorhandler(500)
def error_500(e):
    err_id = make_error_id()
    print(f"[{err_id}] Internal Server Error: {e}")
    if os.getenv("SENTRY_DSN"):
        try:
            sentry_sdk.capture_exception(e)
        except Exception:
            pass
    if request.path.startswith("/extension/") or request.path.startswith("/api/") or request.is_json:
        return flask.jsonify({"status": "error", "message": "Internal server error. Please try again.", "error_id": err_id}), 500
    try:
        return render_template("error.html", active_page="error", error_code=500, error_id=err_id), 500
    except Exception:
        return flask.Response(f"<h1>500 Server Error</h1><p>Error ID: {err_id}</p><a href='/'>Home</a>", status=500, mimetype="text/html")

@app.errorhandler(503)
def error_503(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/") or request.is_json:
        return flask.jsonify({"status": "error", "message": "Service temporarily unavailable. Please try again later."}), 503
    try:
        return render_template("error.html", active_page="error", error_code=503, error_id=make_error_id()), 503
    except Exception:
        return flask.Response("<h1>503 Service Unavailable</h1><a href='/'>Home</a>", status=503, mimetype="text/html")

@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    import traceback
    err_id = make_error_id()
    print(f"[{err_id}] Unhandled exception:\n{traceback.format_exc()}")
    if os.getenv("SENTRY_DSN"):
        try:
            sentry_sdk.capture_exception(e)
        except Exception:
            pass
    if request.path.startswith("/extension/") or request.path.startswith("/api/") or request.is_json:
        return flask.jsonify({"status": "error", "message": "An unexpected error occurred. Please try again.", "error_id": err_id}), 500
    try:
        return render_template("error.html", active_page="error", error_code=500, error_id=err_id), 500
    except Exception:
        return flask.Response(f"<h1>Server Error</h1><p>Error ID: {err_id}</p><a href='/'>Home</a>", status=500, mimetype="text/html")


# ── Public REST API for third-party clients and the IntelliPlan MCP server.
# Registered at the very end so all models and helpers are defined.
# Attach key references to the Flask app so the blueprint can resolve them
# via `current_app` (avoids a double-import / double-SQLAlchemy-instance bug
# when running as `python App.py` — module loaded as both `__main__` and
# `App`).
app.intelliplan_db = db
app.intelliplan_bcrypt = bcrypt
app.intelliplan_user_model = User
app.intelliplan_get_identity = _get_or_create_identity
from intelliplan_api import api_bp as intelliplan_api_bp
app.register_blueprint(intelliplan_api_bp)


def _existing_columns(table_name):
    """Return a set of column names that exist on `table_name`, or an
    empty set if the table doesn't exist. Works on both SQLite and
    Postgres without needing the ORM's metadata to be in sync."""
    from sqlalchemy import inspect as _inspect
    try:
        insp = _inspect(db.engine)
        if table_name not in insp.get_table_names():
            return set()
        return {c["name"] for c in insp.get_columns(table_name)}
    except Exception as e:
        print(f"[migrate] inspect {table_name} failed: {e}")
        return set()


def _migrate_user_columns():
    """Bulletproof ALTER TABLE shim for SQLite/Postgres.

    The previous version used `db.session.execute(...)` per column,
    which on Postgres aborts the WHOLE transaction the moment any one
    of the ALTERs fails (e.g. "column already exists"). Subsequent
    ALTERs in the same transaction then silently no-op and the App
    boots without the new columns — which is the bug that left users
    seeing "Login is briefly unavailable" every time.

    Now we:
      1. Inspect the live schema first and only attempt ALTERs for
         columns that are genuinely missing.
      2. Use a fresh, autocommit-style connection per ALTER so a
         failure on one column can't poison the rest.
      3. Verify after the loop that the new columns landed and log
         the result for production debugging.
    """
    from sqlalchemy import text as _t

    targets = [
        # users — referral / tier / phone / reminder prefs
        ("users", "referral_code", "VARCHAR(16)"),
        ("users", "referred_by_id", "INTEGER"),
        ("users", "tier", "VARCHAR(16) DEFAULT 'free'"),
        ("users", "pro_until", "TIMESTAMP"),
        ("users", "phone", "VARCHAR(32)"),
        ("users", "sms_reminders_opt_in", "BOOLEAN DEFAULT FALSE"),
        ("users", "push_reminders_opt_in", "BOOLEAN DEFAULT FALSE"),
        ("users", "reminder_lead_minutes", "INTEGER DEFAULT 60"),
        ("users", "sms_carrier", "VARCHAR(32) DEFAULT 'tmobile'"),
        ("users", "birth_year", "INTEGER"),
        ("users", "parent_email", "VARCHAR(255)"),
        ("users", "parent_consent_granted", "BOOLEAN DEFAULT FALSE"),
        ("users", "parent_consent_token", "VARCHAR(64)"),
        ("users", "stripe_customer_id", "VARCHAR(64)"),
        # user_identities — earlier migration's columns. Without these,
        # _get_or_create_identity() blows up and registration redirects
        # to /onboarding which then 500s.
        ("user_identities", "availability", "TEXT"),
        ("user_identities", "weekly_commitments", "TEXT"),
        ("user_identities", "class_schedule", "TEXT"),
        # notion_integrations
        ("notion_integrations", "auth_type", "VARCHAR(16) DEFAULT 'manual'"),
        ("notion_integrations", "workspace_id", "VARCHAR(64)"),
        ("notion_integrations", "workspace_name", "VARCHAR(256)"),
        ("notion_integrations", "workspace_icon", "VARCHAR(512)"),
        ("notion_integrations", "bot_id", "VARCHAR(64)"),
        ("notion_integrations", "connected_at", "TIMESTAMP"),
        # daily check-in chest tracking
        ("study_points", "last_daily_claim", "VARCHAR(16) DEFAULT ''"),
        # google_integrations — multi-account support
        ("google_integrations", "account_email", "VARCHAR(255)"),
        ("google_integrations", "account_name", "VARCHAR(255)"),
        ("google_integrations", "is_active", "BOOLEAN DEFAULT TRUE"),
        ("google_integrations", "connected_at", "TIMESTAMP"),
    ]

    # Group targets by table so we only inspect each table once.
    by_table = {}
    for t, c, d in targets:
        by_table.setdefault(t, []).append((c, d))

    for table, cols in by_table.items():
        existing = _existing_columns(table)
        if not existing:
            # Table doesn't exist at all — db.create_all() will handle it
            # the next time around. No ALTER needed.
            continue
        for col, decl in cols:
            if col in existing:
                continue
            try:
                # Fresh connection per ALTER, autocommit on, so one failure
                # can't roll back the others.
                with db.engine.connect() as conn:
                    conn.execute(_t(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))
                    try:
                        conn.commit()
                    except Exception:
                        pass
                print(f"[migrate] added {table}.{col}")
            except Exception as e:
                print(f"[migrate] could not add {table}.{col}: {e}")

    # Verify result so production logs make the state obvious.
    users_cols = _existing_columns("users")
    have_new = {"referral_code", "referred_by_id", "tier", "pro_until"}.issubset(users_cols)
    print(f"[migrate] users new columns present: {have_new}  (got: {sorted(users_cols)})")


# Run the schema bootstrap + column migration at IMPORT time so production
# WSGI servers (gunicorn etc.) see the new columns. Previously this was
# gated on __name__=="__main__", which caused every request to surface
# the raw "<h1>Server Error</h1><a href='/'>Home</a>" fallback in prod
# because the new user.tier / pro_until columns didn't exist yet and
# inject_pro's lookup blew up the whole Jinja render — including the
# error page.
_MIGRATION_DONE = False

def _run_boot_migration_once():
    """Idempotent migration helper — safe to call repeatedly."""
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return
    try:
        db.create_all()
        _migrate_user_columns()
        _MIGRATION_DONE = True
    except Exception as _boot_e:
        print(f"[boot] DB bootstrap failed: {_boot_e}")

try:
    with app.app_context():
        _run_boot_migration_once()
except Exception as _boot_e:
    print(f"[boot] App context unavailable at import: {_boot_e}")


@app.route("/health")
def health_check():
    """Diagnostic endpoint. Returns DB-schema state so we can see in
    production whether the migration ran. Public, no secrets exposed."""
    out = {
        "status": "ok",
        "migration_done": bool(_MIGRATION_DONE),
        "users_columns": sorted(_existing_columns("users")),
        "notion_columns": sorted(_existing_columns("notion_integrations")),
    }
    expected = {"id", "email", "password_hash", "referral_code", "referred_by_id", "tier", "pro_until"}
    out["users_schema_ok"] = expected.issubset(set(out["users_columns"]))
    return flask.jsonify(out)


@app.before_request
def _ensure_migration_ran():
    """Belt-and-braces: if for any reason the import-time migration
    didn't complete (e.g. the DB wasn't ready yet during gunicorn cold
    start on Railway), retry it on the very first request. This is the
    only way to guarantee the new user.tier / pro_until / referral_code
    columns exist before any User SELECT hits them."""
    if _MIGRATION_DONE:
        return
    try:
        _run_boot_migration_once()
    except Exception:
        pass


@app.route("/api/syllabus/import", methods=["POST"])
def api_syllabus_import():
    """Extract assignments from an uploaded PDF syllabus using AI.
    Returns a JSON list of {title, due_date, course, description} objects."""
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401

    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".pdf"):
        return jsonify({"status": "error", "message": "Only PDF files are supported"}), 400

    raw_text = ""
    try:
        try:
            import pdfplumber
            with pdfplumber.open(f) as pdf:
                raw_text = "\n".join(
                    page.extract_text() or "" for page in pdf.pages[:20]
                )
        except ImportError:
            # Fallback: read raw bytes and try to extract ASCII text
            f.seek(0)
            data = f.read()
            raw_text = data.decode("latin-1", errors="replace")
            raw_text = re.sub(r"[^\x20-\x7E\n\t]", " ", raw_text)
            raw_text = re.sub(r" {3,}", " ", raw_text)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not read PDF: {e}"}), 422

    if not raw_text.strip():
        return jsonify({"status": "error", "message": "PDF appears to have no readable text"}), 422

    truncated = raw_text[:6000]
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    prompt = f"""You are a syllabus parser. Today is {today_str}.
Extract every assignment, exam, quiz, project, or deadline from the syllabus text below.
Return ONLY a valid JSON array — no markdown, no explanations — where each item has:
  "title": short assignment name (string),
  "due_date": due date as YYYY-MM-DD if determinable, else null,
  "course": course name/code if visible (string or null),
  "description": one-line description (string or null)

Syllabus text:
---
{truncated}
---
JSON array:"""

    try:
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            return jsonify({"status": "error", "message": "AI extraction not configured"}), 503
        client = Groq(api_key=groq_key)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        raw_json = resp.choices[0].message.content.strip()
        # Model sometimes wraps in {"assignments": [...]}
        parsed = json.loads(raw_json)
        if isinstance(parsed, dict):
            for key in ("assignments", "items", "tasks", "deadlines", "data"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break
        if not isinstance(parsed, list):
            parsed = []
    except Exception as e:
        print(f"[syllabus import] AI parse error: {e}")
        return jsonify({"status": "error", "message": "AI could not parse the syllabus"}), 500

    return jsonify({"status": "ok", "assignments": parsed, "count": len(parsed)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
