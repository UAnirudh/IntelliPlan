import flask
from flask import render_template, request, redirect, session, url_for, flash
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from studentvue_helper import test_login, get_assignments as get_sv_assignments
from groq import Groq
import re
import json
from pathlib import Path
import uuid

from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from flask_bcrypt import Bcrypt

load_dotenv()

app = flask.Flask(
    __name__,
    template_folder="Main_Project/templates",
)
app.secret_key = os.getenv("SECRET_KEY", "intelliplan-dev-key")
app.permanent_session_lifetime = timedelta(days=7)

# ── DATABASE ──────────────────────────────────────────────────
# app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
#     "DATABASE_URL", "sqlite:///intelliplan.db"
# )
# app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

uri = os.getenv("DATABASE_URL")

if uri:
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
else:
    uri = "sqlite:///intelliplan.db"

app.config["SQLALCHEMY_DATABASE_URI"] = uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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
    credentials = db.Column(db.Text, nullable=False)  # JSON string
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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── CONSTANTS ─────────────────────────────────────────────────
CANVAS_BASE = "https://canvas.instructure.com/api/v1"

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
    """Returns credentials dict + login_type for the current user."""
    if current_user.is_authenticated:
        acct = LinkedAccount.query.filter_by(
            user_id=current_user.id, is_active=True
        ).first()
        if acct:
            creds = acct.get_credentials()
            creds["login_type"] = acct.login_type
            return creds
        return None
    # Guest fallback
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
        existing = DismissedAssignment.query.filter_by(
            user_id=current_user.id, title=title
        ).first()
        if not existing:
            db.session.add(DismissedAssignment(
                user_id=current_user.id,
                title=title,
                data=json.dumps(data_dict)
            ))
    else:
        gid = get_guest_session_id()
        existing = DismissedAssignment.query.filter_by(
            guest_session_id=gid, title=title
        ).first()
        if not existing:
            db.session.add(DismissedAssignment(
                guest_session_id=gid,
                title=title,
                data=json.dumps(data_dict)
            ))
    db.session.commit()

def delete_dismissed(title):
    if current_user.is_authenticated:
        DismissedAssignment.query.filter_by(
            user_id=current_user.id, title=title
        ).delete()
    else:
        gid = get_guest_session_id()
        DismissedAssignment.query.filter_by(
            guest_session_id=gid, title=title
        ).delete()
    db.session.commit()

def get_custom_description(assignment_title):
    if current_user.is_authenticated:
        row = CustomDescription.query.filter_by(
            user_id=current_user.id,
            assignment_title=assignment_title
        ).first()
    else:
        gid = get_guest_session_id()
        row = CustomDescription.query.filter_by(
            guest_session_id=gid,
            assignment_title=assignment_title
        ).first()
    return row.description if row else None

def save_custom_description(assignment_title, description):
    if current_user.is_authenticated:
        row = CustomDescription.query.filter_by(
            user_id=current_user.id,
            assignment_title=assignment_title
        ).first()
        if row:
            row.description = description
        else:
            db.session.add(CustomDescription(
                user_id=current_user.id,
                assignment_title=assignment_title,
                description=description
            ))
    else:
        gid = get_guest_session_id()
        row = CustomDescription.query.filter_by(
            guest_session_id=gid,
            assignment_title=assignment_title
        ).first()
        if row:
            row.description = description
        else:
            db.session.add(CustomDescription(
                guest_session_id=gid,
                assignment_title=assignment_title,
                description=description
            ))
    db.session.commit()

@app.context_processor
def inject_auth():
    return dict(logged_in=is_logged_in())

