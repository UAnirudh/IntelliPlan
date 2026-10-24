"""Notification sweeps must not re-download unchanged plans every minute."""
import hashlib
import json
import uuid

import pytest
from sqlalchemy import event

import App
import notifications_glue as notifications
from intelliplan.learning import policy


@pytest.fixture
def cohort(monkeypatch):
    prefix = 'egress-' + uuid.uuid4().hex
    with App.app.app_context():
        rows = [App.User(email=f'{prefix}-{i}@example.test', password_hash='',
                         email_reminders_opt_in=True, lms_preferences='x' * 30000) for i in range(10)]
        App.db.session.add_all(rows)
        App.db.session.flush()
        ids = [row.id for row in rows]
        App.db.session.add_all([App.UserStreak(user_id=uid, timezone='America/Los_Angeles') for uid in ids])
        App.db.session.add_all([App.SavedSchedule(user_id=uid, name='Synthetic', is_active=True,
            schedule_data=json.dumps({'schedule': [], 'padding': 'x' * 50000}), progress_json='{}') for uid in ids])
        App.db.session.commit()
        engine = App.db.engine
        connection = engine.raw_connection()
        try:
            connection.create_function('md5', 1, lambda value: hashlib.md5(value.encode()).hexdigest())
        finally:
            connection.close()
    monkeypatch.setattr(policy, 'decision', lambda _user, _feature: (True, None))
    monkeypatch.setattr(notifications.sources, 'events_for_plan', lambda *_a, **_kw: [])
    yield ids
    with App.app.app_context():
        App.SavedSchedule.query.filter(App.SavedSchedule.user_id.in_(ids)).delete(synchronize_session=False)
        App.UserStreak.query.filter(App.UserStreak.user_id.in_(ids)).delete(synchronize_session=False)
        App.PushSubscription.query.filter(App.PushSubscription.user_id.in_(ids)).delete(synchronize_session=False)
        App.User.query.filter(App.User.id.in_(ids)).delete(synchronize_session=False)
        App.db.session.commit()
        if hasattr(notifications, '_clear_plan_payload_cache'):
            notifications._clear_plan_payload_cache()


def statements_for(operation):
    statements = []
    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    engine = App.db.engine
    event.listen(engine, 'before_cursor_execute', record)
    try:
        operation()
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    return statements


def test_sweep_has_constant_query_count_and_projects_user_payloads(cohort, monkeypatch):
    if hasattr(notifications, '_fingerprints_supported'):
        monkeypatch.setattr(notifications, '_fingerprints_supported', lambda: True)
    with App.app.app_context():
        first = statements_for(lambda: notifications.sweep_all(limit=500))
        assert len(first) <= 6
        second = statements_for(lambda: notifications.sweep_all(limit=500))
        assert len(second) <= 5
    assert not any('users.lms_preferences' in statement or 'users.password_hash' in statement for statement in first)
    assert not any('SELECT count(*)' in statement and 'push_subscriptions' in statement for statement in second)


def test_fresh_fingerprints_invalidate_schedule_progress_and_replacement(cohort, monkeypatch):
    monkeypatch.setattr(notifications, '_fingerprints_supported', lambda: True, raising=False)
    uid = cohort[0]
    with App.app.app_context():
        row = App.SavedSchedule.query.filter_by(user_id=uid).first()
        row.schedule_data = json.dumps({'schedule': [{'blocks': [{'block_id': 'b1', 'title': 'Old'}]}]})
        App.db.session.commit()
        assert notifications._plan_for(uid)['schedule'][0]['blocks'][0]['title'] == 'Old'
        row = App.SavedSchedule.query.filter_by(user_id=uid).first()
        row.schedule_data = row.schedule_data.replace('Old', 'New')
        App.db.session.commit()
        assert notifications._plan_for(uid)['schedule'][0]['blocks'][0]['title'] == 'New'
        row = App.SavedSchedule.query.filter_by(user_id=uid).first()
        row.progress_json = json.dumps({'b1': {'done': True}})
        App.db.session.commit()
        assert notifications._plan_for(uid)['schedule'][0]['blocks'][0]['done'] is True
        first = notifications._plan_for(uid)
        first['schedule'][0]['blocks'][0]['title'] = 'Mutated caller'
        assert notifications._plan_for(uid)['schedule'][0]['blocks'][0]['title'] == 'New'
        row.is_active = False
        App.db.session.add(App.SavedSchedule(user_id=uid, name='Replacement', is_active=True,
            schedule_data=json.dumps({'schedule': [], 'replacement': True}), progress_json='{}'))
        App.db.session.commit()
        assert notifications._plan_for(uid)['replacement'] is True
        App.SavedSchedule.query.filter_by(user_id=uid).delete(synchronize_session=False)
        App.db.session.commit()
        assert notifications._plan_for(uid) is None


def test_cache_drops_deleted_accounts_even_if_an_orphan_plan_remains(cohort, monkeypatch):
    monkeypatch.setattr(notifications, '_fingerprints_supported', lambda: True)
    with App.app.app_context():
        uid = cohort[0]
        assert notifications._plan_for(uid) is not None
        # SQLite does not enforce these foreign keys. The live deletion path
        # deletes plans too; the header still must never trust an orphan.
        App.User.query.filter_by(id=uid).delete(synchronize_session=False)
        App.db.session.commit()
        assert notifications._plan_for(uid) is None
        assert (App.db.engine, uid) not in notifications._PLAN_PAYLOAD_CACHE


