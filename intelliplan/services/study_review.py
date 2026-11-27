"""Estimate time for dated assignments using the student's own check-ins."""

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from intelliplan.services.assignment_status import requires_student_action


def next_week_workload(assignments, check_ins, start: date):
    """Group estimates by due date. Prefer similar work from the same course."""
    end = start + timedelta(days=7)
    ratios_by_course = defaultdict(list)
    ratios = []
    for course, estimated, actual in check_ins:
        ratio = max(0.5, min(3, actual / estimated))
        ratios.append(ratio)
        ratios_by_course[(course or "").strip().casefold()].append(ratio)
    baseline = median(ratios) if len(ratios) >= 3 else None
    course_ratios = {course: median(values) for course, values in ratios_by_course.items()
                     if course and len(values) >= 3}

    rows = []
    seen = set()
    for assignment in assignments:
        if not requires_student_action(assignment):
            continue
        title = (assignment.get("title") or "").strip()
        course = (assignment.get("course") or "").strip()
        try:
            due = date.fromisoformat(str(assignment.get("due_date") or "")[:10])
        except ValueError:
            continue
        key = (title.casefold(), course.casefold(), due)
        if not title or not start <= due < end or key in seen:
            continue
        seen.add(key)
        try:
            estimate = max(5, min(1440, int(assignment.get("estimated_time") or 30)))
        except (ValueError, TypeError, OverflowError):
            estimate = 30
        course_ratio = course_ratios.get(course.casefold())
        ratio = course_ratio if course_ratio is not None else baseline
        minutes = estimate if ratio is None else max(5, min(1440, round(estimate * ratio / 5) * 5))
        rows.append({
            "title": title, "course": course, "due": due.isoformat(), "minutes": minutes,
            "original_minutes": estimate,
            "basis": ("Course check-ins" if course_ratio is not None else
                      "Recent check-ins" if baseline is not None else "Assignment estimate"),
        })
    rows.sort(key=lambda row: (row["due"], row["course"].casefold(), row["title"].casefold()))
    minutes_by_day = defaultdict(int)
    count_by_day = defaultdict(int)
    courses = {}
    for row in rows:
        minutes_by_day[row["due"]] += row["minutes"]
        count_by_day[row["due"]] += 1
        course = row["course"]
        summary = courses.setdefault(course.casefold(), {
            "course": course, "minutes": 0, "count": 0, "first_due": row["due"],
        })
        summary["minutes"] += row["minutes"]
        summary["count"] += 1
    busiest_due = min(minutes_by_day, key=lambda due: (-minutes_by_day[due], due)) if minutes_by_day else None
    first_assignment = min(rows, key=lambda row: (row["due"], -row["minutes"]), default=None)
    return {
        "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
        "rows": rows, "minutes": sum(row["minutes"] for row in rows),
        "adjustment": sum(row["minutes"] - row["original_minutes"] for row in rows),
        "first_assignment": first_assignment,
        "courses": sorted(courses.values(), key=lambda item: (-item["minutes"], item["first_due"], item["course"].casefold())),
        "busiest_day": ({"due": busiest_due, "minutes": minutes_by_day[busiest_due],
                         "count": count_by_day[busiest_due]}
                        if busiest_due else None),
    }