# ── GRADE / SCHEDULE LOGIC (unchanged) ───────────────────────
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
        "morning": {
            "label": "morning",
            "summary": "Front-load harder work first, then taper into lighter review.",
            "recommended_start_hour": 7,
            "hard_task_window": "7:00 AM - 11:00 AM",
            "light_task_window": "11:00 AM - 1:00 PM",
        },
        "afternoon": {
            "label": "afternoon",
            "summary": "Place demanding work first, then switch into medium and lighter tasks.",
            "recommended_start_hour": 1,
            "hard_task_window": "1:00 PM - 4:00 PM",
            "light_task_window": "4:00 PM - 6:00 PM",
        },
        "evening": {
            "label": "evening",
            "summary": "Begin with the highest-focus work in the early evening, then move to lighter tasks.",
            "recommended_start_hour": 6,
            "hard_task_window": "6:00 PM - 8:30 PM",
            "light_task_window": "8:30 PM - 10:30 PM",
        },
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
        return f"Today is a heavier {preference} workload, so protect your focus for the first block."
    if hard_task_count >= 2:
        return "You have multiple demanding tasks today — clean starts and no distractions before each block."
    if high_priority_count >= 1:
        return "Knock out the urgent task first while your attention is strongest."
    return "Balanced day — finish each block fully and keep your momentum steady."

def enrich_schedule_data(schedule_data, assignments, preferred_time, hours_per_day):
    assignment_lookup = {
        item["title"]: item
        for item in assignments
        if isinstance(item, dict) and item.get("title")
    }
    schedule = schedule_data.get("schedule", [])
    total_study_minutes = 0
    for day in schedule:
        study_minutes = sum(
            block.get("duration_minutes", 0)
            for block in day.get("blocks", [])
            if not block.get("is_break")
        )
        break_minutes = sum(
            block.get("duration_minutes", 0)
            for block in day.get("blocks", [])
            if block.get("is_break")
        )
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
                block["border_color"] = "#94a3b8"
                block["energy_level"] = "reset"
                block["difficulty"] = "Break"
                continue
            assignment_meta = assignment_lookup.get(block.get("assignment", ""), {})
            priority = assignment_meta.get("priority", "Medium")
            difficulty = assignment_meta.get("difficulty") or infer_task_difficulty(
                assignment_meta.get("points_possible"),
                priority,
                assignment_meta.get("due_date"),
            )
            energy_level = infer_block_energy_level(
                block.get("time_slot"), preferred_time, difficulty,
            )
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
        day["daily_tip"] = build_daily_tip(
            workload_level, preferred_time, high_priority_count, hard_task_count,
        )
        if not day.get("total_hours"):
            day["total_hours"] = round(total_minutes / 60, 1)
    schedule_data["energy_profile"] = get_energy_profile(preferred_time)
    schedule_data["total_study_time"] = (
        f"{total_study_minutes // 60} hours {total_study_minutes % 60} minutes"
    )
    return schedule_data

# ── PAGE ROUTES ───────────────────────────────────────────────
@app.route("/")
def landing():
    return render_template("landing.html", active_page="landing")

@app.route("/schedule")
def home():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("index.html", active_page="home")

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
    return render_template("scheduler.html", active_page="scheduler")

@app.route("/grademodel")
def grademodel():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("grademodel.html", active_page="grademodel")

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

# ── AUTH ROUTES ───────────────────────────────────────────────
@app.route("/login", methods=["GET"])
def login():
    if is_logged_in():
        return redirect(url_for("home"))
    return render_template("login.html", active_page="login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
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
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user, remember=True)
            # Set active account in session context
            acct = LinkedAccount.query.filter_by(
                user_id=user.id, is_active=True
            ).first()
            if not acct:
                # No linked account yet, send to connect
                return redirect(url_for("connect_account"))
            return redirect(url_for("home"))
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
            test = requests.get(
                f"{canvas_url}/api/v1/courses",
                headers={"Authorization": f"Bearer {token}"},
            )
            if test.status_code == 200:
                creds = {"canvas_token": token, "canvas_url": canvas_url}
                if current_user.is_authenticated:
                    # Deactivate all current accounts
                    LinkedAccount.query.filter_by(
                        user_id=current_user.id
                    ).update({"is_active": False})
                    acct = LinkedAccount(
                        user_id=current_user.id,
                        name=profile_name,
                        login_type="canvas",
                        is_active=True,
                    )
                    acct.set_credentials(creds)
                    db.session.add(acct)
                    db.session.commit()
                else:
                    # Guest mode
                    session.permanent = True
                    session["canvas_token"] = token
                    session["canvas_url"] = canvas_url
                    session["login_type"] = "canvas"
                return redirect(url_for("home"))
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
                creds = {
                    "sv_username": username,
                    "sv_password": password,
                    "sv_district_url": district_url,
                }
                if current_user.is_authenticated:
                    LinkedAccount.query.filter_by(
                        user_id=current_user.id
                    ).update({"is_active": False})
                    acct = LinkedAccount(
                        user_id=current_user.id,
                        name=profile_name,
                        login_type="studentvue",
                        is_active=True,
                    )
                    acct.set_credentials(creds)
                    db.session.add(acct)
                    db.session.commit()
                else:
                    session.permanent = True
                    session["sv_username"] = username
                    session["sv_password"] = password
                    session["sv_district_url"] = district_url
                    session["login_type"] = "studentvue"
                return redirect(url_for("home"))
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
            from schoology_helper import test_schoology_login
            if test_schoology_login(key, secret):
                creds = {"schoology_key": key, "schoology_secret": secret}
                if current_user.is_authenticated:
                    LinkedAccount.query.filter_by(
                        user_id=current_user.id
                    ).update({"is_active": False})
                    acct = LinkedAccount(
                        user_id=current_user.id,
                        name=profile_name,
                        login_type="schoology",
                        is_active=True,
                    )
                    acct.set_credentials(creds)
                    db.session.add(acct)
                    db.session.commit()
                else:
                    session["schoology_key"] = key
                    session["schoology_secret"] = secret
                    session["login_type"] = "schoology"
                return redirect(url_for("home"))
            else:
                error = "Invalid Schoology credentials."
    return render_template("login_schoology.html", active_page="login", error=error)

