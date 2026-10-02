"""Grade Pulse: grade and new-assignment alerts that say what to do.

The LMS already tells a student "78%". What it never says is what that does
to the course, or where the new essay is going to fit. These pin:

* the final-grade arithmetic (the same formula the public calculator shows),
  including weighted categories, points-based courses and the honest
  fallback when the gradebook does not say what the final is worth;
* that an alert names the consequence, not just the score;
* that a grade or assignment is announced once, however many times it is
  observed -- and never on the first sync, which would flood a new user;
* that new work is slotted into real free time in the saved plan;
* that delivery goes through the existing outbox and so respects the
  student's kinds, channels and quiet hours.

No network: every LMS payload here is a literal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from intelliplan.notifications.events import (
    DEFAULT_ENABLED_KINDS,
    QUIET_HOURS_EXEMPT,
    Channel,
    EventKind,
    NotificationEvent,
    render,
)
from intelliplan.notifications.preferences import Preferences, QuietHours
from intelliplan.services import grade_pulse as gp


# ── Fixtures: gradebook payloads in the helpers' own shape ───────────


def chem(test_score=None, course_pct=95.5, final_graded=False):
    """A Canvas-shaped weighted course: Homework 30, Tests 40, Final 30."""
    assignments = [
        {"id": "1", "title": "HW 1", "points_possible": 10, "points_earned": 10, "category": "Homework"},
        {"id": "2", "title": "Unit 2 Test", "points_possible": 100, "points_earned": 96, "category": "Tests"},
        {"id": "3", "title": "Unit 3 Test", "points_possible": 100,
         "points_earned": "" if test_score is None else test_score, "category": "Tests"},
        {"id": "4", "title": "Final Exam", "points_possible": 200,
         "points_earned": 180 if final_graded else "", "category": "Final Exam"},
    ]
    categories = [
        {"type": "Homework", "weight": 30, "points": 10, "points_possible": 10},
        {"type": "Tests", "weight": 40, "points": 96, "points_possible": 100},
    ]
    if final_graded:
        categories.append({"type": "Final Exam", "weight": 30, "points": 180, "points_possible": 200})
    return {
        "course": "Chem",
        "percentage": course_pct,
        "letter": gp.letter_for(course_pct),
        "weighting": "groups",
        "groups": [{"id": "g1", "name": "Homework", "weight": 30},
                   {"id": "g2", "name": "Tests", "weight": 40},
                   {"id": "g3", "name": "Final Exam", "weight": 30}],
        "categories": categories,
        "assignments": assignments,
    }


def render_ctx(kind, ctx):
    event = NotificationEvent(kind=kind, user_id=1, dedupe_key="k", context=ctx)
    return render(event, Channel.PUSH)


# ── The arithmetic ────────────────────────────────────────────────────


def test_final_needed_matches_the_public_calculators_worked_example():
    """tool_final_grade.html: 88% now, 90% target, 20% final -> 98%."""
    assert gp.final_needed(88, 90, 0.20) == pytest.approx(98.0)


def test_letters_match_the_grades_page():
    import canvas_helper

    for pct in (100, 93, 92.99, 90, 87, 83, 80, 77, 73, 70, 67, 63, 60, 59.9, 0):
        assert gp.letter_for(pct) == canvas_helper._letter_from_pct(pct)


def test_weighted_category_final_uses_its_real_share():
    """Final is 30 of 100 weight; graded categories hold the other 70."""
    out = gp.final_outlook(93.9, held_letter="A", course=chem())
    assert out.method == "categories"
    assert out.final_weight == pytest.approx(0.30)
    # (93 - 93.9 * 0.7) / 0.3 = 90.9 -> rounds UP to 91
    assert (out.outlook, out.need_pct, out.target_verb) == ("need", 91.0, "keep")
    assert "assumed_final_weight" not in out.as_context()


def test_points_based_course_uses_the_finals_points():
    course = {
        "weighting": "points",
        "assignments": [
            {"title": "Quiz", "points_possible": 400, "points_earned": 352},
            {"title": "Lab", "points_possible": 400, "points_earned": 360},
            {"title": "Final exam", "points_possible": 200, "points_earned": ""},
        ],
    }
    out = gp.final_outlook(89.0, held_letter="A-", course=course)
    assert out.method == "points"
    assert out.final_weight == pytest.approx(0.2)


def test_unknown_final_weight_is_assumed_and_says_so():
    out = gp.final_outlook(88.4, held_letter="A-")
    assert out.method == "assumed"
    ctx = out.as_context()
    assert ctx["assumed_final_weight"] == 20
    # (90 - 88.4 * 0.8) / 0.2 = 96.4 -> 97, and it is a "get back to"
    assert (ctx["need_pct"], ctx["target_verb"]) == (97.0, "get back to")
    assert "(if the final is worth 20%)" in gp_render_clause(ctx)


def gp_render_clause(ctx):
    from intelliplan.notifications.events import _final_clause

    return _final_clause(ctx)


def test_out_of_reach_falls_back_to_a_letter_still_in_reach():
    out = gp.final_outlook(91.2, held_letter="A")
    # A needs (93 - 72.96)/0.2 = 100.2 -> out of reach; A- needs 85.2 -> 86
    assert out.outlook == "unreachable"
    assert (out.fallback_letter, out.fallback_need_pct) == ("A-", 86.0)
    text = gp_render_clause(out.as_context())
    assert text.startswith("An A is out of reach on the final alone; 86% keeps an A-")


def test_locked_letter():
    """A tiny final cannot pull a 99 below 93."""
    course = {"weighting": "points", "assignments": [
        {"title": "Everything", "points_possible": 990, "points_earned": 980},
        {"title": "Final quiz", "points_possible": 10, "points_earned": ""},
    ]}
    out = gp.final_outlook(99.0, held_letter="A", course=course)
    assert out.outlook == "locked"
    assert gp_render_clause(out.as_context()) == "Your A holds even with a 0 on the final."


def test_no_outlook_when_the_final_is_already_graded():
    assert gp.final_outlook(93.0, held_letter="A", course=chem(final_graded=True)) is None


# ── Grade diffs ───────────────────────────────────────────────────────


def test_first_sync_is_a_silent_baseline():
    alerts, snap = gp.diff_gradebook(None, [chem(test_score=78, course_pct=93.9)], source="canvas")
    assert alerts == []
    assert gp.is_baseline(snap)


def test_a_new_grade_says_what_the_final_now_has_to_be():
    _, snap = gp.diff_gradebook(None, [chem()], source="canvas")
    alerts, snap2 = gp.diff_gradebook(snap, [chem(test_score=78, course_pct=93.9)], source="canvas")
    assert len(alerts) == 1
    msg = render_ctx(EventKind.GRADE_POSTED, alerts[0].context())
    assert msg.title == "Chem: 78% on Unit 3 Test"
    assert msg.body == ("Course grade 95.5 → 93.9 (A). "
                        "You now need 91% on the final to keep an A.")


def test_the_same_grade_is_never_announced_twice():
    _, snap = gp.diff_gradebook(None, [chem()], source="canvas")
    _, snap2 = gp.diff_gradebook(snap, [chem(test_score=78, course_pct=93.9)], source="canvas")
    again, _ = gp.diff_gradebook(snap2, [chem(test_score=78, course_pct=93.9)], source="canvas")
    assert again == []


def test_a_concurrent_replay_produces_the_same_dedupe_key():
    """Two requests diffing the same old snapshot raise identical events;
    the outbox's (user, dedupe_key) uniqueness then drops the second."""
    _, snap = gp.diff_gradebook(None, [chem()], source="canvas")
    a, _ = gp.diff_gradebook(snap, [chem(test_score=78, course_pct=93.9)], source="canvas")
    b, _ = gp.diff_gradebook(snap, [chem(test_score=78, course_pct=93.9)], source="canvas")
    assert a[0].dedupe_key == b[0].dedupe_key


