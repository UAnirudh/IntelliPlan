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
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from auth_api import auth_bp
from werkzeug.utils import secure_filename

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

# Rate limiting
limiter = Limiter(key_func=get_remote_address)

load_dotenv()

app = flask.Flask(
    __name__,
    template_folder="Main_Project/templates",
)
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
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB
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
        self.deadline = deadline  # datetime
        self.duration = duration  # minutes
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

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

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
    # Check DB first for authenticated users
    if current_user.is_authenticated:
        gi = GoogleIntegration.query.filter_by(user_id=current_user.id).first()
        if gi:
            return json.loads(gi.token_data)
    # Fall back to session (works for both guests and authenticated users)
    return session.get("google_token")

def get_notion_token_and_db():
    if current_user.is_authenticated:
        ni = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if ni and ni.token:
            return ni.token, ni.database_id
    # Fall back to session
    return session.get("notion_token"), session.get("notion_database_id")

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

# ── AUTH ROUTES ───────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    # If a form POSTs directly to /login, forward to /login/account (preserving POST body)
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

    # Canvas
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

@app.route("/dismiss", methods=["POST"])
def dismiss():
    assignment = request.json
    title = assignment.get("title")
    if title:
        save_dismissed(title, assignment)
    return flask.jsonify({"status": "ok"})

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
    return flask.jsonify({
        "status": "ok",
        "notes": [course_note_payload(n) for n in notes]
    })

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
            return flask.jsonify({
                "status": "error",
                "message": "Only TXT, MD, CSV, PDF, and DOCX files are supported."
            }), 400

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
        return flask.jsonify({
            "status": "error",
            "message": "Upload a note file or paste note text."
        }), 400

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

    return flask.jsonify({
        "status": "ok",
        "note": course_note_payload(note)
    })

@app.route("/notes/<int:note_id>")
def get_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404

    return flask.jsonify({
        "status": "ok",
        "note": course_note_payload(note, include_text=True)
    })

@app.route("/notes/<int:note_id>/download")
def download_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note.stored_filename or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "File not found"}), 404

    owner_dir = os.path.join(
        app.config["NOTES_UPLOAD_FOLDER"],
        f"user_{note.user_id}" if note.user_id else f"guest_{note.guest_session_id}"
    )

    return flask.send_from_directory(
        owner_dir,
        note.stored_filename,
        as_attachment=True,
        download_name=note.original_filename or note.stored_filename
    )

@app.route("/notes/<int:note_id>/summarize", methods=["POST"])
def summarize_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404

    if not (note.text_content or "").strip():
        return flask.jsonify({
            "status": "error",
            "message": "No extracted text is available for this note."
        }), 400

    if not os.getenv("GROQ_API_KEY"):
        return flask.jsonify({
            "status": "error",
            "message": "GROQ_API_KEY is not set."
        }), 500

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    text = (note.text_content or "")[:12000]

    prompt = f"""
Summarize these class notes for a student.

Return:
- 5 to 8 bullet points
- a short "Key takeaways" section
- keep it clear, practical, and concise

Notes:
{text}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=900,
        )
        summary = response.choices[0].message.content.strip()
        note.summary_cache = summary
        db.session.commit()

        return flask.jsonify({"status": "ok", "summary": summary})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/notes/<int:note_id>/study", methods=["POST"])
def study_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404

    if not (note.text_content or "").strip():
        return flask.jsonify({
            "status": "error",
            "message": "No extracted text is available for this note."
        }), 400

    if not os.getenv("GROQ_API_KEY"):
        return flask.jsonify({
            "status": "error",
            "message": "GROQ_API_KEY is not set."
        }), 500

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    text = (note.text_content or "")[:12000]

    prompt = f"""
Turn these notes into study material for a student.

Return ONLY valid JSON in this shape:
{{
  "title": "Study Guide",
  "summary": "short study summary",
  "cards": [
    {{"question": "Q1", "answer": "A1"}},
    {{"question": "Q2", "answer": "A2"}}
  ],
  "quiz": [
    {{"question": "Q1", "answer": "A1"}},
    {{"question": "Q2", "answer": "A2"}}
  ]
}}

