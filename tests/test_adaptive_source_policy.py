"""Adaptive recommendations obey current source policy, including stored imports."""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from flask_login import login_user

import App
import next_action_glue as adaptive
from intelliplan.learning import policy


@pytest.fixture
def owner():
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'adaptive-{uuid4().hex}@example.test', password_hash='x',
                        birth_year=2000, ai_personalization_opt_in=True)
        school = policy.models()['Organization'](name='Adaptive fixture school', kind='school', policy={})
        App.db.session.add_all([user, school])
        App.db.session.flush()
        App.db.session.add(policy.models()['Membership'](
            user_id=user.id, organization_id=school.id, role='student'))
        App.db.session.commit()
        yield user.id, school.id


def restrict(owner, settings):
    policy.invalidate_cache()
    school = App.db.session.get(policy.models()['Organization'], owner[1])
    school.policy = settings
    App.db.session.commit()


@pytest.mark.parametrize('source', sorted(policy.SOURCE_CAPABILITIES))
def test_restricted_stored_import_is_omitted_but_local_work_remains(owner, monkeypatch, source):
    uid, _ = owner
    for title, import_source in [('Local work', ''), ('Quick Add', 'quickadd'),
                                  ('Tracked course', 'course'), ('Restricted work', source),
                                  ('Unknown import', 'csv')]:
        App.db.session.add(App.ManualTask(user_id=uid, title=title, done=False, import_source=import_source))
    App.db.session.commit()
    restrict(owner, {source: False})
    fetch = Mock(side_effect=AssertionError('mixed LMS fetch must be skipped'))
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', fetch)
    assert {row['title'] for row in adaptive._assignments(uid)} == {'Local work', 'Quick Add', 'Tracked course'}
    fetch.assert_not_called()


def test_restricted_saved_aggregates_are_not_queried_or_overwritten(owner, monkeypatch):
    restrict(owner, {'canvas': False})
    class Trap:
        @property
        def query(self):
            raise AssertionError('restricted saved plan must not be queried')
    monkeypatch.setattr(App, 'SavedSchedule', Trap())
    audit = Mock(side_effect=AssertionError('restricted audit must not be queried'))
    monkeypatch.setattr(adaptive, '_audit', audit)
    assert adaptive._active_schedule(owner[0]) is None
    assert adaptive._save_schedule(owner[0], {'schedule': []}) is False
    assert adaptive._versions_for(owner[0], 5) == []
    adaptive._record_decisions(owner[0], [])
    adaptive._record_override(owner[0])
    adaptive._record_version(owner[0])
    assert adaptive._resolve_decision(owner[0], 'private-task', True) is False
    assert adaptive._reschedule_count(owner[0]) == 0
    assert adaptive._dismissed(owner[0], datetime.now()) == frozenset()
    audit.assert_not_called()


def test_lms_result_discarded_if_permission_changes_during_fetch(owner, monkeypatch):
    def fetch(uid):
        restrict(owner, {'canvas': False})
        return [{'id': 'private', 'title': 'Private fetched task'}]
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', fetch)
    assert adaptive._assignments(owner[0]) == []


def test_saved_plan_discarded_if_permission_changes_during_read(owner, monkeypatch):
    allowed = iter([True, False])
    monkeypatch.setattr(adaptive, '_aggregate_allowed', lambda uid: next(allowed))
    query = Mock()
    query.filter_by.return_value.order_by.return_value.first.return_value = SimpleNamespace(
        schedule_data=json.dumps({'schedule': [], 'overview': 'Private old plan'}))
    monkeypatch.setattr(App, 'SavedSchedule', SimpleNamespace(query=query, created_at=Mock()))
    assert adaptive._active_schedule(owner[0]) is None


def test_response_withheld_when_school_policy_changes_during_computation(owner, monkeypatch):
    from intelliplan.api.next_action import NextActionDeps, create_next_action_blueprint
    from flask import Flask
    application = Flask('adaptive-policy-fixture')
    application.register_blueprint(create_next_action_blueprint(NextActionDeps(
        current_user_id=lambda: owner[0], feature_enabled_for_user=lambda *a: True,
        now=lambda: datetime(2026, 10, 13), get_service=lambda: None,
        access_stamp=Mock(side_effect=['allowed', 'changed']),
        versions_for=lambda *a: [{'private': 'old plan'}],
    )))
    response = application.test_client().get('/api/schedule/versions')
    assert response.status_code == 409
    assert response.json['error'] == 'account_access_changed'
    assert 'old plan' not in response.get_data(as_text=True)