def test_a_regrade_is_a_new_fact():
    _, snap = gp.diff_gradebook(None, [chem()], source="canvas")
    first, snap = gp.diff_gradebook(snap, [chem(test_score=78, course_pct=93.9)], source="canvas")
    second, _ = gp.diff_gradebook(snap, [chem(test_score=70, course_pct=92.0)], source="canvas")
    assert len(second) == 1 and second[0].dedupe_key != first[0].dedupe_key


def test_a_small_wobble_is_not_worth_a_buzz():
    """Guardrail: under the drop threshold and no letter change -> silent,
    but still recorded so it is not announced later either."""
    _, snap = gp.diff_gradebook(None, [chem()], source="canvas")
    alerts, snap2 = gp.diff_gradebook(snap, [chem(test_score=95, course_pct=95.3)], source="canvas")
    assert alerts == []
    later, _ = gp.diff_gradebook(snap2, [chem(test_score=95, course_pct=93.0)], source="canvas")
    assert later == []


def test_a_new_course_is_a_baseline_too():
    _, snap = gp.diff_gradebook(None, [chem()], source="canvas")
    bio = dict(chem(test_score=50, course_pct=70.0), course="Bio")
    alerts, _ = gp.diff_gradebook(snap, [chem(), bio], source="canvas")
    assert alerts == []


