"""Perfect Session vs Perfect Week.

Badges are the retention loop's currency, and currency only works if it is
scarce. The session-complete endpoint used to award ``perfect_week`` -- the
rarest-sounding badge in the catalog -- for a single all-correct session,
so a student could collect it on their first ever run, before they had used
IntelliPlan for a second day. Meanwhile a genuinely perfect week paid XP and
awarded no badge at all, so the one achievement worth a week of retention was
invisible.

These tests pin both halves: the cheap thing gets the cheap badge, the week
gets the week badge, and neither can be earned by the other's work.
"""

from __future__ import annotations

import json

import pytest

import App


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


def badges(client):
    with client.session_transaction() as sess:
        gid = sess.get("guest_id")
    with App.app.app_context():
        p = App.StudyPoints.query.filter_by(guest_session_id=gid).first()
        return App.safe_json_load(p.badges, []) if p else []


def complete(client, *, total, correct, **extra):
    body = {"questions_total": total, "questions_correct": correct,
            "points_earned": 0, "duration_seconds": 900, "local_hour": 15}
    body.update(extra)
    return client.post("/study/session/complete", json=body)


# ── The cheap achievement gets the cheap badge ───────────────────────


def test_an_all_correct_session_earns_perfect_session(client):
    assert complete(client, total=8, correct=8).status_code == 200
    assert "perfect_session" in badges(client)


def test_an_all_correct_session_does_not_earn_perfect_week(client):
    """The bug, stated directly: one session is not a week."""
    complete(client, total=8, correct=8)
    assert "perfect_week" not in badges(client), (
        "a single session must not award the week badge")


def test_a_one_question_session_earns_nothing(client):
    """Otherwise the badge is a single click away and means nothing."""
    complete(client, total=1, correct=1)
    assert "perfect_session" not in badges(client)


def test_the_guest_cap_can_still_earn_it(client):
    """Guests are capped at five questions. A threshold above that would
    make the badge unreachable for every student before they sign up --
    which is exactly the audience the badge exists to convert."""
    assert App.PERFECT_SESSION_MIN_QUESTIONS <= App.GUEST_STUDY_LIMITS["max_questions"]
    complete(client, total=App.GUEST_STUDY_LIMITS["max_questions"],
             correct=App.GUEST_STUDY_LIMITS["max_questions"])
    assert "perfect_session" in badges(client)


def test_one_wrong_answer_is_not_perfect(client):
    complete(client, total=8, correct=7)
    assert "perfect_session" not in badges(client)


def test_a_zero_question_session_is_not_perfect(client):
    """0 >= 0 is true, and an empty session would otherwise qualify."""
    complete(client, total=0, correct=0)
    assert "perfect_session" not in badges(client)


def test_the_badge_has_a_display_name(client):
    """An id with no catalog entry renders as a title-cased slug."""
    assert App.BADGE_CATALOG["perfect_session"]["name"] == "Perfect Session"


# ── The week badge, for an actual week ───────────────────────────────


@pytest.fixture
def student(request):
    from App import PlaniPet, User, UserStreak, StudyPoints, db
    with App.app.app_context():
        User.query.filter(User.email.like("weekuser+%")).delete(synchronize_session=False)
        db.session.commit()
        u = User(email="weekuser+a@example.com",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="Week User")
        db.session.add(u)
        db.session.commit()
        uid = u.id

    def cleanup():
        # Children before parent: a failed commit here leaves the session
        # dirty and fails an unrelated test file later in the run.
        with App.app.app_context():
            PlaniPet.query.filter_by(user_id=uid).delete()
            StudyPoints.query.filter_by(user_id=uid).delete()
            UserStreak.query.filter_by(user_id=uid).delete()
            User.query.filter_by(id=uid).delete()
            db.session.commit()
    request.addfinalizer(cleanup)
    return uid


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def monday_of(weeks_back=0):
    from datetime import date, timedelta
    today = date.today()
    return today - timedelta(days=today.weekday() + 7 * weeks_back)