def test_policy_stamp_is_owner_specific_and_fails_for_deleted_owner(owner):
    before = adaptive._access_stamp(owner[0])
    restrict(owner, {'canvas': False})
    assert adaptive._access_stamp(owner[0]) != before
    with pytest.raises(PermissionError):
        adaptive._access_stamp(owner[0] + 1000000)


@pytest.mark.parametrize('change', ['consent', 'canvas'])
def test_narration_never_sends_stale_decision_to_provider(owner, monkeypatch, change):
    from flask import g
    from ai_provider import AIUnavailable
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        if change == 'consent':
            App.db.session.get(App.User, owner[0]).ai_personalization_opt_in = False
            App.db.session.commit()
        else:
            restrict(owner, {'canvas': False})
        provider = Mock(side_effect=AssertionError('stale prompt must not leave the server'))
        monkeypatch.setattr('ai_provider.chat_json', provider)
        with pytest.raises(AIUnavailable):
            adaptive._narration_ai(owner[0])([{'role': 'user', 'content': 'Old private task'}])
        provider.assert_not_called()


def test_narration_binds_actual_owner_and_restores_context(owner, monkeypatch):
    from flask import g
    from intelliplan.learning.courses import provider_user
    def provider(*args, **kwargs):
        assert provider_user.get().id == owner[0]
        assert kwargs['single_attempt'] is True
        return {'why': 'Allowed explanation'}
    monkeypatch.setattr('ai_provider.chat_json', provider)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        previous = provider_user.get()
        assert adaptive._narration_ai(owner[0])([]) == {'why': 'Allowed explanation'}
        assert provider_user.get() is previous


@pytest.mark.parametrize('change', ['consent', 'canvas'])
def test_narration_does_not_retry_or_switch_provider_after_revocation(owner, monkeypatch, change):
    import ai_provider
    from flask import g
    from intelliplan.learning.courses import provider_user
    monkeypatch.setattr(ai_provider, '_linked_steps', lambda tier: [])
    monkeypatch.setattr(ai_provider, '_apply_account_hooks', lambda plan: plan)
    monkeypatch.setattr(ai_provider, 'model_chain', lambda *a: [('gemini', 'first'), ('groq', 'second')])
    calls = []
    def first(*args, **kwargs):
        calls.append(kwargs)
        if change == 'consent':
            App.db.session.get(App.User, owner[0]).ai_personalization_opt_in = False
            App.db.session.commit()
        else:
            restrict(owner, {'canvas': False})
        raise RuntimeError('503 UNAVAILABLE')
    second = Mock(side_effect=AssertionError('revoked context must not reach another provider'))
    monkeypatch.setattr(ai_provider, '_gemini_chat', first)
    monkeypatch.setattr(ai_provider, '_groq_chat', second)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        previous = provider_user.get()
        with pytest.raises(ai_provider.AIUnavailable):
            adaptive._narration_ai(owner[0])([{'role': 'user', 'content': 'Private task'}])
        assert provider_user.get() is previous
    assert len(calls) == 1
    assert calls[0]['allow_config_retry'] is False
    second.assert_not_called()


def test_narration_discards_output_after_revocation_and_restores_context(owner, monkeypatch):
    from flask import g
    from ai_provider import AIUnavailable
    from intelliplan.learning.courses import provider_user
    def provider(*args, **kwargs):
        restrict(owner, {'canvas': False})
        return {'why': 'Old private recommendation'}
    monkeypatch.setattr('ai_provider.chat_json', provider)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        previous = provider_user.get()
        with pytest.raises(AIUnavailable):
            adaptive._narration_ai(owner[0])([])
        assert provider_user.get() is previous


def test_changed_request_cannot_save_or_audit_stale_plan(owner, monkeypatch):
    from flask import g
    row = App.SavedSchedule(user_id=owner[0], name='Existing plan', is_active=True,
                            schedule_data='{"overview":"Keep this"}')
    App.db.session.add(row)
    App.db.session.commit()
    row_id = row.id
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        App.db.session.get(App.User, owner[0]).ai_personalization_opt_in = False
        App.db.session.commit()
        audit = Mock(side_effect=AssertionError('stale audit must not be written'))
        monkeypatch.setattr(adaptive, '_audit', audit)
        assert adaptive._save_schedule(owner[0], {'overview': 'Stale overwrite'}) is False
        adaptive._record_decisions(owner[0], [])
        adaptive._record_version(owner[0])
        audit.assert_not_called()
        assert json.loads(App.db.session.get(App.SavedSchedule, row_id).schedule_data)['overview'] == 'Keep this'


