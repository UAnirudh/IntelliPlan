import flask
from flask import render_template, request, redirect, session, url_for
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from studentvue_helper import test_login, get_assignments as get_sv_assignments, get_missing_assignments
from groq import Groq
import re
import json
import uuid
import base64
import io
import functools
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from auth_api import auth_bp, verify_token
from werkzeug.utils import secure_filename
import secrets as secrets_module
from flask import jsonify
from datetime import datetime, timedelta

from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user
)
from flask_bcrypt import Bcrypt

try:
    from google_calendar_helper import (
        get_auth_url, exchange_code_for_token,
        get_upcoming_events, add_schedule_to_calendar, find_free_slots
    )
    GCAL_AVAILABLE = True
except Exception as e:
    print(f"Google Calendar not available: {e}")
    GCAL_AVAILABLE = False

try:
    from notion_helper import (
        test_notion_token, get_notion_databases,
        get_notion_tasks, create_notion_task,
        update_notion_task, complete_notion_task
    )
    NOTION_AVAILABLE = True
except Exception as e:
    print(f"Notion not available: {e}")
    NOTION_AVAILABLE = False

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

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Extension-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)
app.secret_key = os.getenv("SECRET_KEY", "intelliplan-dev-key")
app.permanent_session_lifetime = timedelta(days=7)
app.register_blueprint(auth_bp)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///intelliplan.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["NOTES_UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads", "course_notes")
os.makedirs(app.config["NOTES_UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ── MODELS ────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    linked_accounts = db.relationship("LinkedAccount", backref="user", lazy=True, cascade="all, delete-orphan")
    dismissed = db.relationship("DismissedAssignment", backref="user", lazy=True, cascade="all, delete-orphan")
    descriptions = db.relationship("CustomDescription", backref="user", lazy=True, cascade="all, delete-orphan")

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

class NotionIntegration(db.Model):
    __tablename__ = "notion_integrations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    token = db.Column(db.String(512), nullable=False)
    database_id = db.Column(db.String(256), nullable=True)

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
    streak_count = db.Column(db.Integer, default=0)
    streak_freeze_count = db.Column(db.Integer, default=0)
    last_active_date = db.Column(db.String(16), default="")
    streak_history = db.Column(db.Text, default="[]")
    session_history = db.Column(db.Text, default="[]")
    longest_streak = db.Column(db.Integer, default=0)
    total_sessions = db.Column(db.Integer, default=0)
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

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

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
        gi = GoogleIntegration.query.filter_by(user_id=current_user.id).first()
        if gi:
            return json.loads(gi.token_data)
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
    return p

@app.context_processor
def inject_auth():
    return dict(logged_in=is_logged_in())

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
@app.route("/")
def landing():
    return render_template("landing.html", active_page="landing")

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
    return render_template("gradebook.html", active_page="grademodel")

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
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("settings.html", active_page="settings")

@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("dashboard.html", active_page="dashboard")

@app.route("/study")
def study():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("study.html", active_page="study")

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
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        else:
            pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
            user = User(email=email, password_hash=pw_hash)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            return redirect(url_for("connect_account"))
    return render_template("register.html", active_page="login", error=error)

@app.route("/login/account", methods=["GET", "POST"])
def login_account():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user, remember=True)
            acct = LinkedAccount.query.filter_by(user_id=user.id, is_active=True).first()
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
        if not token or not canvas_url:
            error = "Please fill in both fields."
        else:
            test = requests.get(f"{canvas_url}/api/v1/courses", headers={"Authorization": f"Bearer {token}"})
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
    return render_template("login_canvas.html", active_page="login", error=error)

@app.route("/login/studentvue", methods=["GET", "POST"])
def login_studentvue():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        district_url = request.form.get("district_url", "").strip().rstrip("/")
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
                error = "Invalid credentials."
    return render_template("login_studentvue.html", active_page="login", error=error)

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
    profile_id = request.json.get("id")
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
    profile_id = request.json.get("id")
    acct = LinkedAccount.query.filter_by(user_id=current_user.id, profile_id=profile_id).first()
    if acct:
        db.session.delete(acct)
        db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/profiles/rename", methods=["POST"])
def profiles_rename():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    profile_id = request.json.get("id")
    name = request.json.get("name", "").strip()
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
    login_type = acct["login_type"]

    if login_type == "studentvue":
        try:
            result = get_sv_assignments(acct["sv_district_url"], acct["sv_username"], acct["sv_password"])
        except:
            result = []
        return flask.jsonify([a for a in result if a["title"] not in dismissed])

    if login_type == "schoology":
        try:
            from schoology_helper import get_schoology_assignments
            result = get_schoology_assignments(acct["schoology_key"], acct["schoology_secret"])
            return flask.jsonify([a for a in result if a["title"] not in dismissed])
        except:
            return flask.jsonify([])

    try:
        token = acct["canvas_token"]
        canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
        base = f"{canvas_url}/api/v1"
        headers = {"Authorization": f"Bearer {token}"}
        course_response = requests.get(f"{base}/courses", headers=headers)
        courses = course_response.json()
        course_map = {}
        for c in courses:
            if isinstance(c, dict) and "id" in c:
                course_map[c["id"]] = c.get("name", "Unknown")
        assignments = []
        for course_id in course_map:
            response = requests.get(f"{base}/courses/{course_id}/assignments", headers=headers)
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
            priority = "High" if days < 0 or days <= 3 else "Medium" if days <= 7 else "Low"
            raw_minutes = a["points_possible"] * 1.5
            rounded_minutes = max(30, round(raw_minutes / 30) * 30)
            difficulty = infer_task_difficulty(a["points_possible"], priority, due_str[:10])
            title = a["name"]
            if title in dismissed: continue
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
        except:
            return flask.jsonify([])
    token = acct["canvas_token"]
    canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
    headers = {"Authorization": f"Bearer {token}"}
    course_response = requests.get(f"{canvas_url}/api/v1/courses", headers=headers)
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
        except:
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
    return flask.jsonify([])

@app.route("/dismissed/data")
def dismissed_data():
    rows = get_dismissed_rows()
    result = []
    for r in rows:
        try:
            result.append(json.loads(r.data))
        except:
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
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
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
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
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
    history = request.json.get("history", []) if request.is_json else []
    history_text = json.dumps(history[-8:], ensure_ascii=False)
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
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
        resp = requests.get(f"{canvas_url}/api/v1/courses/{course_id}/assignments/{assignment_id}", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            raw = resp.json().get("description") or ""
            clean = re.sub(r"<[^>]+>", " ", raw).strip()
            clean = re.sub(r"\s+", " ", clean)
            if clean:
                return flask.jsonify({"description": clean, "source": "canvas"})
    return flask.jsonify({"description": "", "source": "none"})

@app.route("/assignment/description", methods=["POST"])
def save_description():
    data = request.json
    title = data.get("title")
    description = data.get("description", "").strip()
    if title and description:
        save_custom_description(title, description)
    return flask.jsonify({"status": "ok"})

@app.route("/generate_schedule", methods=["POST"])
@limiter.limit("10 per hour")
def generate_schedule():
    data = request.json
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
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
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

    prompt = f"""You are IntelliPlan — an adaptive academic study-planning system. Today is {today}.

You must schedule ALL {total} items below. Every single one must appear in the schedule.
{overdue_text}
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

# ── GOOGLE CALENDAR ───────────────────────────────────────────
@app.route("/oauth/google")
def google_oauth_start():
    if not is_logged_in():
        return redirect(url_for("login"))
    if not GCAL_AVAILABLE:
        return "Google Calendar not configured", 500
    import secrets
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    session.permanent = True
    session.modified = True
    from google_calendar_helper import get_auth_url
    auth_url = get_auth_url(state)
    return redirect(auth_url)

@app.route("/oauth/google/callback")
def google_oauth_callback():
    if not GCAL_AVAILABLE:
        return redirect(url_for("dashboard"))
    error_msg = request.args.get("error")
    if error_msg:
        print(f"OAuth error from Google: {error_msg}")
        return redirect(url_for("dashboard"))
    code = request.args.get("code")
    if not code:
        print("No code in callback")
        return redirect(url_for("dashboard"))
    try:
        from google_calendar_helper import exchange_code_for_token
        token_dict = exchange_code_for_token(code)
        session["google_token"] = token_dict
        session.permanent = True
        session.modified = True
        if current_user.is_authenticated:
            existing = GoogleIntegration.query.filter_by(user_id=current_user.id).first()
            if existing:
                existing.token_data = json.dumps(token_dict)
            else:
                db.session.add(GoogleIntegration(user_id=current_user.id, token_data=json.dumps(token_dict)))
            db.session.commit()
        return redirect(url_for("dashboard"))
    except Exception as e:
        import traceback
        print(f"Token exchange error: {traceback.format_exc()}")
        return redirect(url_for("dashboard"))

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
        return flask.jsonify({"slot": slot, "connected": True})
    except Exception as e:
        return flask.jsonify({"slot": "7:00 PM", "connected": False, "error": str(e)})

@app.route("/calendar/export", methods=["POST"])
def calendar_export():
    if not GCAL_AVAILABLE:
        return flask.jsonify({"status": "error", "message": "Google Calendar not configured"})
    token = get_google_token()
    if not token:
        return flask.jsonify({"status": "error", "message": "Google Calendar not connected"})
    data = request.json
    schedule_data = data.get("schedule_data")
    skip_overlaps = data.get("skip_overlaps", False)
    try:
        existing_events = []
        if skip_overlaps:
            try:
                existing_events = get_upcoming_events(token)
            except:
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

# ── NOTION ────────────────────────────────────────────────────
@app.route("/notion/connect", methods=["POST"])
def notion_connect():
    if not NOTION_AVAILABLE:
        return flask.jsonify({"status": "error", "message": "Notion not configured"})
    token = request.json.get("token", "").strip()
    if not token:
        return flask.jsonify({"status": "error", "message": "No token provided"})
    if not test_notion_token(token):
        return flask.jsonify({"status": "error", "message": "Invalid Notion token"})
    session["notion_token"] = token
    session.modified = True
    if current_user.is_authenticated:
        existing = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if existing:
            existing.token = token
            existing.database_id = None
        else:
            db.session.add(NotionIntegration(user_id=current_user.id, token=token))
        db.session.commit()
    dbs = get_notion_databases(token)
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
    db_id = request.json.get("database_id")
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
    data = request.json
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
    data = request.json
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
    page_id = request.json.get("page_id")
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
            except Exception as e:
                print(f"SV assignments error: {e}")
                raw = []
            try:
                missing_raw = get_missing_assignments(acct["sv_district_url"], acct["sv_username"], acct["sv_password"])
            except Exception as e:
                print(f"Missing assignments error: {e}")
                missing_raw = []
            for a in raw:
                if a["title"] not in dismissed:
                    a["source"] = "studentvue"
                    a.setdefault("color", PRIORITY_COLORS.get(a.get("priority", "Medium"), "#f59e0b"))
                    tasks.append(a)
            for a in missing_raw:
                if a["title"] not in dismissed:
                    if "source" not in a:
                        a["source"] = "studentvue_missing"
                    tasks.append(a)
        elif login_type == "canvas":
            try:
                token = acct["canvas_token"]
                canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
                base = f"{canvas_url}/api/v1"
                headers = {"Authorization": f"Bearer {token}"}
                course_response = requests.get(f"{base}/courses", headers=headers)
                courses = course_response.json()
                course_map = {c["id"]: c.get("name", "Unknown") for c in courses if isinstance(c, dict) and "id" in c}
                for course_id in course_map:
                    resp = requests.get(f"{base}/courses/{course_id}/assignments", headers=headers)
                    data = resp.json()
                    if not isinstance(data, list):
                        continue
                    for a in data:
                        if not isinstance(a, dict) or not a.get("due_at"):
                            continue
                        due_str = a["due_at"][:10]
                        try:
                            due = datetime.strptime(due_str, "%Y-%m-%d").date()
                        except:
                            continue
                        days = (due - today).days
                        if days < -14:
                            continue
                        priority = "High" if days <= 3 else "Medium" if days <= 7 else "Low"
                        title = a["name"]
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
                    if t["title"] not in dismissed:
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
        except:
            result["upcoming"].append(t)

    for key in result:
        result[key].sort(key=lambda x: (x.get("due_date", "9999-12-31"), priority_order.get(x.get("priority", "Low"), 2)))

    return flask.jsonify(result)

@app.route("/missing/data")
def missing_data():
    acct = get_active_account()
    if not acct or acct["login_type"] != "studentvue":
        return flask.jsonify([])
    try:
        missing = get_missing_assignments(acct["sv_district_url"], acct["sv_username"], acct["sv_password"])
        return flask.jsonify(missing)
    except Exception as e:
        return flask.jsonify([])

# ── MANUAL TASKS ──────────────────────────────────────────────
@app.route("/tasks/manual/create", methods=["POST"])
def manual_create_task():
    data = request.json
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
            except:
                pass
    return flask.jsonify({"status": "ok", "id": task.id})

@app.route("/tasks/manual/update", methods=["POST"])
def manual_update_task():
    data = request.json
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
            except:
                pass
    return flask.jsonify({"status": "ok"})

@app.route("/tasks/manual/delete", methods=["POST"])
def manual_delete_task():
    task_id = request.json.get("id")
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
    data = request.json
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
@app.route("/feedback/complete", methods=["POST"])
def feedback_complete():
    data = request.json
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
    data = request.json
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
    data = request.json
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
        if not user or not bcrypt.check_password_hash(user.password_hash, password):
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
    except:
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
                            except:
                                continue
                            days = (due - today).days
                            if days < -14:
                                continue
                            priority = "High" if days <= 3 else "Medium" if days <= 7 else "Low"
                            title = a["name"]
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
            except:
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
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
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

        # --- Leniency Overrides ---
        ua = user_answer.lower()
        ca = correct_answer.lower()

        # Keyword overlap check
        keywords = [w for w in re.findall(r'\w+', ca) if len(w) > 4]
        keyword_hits = sum(1 for w in keywords if w in ua)

        # Similarity check
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, ua, ca).ratio()

        # Override overly harsh grading
        if result["verdict"] == "incorrect":
            if keyword_hits >= 2 or similarity > 0.5:
                result["verdict"] = "partial"
                result["score"] = max(result.get("score", 0), 45)

        if result["verdict"] == "partial" and similarity > 0.75:
            result["verdict"] = "correct"
            result["score"] = max(result.get("score", 0), 75)

        # --- Points System (less punishing) ---
        base = {"correct": 10, "partial": 7, "incorrect": 3}.get(result["verdict"], 3)
        conf_mult = {"high": 1.5, "medium": 1.0, "low": 0.7}.get(confidence, 1.0)

        if result["verdict"] == "incorrect" and confidence == "high":
            conf_mult = 0.6  # softer penalty

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
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
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
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}, {"type": "text", "text": question}]}],
            max_tokens=1500,
            temperature=0.3
        )
        return flask.jsonify({"status": "ok", "response": response.choices[0].message.content.strip()})
    except Exception as e:
        print(f"General image analysis error: {e}")
        return flask.jsonify({"status": "error", "message": "Service temporarily unavailable. Please try again later."})

@app.route("/study/points", methods=["GET"])
def study_get_points():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else session.get("guest_id", "guest")
    try:
        p = get_study_profile(uid, gid)
        history = json.loads(p.streak_history or "[]")
        sessions = json.loads(p.session_history or "[]")
        return flask.jsonify({
            "status": "ok",
            "total_points": p.total_points,
            "streak_count": p.streak_count,
            "streak_freeze_count": p.streak_freeze_count,
            "last_active_date": p.last_active_date,
            "streak_history": history,
            "session_history": sessions[-20:],
            "longest_streak": p.longest_streak or p.streak_count,
            "total_sessions": p.total_sessions or 0
        })
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/points/update", methods=["POST"])
def study_update_points():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else session.get("guest_id", "guest")
    data = request.json or {}
    delta = int(data.get("delta", 0))
    try:
        p = get_study_profile(uid, gid)
        p.total_points = max(0, p.total_points + delta)
        p.updated_at = datetime.utcnow()
        db.session.commit()
        return flask.jsonify({"status": "ok", "total_points": p.total_points})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/streak/update", methods=["POST"])
def study_update_streak():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else session.get("guest_id", "guest")
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        p = get_study_profile(uid, gid)
        history = json.loads(p.streak_history or "[]")
        last = p.last_active_date
        bonus_points = 0
        if last == today_str:
            return flask.jsonify({"status": "ok", "streak_count": p.streak_count, "bonus_points": 0, "total_points": p.total_points, "streak_history": history, "longest_streak": p.longest_streak or p.streak_count})
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
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
        if p.streak_count == 3:
            bonus_points = 10
        elif p.streak_count == 7:
            bonus_points = 25
            if p.streak_freeze_count < 2:
                p.streak_freeze_count += 1
        elif p.streak_count > 7 and p.streak_count % 7 == 0:
            bonus_points = 25
            if p.streak_freeze_count < 2:
                p.streak_freeze_count += 1
        p.total_points += bonus_points
        p.total_sessions = (p.total_sessions or 0) + 1
        db.session.commit()
        return flask.jsonify({"status": "ok", "streak_count": p.streak_count, "streak_freeze_count": p.streak_freeze_count, "bonus_points": bonus_points, "total_points": p.total_points, "streak_history": history, "longest_streak": p.longest_streak or p.streak_count})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/study/mastery/update", methods=["POST"])
def study_mastery_update():
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if uid else session.get("guest_id", "guest")
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
    gid = None if uid else session.get("guest_id", "guest")
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
    gid = None if uid else session.get("guest_id", "guest")
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
    gid = None if uid else session.get("guest_id", "guest")
    data = request.json or {}
    try:
        p = get_study_profile(uid, gid)
        sessions = json.loads(p.session_history or "[]")
        sessions.append({"date": datetime.now().strftime("%Y-%m-%d"), "mode": data.get("mode", "casual"), "questions": data.get("questions_total", 0), "correct": data.get("questions_correct", 0), "partial": data.get("questions_partial", 0), "points": data.get("points_earned", 0), "duration": data.get("duration_seconds", 0)})
        p.session_history = json.dumps(sessions[-50:])
        p.total_points += data.get("points_earned", 0)
        db.session.commit()
        return flask.jsonify({"status": "ok", "total_points": p.total_points})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

# ── ERROR HANDLERS ────────────────────────────────────────────
@app.errorhandler(404)
def error_404(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/"):
        return flask.jsonify({"status": "error", "message": "Not found"}), 404
    try:
        return render_template("error.html", active_page="error", error_code=404, error_id=make_error_id()), 404
    except:
        return flask.Response("<h1>404 Not Found</h1><a href='/'>Home</a>", status=404, mimetype="text/html")

@app.errorhandler(403)
def error_403(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/"):
        return flask.jsonify({"status": "error", "message": "Forbidden"}), 403
    try:
        return render_template("error.html", active_page="error", error_code=403, error_id=make_error_id()), 403
    except:
        return flask.Response("<h1>403 Forbidden</h1><a href='/'>Home</a>", status=403, mimetype="text/html")

@app.errorhandler(429)
def error_429(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/") or request.is_json:
        return flask.jsonify({"status": "error", "message": "Rate limit exceeded. Please wait a moment before trying again."}), 429
    try:
        return render_template("error.html", active_page="error", error_code=429, error_id=make_error_id()), 429
    except:
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
    except:
        return flask.Response(f"<h1>500 Server Error</h1><p>Error ID: {err_id}</p><a href='/'>Home</a>", status=500, mimetype="text/html")

@app.errorhandler(503)
def error_503(e):
    if request.path.startswith("/extension/") or request.path.startswith("/api/") or request.is_json:
        return flask.jsonify({"status": "error", "message": "Service temporarily unavailable. Please try again later."}), 503
    try:
        return render_template("error.html", active_page="error", error_code=503, error_id=make_error_id()), 503
    except:
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


# ── STUDY ACCESS LIMITS ───────────────────────────────────────

GUEST_STUDY_LIMITS = {
    "uploads": 1,        # one pasted text or one file
    "generations": 1,    # one generated study session
    "max_chars": 6000,   # shorter content for guests
    "max_questions": 5   # fewer questions for guests
}

def _get_guest_usage():
    """
    Tracks guest study usage in session.
    This is enough to enforce one-use access without creating a full DB row.
    """
    if "guest_study_usage" not in session:
        session["guest_study_usage"] = {
            "uploads": 0,
            "generations": 0
        }
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
    """
    Optional helper for the frontend.
    Lets the UI know whether the user is logged in and what guest limits apply.
    """
    if current_user.is_authenticated:
        return flask.jsonify({
            "status": "ok",
            "logged_in": True,
            "limits": None
        })

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

    # Guest restriction: only one upload total
    if _is_guest():
        usage = _get_guest_usage()
        if usage["uploads"] >= GUEST_STUDY_LIMITS["uploads"]:
            return _guest_limit_response()

    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(f.read()))
        text = " ".join(page.extract_text() or "" for page in reader.pages)

        # Mark guest upload used only after a successful extraction
        if _is_guest():
            usage = _get_guest_usage()
            usage["uploads"] += 1
            _save_guest_usage(usage)

        return flask.jsonify({
            "status": "ok",
            "text": text[:15000]
        })
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

    # Guest restriction: one generation total
    if _is_guest():
        usage = _get_guest_usage()
        if usage["generations"] >= GUEST_STUDY_LIMITS["generations"]:
            return _guest_limit_response()

        # Make the guest version lighter and more limited
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
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
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

        # Mark guest generation used only after successful output
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


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)