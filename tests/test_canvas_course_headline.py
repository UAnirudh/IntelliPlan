"""A Canvas course must arrive with a grade on it, not the word "undefined".

Reported symptom: "I have a 100 percent in History and it shows as
undefined."

``/gradebook/detail`` is the feed behind the Grade Modeler, and the page
writes three of its fields straight into the header — ``percentage``,
``letter`` and ``categories``. The StudentVue reader sends all three. The
Canvas reader sent a course name and a list of assignments and nothing
else, so the header rendered the literal text ``undefined`` where the
letter grade goes and ``undefined%`` beside it, and the category weight
panel and category tabs came up empty. A course at 100% showed it exactly
as loudly as a failing one.

The fix is in the reader, where the missing fields are: the enrollment's
own computed score (what Canvas itself shows the student, already
honouring the course's weighting and grading scheme), its letter, and the
assignment groups that are Canvas's categories. The templates carry a
belt-and-braces guard too, so a course from any provider that omits a
total is totalled from its graded work rather than printed raw.
"""

from __future__ import annotations

import types

import pytest

import canvas_helper


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.links = {}
        self.status_code = 200

    def json(self):
        return self._payload


def install_canvas(monkeypatch, *, course, groups, assignments, submissions):
    def fake_get(url, headers=None, timeout=None):
        if "assignment_groups" in url:
            return _Resp(groups)
        if "/courses/55/assignments" in url:
            return _Resp(assignments)
        if "submissions" in url:
            return _Resp(submissions)
        if url.rstrip("/").endswith("/courses") or "/courses?" in url:
            return _Resp([course])
        return _Resp([])

    monkeypatch.setattr(canvas_helper, "requests", types.SimpleNamespace(get=fake_get))


def history_course(monkeypatch, **kwargs):
    install_canvas(monkeypatch, **kwargs)
    detail = canvas_helper.get_gradebook_detail("https://x", "tok")
    return next(c for c in detail if "History" in c["course"])


PERFECT = dict(
    course={
        "id": 55,
        "name": "AP United States History",
        "teachers": [{"display_name": "Mr. Ramirez"}],
        "enrollments": [{"computed_current_score": 100.0, "computed_current_grade": "A"}],
    },
    groups=[
        {"id": 7, "name": "Homework", "group_weight": 40},
        {"id": 8, "name": "Essays", "group_weight": 60},
    ],
    assignments=[
        {"id": 1, "name": "Chapter 12 guide", "points_possible": 20,
         "due_at": "2026-09-02T00:00:00Z", "assignment_group_id": 7},
        {"id": 2, "name": "DBQ: Reconstruction", "points_possible": 50,
         "due_at": "2026-09-05T00:00:00Z", "assignment_group_id": 8},
        {"id": 3, "name": "Unit 3 test", "points_possible": 100,
         "due_at": "2026-09-30T00:00:00Z", "assignment_group_id": 8},
    ],
    submissions=[
        {"assignment_id": 1, "score": 20, "grade": "A"},
        {"assignment_id": 2, "score": 50, "grade": "A"},
    ],
)


# ── The reported bug ─────────────────────────────────────────────────


def test_a_hundred_percent_course_reports_a_hundred_percent(monkeypatch):
    course = history_course(monkeypatch, **PERFECT)
    assert course["percentage"] == 100.0
    assert course["letter"] == "A"


@pytest.mark.parametrize("field", ["percentage", "letter", "categories", "teacher"])
def test_every_field_the_page_reads_is_present(monkeypatch, field):
    """The page reads these without checking. Absent is how "undefined"
    reaches a student's screen."""
    course = history_course(monkeypatch, **PERFECT)
    assert field in course
    assert course[field] is not None


# ── Categories ───────────────────────────────────────────────────────


def test_categories_carry_the_group_name_not_its_id(monkeypatch):
    """Canvas's assignment_group_id is a number. It was being used as the
    category label, so the modeler offered tabs called "7" and "8"."""
    course = history_course(monkeypatch, **PERFECT)
    names = {c["type"] for c in course["categories"]}
    assert names == {"Homework", "Essays"}
    assert {a["category"] for a in course["assignments"]} == {"Homework", "Essays"}