@app.route("/logout")
def logout():
    if current_user.is_authenticated:
        logout_user()
    session.clear()
    return redirect(url_for("landing"))

# ── PROFILE / ACCOUNT MANAGEMENT ─────────────────────────────
@app.route("/profiles/list")
def profiles_list():
    if not current_user.is_authenticated:
        # Guest — return session info
        login_type = session.get("login_type")
        if login_type:
            return flask.jsonify({
                "is_guest": True,
                "profiles": [{"id": "guest", "name": "Guest Session", "login_type": login_type, "is_active": True}],
                "active": "guest"
            })
        return flask.jsonify({"is_guest": True, "profiles": [], "active": None})

    accounts = LinkedAccount.query.filter_by(user_id=current_user.id).all()
    active = next((a for a in accounts if a.is_active), None)
    return flask.jsonify({
        "is_guest": False,
        "email": current_user.email,
        "profiles": [
            {
                "id": a.profile_id,
                "name": a.name,
                "login_type": a.login_type,
                "is_active": a.is_active
            } for a in accounts
        ],
        "active": active.profile_id if active else None
    })

@app.route("/profiles/switch", methods=["POST"])
def profiles_switch():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error", "message": "Not logged in"})
    profile_id = request.json.get("id")
    acct = LinkedAccount.query.filter_by(
        user_id=current_user.id, profile_id=profile_id
    ).first()
    if not acct:
        return flask.jsonify({"status": "error", "message": "Not found"})
    LinkedAccount.query.filter_by(user_id=current_user.id).update({"is_active": False})
    acct.is_active = True
    db.session.commit()
    return flask.jsonify({"status": "ok"})

@app.route("/profiles/delete", methods=["POST"])
def profiles_delete():
    if not current_user.is_authenticated:
        return flask.jsonify({"status": "error"})
    profile_id = request.json.get("id")
    acct = LinkedAccount.query.filter_by(
        user_id=current_user.id, profile_id=profile_id
    ).first()
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
    acct = LinkedAccount.query.filter_by(
        user_id=current_user.id, profile_id=profile_id
    ).first()
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
def get_live_schedule():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])

    dismissed = get_dismissed_titles()
    login_type = acct["login_type"]

    if login_type == "studentvue":
        result = get_sv_assignments(
            acct["sv_district_url"],
            acct["sv_username"],
            acct["sv_password"],
        )
        return flask.jsonify([a for a in result if a["title"] not in dismissed])

    if login_type == "schoology":
        from schoology_helper import get_schoology_assignments
        result = get_schoology_assignments(acct["schoology_key"], acct["schoology_secret"])
        return flask.jsonify([a for a in result if a["title"] not in dismissed])

    # Canvas
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

        schedule.append({
            "id": str(a["id"]),
            "course_id": str(a["course_id"]),
            "title": a["name"],
            "course": course_map.get(a["course_id"], "Unknown Course"),
            "due_date": due_str[:10],
            "points_possible": a["points_possible"],
            "priority": priority,
            "difficulty": difficulty,
            "estimated_time": rounded_minutes,
            "color": PRIORITY_COLORS.get(priority, "#60a5fa"),
        })

    sorted_schedule = sorted(schedule, key=lambda x: x["due_date"])
    return flask.jsonify([a for a in sorted_schedule if a["title"] not in dismissed])

