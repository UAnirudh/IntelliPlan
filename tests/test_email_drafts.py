"""The unfinished-work nudge: what counts as a draft, and who gets told.

Two rules carry the feature and both are easy to regress into something
obnoxious: silence when there is nothing to say, and one email a week
however often the cron fires.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import App as app_module
from intelliplan.email import drafts


@pytest.fixture
def ctx():
    """See the note in test_email_onboarding: one shared in-memory database
    means each test has to start from an empty user table."""
    with app_module.app.app_context():
        for model in (
            app_module.EmailSend,
            app_module.EmailSuppression,
            app_module.ActiveSession,
            app_module.SavedSchedule,
            app_module.ManualTask,
            app_module.User,
        ):
            model.query.delete()
        app_module.db.session.commit()
        yield


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "IntelliPlan, 1 Test Way, Testville CA 94000")
    captured: list[dict] = []

    class FakeResponse:
        def read(self):
            return json.dumps({"id": "msg_test_00000000"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        yield captured


def make_user(**overrides):
    """A student who asked for email reminders."""
    defaults = {
        "email": f"drafts-{uuid.uuid4().hex[:10]}@example.test",
        "name": "Sam",
        "birth_year": 2000,
        "role": "student",
        "password_hash": "x",
        "email_reminders_opt_in": True,
        "created_at": datetime.utcnow() - timedelta(days=30),
    }
    defaults.update(overrides)
    user = app_module.User(**defaults)
    app_module.db.session.add(user)
    app_module.db.session.commit()
    return user


def add_stale_session(user, hours_ago=24, state="paused", **kw):
    row = app_module.ActiveSession(
        user_id=user.id,
        title=kw.pop("title", "Lab writeup"),
        state=state,
        started_at=datetime.utcnow() - timedelta(hours=hours_ago),
        planned_minutes=kw.pop("planned_minutes", 45),
        active_seconds=kw.pop("active_seconds", 600),
        **kw,
    )
    app_module.db.session.add(row)
    app_module.db.session.commit()
    return row


def add_task(user, due_days_ago=3, done=False, **kw):
    due = (datetime.utcnow() - timedelta(days=due_days_ago)).strftime("%Y-%m-%d")
    row = app_module.ManualTask(
        user_id=user.id,
        title=kw.pop("title", "Read chapters 4-6"),
        due_date=kw.pop("due_date", due),
        done=done,
        **kw,
    )
    app_module.db.session.add(row)
    app_module.db.session.commit()
    return row


def add_plan(user, days_ago=5, progress_json=None, **kw):
    row = app_module.SavedSchedule(
        user_id=user.id,
        name=kw.pop("name", "My Schedule"),
        schedule_data="{}",
        progress_json=progress_json,
        is_active=True,
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    app_module.db.session.add(row)
    app_module.db.session.commit()
    return row


# ── What counts as a draft ──────────────────────────────────────────


def test_a_session_left_paused_is_a_draft(ctx):
    user = make_user()
    add_stale_session(user)
    found = drafts.find_drafts(user.id)
    assert [d.kind for d in found] == ["session"]
    assert "Lab writeup" == found[0].title


def test_a_session_from_ten_minutes_ago_is_not_a_draft(ctx):
    """Someone who paused for a coffee has not abandoned anything."""
    user = make_user()
    add_stale_session(user, hours_ago=0.2)
    assert drafts.find_drafts(user.id) == []


def test_a_finished_session_is_not_a_draft(ctx):
    user = make_user()
    add_stale_session(user, state="completed")
    add_stale_session(user, state="abandoned")
    assert drafts.find_drafts(user.id) == []


def test_a_very_old_session_stops_being_mentioned(ctx):
    user = make_user()
    add_stale_session(user, hours_ago=24 * (drafts.SESSION_MAX_AGE_DAYS + 5))
    assert drafts.find_drafts(user.id) == []


def test_a_plan_with_nothing_checked_off_is_a_draft(ctx):
    user = make_user()
    add_plan(user)
    assert [d.kind for d in drafts.find_drafts(user.id)] == ["plan"]


def test_a_plan_that_was_started_is_not_a_draft(ctx):
    user = make_user()
    add_plan(user, progress_json=json.dumps({"b1": {"done": True}}))
    assert drafts.find_drafts(user.id) == []


def test_unreadable_progress_is_not_treated_as_no_progress(ctx):
    """Telling someone they never started a plan they may have finished is
    the one wrong answer here, so a corrupt column skips the row."""
    user = make_user()
    add_plan(user, progress_json="{not json at all")
    assert drafts.find_drafts(user.id) == []


def test_a_brand_new_plan_is_left_alone(ctx):
    user = make_user()
    add_plan(user, days_ago=0)
    assert drafts.find_drafts(user.id) == []


def test_an_overdue_open_task_is_a_draft(ctx):
    user = make_user()
    add_task(user)
    found = drafts.find_drafts(user.id)
    assert [d.kind for d in found] == ["task"]


def test_a_done_task_is_not_a_draft(ctx):
    user = make_user()
    add_task(user, done=True)
    assert drafts.find_drafts(user.id) == []


def test_a_task_that_is_not_due_yet_is_not_a_draft(ctx):
    user = make_user()
    add_task(user, due_days_ago=-5)
    assert drafts.find_drafts(user.id) == []


def test_a_task_with_no_or_unparseable_due_date_is_skipped(ctx):
    user = make_user()
    add_task(user, due_date="")
    add_task(user, due_date="next tuesday")
    assert drafts.find_drafts(user.id) == []


def test_the_list_is_capped_and_newest_first(ctx):
    user = make_user()
    for days in range(1, 12):
        add_task(user, due_days_ago=days, title=f"Task {days}")
    found = drafts.find_drafts(user.id)
    assert len(found) == drafts.MAX_ITEMS
    assert [d.age_days for d in found] == sorted(d.age_days for d in found)
    assert found[0].title == "Task 1"


def test_the_summary_line_reads_like_a_sentence(ctx):
    user = make_user()
    add_stale_session(user)
    add_task(user)
    add_task(user, title="Second one", due_days_ago=4)
    line = drafts.summarise(drafts.find_drafts(user.id))
    assert line == "1 study session and 2 tasks"


def test_the_summary_of_nothing_is_empty():
    assert drafts.summarise([]) == ""


# ── The sweep ───────────────────────────────────────────────────────


def test_a_student_with_drafts_is_mailed_once(ctx, resend):
    user = make_user()
    add_task(user)
    summary = drafts.sweep_drafts()
    assert summary["sent"] == 1
    assert len(resend) == 1
    assert "unfinished" in resend[0]["subject"]
    assert "Read chapters 4-6" in resend[0]["text"]


def test_a_student_with_nothing_unfinished_is_not_mailed(ctx, resend):
    """No email is the correct output. A weekly 'you have 0 items' is how a
    reminder becomes noise."""
    make_user()
    summary = drafts.sweep_drafts()
    assert summary["sent"] == 0
    assert summary["no_drafts"] == 1
    assert resend == []


def test_a_second_run_in_the_same_week_sends_nothing(ctx, resend):
    user = make_user()
    add_task(user)
    drafts.sweep_drafts()
    drafts.sweep_drafts()
    drafts.sweep_drafts()
    assert len(resend) == 1


def test_the_key_rolls_over_between_weeks(ctx):
    """A fixed key would send this once per account ever; the ISO-week key is
    what turns the ledger's unique constraint into a weekly cap."""
    now = datetime(2026, 8, 25)
    assert drafts.weekly_key(now) != drafts.weekly_key(now + timedelta(days=7))
    assert drafts.weekly_key(now) == drafts.weekly_key(now + timedelta(days=1))