def test_snapshot_holds_no_titles():
    _, snap = gp.diff_gradebook(None, [chem(test_score=78)], source="canvas")
    blob = json.dumps(snap)
    assert "Chem" not in blob and "Unit 3" not in blob


def test_studentvue_shape_without_ids():
    sv = {
        "course": "English 10", "percentage": 91.0, "letter": "A-",
        "categories": [{"type": "Essays", "weight": 60, "points": 90, "points_possible": 100},
                       {"type": "Final", "weight": 20, "points": 0, "points_possible": 0}],
        "assignments": [{"title": "Essay 1", "due_date": "2026-09-01", "points_earned": 90,
                         "points_possible": 100, "graded": True, "category": "Essays"},
                        {"title": "Essay 2", "due_date": "2026-09-20", "points_earned": None,
                         "points_possible": 100, "graded": False, "category": "Essays"}],
    }
    _, snap = gp.diff_gradebook(None, [sv], source="studentvue")
    graded = dict(sv, percentage=88.5, assignments=[
        sv["assignments"][0], dict(sv["assignments"][1], points_earned=86, graded=True)])
    alerts, _ = gp.diff_gradebook(snap, [graded], source="studentvue")
    assert len(alerts) == 1
    ctx = alerts[0].context()
    assert ctx["item"] == "Essay 2" and ctx["target_letter"] == "A-"
    # Final is 20 of (60 + 20): a real weight, not the assumed one.
    assert "assumed_final_weight" not in ctx


def test_many_grades_at_once_become_one_message():
    courses = [dict(chem(), course=f"Course {i}") for i in range(5)]
    _, snap = gp.diff_gradebook(None, courses, source="canvas")
    dropped = [dict(chem(test_score=60 + i, course_pct=90.0 - i), course=f"Course {i}") for i in range(5)]
    alerts, _ = gp.diff_gradebook(snap, dropped, source="canvas")
    pairs = gp.grade_event_contexts(alerts)
    assert len(alerts) == 5 and len(pairs) == 1
    key, ctx = pairs[0]
    assert key.startswith("digest:") and ctx["count"] == 5
    msg = render_ctx(EventKind.GRADE_POSTED, ctx)
    assert msg.title == "5 new grades"
    assert msg.body.startswith("Course 4 Unit 3 Test 64%.")  # biggest drop leads
    assert msg.body.endswith("+3 more.")
    # The same batch replayed is the same digest.
    assert gp.grade_event_contexts(list(reversed(alerts)))[0][0] == key


def test_course_level_sources_get_an_assumed_final():
    _, snap = gp.diff_course_grades(None, [{"course": "Chem", "percentage": 91.0}], source="schoology")
    alerts, _ = gp.diff_course_grades(snap, [{"course": "Chem", "percentage": 88.4}], source="schoology")
    msg = render_ctx(EventKind.GRADE_POSTED, alerts[0].context())
    assert msg.title == "Chem: grade updated"
    assert msg.body == ("Course grade 91 → 88.4 (B+). You now need 97% on the final to "
                        "get back to an A- (if the final is worth 20%).")


# ── New assignments ───────────────────────────────────────────────────

MON = date(2026, 9, 28)


