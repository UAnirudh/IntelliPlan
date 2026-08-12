"""Tests for the notification subsystem."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from intelliplan.notifications.dispatcher import (
    CLAIM_TIMEOUT,
    Dispatcher,
    PermanentDeliveryError,
)
from intelliplan.notifications.events import (
    SMS_MAX_CHARS,
    Channel,
    EventKind,
    NotificationEvent,
    render,
)
from intelliplan.notifications.models import MAX_ATTEMPTS, backoff_for
from intelliplan.notifications.preferences import (
    Preferences,
    QuietHours,
    preferences_from_user,
)
from intelliplan.notifications import sources

NOW = datetime(2026, 8, 12, 16, 0)
TODAY = NOW.date()


# ── Rendering ─────────────────────────────────────────────────────────


def event(kind=EventKind.SESSION_UPCOMING, **context):
    return NotificationEvent(kind=kind, user_id=1, dedupe_key="k", context=context)


def test_every_kind_renders_on_every_channel():
    for kind in EventKind:
        for channel in Channel:
            msg = render(event(kind, title="Essay", course="APUSH", minutes_until=15,
                               days_until=1, count=2, actual_minutes=40, minutes_short=90,
                               new_tasks=3), channel)
            assert msg.title and msg.body, (kind, channel)


def test_sms_is_truncated_to_one_segment():
    msg = render(event(title="X" * 400, course="Y" * 200, minutes_until=5), Channel.SMS)
    assert len(msg.as_sms()) <= SMS_MAX_CHARS


def test_sms_folds_the_title_in_because_it_has_no_title_field():
    msg = render(event(title="Essay", minutes_until=10), Channel.SMS)
    assert msg.title in msg.as_sms()


def test_email_carries_detail_that_push_drops():
    ev = NotificationEvent(
        kind=EventKind.PLAN_OVERLOADED, user_id=1, dedupe_key="k",
        context={"minutes_short": 90, "detail": "- Essay: 90 min"},
    )
    assert "Essay" in render(ev, Channel.EMAIL).body
    assert "Essay" not in render(ev, Channel.PUSH).body


def test_minutes_phrase_reads_naturally():
    assert "in 5 min" in render(event(minutes_until=5), Channel.PUSH).body
    assert "in 2h" in render(event(minutes_until=120), Channel.PUSH).body
    assert "now" in render(event(minutes_until=0), Channel.PUSH).body


def test_missing_context_does_not_raise():
    for kind in EventKind:
        assert render(event(kind), Channel.PUSH).body


def test_missed_session_message_is_not_a_telling_off():
    body = render(event(EventKind.SESSION_MISSED, title="Chem lab"), Channel.PUSH).body.lower()
    for scold in ("should have", "failed", "you didn't bother", "again"):
        assert scold not in body


def test_dedupe_key_is_per_channel():
    ev = event()
    assert ev.key_for(Channel.PUSH) != ev.key_for(Channel.SMS)


# ── Preferences and quiet hours ───────────────────────────────────────


def prefs(**over):
    base = dict(
        user_id=1,
        channels=frozenset({Channel.PUSH}),
        quiet_hours=QuietHours(22, 7, True),
    )
    base.update(over)
    return Preferences(**base)


def test_quiet_hours_wrap_past_midnight():
    q = QuietHours(22, 7, True)
    assert q.contains(datetime(2026, 8, 12, 23, 0))
    assert q.contains(datetime(2026, 8, 12, 2, 0))
    assert not q.contains(datetime(2026, 8, 12, 12, 0))


def test_quiet_hours_can_be_disabled():
    assert not QuietHours(22, 7, False).contains(datetime(2026, 8, 12, 23, 0))


def test_non_urgent_messages_are_held_until_morning():
    p = prefs()
    midnight = datetime(2026, 8, 12, 23, 30)
    out = p.delivery_time(EventKind.SESSION_COMPLETED, midnight)
    assert out > midnight
    assert not p.quiet_hours.contains(p.to_local(out))


def test_urgent_messages_ignore_quiet_hours():
    p = prefs()
    midnight = datetime(2026, 8, 12, 23, 30)
    assert p.delivery_time(EventKind.SESSION_UPCOMING, midnight) == midnight


def test_quiet_hours_use_the_students_timezone_not_the_servers():
    # 23:30 UTC is 09:30 the next morning in UTC+10 — not quiet at all.
    p = prefs(utc_offset_minutes=600)
    moment = datetime(2026, 8, 12, 23, 30)
    assert p.delivery_time(EventKind.SESSION_COMPLETED, moment) == moment
    # And the same instant is 18:30 in UTC-5, also fine.
    assert prefs(utc_offset_minutes=-300).delivery_time(EventKind.SESSION_COMPLETED, moment) == moment


def test_a_channel_the_student_did_not_enable_is_never_wanted():
    p = prefs(channels=frozenset({Channel.PUSH}))
    assert p.wants(EventKind.SESSION_UPCOMING, Channel.PUSH)
    assert not p.wants(EventKind.SESSION_UPCOMING, Channel.SMS)


def test_completion_pats_are_off_by_default():
    p = prefs()
    assert not p.wants(EventKind.SESSION_COMPLETED, Channel.PUSH)
    assert p.wants(EventKind.SESSION_UPCOMING, Channel.PUSH)


def test_preferences_read_off_a_user_row():
    user = SimpleNamespace(
        id=7, push_reminders_opt_in=True, sms_reminders_opt_in=True, phone="+15551234567",
        email_reminders_opt_in=True, email="a@b.com", utc_offset_minutes=-420,
        quiet_hours_start=23, quiet_hours_end=6, quiet_hours_enabled=True,
        reminder_lead_minutes=45, notification_kinds="session_upcoming,session_missed",
    )
    p = preferences_from_user(user, push_subscribed=True)
    assert p.channels == frozenset({Channel.PUSH, Channel.SMS, Channel.EMAIL})
    assert p.utc_offset_minutes == -420
    assert p.lead_minutes == 45
    assert p.kinds == frozenset({EventKind.SESSION_UPCOMING, EventKind.SESSION_MISSED})


def test_push_needs_a_subscription_not_just_a_checkbox():
    user = SimpleNamespace(id=7, push_reminders_opt_in=True, email=None, phone=None)
    assert Channel.PUSH not in preferences_from_user(user, push_subscribed=False).channels


def test_garbage_preferences_fall_back_to_safe_defaults():
    user = SimpleNamespace(
        id=7, utc_offset_minutes="nonsense", quiet_hours_start=99,
        reminder_lead_minutes=-5, notification_kinds="retired_kind,also_gone",
    )
    p = preferences_from_user(user)
    assert p.utc_offset_minutes == 0
    assert p.quiet_hours.start_hour == 22
    assert p.lead_minutes >= 5
    assert p.kinds  # an all-invalid list must not silence the student entirely


# ── Outbox / dispatcher ───────────────────────────────────────────────
# These run against a real in-memory SQLite database rather than a fake
# query layer. The behaviour under test — dedupe, claiming, ordering — is
# enforced by the database, so faking it would only test the fake.


@pytest.fixture
def bus():
    from flask import Flask
    from flask_sqlalchemy import SQLAlchemy
    from intelliplan.notifications.models import register

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db = SQLAlchemy(app)

    class User(db.Model):
        __tablename__ = "users"
        id = db.Column(db.Integer, primary_key=True)

    Outbox = register(db)
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    db.session.add(User(id=1))
    db.session.commit()
    yield SimpleNamespace(model=Outbox, session=db.session, db=db)
    db.session.remove()
    ctx.pop()


def make_dispatcher(bus, senders=None, now=NOW):
    return Dispatcher(bus.model, bus.session, senders or {}, now=lambda: now)


def rows_in(bus):
    return bus.model.query.all()


def upcoming(user_id=1, key="s1"):
    return NotificationEvent(
        kind=EventKind.SESSION_UPCOMING, user_id=user_id, dedupe_key=key,
        context={"title": "Essay", "minutes_until": 15},
    )


def test_enqueue_writes_one_row_per_enabled_channel(bus):
    make_dispatcher(bus).enqueue(upcoming(), prefs(channels=frozenset({Channel.PUSH, Channel.SMS})))
    assert {r.channel for r in rows_in(bus)} == {"push", "sms"}


def test_enqueue_skips_channels_the_student_disabled(bus):
    make_dispatcher(bus).enqueue(upcoming(), prefs(channels=frozenset({Channel.PUSH})))
    assert {r.channel for r in rows_in(bus)} == {"push"}


def test_enqueue_skips_kinds_the_student_disabled(bus):
    ev = NotificationEvent(kind=EventKind.SESSION_COMPLETED, user_id=1, dedupe_key="s1")
    make_dispatcher(bus).enqueue(ev, prefs())
    assert rows_in(bus) == []


def test_the_same_fact_is_only_queued_once(bus):
    d, p = make_dispatcher(bus), prefs()
    d.enqueue(upcoming(), p)
    d.enqueue(upcoming(), p)   # cron ran twice
    d.enqueue(upcoming(), p)   # and overlapped itself
    assert len(rows_in(bus)) == 1


def test_a_duplicate_does_not_stop_the_other_channels(bus):
    d = make_dispatcher(bus)
    p = prefs(channels=frozenset({Channel.PUSH, Channel.SMS}))
    d.enqueue(upcoming(), p)
    bus.session.delete(bus.model.query.filter_by(channel="sms").first())
    bus.session.commit()
    d.enqueue(upcoming(), p)
    assert {r.channel for r in rows_in(bus)} == {"push", "sms"}


def test_quiet_hours_push_the_scheduled_time_forward(bus):
    late = datetime(2026, 8, 12, 23, 30)
    d = make_dispatcher(bus, now=late)
    ev = NotificationEvent(kind=EventKind.SESSION_MISSED, user_id=1, dedupe_key="s1",
                           context={"title": "Essay"})
    d.enqueue(ev, prefs(kinds=frozenset({EventKind.SESSION_MISSED})))
    assert rows_in(bus)[0].scheduled_for > late


# ── Delivery ──────────────────────────────────────────────────────────


def queued(bus, **over):
    fields = {
        "user_id": 1, "kind": "session_upcoming", "channel": "push",
        "dedupe_key": f"k{len(rows_in(bus))}", "title": "T", "body": "B", "url": "/active",
        "state": "pending", "attempts": 0,
        "scheduled_for": NOW - timedelta(minutes=1),
        "expires_at": NOW + timedelta(hours=1), "created_at": NOW,
    }
    fields.update(over)
    row = bus.model(**fields)
    bus.session.add(row)
    bus.session.commit()
    return row


def test_flush_delivers_and_marks_sent(bus):
    row = queued(bus)
    assert make_dispatcher(bus, {Channel.PUSH: lambda r: True}).flush().sent == 1
    assert row.state == "sent" and row.sent_at


def test_messages_scheduled_in_the_future_are_left_alone(bus):
    row = queued(bus, scheduled_for=NOW + timedelta(hours=2))
    assert make_dispatcher(bus, {Channel.PUSH: lambda r: True}).flush().sent == 0
    assert row.state == "pending"


def test_a_transient_failure_retries_with_backoff(bus):
    row = queued(bus)

    def boom(r):
        raise OSError("timeout")

    result = make_dispatcher(bus, {Channel.PUSH: boom}).flush()
    assert result.failed == 1
    assert row.state == "pending"
    assert row.attempts == 1
    assert row.scheduled_for > NOW
    assert "OSError" in row.last_error


def test_backoff_grows_with_attempts():
    assert backoff_for(1) < backoff_for(3)


def test_a_permanent_failure_is_not_retried(bus):
    row = queued(bus)

    def dead(r):
        raise PermanentDeliveryError("unsubscribed endpoint")

    assert make_dispatcher(bus, {Channel.PUSH: dead}).flush().dead == 1
    assert row.state == "dead"
    assert row.attempts == 1


def test_repeated_failure_eventually_dies(bus):
    row = queued(bus, attempts=MAX_ATTEMPTS - 1)
    assert make_dispatcher(bus, {Channel.PUSH: lambda r: False}).flush().dead == 1
    assert row.state == "dead"


def test_a_stale_message_is_binned_rather_than_sent_late(bus):
    sent = []
    row = queued(bus, expires_at=NOW - timedelta(minutes=1))
    d = make_dispatcher(bus, {Channel.PUSH: lambda r: sent.append(r) or True})
    assert d.flush().expired == 1
    assert sent == []
    assert row.state == "cancelled"


def test_a_channel_with_no_provider_is_not_charged_a_retry(bus):
    row = queued(bus, channel="sms")
    assert make_dispatcher(bus, {}).flush().skipped == 1
    assert row.state == "pending"
    assert (row.attempts or 0) == 0


def test_claiming_stops_a_second_dispatcher_double_sending(bus):
    queued(bus)
    claimed = make_dispatcher(bus, {Channel.PUSH: lambda r: True})._claim(10)
    assert claimed and claimed[0].state == "sending"
    assert make_dispatcher(bus, {Channel.PUSH: lambda r: True})._claim(10) == []


def test_a_crashed_worker_releases_its_claim(bus):
    row = queued(bus, state="sending",
                 claimed_at=NOW - CLAIM_TIMEOUT - timedelta(minutes=1))
    assert make_dispatcher(bus, {Channel.PUSH: lambda r: True}).flush().sent == 1
    assert row.state == "sent"


def test_a_fresh_claim_is_not_stolen(bus):
    row = queued(bus, state="sending", claimed_at=NOW - timedelta(seconds=30))
    make_dispatcher(bus, {Channel.PUSH: lambda r: True}).flush()
    assert row.state == "sending"


def test_purge_removes_only_finished_rows(bus):
    queued(bus, state="sent", created_at=NOW - timedelta(days=60))
    keep = queued(bus, state="pending", created_at=NOW - timedelta(days=60))
    assert make_dispatcher(bus).purge_delivered(30) == 1
    assert keep.state == "pending"
    assert len(rows_in(bus)) == 1


def test_daily_cap_counter_only_counts_sent(bus):
    queued(bus, state="sent", sent_at=NOW - timedelta(hours=2))
    queued(bus, state="pending")
    d = make_dispatcher(bus)
    assert d.sent_today(1, Channel.PUSH) == 1


# ── Event sources ─────────────────────────────────────────────────────


def plan(**over):
    base = {
        "schedule": [
            {
                "date": TODAY.isoformat(),
                "blocks": [
                    {"id": "b1", "assignment": "Essay (part 1 of 2)", "parent_title": "Essay",
                     "task_id": "t1", "course": "APUSH", "duration_minutes": 45,
                     "due_date": (TODAY + timedelta(days=1)).isoformat(),
                     "start_iso": (NOW + timedelta(minutes=20)).isoformat(),
                     "end_iso": (NOW + timedelta(minutes=65)).isoformat()},
                    {"id": "brk", "assignment": "Long break", "is_break": True,
                     "duration_minutes": 15},
                ],
            }
        ],
        "overloaded": False,
        "deferred": [],
    }
    base.update(over)
    return base


def test_session_reminder_fires_inside_the_lead_window():
    events = sources.session_reminders(plan(), 1, NOW, lead_minutes=30)
    assert len(events) == 1
    assert events[0].kind is EventKind.SESSION_UPCOMING


def test_session_reminder_does_not_fire_too_early():
    assert sources.session_reminders(plan(), 1, NOW, lead_minutes=5) == []


def test_breaks_never_generate_notifications():
    events = sources.session_reminders(plan(), 1, NOW, lead_minutes=600)
    assert all("break" not in (e.context.get("title") or "").lower() for e in events)


def test_finished_blocks_do_not_remind():
    p = plan()
    p["schedule"][0]["blocks"][0]["done"] = True
    assert sources.session_reminders(p, 1, NOW, 60) == []


def test_missed_session_needs_a_grace_period():
    p = plan()
    block = p["schedule"][0]["blocks"][0]
    block["end_iso"] = (NOW - timedelta(minutes=5)).isoformat()
    assert sources.missed_sessions(p, 1, NOW) == []
    block["end_iso"] = (NOW - timedelta(minutes=45)).isoformat()
    assert len(sources.missed_sessions(p, 1, NOW)) == 1


def test_a_started_session_is_never_reported_as_missed():
    p = plan()
    p["schedule"][0]["blocks"][0]["end_iso"] = (NOW - timedelta(minutes=45)).isoformat()
    assert sources.missed_sessions(p, 1, NOW, started_block_ids=["b1"]) == []


def test_deadline_reminders_are_grouped_per_task_not_per_block():
    p = plan()
    blocks = p["schedule"][0]["blocks"]
    blocks.append({**blocks[0], "id": "b2", "assignment": "Essay (part 2 of 2)"})
    events = sources.deadline_reminders(p, 1, TODAY)
    assert len(events) == 1


def test_deadline_reminders_only_fire_at_thresholds():
    p = plan()
    p["schedule"][0]["blocks"][0]["due_date"] = (TODAY + timedelta(days=5)).isoformat()
    assert sources.deadline_reminders(p, 1, TODAY) == []


def test_a_finished_task_gets_no_deadline_reminder():
    p = plan()
    p["schedule"][0]["blocks"][0]["done"] = True
    assert sources.deadline_reminders(p, 1, TODAY) == []


def test_overload_warning_only_when_actually_overloaded():
    assert sources.overload_warning(plan(), 1, TODAY) == []
    p = plan(overloaded=True, deferred=[{"title": "Essay", "minutes": 90, "reason": "no room"}])
    events = sources.overload_warning(p, 1, TODAY)
    assert len(events) == 1
    assert events[0].context["minutes_short"] == 90


def test_overload_warning_is_once_a_day():
    p = plan(overloaded=True, deferred=[{"title": "E", "minutes": 90, "reason": "r"}])
    a = sources.overload_warning(p, 1, TODAY)[0]
    b = sources.overload_warning(p, 1, TODAY)[0]
    assert a.dedupe_key == b.dedupe_key


def test_no_plan_produces_no_events():
    assert sources.events_for_plan(None, 1, NOW) == []
    assert sources.events_for_plan({}, 1, NOW) == []


def test_malformed_plan_does_not_raise():
    for bad in ({"schedule": None}, {"schedule": [None]}, {"schedule": [{"blocks": None}]},
                {"schedule": [{"blocks": [{"start_iso": "not-a-date"}]}]}):
        assert sources.events_for_plan(bad, 1, NOW) == []


def test_reschedule_event_is_silent_when_nothing_moved():
    assert sources.reschedule_event(1, 0, "caught up", TODAY) is None
    assert sources.reschedule_event(1, 3, "caught up", TODAY) is not None