@app.route("/courses")
def get_courses():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    login_type = acct["login_type"]

    if login_type == "studentvue":
        from studentvue_helper import get_courses as get_sv_courses
        return flask.jsonify(get_sv_courses(
            acct["sv_district_url"], acct["sv_username"], acct["sv_password"]
        ))
    if login_type == "schoology":
        from schoology_helper import get_schoology_courses
        return flask.jsonify(get_schoology_courses(acct["schoology_key"], acct["schoology_secret"]))

    # Canvas
    token = acct["canvas_token"]
    canvas_url = acct.get("canvas_url", "https://canvas.instructure.com")
    headers = {"Authorization": f"Bearer {token}"}
    course_response = requests.get(f"{canvas_url}/api/v1/courses", headers=headers)
    courses = course_response.json()
    return flask.jsonify([
        {"name": c.get("name", "Unknown")}
        for c in courses if isinstance(c, dict) and "id" in c
    ])

@app.route("/grades/data")
def grades_data():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    login_type = acct["login_type"]
    if login_type == "studentvue":
        from studentvue_helper import get_grades as get_sv_grades
        return flask.jsonify(get_sv_grades(
            acct["sv_district_url"], acct["sv_username"], acct["sv_password"]
        ))
    if login_type == "schoology":
        from schoology_helper import get_schoology_grades
        return flask.jsonify(get_schoology_grades(acct["schoology_key"], acct["schoology_secret"]))
    return flask.jsonify([])

@app.route("/gradebook/detail")
def gradebook_detail():
    acct = get_active_account()
    if not acct:
        return flask.jsonify([])
    if acct["login_type"] == "studentvue":
        from studentvue_helper import get_gradebook_detail
        return flask.jsonify(get_gradebook_detail(
            acct["sv_district_url"], acct["sv_username"], acct["sv_password"]
        ))
    return flask.jsonify([])

@app.route('/gradebook')
def gradebook():
    return render_template('gradebook.html')

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
        resp = requests.get(
            f"{canvas_url}/api/v1/courses/{course_id}/assignments/{assignment_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
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
def generate_schedule():
    data = request.json
    assignments = data.get("assignments", [])
    hours_per_day = data.get("hours_per_day", 2)
    preferred_time = data.get("preferred_time", "evening")
    custom_tasks = data.get("custom_tasks", [])

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

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    energy_profile = get_energy_profile(preferred_time)

    assignment_text = "\n".join([
        f"- {a['title']} ({a['course']}) — Due: {a['due_date']}, Priority: {a['priority']}, "
        f"Difficulty: {a['difficulty']}, Estimated time: {a['estimated_time']} minutes"
        for a in normalized_assignments
    ])

    custom_text = ""
    if custom_tasks:
        custom_text = "\nAdditional tasks:\n" + "\n".join([f"- {t}" for t in custom_tasks])

    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
You are IntelliPlan — an adaptive academic study-planning system. Today is {today}.

Assignments:
{assignment_text}
{custom_text}

The student can study {hours_per_day} hours per day and prefers {preferred_time}.

Create a realistic study plan. Return ONLY valid JSON:
{{
  "schedule": [
    {{
      "date": "YYYY-MM-DD",
      "day_name": "Monday",
      "total_hours": 2,
      "blocks": [
        {{
          "assignment": "Title",
          "course": "Course",
          "duration_minutes": 45,
          "time_slot": "7:00 PM - 7:45 PM",
          "notes": "Focus area",
          "is_break": false
        }}
      ],
      "daily_tip": "Tip"
    }}
  ],
  "overview": "Strategy overview",
  "total_study_time": "X hours Y minutes"
}}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000,
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
    response = flask.make_response(
        flask.send_from_directory("static", "sw.js")
    )
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Service-Worker-Allowed"] = "/"
    return response

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)