def lab(**kw):
    base = {"title": "Lab Report", "course": "Chem", "due_date": "2026-10-02",
            "estimated_time": 45, "source": "canvas"}
    base.update(kw)
    return base


def test_new_assignment_needs_a_baseline_first():
    fresh, snap = gp.diff_assignments(None, [lab()], today=MON)
    assert fresh == []
    fresh, _ = gp.diff_assignments(snap, [lab(), lab(title="Essay")], today=MON)
    assert [f.title for f in fresh] == ["Essay"]


def test_new_assignment_is_announced_once_across_observers():
    _, snap = gp.diff_assignments(None, [lab()], today=MON)
    fresh, snap = gp.diff_assignments(snap, [lab(title="Essay")], today=MON)
    assert len(fresh) == 1
    # A second observer that sees only a subset must not resurrect anything.
    again, _ = gp.diff_assignments(snap, [lab(title="Essay")], today=MON)
    assert again == []


def test_students_own_tasks_and_past_work_are_not_announced():
    _, snap = gp.diff_assignments(None, [lab()], today=MON)
    fresh, _ = gp.diff_assignments(snap, [
        lab(title="Mine", source="manual"),
        lab(title="Late", due_date="2026-09-01"),
        lab(title="Undated", due_date=""),
    ], today=MON)
    assert fresh == []


def test_studentvue_missing_shares_the_studentvue_baseline():
    _, snap = gp.diff_assignments(None, [lab(source="studentvue")], today=MON)
    fresh, _ = gp.diff_assignments(snap, [lab(source="studentvue_missing")], today=MON)
    assert fresh == []


@dataclass
class W:
    start: datetime
    end: datetime


def wed_afternoons(day):
    """Free 4-7 PM on Wednesdays and Thursdays only."""
    if day.weekday() in (2, 3):
        return [W(datetime.combine(day, datetime.min.time()) + timedelta(hours=16),
                  datetime.combine(day, datetime.min.time()) + timedelta(hours=19))]
    return []


def plan_with_wednesday_block():
    return {"schedule": [
        {"date": "2026-09-30", "day_name": "Wednesday", "blocks": [
            {"assignment": "Read ch. 4", "course": "Bio", "duration_minutes": 60,
             # The ISO date is the generation date, as humanize_schedule
             # writes it; only the clock part may be trusted.
             "start_iso": "2026-09-27T17:00:00", "end_iso": "2026-09-27T18:00:00"}]},
    ]}


def new(title="Lab Report", minutes=45, due=date(2026, 10, 2)):
    return gp.NewAssignment(key=title, title=title, course="Chem", due=due,
                            minutes=minutes, source="canvas")


def test_new_assignment_lands_in_free_time_and_the_alert_says_where():
    plan = plan_with_wednesday_block()
    placement = gp.slot_assignment(plan, new(), windows_for=wed_afternoons, today=MON)
    assert placement.placed
    start, end = placement.sittings[0]
    assert (start, end) == (datetime(2026, 9, 30, 16, 0), datetime(2026, 9, 30, 16, 45))
    blocks = plan["schedule"][0]["blocks"]
    assert [b["assignment"] for b in blocks] == ["Lab Report", "Read ch. 4"]
    assert blocks[0]["auto_added"] and blocks[0]["due_date"] == "2026-10-02"
    msg = render_ctx(EventKind.ASSIGNMENT_POSTED, gp.assignment_event_context(new(), placement, MON))
    assert msg.title == "New: Lab Report due Fri"
    assert msg.body == "Scheduled Wed 4:00–4:45 PM."


def test_never_on_top_of_an_existing_block():
    plan = plan_with_wednesday_block()
    gp.slot_assignment(plan, new(minutes=45), windows_for=wed_afternoons, today=MON)
    placement = gp.slot_assignment(plan, new("Essay", minutes=50), windows_for=wed_afternoons, today=MON)
    start, end = placement.sittings[0]
    # 4:00-4:45 is the lab, 5:00-6:00 the reading, each padded by 5 minutes,
    # so the first room is 6:05.
    assert start == datetime(2026, 9, 30, 18, 5)


