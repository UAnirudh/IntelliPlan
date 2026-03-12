import flask
from flask import render_template
import requests
import os
from dotenv import load_dotenv
import TestData
from datetime import datetime, timezone
load_dotenv()  # Load environment variables from .env file

CANVAS_TOKEN = os.getenv("CANVAS_TOKEN")
CANVAS_BASE = "https://canvas.instructure.com/api/v1"
app = flask.Flask(__name__)

# @app.route('/')
# def hello():
#     return 'Hello, World!'

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/schedule')
def data():
    return flask.jsonify(TestData.generate_schedule(TestData.assignments))

@app.route('/courses')
def get_courses():
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    response = requests.get(f"{CANVAS_BASE}/courses", headers=headers)
    return flask.jsonify(response.json())

@app.route('/assignments')
def get_assignments():
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    response = requests.get(f"{CANVAS_BASE}/courses/14365743/assignments", headers=headers)
    return flask.jsonify(response.json())

@app.route('/clean')
def get_clean_assignments():
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    response = requests.get(f"{CANVAS_BASE}/courses/14365743/assignments", headers=headers)
    assignments = response.json()  
    clean = []
    for a in assignments:
        clean.append({
            "name": a["name"],
            "due_at": a["due_at"],
            "points_possible": a["points_possible"],
            "course_id": a["course_id"]
        })
    sorted_clean = sorted(clean, key=lambda x: x['due_at'])
    return flask.jsonify(sorted_clean)
@app.route('/live')
def get_live_schedule():
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    response = requests.get(f"{CANVAS_BASE}/courses/14365743/assignments", headers=headers)
    assignments = response.json()
    
    schedule = []
    today = datetime.now(timezone.utc)
    
    for a in assignments:
        due_str = a["due_at"]  # "2026-03-31T05:59:59Z"
        due_date = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        days = (due_date - today).days
        
        # priority logic here
        days = (due_date - today).days
        if days <= 3: priority = "High"
        elif days <= 7: priority = "Medium"
        else: priority = "Low"
        # estimated_time logic here
        raw_minutes = a["points_possible"] * 1.5
        rounded_minutes = round(raw_minutes / 30) * 30
        schedule.append({
            "title": a["name"],
            "course": a["course_id"],
            "due_date": due_str[:10],
            "points_possible": a["points_possible"],
            "priority": priority,
            "estimated_time": rounded_minutes
        })
    
    sorted_schedule = sorted(schedule, key=lambda x: x['due_date'])
    return flask.jsonify(sorted_schedule)
if __name__ == '__main__':
    app.run(debug=True)