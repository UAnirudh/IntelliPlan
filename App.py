import flask
from flask import render_template, request, redirect, session, url_for
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from studentvue_helper import test_login, get_assignments as get_sv_assignments
from groq import Groq
import re
import json
from pathlib import Path


load_dotenv()

app = flask.Flask(
    __name__,
    template_folder="Main_Project/templates",
)
app.secret_key = os.getenv("SECRET_KEY", "intelliplan-dev-key")
app.permanent_session_lifetime = timedelta(days=7)

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
DESCRIPTIONS_FILE = Path("descriptions.json")
DISMISSED_FILE = Path("dismissed.json")

def load_json_file(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}

def save_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

def is_logged_in():
    return "canvas_token" in session or session.get("login_type") == "studentvue"


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
            "summary": "The student is most focused earlier in the day. Front-load harder work first, then taper into lighter review.",
            "recommended_start_hour": 7,
            "hard_task_window": "7:00 AM - 11:00 AM",
            "light_task_window": "11:00 AM - 1:00 PM",
        },
        "afternoon": {
            "label": "afternoon",
            "summary": "The student reaches peak momentum in the middle of the day. Place demanding work first, then switch into medium and lighter tasks.",
            "recommended_start_hour": 1,
            "hard_task_window": "1:00 PM - 4:00 PM",
            "light_task_window": "4:00 PM - 6:00 PM",
        },
        "evening": {
            "label": "evening",
            "summary": "The student is most productive later in the day. Begin with the highest-focus work in the early evening, then move to lighter tasks as the night progresses.",
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
        if hour < 10:
            return "peak"
        if hour < 13:
            return "steady"
        return "wind-down"

    if preference == "afternoon":
        if 13 <= hour < 16:
            return "peak"
        if 11 <= hour < 18:
            return "steady"
        return "wind-down"

    if 18 <= hour < 21:
        return "peak"
    if 16 <= hour < 22:
        return "steady"
    if difficulty == "Hard":
        return "steady"
    return "wind-down"


def build_daily_tip(workload_level, preferred_time, high_priority_count, hard_task_count):
    preference = (preferred_time or "evening").lower()

    if workload_level == "heavy":
        return (
            f"Today is a heavier {preference} workload, so protect your focus for the first block "
            "and let the breaks reset you before the next push."
        )
    if hard_task_count >= 2:
        return (
            "You have multiple demanding tasks today, so aim for clean starts: remove distractions "
            "before each hard block and keep the easier work for the end."
        )
    if high_priority_count >= 1:
        return (
            "Knock out the urgent task first while your attention is strongest, then use the later "
            "blocks to build momentum with lighter work."
        )
    return (
        "This is a balanced day, so focus on consistency: finish each block fully and let the lighter "
        "sessions keep your momentum steady."
    )


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
                block.get("time_slot"),
                preferred_time,
                difficulty,
            )

            if priority == "High":
                high_priority_count += 1
            if difficulty == "Hard":
                hard_task_count += 1

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
            workload_level,
            preferred_time,
            high_priority_count,
            hard_task_count,
        )

        if not day.get("total_hours"):
            day["total_hours"] = round(total_minutes / 60, 1)

    schedule_data["energy_profile"] = get_energy_profile(preferred_time)
    schedule_data["total_study_time"] = (
        f"{total_study_minutes // 60} hours {total_study_minutes % 60} minutes"
    )
    return schedule_data


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


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        token = request.form.get("canvas_token", "").strip()
        canvas_url = request.form.get("canvas_url", "").strip().rstrip("/")
        if not token or not canvas_url:
            error = "Please fill in both fields."
        else:
            test = requests.get(
                f"{canvas_url}/api/v1/courses",
                headers={"Authorization": f"Bearer {token}"},
            )
            if test.status_code == 200:
                session.permanent = True
                session["canvas_token"] = token
                session["canvas_url"] = canvas_url
                session["login_type"] = "canvas"
                return redirect(url_for("home"))
            else:
                error = "Invalid token or Canvas URL. Please check and try again."
    return render_template("login.html", active_page="login", error=error)