def test_long_work_is_split_across_days_and_never_after_the_due_date():
    plan = {"schedule": []}
    placement = gp.slot_assignment(plan, new(minutes=120), windows_for=wed_afternoons, today=MON)
    days = [s.date() for s, _ in placement.sittings]
    assert days == [date(2026, 9, 30), date(2026, 10, 1)]
    assert all(d < date(2026, 10, 2) for d in days)
    assert [d["date"] for d in plan["schedule"]] == ["2026-09-30", "2026-10-01"]
    msg = render_ctx(EventKind.ASSIGNMENT_POSTED, gp.assignment_event_context(new(minutes=120), placement, MON))
    assert msg.body == "Scheduled Wed 4:00–5:00 PM (+1 more sitting)."


@pytest.mark.parametrize("plan,windows,reason,phrase", [
    (None, wed_afternoons, "no_plan", "Build this week's plan"),
    ({"schedule": []}, lambda d: [], "no_room", "No free time before it's due"),
    ({"schedule": [{"date": "2026-09-29", "blocks": [{"assignment": "Lab Report"}]}]},
     wed_afternoons, "already_planned", "already in your plan"),
])
def test_when_it_cannot_be_slotted_the_alert_says_why(plan, windows, reason, phrase):
    placement = gp.slot_assignment(plan, new(), windows_for=windows, today=MON)
    assert placement.reason == reason
    msg = render_ctx(EventKind.ASSIGNMENT_POSTED, gp.assignment_event_context(new(), placement, MON))
    assert phrase in msg.body


def test_a_unit_of_new_work_is_one_message():
    items = [(new(f"Task {i}", due=date(2026, 10, 2 + i)), gp.Placement(reason="no_room")) for i in range(4)]
    pairs = gp.assignment_event_contexts(items, MON)
    assert len(pairs) == 1
    msg = render_ctx(EventKind.ASSIGNMENT_POSTED, pairs[0][1])
    assert msg.title == "4 new assignments"
    assert msg.body.startswith("Task 0 due Fri.")


# ── Preferences and quiet hours ───────────────────────────────────────


def test_new_kinds_are_on_by_default_but_wait_for_morning():
    assert EventKind.GRADE_POSTED in DEFAULT_ENABLED_KINDS
    assert EventKind.ASSIGNMENT_POSTED in DEFAULT_ENABLED_KINDS
    assert EventKind.GRADE_POSTED not in QUIET_HOURS_EXEMPT
    prefs = Preferences(user_id=1, tz_name="America/Los_Angeles",
                        quiet_hours=QuietHours(22, 7, True), channels=frozenset({Channel.PUSH}))
    teacher_at_11pm = datetime(2026, 10, 1, 6, 0)          # 23:00 in LA
    sent = prefs.delivery_time(EventKind.GRADE_POSTED, teacher_at_11pm)
    assert prefs.to_local(sent).hour == 7


def test_a_student_who_turned_grade_alerts_off_gets_none():
    prefs = Preferences(user_id=1, channels=frozenset({Channel.PUSH}),
                        kinds=frozenset({EventKind.SESSION_UPCOMING}))
    assert not prefs.wants(EventKind.GRADE_POSTED, Channel.PUSH)


def test_sms_copy_stays_inside_one_segment():
    _, snap = gp.diff_gradebook(None, [dict(chem(), course="AP Chemistry with a very long name")],
                                source="canvas")
    alerts, _ = gp.diff_gradebook(snap, [dict(chem(test_score=78, course_pct=93.9),
                                              course="AP Chemistry with a very long name")],
                                  source="canvas")
    event = NotificationEvent(kind=EventKind.GRADE_POSTED, user_id=1, dedupe_key="k",
                              context=alerts[0].context())
    assert len(render(event, Channel.SMS).as_sms()) <= 160


# ── Through the app: outbox, dedupe, preferences, the saved plan ──────


@pytest.fixture
def app_user():
    import App
    from App import (GradePulseSnapshot, NotificationOutbox, SavedSchedule, User, db)

    email = "pulse+alerts@example.com"
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email, password_hash="x", name="Pulse Tester",
                    email_reminders_opt_in=True, quiet_hours_enabled=False)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    yield uid
    with App.app.app_context():
        for model in (NotificationOutbox, GradePulseSnapshot, SavedSchedule):
            model.query.filter_by(user_id=uid).delete(synchronize_session=False)
        User.query.filter_by(id=uid).delete(synchronize_session=False)
        db.session.commit()


