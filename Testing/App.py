import flask
from flask import render_template
import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
load_dotenv()  # Load environment variables from .env file

CANVAS_TOKEN = os.getenv("CANVAS_TOKEN")
CANVAS_BASE = "https://canvas.instructure.com/api/v1"
app = flask.Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/live')
def get_live_schedule():
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    course_response = requests.get(f"{CANVAS_BASE}/courses", headers=headers)
    courses = course_response.json()
    course_map = {}
    for c in courses:
        course_map[c["id"]] = c["name"]
    assignments = []
    for course_id in course_map:
        response = requests.get(f"{CANVAS_BASE}/courses/{course_id}/assignments", headers=headers)
        assignments += response.json()
    
    schedule = []
    today = datetime.now(timezone.utc)
    
    for a in assignments:
        if a["due_at"] is None:
            continue
        due_str = a["due_at"]  # "2026-03-31T05:59:59Z"
        due_date = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        days = (due_date - today).days
        
        if days <= 3: priority = "High"
        elif days <= 7: priority = "Medium"
        else: priority = "Low"
        # estimated_time logic here
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