def test_a_student_who_did_not_ask_for_reminders_is_not_mailed(ctx, resend):
    user = make_user(email_reminders_opt_in=False)
    add_task(user)
    summary = drafts.sweep_drafts()
    assert summary["sent"] == 0
    assert resend == []


def test_marketing_consent_is_not_what_gates_this(ctx, resend):
    """A planner has to be able to remind you about your own plan without a
    marketing opt-in."""
    user = make_user(marketing_emails_opt_in=False, marketing_opt_in_at=None)
    add_task(user)
    assert drafts.sweep_drafts()["sent"] == 1


def test_a_suppressed_address_is_still_never_mailed(ctx, resend):
    user = make_user()
    add_task(user)
    app_module.db.session.add(app_module.EmailSuppression(email=user.email.lower()))
    app_module.db.session.commit()
    summary = drafts.sweep_drafts()
    assert summary["sent"] == 0
    assert summary["reasons"].get("suppressed") == 1


def test_an_under_13_account_without_consent_is_not_mailed(ctx, resend):
    user = make_user(birth_year=datetime.utcnow().year - 10)
    add_task(user)
    assert drafts.sweep_drafts()["sent"] == 0
    assert resend == []


def test_a_dry_run_sends_nothing_but_reports_who_would_get_it(ctx, resend):
    user = make_user()
    add_task(user)
    summary = drafts.sweep_drafts(dry_run=True)
    assert resend == []
    assert summary["would_send"] == 1
    assert summary["preview"][0]["email"] == user.email


