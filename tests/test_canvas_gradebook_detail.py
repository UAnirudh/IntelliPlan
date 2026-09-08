"""A graded Canvas assignment must actually reach the gradebook.

Reported symptom: a grade came in for a History assignment and did not show
up. Two independent causes, either of which produces exactly that, and both
of which fail silently -- the page renders cleanly, just without the row.

1. **Only the first ten assignments were ever fetched.** Canvas defaults to
   ten items per page. ``get_assignments`` had already been fixed for this
   (its comment spells the bug out), but ``get_gradebook_detail``,
   ``get_missing_assignments``, ``get_grades`` and ``_fetch_courses`` were
   all still single un-paginated GETs. In a course with more than ten
   assignments the newly graded one usually is not in the first ten, and
   Canvas orders that endpoint by position rather than by date, so which ten
   you get is arbitrary -- matching "some of my grades show and this one
   doesn't". ``per_page=100`` alone only moves the cliff, so ``_get_list``
   follows the Link header.

2. **A score with no letter grade was labelled ""**. Canvas leaves ``grade``
   empty whenever there is no letter to report -- an assignment graded on
   points alone, or a course with no grading scheme -- while still sending a
   real ``score``. ``display_score`` fell through to ``""``, and both
   gradebook.html and grademodel.html list ``""`` among their pending
   labels, so the row was classified ungraded and dropped out of the graded
   view. The score was fetched, carried the whole way, and discarded at the
   last step. This one does not need a big course: it hides a grade on
   assignment number two.
"""

from __future__ import annotations

import types

import pytest

import canvas_helper


#: The frontend's own graded test, transcribed from gradebook.html's
#: isGraded() and grademodel.html's normalizeAssignment(). Kept here so a
#: change to display_score that breaks the page fails a test instead.
_PENDING_LABELS = {
    "", "not graded", "not due", "missing", "pending", "ungraded", "-", "–", "n/a",
}


def frontend_counts_as_graded(row):
    if row is None:
        return False
    earned = row.get("points_earned")
    if earned == "" or earned is None:
        return False
    label = str(row.get("display_score") or "").strip().lower()
    return label not in _PENDING_LABELS


class _Resp:
    def __init__(self, payload, links=None):
        self._payload = payload
        self.links = links or {}
        self.status_code = 200

    def json(self):
        return self._payload


def fake_canvas(monkeypatch, *, n_assignments, graded_index, grade_value):
    """A Canvas that paginates at ten like the real one."""
    assignments = [
        {"id": 1000 + i, "name": f"History essay {i}", "points_possible": 20,
         "due_at": "2026-09-01T00:00:00Z", "course_id": 55}
        for i in range(n_assignments)
    ]
    submissions = [
        {"assignment_id": 1000 + graded_index, "score": 18, "grade": grade_value},
    ]

    def fake_get(url, headers=None, timeout=None):
        if "/courses/55/assignments" in url:
            per = 100 if "per_page=100" in url else 10
            page = 1
            import re
            m = re.search(r"[?&]page=(\d+)", url)
            if m:
                page = int(m.group(1))
            start = (page - 1) * per
            chunk = assignments[start:start + per]
            links = {}
            if start + per < len(assignments):
                links = {"next": {"url":
                    f"https://x/api/v1/courses/55/assignments?per_page={per}&page={page + 1}"}}
            return _Resp(chunk, links)
        if "submissions" in url:
            return _Resp(submissions)
        if url.rstrip("/").endswith("/courses") or "/courses?" in url:
            return _Resp([{"id": 55, "name": "AP US History"}])
        return _Resp([])

    monkeypatch.setattr(canvas_helper, "requests", types.SimpleNamespace(get=fake_get))


def history_rows():
    detail = canvas_helper.get_gradebook_detail("https://x", "tok")
    course = next((c for c in detail if "History" in c["course"]), None)
    return course["assignments"] if course else []


# ── Pagination ───────────────────────────────────────────────────────


def test_every_assignment_is_fetched_not_just_the_first_page(monkeypatch):
    fake_canvas(monkeypatch, n_assignments=25, graded_index=17, grade_value=None)
    assert len(history_rows()) == 25


def test_a_grade_beyond_the_first_page_reaches_the_gradebook(monkeypatch):
    fake_canvas(monkeypatch, n_assignments=25, graded_index=17, grade_value=None)
    row = next((r for r in history_rows() if r["title"] == "History essay 17"), None)
    assert row is not None, "the graded assignment never arrived"
    assert frontend_counts_as_graded(row)