@app.route("/login/studentvue", methods=["GET", "POST"])
def login_studentvue():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        district_url = request.form.get("district_url", "").strip().rstrip("/")
        if not username or not password or not district_url:
            error = "Please fill in all fields."
        else:
            if test_login(district_url, username, password):
                session.permanent = True
                session["sv_username"] = username
                session["sv_password"] = password
                session["sv_district_url"] = district_url
                session["login_type"] = "studentvue"
                return redirect(url_for("home"))
            else:
                error = "Invalid credentials. Please check and try again."
    return render_template("login_studentvue.html", active_page="login", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))

@app.route("/live")
def get_live_schedule():
    login_type = session.get("login_type", "canvas")
    dismissed = list(load_json_file(DISMISSED_FILE).keys())

    if login_type == "studentvue":
        username = session.get("sv_username")
        password = session.get("sv_password")
        district_url = session.get("sv_district_url")
        sorted_schedule = get_sv_assignments(district_url, username, password)
        sorted_schedule = [a for a in sorted_schedule if a["title"] not in dismissed]
        return flask.jsonify(sorted_schedule)

    token = session.get("canvas_token") or os.getenv("CANVAS_TOKEN")
    canvas_url = session.get("canvas_url") or "https://canvas.instructure.com"
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
        if not isinstance(a, dict):
            continue
        if a.get("due_at") is None:
            continue
        if a.get("points_possible") is None:
            a["points_possible"] = 60

        due_str = a["due_at"]
        due_date = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        days = (due_date - today).days

        if days < -14:
            continue
        elif days < 0:
            priority = "High"
        elif days <= 3:
            priority = "High"
        elif days <= 7:
            priority = "Medium"
        else:
            priority = "Low"

        raw_minutes = a["points_possible"] * 1.5
        rounded_minutes = round(raw_minutes / 30) * 30
        rounded_minutes = max(30, rounded_minutes)
        difficulty = infer_task_difficulty(a["points_possible"], priority, due_str[:10])

        schedule.append(
            {
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
            }
        )

    sorted_schedule = sorted(schedule, key=lambda x: x["due_date"])
    sorted_schedule = [a for a in sorted_schedule if a["title"] not in dismissed]
    return flask.jsonify(sorted_schedule)


@app.route("/courses")
def get_courses():
    login_type = session.get("login_type", "canvas")

    if login_type == "studentvue":
        username = session.get("sv_username")
        password = session.get("sv_password")
        district_url = session.get("sv_district_url")
        from studentvue_helper import get_courses as get_sv_courses

        courses = get_sv_courses(district_url, username, password)
        return flask.jsonify(courses)

    token = session.get("canvas_token") or os.getenv("CANVAS_TOKEN")
    canvas_url = session.get("canvas_url") or "https://canvas.instructure.com"
    base = f"{canvas_url}/api/v1"
    headers = {"Authorization": f"Bearer {token}"}
    course_response = requests.get(f"{base}/courses", headers=headers)
    courses = course_response.json()
    result = []
    for c in courses:
        if isinstance(c, dict) and "id" in c:
            result.append({"name": c.get("name", "Unknown")})
    return flask.jsonify(result)


@app.route("/grades")
def grades():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("grades.html", active_page="grades")


@app.route("/grades/data")
def grades_data():
    login_type = session.get("login_type", "canvas")
    if login_type == "studentvue":
        from studentvue_helper import get_grades as get_sv_grades

        username = session.get("sv_username")
        password = session.get("sv_password")
        district_url = session.get("sv_district_url")
        return flask.jsonify(get_sv_grades(district_url, username, password))
    return flask.jsonify([])


@app.route("/scheduler", methods=["GET", "POST"])
def scheduler():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("scheduler.html", active_page="scheduler")


@app.route("/dismiss", methods=["POST"])
def dismiss():
    assignment = request.json
    title = assignment.get("title")
    if title:
        dismissed = load_json_file(DISMISSED_FILE)
        dismissed[title] = assignment  # store full object so we can restore it
        save_json_file(DISMISSED_FILE, dismissed)
        # keep session in sync
        session["dismissed"] = list(dismissed.keys())
        session.modified = True
    return flask.jsonify({"status": "ok"})

@app.route("/dismissed")
def dismissed_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("dismissed.html", active_page="dismissed")

@app.route("/dismissed/data")
def dismissed_data():
    dismissed = load_json_file(DISMISSED_FILE)
    return flask.jsonify(list(dismissed.values()))