def test_missing_narration_owner_cannot_clear_provider_binding(owner, monkeypatch):
    from flask import g
    from ai_provider import AIUnavailable
    from intelliplan.learning.courses import provider_user
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        monkeypatch.setattr(adaptive, '_aggregate_allowed', lambda uid: True)
        monkeypatch.setattr(adaptive, '_student_profile', lambda uid: ('Student', True))
        query = Mock()
        query.populate_existing.return_value.filter_by.return_value.first.return_value = None
        monkeypatch.setattr(App, 'User', SimpleNamespace(query=query))
        provider = Mock(side_effect=AssertionError('missing owner must not reach provider'))
        monkeypatch.setattr('ai_provider.chat_json', provider)
        previous = provider_user.get()
        with pytest.raises(AIUnavailable):
            adaptive._narration_ai(owner[0])([])
        assert provider_user.get() is previous
        provider.assert_not_called()


def signed_client(owner):
    client = App.app.test_client()
    with client.session_transaction() as session:
        session['_user_id'], session['_fresh'] = str(owner[0]), True
    return client


def test_actual_version_route_omits_restricted_audit_before_query(owner, monkeypatch):
    restrict(owner, {'canvas': False})
    monkeypatch.setattr(App, 'feature_enabled_for_user', lambda *a: True)
    audit = Mock(side_effect=AssertionError('restricted route must not query versions'))
    monkeypatch.setattr(adaptive, '_audit', audit)
    response = signed_client(owner).get('/api/schedule/versions')
    assert response.status_code == 200
    assert response.json['versions'] == []
    audit.assert_not_called()


@pytest.mark.parametrize('change', ['consent', 'canvas'])
def test_actual_explain_route_withholds_changed_decision_before_provider(owner, monkeypatch, change):
    from datetime import timedelta
    from intelliplan.services.next_action import NextActionService
    now = datetime(2026, 10, 13, 18)
    decision = NextActionService(
        get_active_schedule=lambda uid: None,
        get_assignments=lambda uid: [{'id': 'private', 'title': 'Private Canvas quiz',
            'course': 'Math', 'kind': 'test', 'priority': 90, 'est_minutes': 30,
            'due_date': (now.date() + timedelta(days=1)).isoformat()}],
        get_remaining_minutes=lambda *a: 90,
    ).decide(owner[0], now)
    assert decision.headline is not None
    def decide(*args, **kwargs):
        if change == 'canvas':
            restrict(owner, {'canvas': False})
        else:
            App.db.session.get(App.User, owner[0]).ai_personalization_opt_in = False
            App.db.session.commit()
        return decision
    monkeypatch.setattr(App, 'feature_enabled_for_user', lambda *a: True)
    monkeypatch.setattr(NextActionService, 'decide', decide)
    provider = Mock(side_effect=AssertionError('changed decision must not reach provider'))
    monkeypatch.setattr('ai_provider.chat_json', provider)
    response = signed_client(owner).get('/api/next-action/explain')
    assert response.status_code == 409
    assert 'Private Canvas quiz' not in response.get_data(as_text=True)
    provider.assert_not_called()


@pytest.mark.parametrize('field', ['schedule_data', 'progress_json', 'is_active'])
def test_override_cannot_overwrite_concurrent_plan_or_completion_changes(owner, field):
    from flask import g
    row = App.SavedSchedule(user_id=owner[0], name='Plan', is_active=True,
                            schedule_data='{"schedule":[],"overview":"original"}', progress_json='{}')
    App.db.session.add(row)
    App.db.session.commit()
    row_id = row.id
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        assert adaptive._active_schedule(owner[0])['overview'] == 'original'
        current = App.db.session.get(App.SavedSchedule, row_id)
        newer = {'schedule_data': '{"overview":"newer"}', 'progress_json': '{"b1":{"done":true}}',
                 'is_active': False}[field]
        setattr(current, field, newer)
        App.db.session.commit()
        assert adaptive._save_schedule(owner[0], {'overview': 'stale'}) is False
        App.db.session.refresh(current)
        assert getattr(current, field) == newer