def test_cache_enforces_entry_and_total_byte_limits(monkeypatch):
    notifications._clear_plan_payload_cache()
    monkeypatch.setattr(notifications, '_PLAN_CACHE_MAX_ENTRIES', 2)
    monkeypatch.setattr(notifications, '_PLAN_CACHE_MAX_BYTES', 100)
    monkeypatch.setattr(notifications, '_PLAN_CACHE_ENTRY_BYTES', 50)
    try:
        for uid in range(3):
            notifications._cache_payload(('synthetic-engine', uid), (uid, 'data', 'progress'), (b'12345678', b'{}'))
        assert len(notifications._PLAN_PAYLOAD_CACHE) == 2
        assert ('synthetic-engine', 0) not in notifications._PLAN_PAYLOAD_CACHE
        monkeypatch.setattr(notifications, '_PLAN_CACHE_MAX_BYTES', 15)
        notifications._cache_payload(('synthetic-engine', 3), (3, 'data', 'progress'), (b'12345678', b'{}'))
        assert notifications._PLAN_CACHE_BYTES <= 15
        assert len(notifications._PLAN_PAYLOAD_CACHE) == 1
        notifications._cache_payload(('synthetic-engine', 4), (4, 'data', 'progress'), (b'x' * 51, b'{}'))
        assert ('synthetic-engine', 4) not in notifications._PLAN_PAYLOAD_CACHE
    finally:
        notifications._clear_plan_payload_cache()


def test_sweep_preserves_channel_consent_quiet_hours_and_outbox_dedup(cohort, monkeypatch):
    from datetime import datetime
    from intelliplan.notifications.events import NotificationEvent, EventKind
    monkeypatch.setattr(notifications, '_fingerprints_supported', lambda: True)
    uid = cohort[0]
    key = 'egress-dedupe-' + uuid.uuid4().hex
    moment = datetime(2026, 10, 23, 6, 0)  # 23:00 on the previous day in LA.
    def events(_plan, *, user_id, **_kwargs):
        return [NotificationEvent(kind=EventKind.SESSION_RESCHEDULED, user_id=uid,
            dedupe_key=key, context={'title': 'Synthetic', 'count': 1}, deliver_at=moment)] if user_id == uid else []
    monkeypatch.setattr(notifications.sources, 'events_for_plan', events)
    with App.app.app_context():
        try:
            assert notifications.sweep_all(now=moment)['queued'] == 1
            assert notifications.sweep_all(now=moment)['queued'] == 0
            rows = App.NotificationOutbox.query.filter_by(user_id=uid).all()
            assert len(rows) == 1
            assert rows[0].channel == 'email'
            assert rows[0].scheduled_for == datetime(2026, 10, 23, 14, 0)  # 07:00 LA
            user = App.db.session.get(App.User, uid)
            user.email_reminders_opt_in = False
            App.db.session.commit()
            assert notifications.sweep_all(now=moment)['queued'] == 0
        finally:
            App.NotificationOutbox.query.filter_by(user_id=uid).delete(synchronize_session=False)
            App.db.session.commit()


def test_cached_plan_never_caches_notification_policy(cohort, monkeypatch):
    monkeypatch.setattr(notifications, '_fingerprints_supported', lambda: True, raising=False)
    checks = []
    def decision(user, feature):
        checks.append((user.id, feature))
        return False, 'institution_policy'
    with App.app.app_context():
        notifications._plan_for(cohort[0])
        user = App.db.session.get(App.User, cohort[0])
        monkeypatch.setattr(policy, 'decision', decision)
        assert notifications.sweep_user(user) == 0
        assert checks == [(cohort[0], 'notifications')]


def test_sqlite_fallback_remains_fresh_and_owner_scoped(cohort, monkeypatch):
    monkeypatch.setattr(notifications, '_fingerprints_supported', lambda: False, raising=False)
    with App.app.app_context():
        assert notifications._plan_for(cohort[0])['padding'] == 'x' * 50000
        row = App.SavedSchedule.query.filter_by(user_id=cohort[1]).first()
        row.schedule_data = json.dumps({'schedule': [], 'owner': cohort[1]})
        App.db.session.commit()
        assert 'owner' not in notifications._plan_for(cohort[0])
        assert notifications._plan_for(cohort[1])['owner'] == cohort[1]


def test_hash_failure_does_not_reuse_a_previously_cached_plan(cohort, monkeypatch):
    monkeypatch.setattr(notifications, '_fingerprints_supported', lambda: True, raising=False)
    with App.app.app_context():
        assert notifications._plan_for(cohort[0]) is not None
        engine = App.db.engine
        def fail_hash(_conn, _cursor, statement, _params, _context, _many):
            if 'md5(' in statement:
                raise RuntimeError('synthetic fingerprint outage')
        event.listen(engine, 'before_cursor_execute', fail_hash)
        try:
            assert notifications._plan_for(cohort[0]) is None
        finally:
            event.remove(engine, 'before_cursor_execute', fail_hash)