@app.route("/restore", methods=["POST"])
def restore():
    title = request.json.get("title")
    if title:
        dismissed = load_json_file(DISMISSED_FILE)
        dismissed.pop(title, None)
        save_json_file(DISMISSED_FILE, dismissed)
        session["dismissed"] = list(dismissed.keys())
        session.modified = True
    return flask.jsonify({"status": "ok"})

@app.route("/assignment/description", methods=["GET"])
def get_description():
    assignment_id = request.args.get("id")
    course_id = request.args.get("course_id")
    title = request.args.get("title", "")

    # Check custom descriptions first
    descriptions = load_json_file(DESCRIPTIONS_FILE)
    if title in descriptions:
        return flask.jsonify({"description": descriptions[title], "source": "custom"})

    login_type = session.get("login_type", "canvas")

    if login_type == "canvas" and assignment_id and course_id:
        token = session.get("canvas_token") or os.getenv("CANVAS_TOKEN")
        canvas_url = session.get("canvas_url") or "https://canvas.instructure.com"
        resp = requests.get(
            f"{canvas_url}/api/v1/courses/{course_id}/assignments/{assignment_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data.get("description") or ""
            # Strip HTML tags simply
            import re
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
        descriptions = load_json_file(DESCRIPTIONS_FILE)
        descriptions[title] = description
        save_json_file(DESCRIPTIONS_FILE, descriptions)
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
        normalized_assignment = {
            **assignment,
            "difficulty": difficulty,
            "color": assignment.get("color")
            or PRIORITY_COLORS.get(assignment.get("priority", "Medium"), "#60a5fa"),
        }
        normalized_assignments.append(normalized_assignment)

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    energy_profile = get_energy_profile(preferred_time)

    assignment_text = "\n".join(
        [
            (
                f"- {a['title']} ({a['course']}) — Due: {a['due_date']}, Priority: {a['priority']}, "
                f"Difficulty: {a['difficulty']}, Estimated time: {a['estimated_time']} minutes"
            )
            for a in normalized_assignments
        ]
    )

    custom_text = ""
    if custom_tasks:
        custom_text = (
            "\nAdditional tasks the student added:\n"
            + "\n".join([f"- {t}" for t in custom_tasks])
        )

    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
You are IntelliPlan — an adaptive academic study‑planning system. Today is {today}.

The student has the following assignments due:
{assignment_text}
{custom_text}

The student can study {hours_per_day} hours per day and prefers to study in the {preferred_time}.

---------------------------------------
CORE OBJECTIVES:
---------------------------------------
1. Create a realistic, human‑friendly study plan that avoids burnout.
2. Ensure all assignments are completed before their due dates.
3. Adapt dynamically: if workload is heavy, distribute it across more days; if light, use fewer days.
4. Maintain cognitive variety by alternating heavy and light tasks.
5. Respect the student’s preferred study time and daily hour limit.
6. Produce a schedule that feels varied, balanced, and natural across the entire timeline.

---------------------------------------
DYNAMIC SCHEDULE LENGTH:
---------------------------------------
- Determine the optimal number of days needed based on:
  • total estimated workload  
  • daily study limit  
  • assignment due dates  
- If the student can finish early, generate fewer days.
- If the workload requires more than a week, extend the schedule automatically.
- Never exceed assignment due dates; the schedule must end when all tasks are completed.

---------------------------------------
NON‑NEGOTIABLE RULES:
---------------------------------------
- Assign different tasks on different days; do not repeat the same assignment every day.
- Avoid scheduling the same assignment on consecutive days unless absolutely required.
- Schedule each assignment only as many times as required based on its estimated duration.
- Prioritize high‑priority assignments first.
- Never schedule work past an assignment’s due date.
- If an assignment requires only 30 minutes total, schedule it once.
- Time blocks must not overlap and must follow a natural flow.
- If preferred time is evening, avoid scheduling past a reasonable hour (e.g., not beyond 11 PM unless necessary).

---------------------------------------
SESSION STRUCTURE & ADAPTIVE PACING:
---------------------------------------
- Prefer shorter, more frequent study sessions:
  • Typical session length: 25–45 minutes  
  • Longer sessions (50–60 minutes) only when necessary  
- Break long assignments into multiple smaller sessions across different days.
- Increase the number of sessions per day while keeping total hours within the limit.
- Vary session lengths to avoid predictable patterns.
- Sessions should become slightly shorter later in the day to reflect natural fatigue.

---------------------------------------
ADVANCED ADAPTIVE BREAK LOGIC:
---------------------------------------
- Break duration must scale with session intensity:
  • 25–35 minute session → 5–8 minute break  
  • 35–45 minute session → 8–12 minute break  
  • 45–60 minute session → 12–15 minute break  
- Insert micro‑breaks (2–4 minutes) between mentally demanding back‑to‑back tasks.
- Breaks must feel natural and human:
  • No identical break lengths in a row  
  • No repeating the same break pattern daily  
- Breaks should appear after each session unless the next session is extremely light.

---------------------------------------
COGNITIVE FATIGUE MODELING:
---------------------------------------
- Alternate heavy and light tasks within the same day.
- Avoid stacking more than two heavy tasks in a row.
- Insert additional micro‑breaks later in the day to simulate mental fatigue.
- Use lighter tasks toward the end of the day.

---------------------------------------
DIVERSIFICATION & DISTRIBUTION:
---------------------------------------
- Distribute assignments across the entire schedule so each day contains a different mix of tasks.
- No two days should have the same combination of assignments.
- Every assignment should appear at least once unless completed in a single session.
- Spread longer assignments across multiple days rather than clustering them.
- Rotate assignments so each task appears, then rests for at least one day before appearing again (unless deadlines require otherwise).
- Ensure daily variety in task order, cognitive load, and block structure.

---------------------------------------
OPTIONAL TIME USAGE:
---------------------------------------
- If a day has leftover time after required tasks, you may add:
  • light review  
  • preview of upcoming material  
  • organization or planning time  
—but only after all required work is scheduled.

---------------------------------------
OUTPUT REQUIREMENTS:
---------------------------------------
Create a day‑by‑day study plan starting today. Each day must include:
- Ordered time blocks with accurate start and end times
- Breaks where appropriate
- A short motivational daily tip
- A total_hours field that matches the sum of all study + break time
- The schedule should end exactly when all assignments are completed.

---------------------------------------
JSON FORMAT (USE EXACT STRUCTURE):
---------------------------------------
{{
  "schedule": [
    {{
      "date": "YYYY-MM-DD",
      "day_name": "Monday",
      "total_hours": 2,
      "blocks": [
        {{
          "assignment": "Assignment title",
          "course": "Course name",
          "duration_minutes": 60,
          "time_slot": "7:00 PM - 8:00 PM",
          "notes": "What to focus on",
          "is_break": false
        }},
        {{
          "assignment": "Break",
          "course": "",
          "duration_minutes": 10,
          "time_slot": "8:00 PM - 8:10 PM",
          "notes": "Rest and recharge",
          "is_break": true
        }}
      ],
      "daily_tip": "Short motivational tip"
    }}
  ],
  "overview": "Brief overview of the study strategy",
  "total_study_time": "X hours Y minutes"
}}

Return ONLY valid JSON with no additional text.
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
            schedule_data,
            normalized_assignments,
            preferred_time,
            hours_per_day,
        )
        return flask.jsonify({"status": "ok", "data": schedule_data})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})


@app.route("/gradebook/detail")
def gradebook_detail():
    if not is_logged_in():
        return flask.jsonify([])
    login_type = session.get("login_type", "canvas")
    if login_type == "studentvue":
        from studentvue_helper import get_gradebook_detail
        username = session.get("sv_username")
        password = session.get("sv_password")
        district_url = session.get("sv_district_url")
        return flask.jsonify(get_gradebook_detail(district_url, username, password))
    return flask.jsonify([])


# @app.route("/grademodel")
# def grademodel():
#     if not is_logged_in():
#         return redirect(url_for("login"))
#     return render_template("grademodel.html", active_page="grademodel")

@app.route("/gradebook")
def gradebook():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("gradebook.html", active_page="gradebook")


@app.route('/static/sw.js')
def service_worker():
    response = flask.make_response(
        flask.send_from_directory('static', 'sw.js')
    )
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

@app.context_processor
def inject_auth():
    return dict(logged_in=is_logged_in())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)