Make the questions useful for studying and keep answers short.

Notes:
{text}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1200,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```", "", raw).strip()

        try:
            study = json.loads(raw)
        except Exception:
            study = {
                "title": "Study Guide",
                "summary": raw,
                "cards": [],
                "quiz": []
            }

        return flask.jsonify({"status": "ok", "study": study})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500

@app.route("/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    note = db.session.get(CourseNote, note_id)
    if not note or not note_belongs_to_current_user(note):
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404

    if note.stored_filename:
        owner_dir = os.path.join(
            app.config["NOTES_UPLOAD_FOLDER"],
            f"user_{note.user_id}" if note.user_id else f"guest_{note.guest_session_id}"
        )
        file_path = os.path.join(owner_dir, note.stored_filename)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Could not remove note file: {e}")

    db.session.delete(note)
    db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/restore", methods=["POST"])
def restore():
    title = request.json.get("title")
    if title:
        delete_dismissed(title)
    return flask.jsonify({"status": "ok"})

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

    # Normalize assignments
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
            "color": assignment.get("color") or PRIORITY_COLORS.get(
                assignment.get("priority", "Medium"), "#60a5fa"
            ),
        })

    # Separate overdue from upcoming
    today_str = datetime.now().strftime("%Y-%m-%d")
    overdue = [a for a in normalized_assignments if a.get("due_date", "9999") < today_str]
    upcoming = [a for a in normalized_assignments if a.get("due_date", "9999") >= today_str]
    
    # Sort upcoming by due date
    upcoming.sort(key=lambda x: x.get("due_date", "9999"))

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

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
        custom_text = f"\nCUSTOM TASKS ADDED BY STUDENT — use EXACT names as written ({len(custom_tasks)}):\n" + "\n".join([
            f"  - {t}" for t in custom_tasks
        ])

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
        schedule_data = enrich_schedule_data(
            schedule_data, normalized_assignments, preferred_time, hours_per_day,
        )
        return flask.jsonify({"status": "ok", "data": schedule_data})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

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
        print(f"Exchanging code for token...")
        token_dict = exchange_code_for_token(code)
        print(f"Token exchange successful. Token: {token_dict.get('token', '')[:20]}")

        session["google_token"] = token_dict
        session.permanent = True
        session.modified = True

        if current_user.is_authenticated:
            existing = GoogleIntegration.query.filter_by(user_id=current_user.id).first()
            if existing:
                existing.token_data = json.dumps(token_dict)
            else:
                db.session.add(GoogleIntegration(
                    user_id=current_user.id,
                    token_data=json.dumps(token_dict)
                ))
            db.session.commit()
            print(f"Saved to DB for user {current_user.id}")

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
        # If we got here token is valid — make sure it's saved in session
        session["google_token"] = token
        session.modified = True
        return flask.jsonify({"connected": True, "events": events})
    except Exception as e:
        print(f"Calendar events error: {e}")
        # Token is bad — clear it
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
    
    # Always save to session
    session["notion_token"] = token
    session.modified = True
    
    if current_user.is_authenticated:
        existing = NotionIntegration.query.filter_by(user_id=current_user.id).first()
        if existing:
            existing.token = token
            existing.database_id = None  # Reset db selection on reconnect
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
            # No integration row yet — shouldn't happen but handle it
            token = session.get("notion_token")
            if token:
                db.session.add(NotionIntegration(
                    user_id=current_user.id,
                    token=token,
                    database_id=db_id
                ))
                db.session.commit()
    
    # Always save to session too as backup
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
                raw = get_sv_assignments(
                    acct["sv_district_url"],
                    acct["sv_username"],
                    acct["sv_password"]
                )
            except Exception as e:
                print(f"SV assignments error: {e}")
                raw = []

            try:
                missing_raw = get_missing_assignments(
                    acct["sv_district_url"],
                    acct["sv_username"],
                    acct["sv_password"]
                )
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

    # Notion tasks
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

    # Manual tasks
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

    # Remove duplicates, especially from StudentVue sync overlaps
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

# @app.route("/privacy")
# def privacy():
#     return render_template("privacy.html")

# @app.route("/terms")
# def terms():
#     return render_template("terms.html")

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
        # Get existing events for overlap checking
        existing_events = []
        if skip_overlaps:
            try:
                existing_events = get_upcoming_events(token)
            except:
                existing_events = []

        ids, new_token, skipped = add_schedule_to_calendar(
            token, schedule_data, existing_events if skip_overlaps else []
        )

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
        return flask.jsonify({"status": "error", "message": str(e)})

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
    
    # Also dismiss the assignment
    if data.get("dismiss"):
        save_dismissed(title, data)
    
    db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/feedback/export")
def feedback_export():
    """Export training data as CSV — for model training later."""
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    rows = TaskFeedback.query.filter_by(user_id=current_user.id).all()
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Subject", "Estimate", "Actual", "Difficulty", "Priority", "DayOfWeek", "TimeOfDay"])
    for r in rows:
        writer.writerow([r.course, r.estimated_time, r.actual_time or "", r.difficulty, r.priority, r.day_of_week, r.time_of_day])
    output.seek(0)
    return flask.Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=intelliplan_data.csv"}
    )

@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    data = request.json
    sub_json = json.dumps(data.get("subscription"))
    uid = current_user.id if current_user.is_authenticated else None
    gid = None if current_user.is_authenticated else get_guest_session_id()
    existing = PushSubscription.query.filter_by(
        user_id=uid, guest_session_id=gid
    ).first()
    if existing:
        existing.subscription_json = sub_json
    else:
        db.session.add(PushSubscription(
            user_id=uid,
            guest_session_id=gid,
            subscription_json=sub_json
        ))
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
        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=json.loads(sub.subscription_json),
            data=json.dumps({"title": "IntelliPlan", "body": "Notifications are working! 🎉"}),
            vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
            vapid_claims={"sub": f"mailto:{os.getenv('VAPID_EMAIL', 'hello@intelliplan.app')}"}
        )
        return flask.jsonify({"status": "ok"})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})

@app.route("/push/vapid-public")
def vapid_public():
    return flask.jsonify({"key": os.getenv("VAPID_PUBLIC_KEY", "")})

@app.route("/legal")
def legal():
    return render_template("legal.html", active_page="legal")

@app.route("/install")
def install():
    return render_template("install.html")

# ── Notes ─────────────────────────────────────────────────────
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

@app.route("/notes/<int:note_id>/quiz", methods=["POST"])
def notes_quiz(note_id):
    if not NOTION_AVAILABLE and False:
        pass

    # Replace CourseNote with your actual model name if needed.
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

    prompt = f"""
You are generating one study question from the note below.

Rules:
- Return ONLY valid JSON.
- Return a single question that tests understanding.
- Do not repeat prior questions in history.
- Keep the question clear and specific.
- The answer should be concise but correct.
- Include 2 to 5 key points if helpful.

Prior questions and answers:
{history_text}

Note:
{note_text[:12000]}

Return JSON in this exact shape:
{{
  "question": "one question",
  "answer": "one correct answer",
  "key_points": ["point 1", "point 2"]
}}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=900,
        )

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```", "", raw)

        quiz = json.loads(raw)
        return flask.jsonify({"status": "ok", "quiz": quiz})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)}), 500


@app.route("/notes/<int:note_id>/file", methods=["GET"])
def notes_file(note_id):
    # Replace CourseNote with your actual model name if needed.
    note = None
    if current_user.is_authenticated:
        note = CourseNote.query.filter_by(id=note_id, user_id=current_user.id).first()
    else:
        note = CourseNote.query.filter_by(id=note_id, guest_session_id=get_guest_session_id()).first()

    if not note:
        return flask.jsonify({"status": "error", "message": "Note not found"}), 404

    # If your upload flow already stores a file URL/path, return it here.
    return flask.jsonify({
        "status": "ok",
        "view_url": getattr(note, "download_url", None),
        "filename": getattr(note, "original_filename", None),
        "text_content": getattr(note, "text_content", "")
    })

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)