def test_unchanged_plan_override_can_save_and_preserves_progress(owner):
    from flask import g
    row = App.SavedSchedule(user_id=owner[0], name='Plan', is_active=True,
                            schedule_data='{"schedule":[],"overview":"original"}', progress_json='{}')
    App.db.session.add(row)
    App.db.session.commit()
    row_id = row.id
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owner[0]))
        g._adaptive_access = (owner[0], adaptive._access_stamp(owner[0]))
        adaptive._active_schedule(owner[0])
        assert adaptive._save_schedule(owner[0], {'overview': 'updated'}) is True
        current = App.db.session.get(App.SavedSchedule, row_id)
        assert json.loads(current.schedule_data)['overview'] == 'updated'
        assert current.progress_json == '{}'


def test_restricted_availability_does_not_read_mixed_history(owner, monkeypatch):
    restrict(owner, {'canvas': False})
    read = Mock(side_effect=AssertionError('restricted history must not be loaded'))
    monkeypatch.setattr(App, 'build_scheduler_personalization', read)
    class Trap:
        @property
        def query(self):
            raise AssertionError('restricted availability must not be loaded')
    monkeypatch.setattr(App, 'UserIdentity', Trap())
    assert adaptive._remaining_minutes(owner[0], datetime(2026, 10, 13, 18)) == 0
    assert adaptive._windows_for(owner[0]) is None
    read.assert_not_called()


@pytest.mark.parametrize('helper', ['remaining', 'windows'])
def test_availability_discards_policy_change_during_preference_read(owner, monkeypatch, helper):
    App.db.session.add(App.UserIdentity(user_id=owner[0], availability='{}'))
    App.db.session.commit()
    def read(identity):
        restrict(owner, {'canvas': False})
        return {}
    monkeypatch.setattr(App.UserIdentity, 'avail_dict', read)
    if helper == 'remaining':
        assert adaptive._remaining_minutes(owner[0], datetime(2026, 10, 13, 18)) == 0
    else:
        assert adaptive._windows_for(owner[0]) is None


def test_retained_availability_callback_rechecks_current_policy(owner, monkeypatch):
    monkeypatch.setattr(App, 'build_scheduler_personalization', lambda **kwargs: ({}, {}, []))
    windows = adaptive._windows_for(owner[0])
    assert callable(windows)
    restrict(owner, {'canvas': False})
    assert windows(datetime(2026, 10, 13).date()) == []


def test_opted_out_behavior_helpers_do_not_query_history(owner, monkeypatch):
    App.db.session.get(App.User, owner[0]).ai_personalization_opt_in = False
    App.db.session.commit()
    class Trap:
        @property
        def query(self):
            raise AssertionError('opted-out history must not be queried')
    monkeypatch.setattr(App, 'ActiveSession', Trap())
    monkeypatch.setattr(App, 'TaskFeedback', Trap())
    audit = Mock(side_effect=AssertionError('opted-out override counts must not be queried'))
    monkeypatch.setattr(adaptive, '_audit', audit)
    assert adaptive._session_rows(owner[0]) == []
    assert adaptive._feedback_rows(owner[0]) == []
    assert adaptive._daily_minutes(owner[0]) == {}
    assert adaptive._reschedule_count(owner[0]) == 0
    audit.assert_not_called()


def test_actual_opted_out_route_keeps_recommendation_without_behavior_reads(owner, monkeypatch):
    from datetime import timedelta
    App.db.session.get(App.User, owner[0]).ai_personalization_opt_in = False
    App.db.session.add(App.ManualTask(user_id=owner[0], title='Local assignment',
                                    due_date=(datetime.now().date() + timedelta(days=1)).isoformat()))
    App.db.session.add(App.UserIdentity(user_id=owner[0], availability=json.dumps({
        day: '08:00-22:00' for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']})))
    App.db.session.commit()
    history = Mock(side_effect=AssertionError('opted-out behavior must not be read'))
    for helper in ('_session_rows', '_feedback_rows', '_daily_minutes', '_reschedule_count'):
        monkeypatch.setattr(adaptive, helper, history)
    monkeypatch.setattr(App, 'build_scheduler_personalization', history)
    from intelliplan.services.next_action import NextActionService
    decide = NextActionService.decide
    fixed = datetime.combine(datetime.now().date(), datetime.min.time()).replace(hour=12)
    monkeypatch.setattr(NextActionService, 'decide',
                        lambda service, uid, now, **kw: decide(service, uid, fixed, **kw))
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [])
    monkeypatch.setattr(App, 'feature_enabled_for_user', lambda *a: True)
    response = signed_client(owner).get('/api/next-action')
    assert response.status_code == 200
    assert response.json['has_recommendation']
    assert response.json['behavior'] is None
    history.assert_not_called()
