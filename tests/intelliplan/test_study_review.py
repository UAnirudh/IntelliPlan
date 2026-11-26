from datetime import date

from intelliplan.services.study_review import next_week_workload


def test_workload_uses_course_pacing_then_account_history():
    start = date(2026, 11, 30)
    assignments = [
        {"title": "Problem set", "course": "Math", "due_date": start.isoformat(), "estimated_time": 30},
        {"title": "Essay", "course": "English", "due_date": "2026-12-06", "estimated_time": 40},
        {"title": "Past", "due_date": "2026-11-29", "estimated_time": 30},
        {"title": "Later", "due_date": "2026-12-07", "estimated_time": 30},
        {"title": "Done", "due_date": "2026-12-04", "submission_status": "submitted", "estimated_time": 30},
    ]
    logged = [("math", 30, 60)] * 3 + [("History", 30, 45)] * 4
    result = next_week_workload(assignments + assignments[:1], logged, start)
    assert result["minutes"] == 120
    assert [(row["title"], row["minutes"], row["basis"]) for row in result["rows"]] == [
        ("Problem set", 60, "Course check-ins"),
        ("Essay", 60, "Recent check-ins"),
    ]


def test_workload_keeps_base_estimate_with_sparse_history():
    start = date(2026, 11, 30)
    result = next_week_workload([
        {"title": "Essay", "course": "English", "due_date": "2026-12-06", "estimated_time": 45},
        {"title": "Undated", "estimated_time": 200},
    ], [("English", 20, 90)], start)
    assert result["minutes"] == 45
    assert result["rows"][0]["basis"] == "Assignment estimate"
