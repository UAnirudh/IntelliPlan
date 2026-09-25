"""The public demo week the landing page and FAQ promise.

Checks that it is produced by the real engines (ranked, scheduled, dated
relative to today) and that the page renders without an account.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from intelliplan.services import demo

TODAY = date(2026, 9, 28)  # a Monday


@pytest.fixture(scope="module")
def week():
    return demo.build(TODAY)


def test_every_sample_assignment_is_ranked_highest_first(week):
    scores = [p["score"] for p in week["priorities"]]
    assert len(scores) == len(demo.SAMPLE)
    assert scores == sorted(scores, reverse=True)
    assert all(p["label"] in {"High", "Medium", "Low"} for p in week["priorities"])


def test_the_week_is_scheduled_and_nothing_lands_after_its_due_date(week):
    due = {title: TODAY.toordinal() + days for _, title, _, _, days, _, _ in demo.SAMPLE}
    assert week["days"] and week["total_minutes"] > 0
    for day in week["days"]:
        d = date.fromisoformat(day["date"]).toordinal()
        for b in day["blocks"]:
            parent = b.get("parent_title") or b.get("assignment")
            base = next((t for t in due if str(parent).startswith(t)), None)
            if base:
                assert d <= due[base], f"{b['assignment']} placed after it is due"


def test_sittings_stay_inside_the_stated_study_windows(week):
    for day in week["days"]:
        weekend = date.fromisoformat(day["date"]).weekday() >= 5
        lo, hi = ("10:00", "16:00") if weekend else ("15:30", "21:30")
        for b in day["blocks"]:
            start = str(b["time_slot"]).split(" - ")[0]
            t = datetime.strptime(start, "%I:%M %p").strftime("%H:%M")
            assert lo <= t < hi, f"{day['label']} {b['time_slot']}"


def test_the_demo_page_renders_for_anyone():
    import App
    body = App.app.test_client().get("/demo").get_data(as_text=True)
    assert "How IntelliPlan sorts a week" in body
    assert "Related rates problem set" in body
    assert "could not be built" not in body
