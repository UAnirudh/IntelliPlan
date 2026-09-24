"""Canvas LMS helper — mirrors the surface area of studentvue_helper.

All functions take (token, base_url) and return JSON-serializable structures
that match the shapes produced by studentvue_helper so the route handlers in
App.py can treat both sources uniformly.
"""

import re
import requests
from urllib.parse import urlparse
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


#: Canvas caps per_page at 100 regardless of what you ask for, so a course
#: with more than 100 assignments needs the Link header followed. The cap
#: below is a runaway guard, not a product limit: 50 pages is 5000 items.
_MAX_PAGES = 50


def _get_list(url, headers, timeout=15):
    """GET a Canvas list endpoint and return *every* page of it.

    Canvas defaults to **ten** items per page. Asking for per_page=100 moves
    that cliff but does not remove it, and every list endpoint here was
    written as a single un-paginated GET -- so a course past the limit had
    the rest of its assignments silently dropped. The symptom is not an
    error: it is a gradebook that renders cleanly while missing the
    assignment you were looking for, which is much harder to notice than a
    failure would be.

    Returns [] on any failure, matching what the callers already expect.
    """
    out = []
    sep = "&" if "?" in url else "?"
    next_url = f"{url}{sep}per_page=100"

    for _ in range(_MAX_PAGES):
        try:
            resp = requests.get(next_url, headers=headers, timeout=timeout)
            page = resp.json()
        except Exception:
            break
        if not isinstance(page, list):
            # An error body ({"errors": [...]}) rather than a list of items.
            # Said out loud: a token whose developer key lacks a scope gets a
            # 401 here, and an empty list is otherwise indistinguishable from
            # "this course has no assignments". The path is logged, never the
            # query string or the token.
            try:
                path = urlparse(next_url).path
            except Exception:
                path = "?"
            print(f"[canvas] {getattr(resp, 'status_code', '?')} non-list body from {path}")
            break
        out.extend(page)

        # Canvas paginates via RFC 5988 Link headers; requests parses them.
        try:
            nxt = resp.links.get("next", {}).get("url")
        except Exception:
            nxt = None
        if not nxt or nxt == next_url:
            break
        next_url = nxt

    return out


def _rubric_len(rubric):
    """Number of rubric criteria, or 0. Canvas omits the key when there is
    no rubric and returns a list of criterion objects when there is one."""
    return len(rubric) if isinstance(rubric, list) else 0


def _estimate_minutes(assignment, points_possible):
    """Minutes this assignment is likely to take.

    Reads the description, submission type, and rubric via the shared sizing
    module, and falls back to the old points heuristic only when the metadata
    says nothing. Rounding to the nearest half hour is deliberately *not*
    applied to a measured figure — "7 problems, 28 minutes" is information,
    and rounding it to 30 throws that information away for tidiness.
    """
    try:
        from intelliplan.intelligence.sizing import size_from_metadata

        sized = size_from_metadata(
            title=assignment.get("name") or "",
            description=assignment.get("description") or "",
            points_possible=points_possible,
            submission_types=assignment.get("submission_types"),
            rubric_rows=_rubric_len(assignment.get("rubric")),
        )
        if sized.is_measured:
            return sized.minutes
    except Exception:
        pass
    return max(30, round(float(points_possible) * 1.5 / 30) * 30)


