"""Canvas LMS helper — mirrors the surface area of studentvue_helper.

All functions take (token, base_url) and return JSON-serializable structures
that match the shapes produced by studentvue_helper so the route handlers in
App.py can treat both sources uniformly.
"""

import re
import requests
from datetime import datetime, timezone


PRIORITY_COLORS = {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#22c55e"}
DEFAULT_URL = "https://canvas.instructure.com"


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _base(canvas_url):
    return f"{(canvas_url or DEFAULT_URL).rstrip('/')}/api/v1"


def _strip_html(s):
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", s).strip()


def test_login(canvas_url, token):
    try:
        r = requests.get(f"{_base(canvas_url)}/courses",
                         headers=_headers(token), timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def _fetch_courses(canvas_url, token):
    try:
        r = requests.get(f"{_base(canvas_url)}/courses",
                         headers=_headers(token), timeout=15)
        data = r.json()
        return [c for c in data if isinstance(c, dict) and "id" in c]
    except Exception:
        return []


def get_courses(canvas_url, token):
    return [{"name": c.get("name", "Unknown")} for c in _fetch_courses(canvas_url, token)]


def get_assignments(canvas_url, token):
    """Return upcoming/ungraded assignments in StudentVue-compatible shape."""
    base = _base(canvas_url)
    headers = _headers(token)
    courses = _fetch_courses(canvas_url, token)
    course_map = {c["id"]: c.get("name", "Unknown") for c in courses}

    assignments = []
    today = datetime.now(timezone.utc)

    for cid in course_map:
        try:
            resp = requests.get(f"{base}/courses/{cid}/assignments",
                                headers=headers, timeout=15).json()
        except Exception:
            continue
        if not isinstance(resp, list):
            continue
        for a in resp:
            if not isinstance(a, dict):
                continue
            due_str = a.get("due_at")
            if not due_str:
                continue
            title = a.get("name") or ""
            if not title:
                continue
            points_possible = a.get("points_possible")
            if points_possible is None:
                points_possible = 60
            try:
                due_date = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
            except Exception:
                continue
            days = (due_date - today).days
            if days < -14:
                continue
            if days < 0 or days <= 3:
                priority = "High"
            elif days <= 7:
                priority = "Medium"
            else:
                priority = "Low"
            raw_minutes = float(points_possible) * 1.5
            rounded_minutes = max(30, round(raw_minutes / 30) * 30)
            assignments.append({
                "id": str(a.get("id", "")),
                "course_id": str(a.get("course_id", cid)),
                "title": title,
                "course": course_map.get(cid, "Unknown Course"),
                "due_date": due_str[:10],
                "points_possible": points_possible,
                "priority": priority,
                "estimated_time": rounded_minutes,
                "display_score": "",
                "color": PRIORITY_COLORS.get(priority, "#60a5fa"),
            })

    return sorted(assignments, key=lambda x: x["due_date"])


def _letter_from_pct(pct):
    if pct is None:
        return "N/A"
    if pct >= 93: return "A"
    if pct >= 90: return "A-"
    if pct >= 87: return "B+"
    if pct >= 83: return "B"
    if pct >= 80: return "B-"
    if pct >= 77: return "C+"
    if pct >= 73: return "C"
    if pct >= 70: return "C-"
    if pct >= 67: return "D+"
    if pct >= 63: return "D"
    if pct >= 60: return "D-"
    return "F"


def get_grades(canvas_url, token):
    """Return per-course current grades in StudentVue-compatible shape."""
    base = _base(canvas_url)
    headers = _headers(token)
    try:
        resp = requests.get(
            f"{base}/courses?include[]=total_scores&include[]=teachers&enrollment_state=active",
            headers=headers, timeout=15
        ).json()
    except Exception:
        return []

    grades = []
    if not isinstance(resp, list):
        return []

    for c in resp:
        if not isinstance(c, dict) or "id" not in c:
            continue
        course_name = c.get("name", "Unknown")
        teachers = c.get("teachers") or []
        teacher = ""
        if teachers and isinstance(teachers, list):
            teacher = teachers[0].get("display_name", "") if isinstance(teachers[0], dict) else ""

        enrollments = c.get("enrollments") or []
        pct = None
        letter = None
        for e in enrollments:
            if not isinstance(e, dict):
                continue
            if e.get("computed_current_score") is not None:
                try:
                    pct = round(float(e["computed_current_score"]), 1)
                except Exception:
                    pct = None
            if e.get("computed_current_grade"):
                letter = e["computed_current_grade"]
            if pct is not None:
                break

        if pct is None and not letter:
            continue
        if not letter:
            letter = _letter_from_pct(pct)

        grades.append({
            "course": course_name,
            "teacher": teacher,
            "letter": letter,
            "percentage": pct,
        })

    return grades


def get_gradebook_detail(canvas_url, token):
    """Return per-course gradebook detail (assignments + scores).

    Shape mirrors what the frontend expects from studentvue_helper.get_gradebook_detail —
    a list of course dicts each containing assignments with points, weight, and score.
    """
    base = _base(canvas_url)
    headers = _headers(token)
    courses = _fetch_courses(canvas_url, token)

    detail = []
    for c in courses:
        cid = c["id"]
        course_name = c.get("name", "Unknown")

        try:
            assignments_raw = requests.get(
                f"{base}/courses/{cid}/assignments",
                headers=headers, timeout=15
            ).json()
        except Exception:
            assignments_raw = []
        try:
            submissions = requests.get(
                f"{base}/courses/{cid}/students/submissions?student_ids[]=self&per_page=100",
                headers=headers, timeout=15
            ).json()
        except Exception:
            submissions = []

        sub_map = {}
        if isinstance(submissions, list):
            for s in submissions:
                if isinstance(s, dict) and "assignment_id" in s:
                    sub_map[s["assignment_id"]] = s

        course_assignments = []
        if isinstance(assignments_raw, list):
            for a in assignments_raw:
                if not isinstance(a, dict):
                    continue
                aid = a.get("id")
                points_possible = a.get("points_possible") or 0
                sub = sub_map.get(aid, {})
                score = sub.get("score")
                try:
                    score_val = float(score) if score is not None else None
                except Exception:
                    score_val = None

                course_assignments.append({
                    "title": a.get("name", ""),
                    "due_date": (a.get("due_at") or "")[:10],
                    "points_possible": points_possible,
                    "points_earned": score_val if score_val is not None else "",
                    "score_label": (
                        f"{score_val:g}/{points_possible:g}"
                        if score_val is not None and points_possible
                        else (sub.get("grade") or "")
                    ),
                    "display_score": sub.get("grade") or ("Not Graded" if score_val is None else ""),
                    "type": a.get("assignment_group_id", ""),
                    "weight": "",
                    "calculated_mark": "",
                })

        detail.append({
            "course": course_name,
            "assignments": course_assignments,
        })

    return detail


def get_missing_assignments(canvas_url, token):
    """Return assignments that are missing or have low scores, like studentvue_helper does."""
    base = _base(canvas_url)
    headers = _headers(token)
    courses = _fetch_courses(canvas_url, token)
    course_map = {c["id"]: c.get("name", "Unknown") for c in courses}

    missing = []
    for cid, course_name in course_map.items():
        try:
            assignments_raw = requests.get(
                f"{base}/courses/{cid}/assignments",
                headers=headers, timeout=15
            ).json()
            submissions = requests.get(
                f"{base}/courses/{cid}/students/submissions?student_ids[]=self&per_page=100",
                headers=headers, timeout=15
            ).json()
        except Exception:
            continue

        sub_map = {}
        if isinstance(submissions, list):
            for s in submissions:
                if isinstance(s, dict) and "assignment_id" in s:
                    sub_map[s["assignment_id"]] = s

        if not isinstance(assignments_raw, list):
            continue

        for a in assignments_raw:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            title = a.get("name") or ""
            possible = a.get("points_possible") or 0
            if not title or not possible:
                continue
            sub = sub_map.get(aid, {})
            workflow = (sub.get("workflow_state") or "").lower()
            missing_flag = sub.get("missing") is True
            score = sub.get("score")
            try:
                earned = float(score) if score is not None else None
            except Exception:
                earned = None

            is_missing = False
            if missing_flag:
                is_missing = True
            elif earned is not None and possible > 0 and (earned / possible) < 0.6:
                is_missing = True
            elif workflow == "unsubmitted":
                due = a.get("due_at")
                if due:
                    try:
                        due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                        if due_dt < datetime.now(timezone.utc):
                            is_missing = True
                    except Exception:
                        pass

            if not is_missing:
                continue

            due_date = (a.get("due_at") or "")[:10]
            missing.append({
                "title": title,
                "course": course_name,
                "due_date": due_date,
                "points_earned": earned if earned is not None else 0,
                "points_possible": possible,
                "display_score": sub.get("grade") or "Missing",
                "priority": "High",
                "estimated_time": 60,
                "source": "canvas_missing",
                "is_missing": True,
                "score_label": (
                    f"{int(earned)}/{int(possible)}"
                    if earned is not None else f"0/{int(possible)}"
                ),
                "color": PRIORITY_COLORS["High"],
                "difficulty": "Medium",
            })

    return missing