def test_the_email_carries_an_unsubscribe_header(ctx, resend):
    user = make_user()
    add_task(user)
    drafts.sweep_drafts()
    assert "List-Unsubscribe" in resend[0]["headers"]


def test_all_three_kinds_render_together(ctx, resend):
    user = make_user()
    add_stale_session(user)
    add_plan(user)
    add_task(user)
    drafts.sweep_drafts()
    body = resend[0]["text"]
    for expected in ("Lab writeup", "My Schedule", "Read chapters 4-6"):
        assert expected in body, expected
    assert "{{" not in resend[0]["html"]


# ── Cadence: how often one item is allowed to come up ───────────────
#
# Both of these came out of a day-by-day simulation of a student's first
# three weeks rather than from reasoning about the code, and both were real:
# the same abandoned plan produced four weekly emails, and a student got
# "One focused session is all it takes" and "You left a few things
# unfinished" on the same morning.


def test_one_abandoned_item_is_mentioned_twice_and_then_stops(ctx, resend):
    """Weekly email + a fortnight-long window = at most two mentions."""
    user = make_user()
    add_plan(user, days_ago=drafts.PLAN_UNTOUCHED_DAYS)
    start = datetime.utcnow()

    mentions = 0
    for week in range(6):
        summary = drafts.sweep_drafts(now=start + timedelta(weeks=week))
        mentions += summary["sent"]
    assert mentions == 2, f"the same plan was mentioned {mentions} times"


def test_an_item_past_the_window_is_not_mentioned_at_all(ctx):
    user = make_user()
    add_plan(user, days_ago=drafts.ITEM_MAX_AGE_DAYS + 1)
    add_task(user, due_days_ago=drafts.ITEM_MAX_AGE_DAYS + 1)
    add_stale_session(user, hours_ago=24 * (drafts.ITEM_MAX_AGE_DAYS + 1))
    assert drafts.find_drafts(user.id) == []


def test_an_account_still_being_onboarded_is_left_out(ctx, resend):
    """A student four days in has unfinished work by definition — that is
    what being four days in looks like. The onboarding sequence owns those
    days, and two campaigns mailing the same morning say opposite things
    about the same account."""
    user = make_user(created_at=datetime.utcnow() - timedelta(days=4))
    add_task(user)
    assert drafts.find_drafts(user.id), "fixture should have produced a draft"
    summary = drafts.sweep_drafts()
    assert summary["sent"] == 0
    assert resend == []


def test_an_account_past_the_onboarding_window_is_mailed(ctx, resend):
    """The other side of the same boundary, so the guard cannot silently
    become 'never send'."""
    from intelliplan.email import onboarding

    user = make_user(
        created_at=datetime.utcnow() - timedelta(days=onboarding.MAX_AGE_DAYS + 1)
    )
    add_task(user)
    assert drafts.sweep_drafts()["sent"] == 1