def _outbox(uid, kind):
    from App import NotificationOutbox

    return NotificationOutbox.query.filter_by(user_id=uid, kind=kind).all()


def test_grade_alert_reaches_the_outbox_once(app_user):
    import App
    import grade_pulse_glue

    with App.app.app_context():
        assert grade_pulse_glue.observe_gradebook(app_user, "canvas", [chem()]) == 0
        queued = grade_pulse_glue.observe_gradebook(
            app_user, "canvas", [chem(test_score=78, course_pct=93.9)])
        assert queued == 1
        rows = _outbox(app_user, "grade_posted")
        assert [r.channel for r in rows] == ["email"]
        assert "91% on the final to keep an A" in rows[0].body
        assert rows[0].url == "/grademodel"

        # Same payload again: snapshot says seen. And even a replay against
        # the *old* snapshot (a concurrent request) is dropped by the outbox.
        assert grade_pulse_glue.observe_gradebook(
            app_user, "canvas", [chem(test_score=78, course_pct=93.9)]) == 0
        _, snap = gp.diff_gradebook(None, [chem()], source="canvas")
        row, _ = grade_pulse_glue._load(app_user, "gradebook:canvas")
        grade_pulse_glue._save(row, app_user, "gradebook:canvas", snap)
        assert grade_pulse_glue.observe_gradebook(
            app_user, "canvas", [chem(test_score=78, course_pct=93.9)]) == 0
        assert len(_outbox(app_user, "grade_posted")) == 1


def test_grade_alert_respects_kind_toggles_and_channels(app_user):
    import App
    from App import User, db
    import grade_pulse_glue

    with App.app.app_context():
        user = db.session.get(User, app_user)
        user.notification_kinds = "session_upcoming"
        db.session.commit()
        grade_pulse_glue.observe_gradebook(app_user, "canvas", [chem()])
        assert grade_pulse_glue.observe_gradebook(
            app_user, "canvas", [chem(test_score=78, course_pct=93.9)]) == 0

        user.notification_kinds = None
        user.email_reminders_opt_in = False
        db.session.commit()
        assert grade_pulse_glue.observe_gradebook(
            app_user, "canvas", [chem(test_score=70, course_pct=92.0)]) == 0
        assert _outbox(app_user, "grade_posted") == []


def test_grade_alert_is_held_through_quiet_hours(app_user):
    import App
    from App import User, db
    import grade_pulse_glue
    from time_utils import utcnow

    with App.app.app_context():
        user = db.session.get(User, app_user)
        now = utcnow()
        # A window that contains "now" (UTC: no timezone recorded).
        user.quiet_hours_enabled = True
        user.quiet_hours_start = now.hour
        user.quiet_hours_end = (now.hour + 2) % 24
        db.session.commit()
        grade_pulse_glue.observe_gradebook(app_user, "canvas", [chem()])
        grade_pulse_glue.observe_gradebook(app_user, "canvas", [chem(test_score=78, course_pct=93.9)])
        rows = _outbox(app_user, "grade_posted")
        assert len(rows) == 1
        assert rows[0].scheduled_for >= now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def test_course_level_alert_defers_to_a_fresh_gradebook(app_user):
    """/grades/data and /gradebook/detail both see the same grade; only the
    one that can name the assignment speaks."""
    import App
    import grade_pulse_glue

    with App.app.app_context():
        grade_pulse_glue.observe_gradebook(app_user, "canvas", [chem()])
        grade_pulse_glue.observe_course_grades(app_user, "canvas", [{"course": "Chem", "percentage": 95.5}])
        assert grade_pulse_glue.observe_course_grades(
            app_user, "canvas", [{"course": "Chem", "percentage": 90.0}]) == 0


