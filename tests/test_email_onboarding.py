"""The onboarding sequence: sequencing rules, and the goal checks that stop it.

The rule worth protecting is that a step whose goal is already met is never
sent. Everything else here is about a cron that runs late, twice, or not at
all for a week.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import App as app_module
from intelliplan.email import onboarding


@pytest.fixture
def ctx():
    """An app context over an empty user table.

    The suite shares one in-memory database for the whole process, so
    without this the accounts left behind by an earlier test land inside
    this one's sweep window and every "sent == 1" assertion counts someone
    else's mail. Cleaning up front rather than after means a failing test
    leaves its rows behind to be inspected.
    """
    with app_module.app.app_context():
        for model in (
            app_module.EmailSend,
            app_module.EmailSuppression,
            app_module.LinkedAccount,
            app_module.SavedSchedule,
            app_module.ActiveSession,
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


def make_user(days_old: float = 0.0, **overrides):
    defaults = {
        "email": f"onb-{uuid.uuid4().hex[:10]}@example.test",
        "name": "Sam",
        "birth_year": 2000,
        "role": "student",
        "password_hash": "x",
        "created_at": datetime.utcnow() - timedelta(days=days_old),
    }
    defaults.update(overrides)
    user = app_module.User(**defaults)
    app_module.db.session.add(user)
    app_module.db.session.commit()
    return user


NONE_DONE = {"connected": False, "planned": False, "studied": False}


# ── due_step: the sequencing rules, no database needed ──────────────


def test_nothing_is_due_before_the_first_step():
    assert onboarding.due_step(1.0, set(), NONE_DONE) is None


def test_the_first_step_is_due_on_its_day():
    step = onboarding.due_step(2.0, set(), NONE_DONE)
    assert step is not None and step.goal == "connected"


def test_a_met_goal_drops_its_step_rather_than_deferring_it():
    """The whole point of the module: never ask someone to do what they did."""
    progress = {**NONE_DONE, "connected": True}
    # Day 2 would have been "connect". It is skipped, and nothing else is due
    # yet, so this student gets no mail at all today.
    assert onboarding.due_step(2.0, set(), progress) is None
    # And on day 4 they move to the next rung, never receiving "connect".
    step = onboarding.due_step(4.0, set(), progress)
    assert step is not None and step.goal == "planned"


def test_a_fully_onboarded_student_gets_nothing_ever():
    done = {"connected": True, "planned": True, "studied": True}
    for age in (2.0, 4.0, 7.0, 10.0):
        assert onboarding.due_step(age, set(), done) is None


def test_the_earliest_unmet_rung_wins_not_the_furthest():
    """A student who never connected is asked about that, not about building
    a plan for assignments IntelliPlan cannot see.

    Day 5: both the day-2 and day-4 rungs are due and unmet. The earlier one
    has to win, or the ladder is climbed from the top.
    """
    step = onboarding.due_step(5.0, set(), NONE_DONE)
    assert step is not None and step.goal == "connected"


def test_an_already_sent_step_is_not_resent():
    sent = {onboarding.STEPS[0].key}
    step = onboarding.due_step(4.0, sent, NONE_DONE)
    assert step is not None and step.key == onboarding.STEPS[1].key


def test_a_step_ages_out_of_its_grace_window():
    """A 'connect your school' mail three weeks after signup is not
    onboarding, so the first rung expires and a later one applies."""
    age = onboarding.STEPS[0].day + onboarding.GRACE_DAYS + 1
    step = onboarding.due_step(age, set(), NONE_DONE)
    assert step is None or step.key != onboarding.STEPS[0].key


def test_a_late_cron_still_catches_a_step_inside_grace():
    age = onboarding.STEPS[0].day + onboarding.GRACE_DAYS
    step = onboarding.due_step(age, set(), NONE_DONE)
    assert step is not None and step.key == onboarding.STEPS[0].key


# ── The sweep, against the real database ────────────────────────────


def test_the_sweep_sends_one_email_and_only_one(ctx, resend):
    make_user(days_old=2.1)
    summary = onboarding.sweep_onboarding()
    assert summary["sent"] == 1
    assert len(resend) == 1
    assert "Connect your school" in resend[0]["subject"]


def test_a_second_run_sends_nothing(ctx, resend):
    """The ledger, not the window, is what stops the repeat."""
    make_user(days_old=2.1)
    onboarding.sweep_onboarding()
    before = len(resend)
    onboarding.sweep_onboarding()
    assert len(resend) == before


def test_a_student_who_connected_is_never_asked_to_connect(ctx, resend):
    user = make_user(days_old=2.1)
    app_module.db.session.add(
        app_module.LinkedAccount(
            user_id=user.id,
            profile_id="p1",
            name="Canvas",
            login_type="canvas",
            credentials="{}",
        )
    )
    app_module.db.session.commit()

    onboarding.sweep_onboarding()
    subjects = [m["subject"] for m in resend]
    assert not any("Connect your school" in s for s in subjects), subjects


def test_a_backlogged_account_gets_at_most_one_email_per_run(ctx, resend):
    """Cron down for a week. The student is due for several rungs at once and
    must still receive exactly one message."""
    make_user(days_old=7.5)
    summary = onboarding.sweep_onboarding()
    assert summary["sent"] <= 1
    assert len(resend) <= 1


def test_accounts_outside_the_window_are_left_alone(ctx, resend):
    make_user(days_old=0.5)                                # too new
    make_user(days_old=onboarding.MAX_AGE_DAYS + 5)        # too old
    summary = onboarding.sweep_onboarding()
    assert summary["sent"] == 0
    assert resend == []


def test_the_email_carries_an_unsubscribe_header(ctx, resend):
    """Transactional, but still unsubscribable — the same contract the
    welcome mail signs."""
    make_user(days_old=2.1)
    onboarding.sweep_onboarding()
    assert "List-Unsubscribe" in resend[0]["headers"]


def test_a_suppressed_address_is_not_mailed(ctx, resend):
    user = make_user(days_old=2.1)
    app_module.db.session.add(app_module.EmailSuppression(email=user.email.lower()))
    app_module.db.session.commit()
    summary = onboarding.sweep_onboarding()
    assert summary["sent"] == 0
    assert summary["reasons"].get("suppressed") == 1


def test_an_under_13_account_without_consent_is_not_mailed(ctx, resend):
    make_user(days_old=2.1, birth_year=datetime.utcnow().year - 10)
    summary = onboarding.sweep_onboarding()
    assert summary["sent"] == 0
    assert resend == []


def test_progress_reads_all_of_the_goals(ctx):
    user = make_user(days_old=1)
    progress = onboarding.onboarding_progress(user.id)
    assert progress == {"connected": False, "planned": False, "studied": False}

    app_module.db.session.add(
        app_module.SavedSchedule(user_id=user.id, name="Week", schedule_data="{}")
    )
    app_module.db.session.commit()
    assert onboarding.onboarding_progress(user.id)["planned"] is True


def test_an_abandoned_session_does_not_count_as_having_studied(ctx):
    """Starting a timer and walking away is not the experience the step
    sells, so that student stays on the list."""
    user = make_user(days_old=1)
    app_module.db.session.add(
        app_module.ActiveSession(user_id=user.id, title="Essay", state="abandoned")
    )
    app_module.db.session.commit()
    assert onboarding.onboarding_progress(user.id)["studied"] is False

    app_module.db.session.add(
        app_module.ActiveSession(user_id=user.id, title="Essay", state="completed")
    )
    app_module.db.session.commit()
    assert onboarding.onboarding_progress(user.id)["studied"] is True


def test_steps_are_ordered_by_day(ctx):
    """due_step returns early on the first not-yet-due step, which is only
    correct while STEPS stays sorted."""
    days = [s.day for s in onboarding.STEPS]
    assert days == sorted(days)


def test_every_step_has_a_template_pair(ctx):
    """A step whose template is missing renders nothing and burns its ledger
    row on a `render_failed`."""
    import pathlib

    from intelliplan.email import templates as tmpl

    for step in onboarding.STEPS:
        html = pathlib.Path("Main_Project/templates/emails") / f"{step.template}.html"
        text = pathlib.Path(tmpl._TEXT_DIR) / f"{step.template}.txt"
        assert html.exists(), html
        assert text.exists(), text
