import flask
from flask import render_template, request, redirect, session, url_for
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from studentvue_helper import test_login, get_assignments as get_sv_assignments
from groq import Groq
import re


load_dotenv()

app = flask.Flask(
    __name__,
    template_folder="Main_Project/templates",
)
app.secret_key = os.getenv("SECRET_KEY", "intelliplan-dev-key")
app.permanent_session_lifetime = timedelta(days=7)

CANVAS_BASE = "https://canvas.instructure.com/api/v1"

def is_logged_in():
    return 'canvas_token' in session or session.get('login_type') == 'studentvue'

@app.route('/')
def landing():
    return render_template('landing.html', active_page='landing')

@app.route('/schedule')
def home():
    if not is_logged_in():
        return redirect(url_for('login'))
    return render_template('index.html', active_page='home')

@app.route('/priority')
def priority():
    if not is_logged_in():
        return redirect(url_for('login'))
    return render_template('priority.html', active_page='priority')

@app.route('/classes')
def classes():
    if not is_logged_in():
        return redirect(url_for('login'))
    return render_template('classes.html', active_page='classes')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        token = request.form.get('canvas_token', '').strip()
        canvas_url = request.form.get('canvas_url', '').strip().rstrip('/')
        if not token or not canvas_url:
            error = "Please fill in both fields."
        else:
            test = requests.get(
                f"{canvas_url}/api/v1/courses",
                headers={"Authorization": f"Bearer {token}"}
            )
            if test.status_code == 200:
                session.permanent = True
                session['canvas_token'] = token
                session['canvas_url'] = canvas_url
                session['login_type'] = 'canvas'
                return redirect(url_for('home'))
            else:
                error = "Invalid token or Canvas URL. Please check and try again."
    return render_template('login.html', active_page='login', error=error)

@app.route('/login/studentvue', methods=['GET', 'POST'])
def login_studentvue():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        district_url = request.form.get('district_url', '').strip().rstrip('/')
        if not username or not password or not district_url:
            error = "Please fill in all fields."
        else:
            if test_login(district_url, username, password):
                session.permanent = True
                session['sv_username'] = username
                session['sv_password'] = password
                session['sv_district_url'] = district_url
                session['login_type'] = 'studentvue'
                return redirect(url_for('home'))
            else:
                error = "Invalid credentials. Please check and try again."
    return render_template('login_studentvue.html', active_page='login', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))

@app.route('/dismiss', methods=['POST'])
def dismiss():
    assignment_title = request.json.get('title')
    if assignment_title:
        dismissed = session.get('dismissed', [])
        dismissed.append(assignment_title)
        session['dismissed'] = dismissed
        session.modified = True
    return flask.jsonify({"status": "ok"})

@app.route('/live')
def get_live_schedule():
    login_type = session.get('login_type', 'canvas')
    dismissed = session.get('dismissed', [])

    if login_type == 'studentvue':
        username = session.get('sv_username')
        password = session.get('sv_password')
        district_url = session.get('sv_district_url')
        sorted_schedule = get_sv_assignments(district_url, username, password)
        sorted_schedule = [a for a in sorted_schedule if a['title'] not in dismissed]
        return flask.jsonify(sorted_schedule)

    # Canvas login
    token = session.get('canvas_token') or os.getenv("CANVAS_TOKEN")
    canvas_url = session.get('canvas_url') or "https://canvas.instructure.com"
    base = f"{canvas_url}/api/v1"
    headers = {"Authorization": f"Bearer {token}"}

    course_response = requests.get(f"{base}/courses", headers=headers)
    courses = course_response.json()
    course_map = {}
    for c in courses:
        if isinstance(c, dict) and 'id' in c:
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

        schedule.append({
            "title": a["name"],
            "course": course_map.get(a["course_id"], "Unknown Course"),
            "due_date": due_str[:10],
            "points_possible": a["points_possible"],
            "priority": priority,
            "estimated_time": rounded_minutes
        })

    sorted_schedule = sorted(schedule, key=lambda x: x['due_date'])
    sorted_schedule = [a for a in sorted_schedule if a['title'] not in dismissed]
    return flask.jsonify(sorted_schedule)

@app.route('/courses')
def get_courses():
    login_type = session.get('login_type', 'canvas')
    
    if login_type == 'studentvue':
        username = session.get('sv_username')
        password = session.get('sv_password')
        district_url = session.get('sv_district_url')
        from studentvue_helper import get_courses as get_sv_courses
        courses = get_sv_courses(district_url, username, password)
        return flask.jsonify(courses)
    
    # Canvas
    token = session.get('canvas_token') or os.getenv("CANVAS_TOKEN")
    canvas_url = session.get('canvas_url') or "https://canvas.instructure.com"
    base = f"{canvas_url}/api/v1"
    headers = {"Authorization": f"Bearer {token}"}
    course_response = requests.get(f"{base}/courses", headers=headers)
    courses = course_response.json()
    result = []
    for c in courses:
        if isinstance(c, dict) and 'id' in c:
            result.append({"name": c.get("name", "Unknown")})
    return flask.jsonify(result)

@app.route('/grades')
def grades():
    if not is_logged_in():
        return redirect(url_for('login'))
    return render_template('grades.html', active_page='grades')

@app.route('/grades/data')
def grades_data():
    login_type = session.get('login_type', 'canvas')
    if login_type == 'studentvue':
        from studentvue_helper import get_grades as get_sv_grades
        username = session.get('sv_username')
        password = session.get('sv_password')
        district_url = session.get('sv_district_url')
        return flask.jsonify(get_sv_grades(district_url, username, password))
    return flask.jsonify([])

@app.route('/scheduler', methods=['GET', 'POST'])
def scheduler():
    if not is_logged_in():
        return redirect(url_for('login'))
    return render_template('scheduler.html', active_page='scheduler')

@app.route('/generate_schedule', methods=['POST'])
def generate_schedule():
    data = request.json
    assignments = data.get('assignments', [])
    hours_per_day = data.get('hours_per_day', 2)
    preferred_time = data.get('preferred_time', 'evening')
    custom_tasks = data.get('custom_tasks', [])

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    assignment_text = "\n".join([
        f"- {a['title']} ({a['course']}) — Due: {a['due_date']}, Priority: {a['priority']}, Estimated time: {a['estimated_time']} minutes"
        for a in assignments
    ])

    custom_text = ""
    if custom_tasks:
        custom_text = "\nAdditional tasks the student added:\n" + "\n".join([f"- {t}" for t in custom_tasks])

    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""You are an expert academic study scheduler. Today is {today}.

A student has the following assignments due:
{assignment_text}
{custom_text}

The student can study {hours_per_day} hours per day and prefers to study in the {preferred_time}.

IMPORTANT RULES:
- Distribute DIFFERENT assignments across different days — do not repeat the same assignment every day
- Each assignment should only appear as many times as needed based on its estimated time
- Include 10-15 minute breaks between study blocks
- Prioritize high priority assignments first
- Do not schedule work after the due date
- If an assignment takes 30 minutes total, only schedule it once
- Vary the assignments each day to avoid repetition

Create a day-by-day study schedule starting from today. For each day list specific time blocks.

Format your response as JSON with this exact structure:
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

Return ONLY valid JSON, no extra text."""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000
        )
        result = response.choices[0].message.content.strip()
        # Clean up any markdown code blocks
        result = re.sub(r'```json\n?', '', result)
        result = re.sub(r'```\n?', '', result)
        import json
        schedule_data = json.loads(result)
        return flask.jsonify({"status": "ok", "data": schedule_data})
    except Exception as e:
        return flask.jsonify({"status": "error", "message": str(e)})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)