import flask
from flask import render_template, request, redirect, session, url_for
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
from datetime import timedelta


load_dotenv()

app = flask.Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "intelliplan-dev-key")
app.permanent_session_lifetime = timedelta(days=7)

CANVAS_BASE = "https://canvas.instructure.com/api/v1"

@app.route('/')
def landing():
    return render_template('landing.html', active_page='landing')

@app.route('/schedule')
def home():
    if 'canvas_token' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', active_page='home')

@app.route('/priority')
def priority():
    if 'canvas_token' not in session:
        return redirect(url_for('login'))
    return render_template('priority.html', active_page='priority')

@app.route('/classes')
def classes():
    if 'canvas_token' not in session:
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
                return redirect(url_for('home'))
            else:
                error = "Invalid token or Canvas URL. Please check and try again."
    return render_template('login.html', active_page='login', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))

@app.route('/live')
def get_live_schedule():
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

        if days <= 3: priority = "High"
        elif days <= 7: priority = "Medium"
        else: priority = "Low"

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
    return flask.jsonify(sorted_schedule)

if __name__ == '__main__':
    app.run(debug=True)