def test_new_assignment_is_saved_into_the_plan_and_announced(app_user):
    import App
    from App import SavedSchedule, db
    import grade_pulse_glue

    today = date.today()
    due = (today + timedelta(days=6)).isoformat()
    with App.app.app_context():
        db.session.add(SavedSchedule(user_id=app_user, name="Week", is_active=True,
                                     schedule_data=json.dumps({"schedule": []})))
        db.session.commit()
        existing = {"title": "Old", "course": "Chem", "due_date": due, "source": "canvas"}
        assert grade_pulse_glue.observe_assignments(app_user, [existing], today=today) == 0
        fresh = {"title": "Lab Report", "course": "Chem", "due_date": due,
                 "estimated_time": 45, "source": "canvas"}
        assert grade_pulse_glue.observe_assignments(app_user, [existing, fresh], today=today) == 1

        plan = json.loads(SavedSchedule.query.filter_by(user_id=app_user).first().schedule_data)
        added = [b for d in plan["schedule"] for b in d["blocks"] if b.get("auto_added")]
        assert len(added) == 1 and added[0]["assignment"] == "Lab Report"
        assert added[0]["block_id"].startswith("gp-") and added[0].get("kind")
        row = _outbox(app_user, "assignment_posted")[0]
        assert row.title.startswith("New: Lab Report due ")
        assert row.body.startswith("Scheduled ")

        # Seen now: a re-sync neither re-announces nor double-books.
        assert grade_pulse_glue.observe_assignments(app_user, [existing, fresh], today=today) == 0
        plan = json.loads(SavedSchedule.query.filter_by(user_id=app_user).first().schedule_data)
        assert sum(1 for d in plan["schedule"] for b in d["blocks"] if b.get("auto_added")) == 1


def test_the_gradebook_page_load_is_the_sync(app_user, monkeypatch):
    """The hook in /gradebook/detail: the read the page already makes is
    what Grade Pulse watches. The LMS helper is stubbed -- no network."""
    import App
    import canvas_helper

    payloads = iter([[chem()], [chem(test_score=78, course_pct=93.9)]])
    monkeypatch.setattr(App, "get_grade_account",
                        lambda: {"login_type": "canvas", "canvas_token": "t",
                                 "canvas_url": "https://canvas.example"})
    monkeypatch.setattr(canvas_helper, "get_gradebook_detail", lambda url, token: next(payloads))
    App.app.config["TESTING"] = True
    with App.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(app_user)
            sess["_fresh"] = True
        first = client.get("/gradebook/detail")
        second = client.get("/gradebook/detail")
    assert first.status_code == second.status_code == 200
    assert second.get_json()[0]["course"] == "Chem"   # the response is untouched
    with App.app.app_context():
        rows = _outbox(app_user, "grade_posted")
        assert len(rows) == 1 and rows[0].title == "Chem: 78% on Unit 3 Test"


def test_an_observer_failure_never_breaks_the_page(app_user, monkeypatch):
    import App
    import grade_pulse_glue

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(grade_pulse_glue, "_load", boom)
    with App.app.app_context():
        assert grade_pulse_glue.observe_gradebook(app_user, "canvas", [chem()]) == 0
        assert grade_pulse_glue.observe_assignments(app_user, [lab()]) == 0


def test_background_pull_only_targets_observed_students_with_a_channel(app_user, monkeypatch):
    import App
    from App import GradePulseSnapshot, db
    import grade_pulse_glue
    from time_utils import utcnow

    with App.app.app_context():
        grade_pulse_glue.observe_gradebook(app_user, "canvas", [chem()])
        # Fresh: not due.
        assert app_user not in grade_pulse_glue.due_user_ids(limit=50)
        GradePulseSnapshot.query.filter_by(user_id=app_user).update(
            {"updated_at": utcnow() - timedelta(hours=10)})
        db.session.commit()
        assert app_user in grade_pulse_glue.due_user_ids(limit=50)

        pulled = []
        monkeypatch.setattr(grade_pulse_glue, "pull_user", lambda uid: pulled.append(uid))
        monkeypatch.setattr(grade_pulse_glue, "due_user_ids", lambda limit=5: [app_user])
        assert grade_pulse_glue.pull_due() == {"pulled": 1}
        monkeypatch.setenv("GRADE_PULSE_PULL", "0")
        assert grade_pulse_glue.pull_due() == {"pulled": 0}
        assert pulled == [app_user]
