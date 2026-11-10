"""Regression coverage for completion-aware reminders and opt-in check-ins."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import App
import notifications_glue as notifications
from intelliplan.learning import policy
from intelliplan.notifications.events import Channel, EventKind, NotificationEvent
from time_utils import utcnow


def _user(prefix: str, **fields):
    user = App.User(
        email=f"{prefix}-{uuid.uuid4().hex}@example.test",
        password_hash="",
        **fields,
    )
    App.db.session.add(user)
    App.db.session.commit()
    return user


def _cleanup(uid: int) -> None:
    App.NotificationOutbox.query.filter_by(user_id=uid).delete(synchronize_session=False)
    App.PushSubscription.query.filter_by(user_id=uid).delete(synchronize_session=False)
    App.SavedSchedule.query.filter_by(user_id=uid).delete(synchronize_session=False)
    App.DismissedAssignment.query.filter_by(user_id=uid).delete(synchronize_session=False)
    App.ManualTask.query.filter_by(user_id=uid).delete(synchronize_session=False)
    App.User.query.filter_by(id=uid).delete(synchronize_session=False)
    App.db.session.commit()


def test_completion_cancels_existing_outbox_rows_and_dispatch_rechecks(monkeypatch):
    with App.app.app_context():
        user = _user("notification-complete", push_reminders_opt_in=True)
        uid = user.id
        try:
            event = NotificationEvent(
                kind=EventKind.DEADLINE_APPROACHING,
                user_id=uid,
                dedupe_key="essay-deadline",
                context={"title": "Research Essay", "days_until": 1, "remaining_minutes": 45},
                entity_key="Research Essay",
            )
            prefs = notifications._preferences_for(user)
            # There is no subscription in this setup, so enqueue the durable
            # row directly and test the same cancellation/dispatch contract.
            row = App.NotificationOutbox(
                user_id=uid, kind=event.kind.value, channel=Channel.PUSH.value,
                dedupe_key=event.key_for(Channel.PUSH), entity_key=event.entity_key,
                title="Deadline coming up", body="Research Essay is due tomorrow.",
                url="/scheduler", state="pending", scheduled_for=utcnow() - timedelta(minutes=1),
                expires_at=utcnow() + timedelta(hours=1),
            )
            App.db.session.add(row)
            App.db.session.commit()

            App.db.session.add(App.DismissedAssignment(user_id=uid, title="Research Essay", data="{}"))
            App.db.session.commit()
            assert notifications.cancel_assignment_reminders(uid, "Research Essay") == 1
            assert row.state == "cancelled"

            # The validator is the race-safe backstop if a row was claimed
            # between the completion and the direct queue cancellation.
            row.state = "pending"
            row.last_error = None
            App.db.session.commit()
            sent = []
            result = notifications.get_dispatcher()
            monkeypatch.setattr(result, "_senders", {Channel.PUSH: lambda r: sent.append(r) or True})
            outcome = result.flush()
            assert outcome.sent == 0 and outcome.skipped == 1
            assert row.state == "cancelled" and sent == []
        finally:
            _cleanup(uid)


def test_inactive_check_in_is_push_only_opt_in_and_one_per_absence(monkeypatch):
    with App.app.app_context():
        user = _user(
            "notification-check-in", role="student", push_reminders_opt_in=True,
            email_reminders_opt_in=True, last_active_at=utcnow() - timedelta(days=4, minutes=1),
        )
        uid = user.id
        try:
            App.db.session.add(App.PushSubscription(
                user_id=uid, endpoint=f"https://push.example.test/{uid}", subscription_json="{}"))
            App.db.session.add(App.SavedSchedule(
                user_id=uid, name="Current", is_active=True,
                schedule_data=json.dumps({"schedule": []}), progress_json="{}"))
            App.db.session.commit()
            monkeypatch.setattr(policy, "decision", lambda _user, _feature: (True, None))

            first = notifications.sweep_inactive_check_ins(now=utcnow())
            second = notifications.sweep_inactive_check_ins(now=utcnow())
            rows = App.NotificationOutbox.query.filter_by(user_id=uid).all()
            assert first == {"checked": 1, "queued": 1}
            assert second == {"checked": 1, "queued": 0}
            assert len(rows) == 1
            assert rows[0].kind == EventKind.INACTIVE_CHECK_IN.value
            assert rows[0].channel == Channel.PUSH.value
        finally:
            _cleanup(uid)