def test_login(canvas_url, token):
    try:
        r = requests.get(f"{_base(canvas_url)}/courses",
                         headers=_headers(token), timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def _fetch_courses(canvas_url, token, *, include_total_scores=False):
    """Return active Canvas courses, optionally with their official totals.

    Canvas only includes ``computed_current_score`` and
    ``computed_current_grade`` when ``total_scores`` is requested. The
    Grade Modeler previously fetched bare courses, so its assignment detail
    arrived without the same course percentage that the grades page already
    displayed.
    """
    suffix = "?include[]=total_scores&enrollment_state=active" if include_total_scores else ""
    data = _get_list(f"{_base(canvas_url)}/courses{suffix}", _headers(token))
    return [c for c in data if isinstance(c, dict) and "id" in c]


def _course_total(course):
    """Return Canvas's official percentage and letter for one course."""
    pct = None
    letter = None
    for enrollment in course.get("enrollments") or []:
        if not isinstance(enrollment, dict):
            continue
        score = enrollment.get("computed_current_score")
        if score is not None:
            try:
                pct = round(float(score), 1)
            except (TypeError, ValueError):
                pass
        if enrollment.get("computed_current_grade"):
            letter = enrollment["computed_current_grade"]
        if pct is not None:
            break
    return pct, letter


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
            # Canvas defaults to ten items per page, so a course with thirty
            # assignments silently reported the first ten and the planner
            # scheduled a week that was missing two thirds of the work.
            # _get_list follows the Link header, so >100 works too.
            resp = _get_list(f"{base}/courses/{cid}/assignments", headers)
        except Exception:
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
            assignments.append({
                "id": str(a.get("id", "")),
                "course_id": str(a.get("course_id", cid)),
                "title": title,
                "course": course_map.get(cid, "Unknown Course"),
                "due_date": due_str[:10],
                "points_possible": points_possible,
                "priority": priority,
                "estimated_time": _estimate_minutes(a, points_possible),
                "display_score": "",
                "color": PRIORITY_COLORS.get(priority, "#60a5fa"),
                # Sizing metadata. Canvas already returns all of this in the
                # assignments index; it used to be dropped on the floor, which
                # left "read pages 120–145" and "write a 2,000-word essay"
                # indistinguishable whenever they were worth the same points.
                "description": _strip_html(a.get("description"))[:4000],
                "submission_types": a.get("submission_types") or [],
                "rubric": _rubric_len(a.get("rubric")),
                "quiz_id": a.get("quiz_id"),
                "is_quiz": bool(a.get("quiz_id")),
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
    resp = _get_list(
        f"{base}/courses?include[]=total_scores&include[]=teachers"
        f"&enrollment_state=active",
        headers,
    )

    grades = []

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


def _assignments_with_submissions(base, headers, cid):
    """A course's assignments and the student's submission for each.

    Canvas offers students four routes to the same rows, and which ones a
    token may use depends on the scopes on the school's developer key. The
    Grade Modeler used exactly one — ``/courses/:id/assignments`` — so a key
    scoped for courses and enrollments (enough for the course *total*) got a
    401 there and the modeler showed a grade with no assignments under it.
    Each route is tried in turn and the first that answers wins.

    Returns ``(assignments, {assignment_id: submission})``.
    """
    assignments = _get_list(f"{base}/courses/{cid}/assignments", headers)
    if assignments:
        submissions = _get_list(
            f"{base}/courses/{cid}/students/submissions?student_ids[]=self", headers,
        ) or _get_list(f"{base}/courses/{cid}/students/submissions", headers)
        sub_map = {
            s["assignment_id"]: s for s in submissions
            if isinstance(s, dict) and "assignment_id" in s
        }
        return assignments, sub_map

    # The student's own view, submission embedded.
    embedded = _get_list(
        f"{base}/users/self/courses/{cid}/assignments?include[]=submission", headers,
    )
    if not embedded:
        # Assignment groups — what Canvas's own Grades page is built from.
        groups = _get_list(
            f"{base}/courses/{cid}/assignment_groups"
            "?include[]=assignments&include[]=submission",
            headers,
        )
        embedded = [
            a for g in groups if isinstance(g, dict)
            for a in (g.get("assignments") or []) if isinstance(a, dict)
        ]
    if not embedded:
        # Submissions carrying their assignment.
        subs = _get_list(
            f"{base}/courses/{cid}/students/submissions?include[]=assignment", headers,
        )
        embedded = [
            dict(s["assignment"], submission=s) for s in subs
            if isinstance(s, dict) and isinstance(s.get("assignment"), dict)
        ]

    sub_map = {}
    for a in embedded:
        sub = a.get("submission")
        if isinstance(sub, list):
            sub = sub[0] if sub else None
        if isinstance(sub, dict) and a.get("id") is not None:
            sub_map[a["id"]] = sub
    if not embedded:
        print(f"[canvas] course {cid}: no assignment route answered for this token")
    return embedded, sub_map


def get_gradebook_detail(canvas_url, token):
    """Return per-course gradebook detail (assignments + scores).

    Shape mirrors what the frontend expects from studentvue_helper.get_gradebook_detail —
    a list of course dicts each containing assignments with points, weight, and score.
    """
    base = _base(canvas_url)
    headers = _headers(token)
    courses = _fetch_courses(canvas_url, token, include_total_scores=True)

    detail = []
    for c in courses:
        cid = c["id"]
        course_name = c.get("name", "Unknown")

        assignments_raw, sub_map = _assignments_with_submissions(base, headers, cid)

        course_assignments = []
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
                # Never "" for something that has a score. Canvas leaves
                # `grade` empty whenever there is no letter to report -- an
                # assignment graded on points alone, a course with no grading
                # scheme -- while still sending a real `score`. This fell to
                # "", and both gradebook.html and grademodel.html count "" as
                # a pending label, so a graded assignment was classified
                # ungraded and dropped out of the graded view. The score was
                # fetched, carried, and then thrown away at the last step.
                "display_score": (
                    sub.get("grade")
                    or (f"{score_val:g}" if score_val is not None else "Not Graded")
                ),
                "type": a.get("assignment_group_id", ""),
                "weight": "",
                "calculated_mark": "",
            })

        percentage, letter = _course_total(c)

        # A Canvas course can omit total_scores (for example when the
        # instructor has not enabled a scheme). Keep the model usable with a
        # transparent points-based fallback instead of returning an omitted
        # field that the browser renders as "undefined".
        if percentage is None:
            earned_total = sum(
                row["points_earned"] for row in course_assignments
                if isinstance(row["points_earned"], (int, float))
                and isinstance(row["points_possible"], (int, float))
                and row["points_possible"] > 0
            )
            possible_total = sum(
                row["points_possible"] for row in course_assignments
                if isinstance(row["points_earned"], (int, float))
                and isinstance(row["points_possible"], (int, float))
                and row["points_possible"] > 0
            )
            if possible_total:
                percentage = round((earned_total / possible_total) * 100, 1)

        if letter is None and percentage is not None:
            letter = _letter_from_pct(percentage)

        detail.append({
            "course": course_name,
            "percentage": percentage,
            "letter": letter or "N/A",
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
        assignments_raw = _get_list(f"{base}/courses/{cid}/assignments", headers)
        submissions = _get_list(
            f"{base}/courses/{cid}/students/submissions?student_ids[]=self",
            headers,
        )

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
