"""Pure growth rules: retention math, plans, streak defence, AI hooks.

No database and no Flask app. These are the definitions every growth
decision rests on, so they are pinned here where a change to one is a
visible test change rather than a silently different dashboard.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

import ai_provider
from intelliplan.growth import plans, retention
from intelliplan.growth.retention import UserFacts
from intelliplan.notifications import sources
from intelliplan.notifications.events import Channel, EventKind, render
from intelliplan.notifications.preferences import Preferences, preferences_from_user

D0 = date(2026, 6, 1)  # a Monday


def facts(uid, *, connected=True, planned=True, active=(), signed_up=D0):
    return UserFacts(
        user_id=uid,
        signed_up=signed_up,
        connected=connected,
        planned=planned,
        active_dates=frozenset(signed_up + timedelta(days=n) for n in active),
    )


# ── Funnel ───────────────────────────────────────────────────────────


def test_funnel_steps_are_strictly_nested():
    users = [
        facts(1, active=(0, 1)),
        facts(2, active=(0,)),
        # Came back but never planned: must not count as "came back".
        facts(3, planned=False, active=(0, 3)),
        facts(4, connected=False, planned=False),
    ]
    funnel = {row["step"]: row for row in retention.activation_funnel(users)}
    assert funnel["signed_up"]["count"] == 4
    assert funnel["connected"]["count"] == 3
    assert funnel["planned"]["count"] == 2
    assert funnel["came_back"]["count"] == 1
    assert funnel["planned"]["of_previous"] == pytest.approx(2 / 3, abs=1e-4)


def test_biggest_leak_is_by_share_not_raw_count():
    # 100 -> 90 loses 10 people (10%); 90 -> 9 loses 81 (90%).
    funnel = [
        {"step": "a", "label": "A", "count": 100},
        {"step": "b", "label": "B", "count": 90},
        {"step": "c", "label": "C", "count": 9},
    ]
    leak = retention.biggest_leak(funnel)
    assert leak["to_step"] == "c"
    assert leak["lost"] == 81


def test_biggest_leak_is_none_with_no_signups():
    assert retention.biggest_leak(retention.activation_funnel([])) is None


# ── Cohorts ──────────────────────────────────────────────────────────


def test_cohort_windows_and_eligibility():
    today = D0 + timedelta(days=20)
    users = [
        facts(1, active=(1, 8)),  # D1 and D7
        facts(2, active=(12,)),   # D7 only (window is 7-13)
        facts(3, active=(2,)),    # neither
    ]
    [row] = retention.cohort_retention(users, today)
    assert row["size"] == 3
    assert row["d1"] == {"eligible": 3, "retained": 1, "rate": pytest.approx(0.3333)}
    assert row["d7"]["retained"] == 2
    # Nobody is 60 days old yet: "not measurable", not zero.
    assert row["d30"]["eligible"] == 0
    assert row["d30"]["rate"] is None


def test_student_is_not_eligible_until_window_closes():
    today = D0 + timedelta(days=10)  # D7 window (7-13) still open
    [row] = retention.cohort_retention([facts(1, active=(8,))], today)
    assert row["d7"]["eligible"] == 0 and row["d7"]["rate"] is None


def test_cohorts_group_by_iso_week_newest_first():
    users = [facts(1), facts(2, signed_up=D0 + timedelta(days=6)), facts(3, signed_up=D0 + timedelta(days=7))]
    rows = retention.cohort_retention(users, D0 + timedelta(days=90))
    assert [r["cohort"] for r in rows] == ["2026-06-08", "2026-06-01"]
    assert [r["size"] for r in rows] == [1, 2]


# ── Plans ────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 17, 12, 0)


def test_plan_is_paid_only_while_window_is_open():
    assert plans.plan_for(None, NOW) == plans.FREE
    assert plans.plan_for(NOW - timedelta(seconds=1), NOW) == plans.FREE
    assert plans.plan_for(NOW + timedelta(days=1), NOW) == plans.PAID


def test_referral_months_stack_from_the_end_of_an_open_window():
    open_window = NOW + timedelta(days=14)
    assert plans.extend_paid_until(open_window, NOW, 30) == NOW + timedelta(days=44)
    lapsed = NOW - timedelta(days=100)
    assert plans.extend_paid_until(lapsed, NOW, 30) == NOW + timedelta(days=30)


def test_allowance_state(monkeypatch):
    monkeypatch.setenv("AI_FREE_MONTHLY_GENERATIONS", "3")
    assert plans.allowance_state(2, plans.FREE) == {
        "plan": "free", "used": 2, "limit": 3, "remaining": 1, "exhausted": False,
    }
    assert plans.allowance_state(3, plans.FREE)["exhausted"] is True
    paid = plans.allowance_state(500, plans.PAID)
    assert paid["limit"] is None and paid["exhausted"] is False


def test_bad_allowance_env_falls_back(monkeypatch):
    monkeypatch.setenv("AI_FREE_MONTHLY_GENERATIONS", "lots")
    assert plans.free_monthly_allowance() == plans.DEFAULT_FREE_MONTHLY


# ── Streak defence ───────────────────────────────────────────────────


def test_streak_warning_fires_in_the_evening_for_a_streak_from_yesterday():
    now = datetime(2026, 6, 14, 19, 30)
    event = sources.streak_at_risk(7, 5, date(2026, 6, 13), now)
    assert event is not None
    assert event.kind is EventKind.STREAK_AT_RISK
    assert event.dedupe_key == "streak:2026-06-14"
    msg = render(event, Channel.PUSH)
    assert "5-day streak" in msg.title and "5h" in msg.title


@pytest.mark.parametrize(
    "last, hour",
    [
        (date(2026, 6, 14), 19),  # already safe today
        (date(2026, 6, 12), 19),  # already broken
        (date(2026, 6, 13), 12),  # too early to know
        (date(2026, 6, 13), 22),  # quiet hours
    ],
)
def test_streak_warning_stays_quiet_otherwise(last, hour):
    assert sources.streak_at_risk(7, 5, last, datetime(2026, 6, 14, hour)) is None


def test_no_warning_without_a_streak():
    assert sources.streak_at_risk(7, 0, date(2026, 6, 13), datetime(2026, 6, 14, 19)) is None


class _User:
    id = 3
    email = "s@example.test"
    push_reminders_opt_in = False
    sms_reminders_opt_in = False
    email_reminders_opt_in = False
    streak_emails_opt_in = True


def test_streak_email_reaches_students_who_never_opted_into_reminders():
    prefs = preferences_from_user(_User())
    assert prefs.channels == frozenset()
    assert prefs.wants(EventKind.STREAK_AT_RISK, Channel.EMAIL)
    # ...and only that kind, and only by email.
    assert not prefs.wants(EventKind.DEADLINE_APPROACHING, Channel.EMAIL)
    assert not prefs.wants(EventKind.STREAK_AT_RISK, Channel.PUSH)


def test_streak_email_respects_the_off_switch():
    user = _User()
    user.streak_emails_opt_in = False
    assert not preferences_from_user(user).wants(EventKind.STREAK_AT_RISK, Channel.EMAIL)


def test_streak_email_respects_a_custom_kind_list_without_streaks():
    prefs = Preferences(user_id=1, kinds=frozenset({EventKind.SESSION_UPCOMING}), streak_email=True)
    assert not prefs.wants(EventKind.STREAK_AT_RISK, Channel.EMAIL)


# ── ai_provider account hooks ────────────────────────────────────────


@pytest.fixture
def hooks():
    yield
    ai_provider.set_account_hooks(None, None)


def _stub_chain(monkeypatch, seen):
    def fake_chain(tier="standard", plan="free"):
        seen.append(plan)
        return [("gemini", "m")]

    monkeypatch.setattr(ai_provider, "model_chain", fake_chain)
    monkeypatch.setattr(ai_provider, "_gemini_chat", lambda *a, **k: "ok")


def test_plan_resolver_decides_the_chain_when_caller_passes_none(monkeypatch, hooks):
    seen: list[str] = []
    _stub_chain(monkeypatch, seen)
    ai_provider.set_account_hooks(plan_resolver=lambda: "paid")
    assert ai_provider.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert seen == ["paid"]


def test_explicit_plan_beats_the_resolver(monkeypatch, hooks):
    seen: list[str] = []
    _stub_chain(monkeypatch, seen)
    ai_provider.set_account_hooks(plan_resolver=lambda: "paid")
    ai_provider.chat([{"role": "user", "content": "hi"}], plan="free")
    assert seen == ["free"]


def test_allowance_refusal_propagates_and_is_a_quota_error(monkeypatch, hooks):
    _stub_chain(monkeypatch, [])

    def gate(plan):
        raise ai_provider.AIAllowanceExceeded("spent", limit=3, used=3)

    ai_provider.set_account_hooks(usage_gate=gate)
    with pytest.raises(ai_provider.AIQuotaExhausted) as info:
        ai_provider.chat([{"role": "user", "content": "hi"}])
    assert isinstance(info.value, ai_provider.AIAllowanceExceeded)


def test_a_broken_gate_fails_open(monkeypatch, hooks):
    _stub_chain(monkeypatch, [])
    ai_provider.set_account_hooks(usage_gate=lambda plan: 1 / 0)
    assert ai_provider.chat([{"role": "user", "content": "hi"}]) == "ok"