def test_pagination_follows_the_link_header_past_one_hundred(monkeypatch):
    """per_page=100 alone would silently truncate a very large course."""
    fake_canvas(monkeypatch, n_assignments=230, graded_index=225, grade_value=None)
    rows = history_rows()
    assert len(rows) == 230
    assert any(r["title"] == "History essay 225" for r in rows)


# ── A score with no letter grade ─────────────────────────────────────


def test_a_points_only_grade_is_not_labelled_as_pending(monkeypatch):
    """The case that hides a grade without needing a big course at all."""
    fake_canvas(monkeypatch, n_assignments=5, graded_index=2, grade_value=None)
    row = next(r for r in history_rows() if r["title"] == "History essay 2")
    assert row["points_earned"] == 18
    assert row["display_score"] == "18"
    assert row["display_score"] not in _PENDING_LABELS
    assert frontend_counts_as_graded(row)


def test_a_letter_grade_is_still_preferred_when_canvas_sends_one(monkeypatch):
    fake_canvas(monkeypatch, n_assignments=5, graded_index=2, grade_value="A-")
    row = next(r for r in history_rows() if r["title"] == "History essay 2")
    assert row["display_score"] == "A-"
    assert frontend_counts_as_graded(row)


def test_a_pass_fail_grade_survives(monkeypatch):
    fake_canvas(monkeypatch, n_assignments=5, graded_index=2, grade_value="complete")
    row = next(r for r in history_rows() if r["title"] == "History essay 2")
    assert row["display_score"] == "complete"
    assert frontend_counts_as_graded(row)


def test_a_zero_score_reads_as_graded_not_blank(monkeypatch):
    """Scoring zero is a grade. It used to render blank, which the page then
    counted as pending -- so a zero quietly vanished from the average."""
    fake_canvas(monkeypatch, n_assignments=5, graded_index=2, grade_value=None)

    def zero_sub(url, headers=None, timeout=None):
        return _Resp([{"assignment_id": 1002, "score": 0, "grade": None}])

    original = canvas_helper.requests.get

    def routed(url, headers=None, timeout=None):
        if "submissions" in url:
            return zero_sub(url)
        return original(url, headers=headers, timeout=timeout)

    canvas_helper.requests = types.SimpleNamespace(get=routed)
    row = next(r for r in history_rows() if r["title"] == "History essay 2")
    assert row["points_earned"] == 0
    assert row["display_score"] == "0"
    assert frontend_counts_as_graded(row)


def test_a_genuinely_ungraded_assignment_still_says_not_graded(monkeypatch):
    """The inverse guard: this must not turn every row into a graded one."""
    fake_canvas(monkeypatch, n_assignments=5, graded_index=2, grade_value=None)
    row = next(r for r in history_rows() if r["title"] == "History essay 4")
    assert row["display_score"] == "Not Graded"
    assert row["points_earned"] == ""
    assert not frontend_counts_as_graded(row)


# ── The paginated fetch helper itself ────────────────────────────────


def test_get_list_returns_empty_on_an_error_body(monkeypatch):
    """Canvas answers {"errors": [...]} rather than a list; callers expect []."""
    monkeypatch.setattr(canvas_helper, "requests", types.SimpleNamespace(
        get=lambda *a, **k: _Resp({"errors": [{"message": "nope"}]})))
    assert canvas_helper._get_list("https://x/api/v1/courses", {}) == []


def test_get_list_survives_a_transport_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")
    monkeypatch.setattr(canvas_helper, "requests", types.SimpleNamespace(get=boom))
    assert canvas_helper._get_list("https://x/api/v1/courses", {}) == []


def test_get_list_stops_rather_than_looping_on_a_self_referential_link(monkeypatch):
    """A next link pointing at the page you just asked for would spin forever."""
    same = "https://x/api/v1/courses?per_page=100"
    monkeypatch.setattr(canvas_helper, "requests", types.SimpleNamespace(
        get=lambda *a, **k: _Resp([{"id": 1}], {"next": {"url": same}})))
    assert canvas_helper._get_list("https://x/api/v1/courses", {}) == [{"id": 1}]


def test_get_list_is_bounded_by_a_page_cap(monkeypatch):
    """A server that always advertises another page must not hang the sync."""
    seen = {"n": 0}

    def endless(url, headers=None, timeout=None):
        seen["n"] += 1
        return _Resp([{"id": seen["n"]}], {
            "next": {"url": f"https://x/api/v1/courses?per_page=100&page={seen['n'] + 1}"}})

    monkeypatch.setattr(canvas_helper, "requests", types.SimpleNamespace(get=endless))
    rows = canvas_helper._get_list("https://x/api/v1/courses", {})
    assert len(rows) == canvas_helper._MAX_PAGES
