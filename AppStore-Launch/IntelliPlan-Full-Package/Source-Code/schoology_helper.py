from requests_oauthlib import OAuth1
import requests

SCHOOLOGY_BASE = "https://api.schoology.com/v1"

def make_schoology_request(key, secret, endpoint):
    auth = OAuth1(key, secret)
    response = requests.get(f"{SCHOOLOGY_BASE}{endpoint}", auth=auth)
    return response.json()

def test_schoology_login(key, secret):
    try:
        result = make_schoology_request(key, secret, "/users/me")
        return "uid" in result
    except:
        return False

def get_schoology_courses(key, secret):
    data = make_schoology_request(key, secret, "/courses")
    courses = data.get("course", [])
    return [{"name": c.get("title", "Unknown")} for c in courses]

def get_schoology_assignments(key, secret):
    from datetime import datetime, timezone
    PRIORITY_COLORS = {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#22c55e"}

    sections_data = make_schoology_request(key, secret, "/sections")
    sections = sections_data.get("section", [])

    assignments = []
    today = datetime.now(timezone.utc)

    for section in sections:
        sid = section.get("id")
        course_title = section.get("course_title", "Unknown")
        assignments_data = make_schoology_request(key, secret, f"/sections/{sid}/assignments")
        for a in assignments_data.get("assignment", []):
            due = a.get("due", "")
            if not due:
                continue
            try:
                due_date = datetime.strptime(due, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except:
                continue
            days = (due_date - today).days
            if days < -14:
                continue
            priority = "High" if days <= 3 else "Medium" if days <= 7 else "Low"
            points = float(a.get("max_points", 60) or 60)
            rounded = max(30, round(points * 1.5 / 30) * 30)
            assignments.append({
                "id": str(a.get("id", "")),
                "course_id": str(sid),
                "title": a.get("title", "Untitled"),
                "course": course_title,
                "due_date": due_date.strftime("%Y-%m-%d"),
                "points_possible": points,
                "priority": priority,
                "difficulty": "Medium",
                "estimated_time": rounded,
                "color": PRIORITY_COLORS.get(priority, "#60a5fa"),
                "description": a.get("description", ""),
            })

    return sorted(assignments, key=lambda x: x["due_date"])

def get_schoology_grades(key, secret):
    grades_data = make_schoology_request(key, secret, "/users/me/grades")
    result = []
    for section in grades_data.get("section", []):
        final = section.get("final_grade", [{}])
        if isinstance(final, list) and final:
            grade = final[0]
        elif isinstance(final, dict):
            grade = final
        else:
            continue
        letter = grade.get("grade", "")
        percentage = grade.get("score", None)
        if not letter:
            continue
        result.append({
            "course": section.get("course_title", "Unknown"),
            "teacher": "",
            "letter": letter,
            "percentage": round(float(percentage), 1) if percentage else None,
        })
    return result