def test_categories_carry_their_weight(monkeypatch):
    course = history_course(monkeypatch, **PERFECT)
    weights = {c["type"]: c["weight"] for c in course["categories"]}
    assert weights == {"Homework": 40, "Essays": 60}


def test_only_graded_work_counts_toward_a_category_total(monkeypatch):
    """The ungraded unit test must not drag Essays down to a third."""
    course = history_course(monkeypatch, **PERFECT)
    essays = next(c for c in course["categories"] if c["type"] == "Essays")
    assert essays["points"] == 50
    assert essays["points_possible"] == 50
    assert essays["weighted_pct"] == 100.0


# ── Fallbacks ────────────────────────────────────────────────────────


def test_a_hidden_total_falls_back_to_the_points_on_the_page(monkeypatch):
    """A teacher can hide course totals from students, and then Canvas sends
    no computed score at all. Showing nothing is worse than showing the
    arithmetic the student can already do themselves."""
    payload = dict(PERFECT)
    payload["course"] = dict(PERFECT["course"], enrollments=[{}])
    course = history_course(monkeypatch, **payload)
    assert course["percentage"] == 100.0
    assert course["letter"] == "A"


def test_a_missing_letter_is_derived_from_the_percentage(monkeypatch):
    """A course with no grading scheme sends a score and no letter."""
    payload = dict(PERFECT)
    payload["course"] = dict(
        PERFECT["course"],
        enrollments=[{"computed_current_score": 84.0}],
    )
    course = history_course(monkeypatch, **payload)
    assert course["percentage"] == 84.0
    assert course["letter"] == "B"


def test_canvas_own_letter_wins_over_our_scale(monkeypatch):
    """Schools set their own cutoffs. When Canvas states the letter, that is
    the answer — ours is only ever a fallback."""
    payload = dict(PERFECT)
    payload["course"] = dict(
        PERFECT["course"],
        enrollments=[{"computed_current_score": 89.0, "computed_current_grade": "A"}],
    )
    course = history_course(monkeypatch, **payload)
    assert course["letter"] == "A"


def test_a_course_with_nothing_graded_reports_no_percentage(monkeypatch):
    """Not a zero, and not "undefined" — genuinely nothing to report yet."""
    payload = dict(PERFECT)
    payload["course"] = dict(PERFECT["course"], enrollments=[{}])
    payload["submissions"] = []
    course = history_course(monkeypatch, **payload)
    assert course["percentage"] is None
    assert course["letter"] == ""


def test_a_failed_assignment_groups_call_does_not_lose_the_course(monkeypatch):
    """Group weights are a nicety; the grade is not. A token without the
    assignment_groups scope must still produce a usable course."""
    def fake_get(url, headers=None, timeout=None):
        if "assignment_groups" in url:
            raise OSError("403")
        if "/courses/55/assignments" in url:
            return _Resp(PERFECT["assignments"])
        if "submissions" in url:
            return _Resp(PERFECT["submissions"])
        if url.rstrip("/").endswith("/courses") or "/courses?" in url:
            return _Resp([PERFECT["course"]])
        return _Resp([])

    monkeypatch.setattr(canvas_helper, "requests", types.SimpleNamespace(get=fake_get))
    course = next(c for c in canvas_helper.get_gradebook_detail("https://x", "tok")
                  if "History" in c["course"])
    assert course["percentage"] == 100.0
    assert course["letter"] == "A"
    assert len(course["assignments"]) == 3


# ── The /grades feed shares the same reader ──────────────────────────


def test_the_grades_list_still_reports_the_same_course(monkeypatch):
    install_canvas(monkeypatch, **PERFECT)
    grades = canvas_helper.get_grades("https://x", "tok")
    row = next(g for g in grades if "History" in g["course"])
    assert row["percentage"] == 100.0
    assert row["letter"] == "A"
    assert row["teacher"] == "Mr. Ramirez"