def qualify(user_id, days, tz="UTC"):
    from App import db
    with App.app.app_context():
        row = App._get_or_create_streak(user_id)
        row.timezone = tz
        row.qualified_dates_json = json.dumps(sorted(d.isoformat() for d in days))
        db.session.commit()


def whole_week(weeks_back=0, missing=()):
    from datetime import timedelta
    monday = monday_of(weeks_back)
    return [monday + timedelta(days=i) for i in range(7) if i not in missing]


def user_badges(user_id):
    with App.app.app_context():
        p = App.StudyPoints.query.filter_by(user_id=user_id).first()
        return App.safe_json_load(p.badges, []) if p else []


def test_a_finished_perfect_week_awards_the_badge(client, student):
    """The week that has just ended, which is settled on every day of the
    current one. Before this, a perfect week was only ever detectable on the
    Sunday it ended -- so unless the student happened to load the page in
    those last hours, a flawless week paid nothing and never would again."""
    login(client, student)
    qualify(student, whole_week(weeks_back=1))
    data = client.get("/api/streak/risk").get_json()
    assert data["perfect_week_paid"] == 200
    assert "perfect_week" in user_badges(student)


def test_a_week_with_one_missed_day_awards_nothing(client, student):
    login(client, student)
    qualify(student, whole_week(weeks_back=1, missing=(2,)))
    data = client.get("/api/streak/risk").get_json()
    assert data["perfect_week_paid"] is None
    assert "perfect_week" not in user_badges(student)


def test_a_week_with_no_qualifying_days_awards_nothing(client, student):
    login(client, student)
    qualify(student, [])
    assert client.get("/api/streak/risk").get_json()["perfect_week_paid"] is None
    assert "perfect_week" not in user_badges(student)


def test_the_xp_is_paid_once(client, student):
    login(client, student)
    qualify(student, whole_week(weeks_back=1))
    first = client.get("/api/streak/risk").get_json()
    second = client.get("/api/streak/risk").get_json()
    assert first["perfect_week_paid"] == 200
    assert second["perfect_week_paid"] is None, "XP must not be paid twice"


def test_the_badge_survives_a_second_call_and_is_not_duplicated(client, student):
    """The payout is per-week; the badge is per-lifetime. Tying the badge to
    the payout meant a student whose perfect week predated this code could
    never collect it."""
    login(client, student)
    qualify(student, whole_week(weeks_back=1))
    client.get("/api/streak/risk")
    client.get("/api/streak/risk")
    assert user_badges(student).count("perfect_week") == 1


def test_the_payout_names_the_week_it_paid_for(client, student):
    """Sunday judges the current week and the Monday after judges the same
    week as the finished one. Keying off today rather than off the week
    under judgement would pay that week twice."""
    login(client, student)
    qualify(student, whole_week(weeks_back=1))
    data = client.get("/api/streak/risk").get_json()
    iso = monday_of(1).isocalendar()
    assert data["perfect_week_paid_for"] == f"{iso[0]}-W{iso[1]:02d}"


def test_the_current_week_progress_is_still_reported(client, student):
    """The dots UI reads perfect_week for 'N/M days this week'. Paying for
    last week must not overwrite that with last week's numbers."""
    login(client, student)
    qualify(student, whole_week(weeks_back=1) + [monday_of(0)])
    data = client.get("/api/streak/risk").get_json()
    assert data["perfect_week"]["progress"] == 1


def test_a_bonus_paid_for_an_earlier_week_does_not_block_this_one(client, student):
    """The dedupe is per-week, not a one-time flag: a student who earned a
    perfect week in March must still be paid for one in April."""
    from App import db
    login(client, student)
    with App.app.app_context():
        pet = App._get_or_create_pet(student)
        pet.perfect_week_paid = "2024-W09"
        db.session.commit()
    qualify(student, whole_week(weeks_back=1))
    assert client.get("/api/streak/risk").get_json()["perfect_week_paid"] == 200
