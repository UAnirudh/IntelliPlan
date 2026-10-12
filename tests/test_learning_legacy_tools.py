"""Legacy learning tools must use current institution and source permissions."""
from unittest.mock import Mock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import request
from flask_login import login_user
from sqlalchemy import update

import App
import breakdown_glue as breakdown
import extra_features
from intelliplan.learning import policy


@pytest.fixture
def account():
    previous = App.limiter.enabled
    App.limiter.enabled = False
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'legacy-tools-{uuid4().hex}@example.test', password_hash='x',
                        birth_year=2000, ai_personalization_opt_in=True)
        school = policy.models()['Organization'](name='Legacy tool fixture', kind='school', policy={})
        App.db.session.add_all([user, school])
        App.db.session.flush()
        App.db.session.add(policy.models()['Membership'](user_id=user.id, organization_id=school.id, role='student'))
        App.db.session.commit()
        uid, oid = user.id, school.id
    client = App.app.test_client()
    with client.session_transaction() as session:
        session['_user_id'], session['_fresh'] = str(uid), True
    try:
        yield uid, oid, client
    finally:
        with App.app.app_context():
            App.db.session.rollback()
            App.AssignmentStep.query.filter_by(user_id=uid).delete()
            policy.models()['Membership'].query.filter_by(user_id=uid).delete()
            policy.models()['Organization'].query.filter_by(id=oid).delete()
            App.User.query.filter_by(id=uid).delete()
            App.db.session.commit()
        App.limiter.enabled = previous


def restrict(account, settings):
    with App.app.app_context():
        App.db.session.get(policy.models()['Organization'], account[1]).policy = settings
        App.db.session.commit()


@pytest.mark.parametrize('settings', [
    {'scheduler': False}, {'third_party_processing': False}, {'providers': []},
    {'assessment_protection': True},
    {'canvas': False}, {'studentvue': False},
])
def test_background_briefing_uses_target_school_policy(account, monkeypatch, settings):
    import command_center_glue as glue
    import ai_provider

    restrict(account, settings)
    provider = Mock(side_effect=AssertionError('No background provider access allowed'))
    monkeypatch.setattr(ai_provider, 'chat_json', provider)
    with App.app.app_context():
        briefing = glue._build_service()._briefing
        result = briefing.get_or_refresh(account[0], {'top_tasks': [{'title': 'Local task'}]}, force=True)
        assert result.generated_by == 'fallback-template'
        assert briefing.peek(account[0]) is None
    provider.assert_not_called()


def test_background_briefing_binds_target_owner_and_resets_context(account, monkeypatch):
    import command_center_glue as glue
    import ai_provider
    from intelliplan.learning.courses import provider_user

    def provider(messages, **kwargs):
        assert provider_user.get().id == account[0]
        raise RuntimeError('Synthetic provider failure')

    monkeypatch.setattr(ai_provider, 'chat_json', provider)
    with App.app.app_context():
        previous = provider_user.get()
        with pytest.raises(RuntimeError, match='Synthetic provider failure'):
            glue._briefing_ai(account[0], [])
        assert provider_user.get() is previous


def test_background_briefing_reloads_withdrawn_consent(account, monkeypatch):
    import command_center_glue as glue
    import ai_provider

    provider = Mock(side_effect=AssertionError('Withdrawn consent reached provider'))
    monkeypatch.setattr(ai_provider, 'chat_json', provider)
    with App.app.app_context():
        loaded = App.db.session.get(App.User, account[0])
        App.db.session.execute(update(App.User.__table__).where(App.User.id == account[0])
                               .values(ai_personalization_opt_in=False))
        assert loaded.ai_personalization_opt_in is True
        with pytest.raises(RuntimeError, match='restricted'):
            glue._briefing_ai(account[0], [])
    provider.assert_not_called()


def test_background_briefing_missing_owner_cannot_clear_binding(monkeypatch):
    import command_center_glue as glue
    import ai_provider
    from intelliplan.learning.courses import provider_user

    query = Mock()
    query.populate_existing.return_value.filter_by.return_value.first.return_value = None
    monkeypatch.setattr(App, 'User', SimpleNamespace(query=query))
    provider = Mock()
    monkeypatch.setattr(ai_provider, 'chat_json', provider)
    sentinel = object()
    token = provider_user.set(sentinel)
    try:
        with pytest.raises(RuntimeError, match='restricted'):
            glue._briefing_ai(999999999, [])
        assert provider_user.get() is sentinel
    finally:
        provider_user.reset(token)
    provider.assert_not_called()


@pytest.mark.parametrize('source', sorted(policy.LMS_SOURCES))
def test_command_center_does_not_read_mixed_grades_when_source_denied(account, monkeypatch, source):
    import command_center_glue as glue

    restrict(account, {source: False})
    query = Mock(side_effect=AssertionError('Restricted grade table must not be read'))
    monkeypatch.setattr(App, 'ImportedGrade', SimpleNamespace(query=query))
    with App.app.app_context():
        assert glue._get_grades(account[0]) == ()
        assert glue._planner_allowed(account[0]) is True
    query.filter_by.assert_not_called()


def test_command_center_discards_grades_after_source_revocation(account, monkeypatch):
    import command_center_glue as glue

    checks = Mock(side_effect=[True, False])
    query = Mock()
    query.filter_by.return_value.all.return_value = [SimpleNamespace(course='Restricted', percentage=20)]
    monkeypatch.setattr(App, 'ImportedGrade', SimpleNamespace(query=query))
    monkeypatch.setattr(glue, '_grade_sources_allowed', checks)
    with App.app.app_context():
        assert glue._get_grades(account[0]) == ()
    query.filter_by.assert_called_once_with(user_id=account[0])
    assert checks.call_count == 2


def test_command_center_keeps_allowed_owner_grades(account, monkeypatch):
    import command_center_glue as glue

    query = Mock()
    query.filter_by.return_value.all.return_value = [SimpleNamespace(course='Allowed', percentage=87)]
    monkeypatch.setattr(App, 'ImportedGrade', SimpleNamespace(query=query))
    with App.app.app_context():
        grades = glue._get_grades(account[0])
        assert [(grade.course, grade.current_pct) for grade in grades] == [('Allowed', 87)]
    query.filter_by.assert_called_once_with(user_id=account[0])


def test_command_center_policy_stamp_tracks_consent_and_source_changes(account):
    import command_center_glue as glue

    with App.app.app_context():
        original = glue._today_policy_stamp(account[0])
    restrict(account, {'canvas': False})
    with App.app.app_context():
        source_changed = glue._today_policy_stamp(account[0])
        assert source_changed != original
        user = App.db.session.get(App.User, account[0])
        user.ai_personalization_opt_in = False
        App.db.session.commit()
        assert glue._today_policy_stamp(account[0]) != source_changed
    restrict(account, {'scheduler': False})
    with App.app.app_context():
        assert glue._planner_allowed(account[0]) is False
        with pytest.raises(PermissionError):
            glue._today_policy_stamp(account[0])


def test_stored_task_filter_preserves_local_and_allowed_sources(account):
    import command_center_glue as glue

    rows = [SimpleNamespace(import_source=source) for source in
            ['', 'scraper:canvas', 'scraper:studentvue', 'csv', 'scraper:unknown', 'quickadd', 'course']]
    with App.app.app_context():
        assert glue._filter_stored_tasks(account[0], rows) == rows
    restrict(account, {'canvas': False})
    with App.app.app_context():
        assert glue._filter_stored_tasks(account[0], rows) == [rows[0], rows[2], rows[5], rows[6]]


def test_real_today_cache_rebuilds_after_consent_withdrawal(account, monkeypatch):
    from intelliplan.api import command_center as cc
    from intelliplan.services.today import TodayService

    builds = []

    def build(service, uid, **kwargs):
        assert uid == account[0]
        builds.append(uid)
        return {'generation': len(builds)}

    monkeypatch.setattr(TodayService, 'build', build)
    monkeypatch.setattr(cc, 'today_to_dict', lambda payload: payload)
    cc.invalidate_today(account[0])
    try:
        assert account[2].get('/api/today').get_json() == {'generation': 1}
        assert account[2].get('/api/today').get_json() == {'generation': 1}
        assert len(builds) == 1
        with App.app.app_context():
            user = App.db.session.get(App.User, account[0])
            user.ai_personalization_opt_in = False
            App.db.session.commit()
        response = account[2].get('/api/today')
        assert response.status_code == 200
        assert response.get_json() == {'generation': 2}
        assert len(builds) == 2
    finally:
        cc.invalidate_today(account[0])




def trap_dissector(monkeypatch):
    provider = Mock(side_effect=AssertionError('Provider must not receive the text.'))
    monkeypatch.setattr(extra_features, 'ai_available', lambda: True)
    monkeypatch.setattr(extra_features, 'ai_chat', provider)
    return provider


def test_text_dissector_requires_identity_before_provider(account, monkeypatch):
    provider = trap_dissector(monkeypatch)
    response = App.app.test_client().post('/api/text-dissector', json={'text': 'Synthetic text'})
    assert response.status_code == 401
    provider.assert_not_called()


@pytest.mark.parametrize('settings', [{'tutor': False}, {'assessment_protection': True}])
def test_text_dissector_honors_tutor_and_assessment_policy(account, monkeypatch, settings):
    restrict(account, settings)
    provider = trap_dissector(monkeypatch)
    assert account[2].post('/api/text-dissector', json={'text': 'Synthetic assessed text'}).status_code == 403
    provider.assert_not_called()


def test_allowed_text_dissector_and_nonobject_model_fallback(account, monkeypatch):
    monkeypatch.setattr(extra_features, 'ai_available', lambda: True)
    provider = Mock(return_value='{"summary":"Fixture summary"}')
    monkeypatch.setattr(extra_features, 'ai_chat', provider)
    response = account[2].post('/api/text-dissector', json={'text': 'Synthetic text'})
    assert response.status_code == 200 and response.json['analysis']['summary'] == 'Fixture summary'
    provider.return_value = '[]'
    response = account[2].post('/api/text-dissector', json={'text': 'Synthetic text'})
    assert response.status_code == 200 and response.json['analysis']['_fallback'] is True


@pytest.mark.parametrize('payload', [[], {'text': []}, {'text': 123}])
def test_text_dissector_rejects_bad_input_before_provider(account, monkeypatch, payload):
    provider = trap_dissector(monkeypatch)
    assert account[2].post('/api/text-dissector', json=payload).status_code == 400
    provider.assert_not_called()


@pytest.mark.parametrize('settings', [
    {'tutor': False}, {'assessment_protection': True},
    {'third_party_processing': False}, {'providers': []},
])
def test_breakdown_retains_deterministic_steps_under_ai_denial(account, monkeypatch, settings):
    restrict(account, settings)
    monkeypatch.setattr('ai_provider.ai_available', lambda: True)
    provider = Mock(side_effect=AssertionError('AI breakdown must not run.'))
    monkeypatch.setattr(breakdown, '_ai_breakdown', provider)
    response = account[2].post('/api/breakdown', json={
        'title': 'Synthetic paper', 'description': 'Read the article. Write a summary.', 'schedule': False})
    assert response.status_code == 200
    assert response.json['method'] != 'ai' and response.json['steps']
    provider.assert_not_called()


def test_breakdown_ai_helper_rechecks_own_permissions(account, monkeypatch):
    restrict(account, {'tutor': False})
    monkeypatch.setattr('ai_provider.ai_available', lambda: True)
    provider = Mock(side_effect=AssertionError('Provider must not run.'))
    monkeypatch.setattr('ai_provider.chat_json', provider)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, account[0]))
        with pytest.raises(PermissionError):
            breakdown._ai_breakdown('Synthetic', '', {'description': 'Read.'}, 30, 2)
    provider.assert_not_called()


def test_breakdown_ai_availability_requires_actual_user_consent(account, monkeypatch):
    monkeypatch.setattr('ai_provider.ai_available', lambda: True)
    with App.app.test_request_context():
        loaded = App.db.session.get(App.User, account[0])
        login_user(loaded)
        session = App.db.session()
        previous_expiration = session.expire_on_commit
        session.expire_on_commit = False
        try:
            assert breakdown._ai_allowed() is True
            session.execute(update(App.User.__table__).where(App.User.id == account[0]).values(
                ai_personalization_opt_in=False))
            session.commit()
            assert loaded.ai_personalization_opt_in is True
            assert breakdown._ai_allowed() is False
        finally:
            session.expire_on_commit = previous_expiration


def test_breakdown_canvas_denial_precedes_credentials(account, monkeypatch):
    restrict(account, {'canvas': False})
    credentials = Mock(side_effect=AssertionError('Denied credentials must not be read.'))
    monkeypatch.setattr(App, 'get_active_account', credentials)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, account[0]))
        assert breakdown._canvas_context({'id': '1', 'course_id': '2'}) is None
    credentials.assert_not_called()


def test_breakdown_discards_canvas_context_after_revocation(account, monkeypatch):
    monkeypatch.setattr(App, 'get_active_account', lambda: {'login_type': 'canvas',
        'canvas_url': 'https://canvas.example.test', 'canvas_token': 'fixture'})
    def fetch(*args):
        App.db.session.get(policy.models()['Organization'], account[1]).policy = {'canvas': False}
        App.db.session.commit()
        return {'description': 'Must not be published', 'materials': []}
    monkeypatch.setattr('assignment_materials.load_assignment', fetch)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, account[0]))
        assert breakdown._canvas_context({'id': '1', 'course_id': '2'}) is None


def test_breakdown_keeps_local_steps_without_restricted_scheduling(account, monkeypatch):
    restrict(account, {'scheduler': False})
    monkeypatch.setattr('ai_provider.ai_available', lambda: False)
    plan = Mock(side_effect=AssertionError('Restricted plan must not be read or changed.'))
    monkeypatch.setattr('plan_insert_glue.active_plan_row', plan)
    response = account[2].post('/api/breakdown', json={
        'title': 'Synthetic paper', 'description': 'Read the article. Write a summary.'})
    assert response.status_code == 200 and response.json['steps']
    assert response.json['placement']['status'] == 'denied'
    plan.assert_not_called()


def test_breakdown_timer_start_honors_study_restriction(account):
    restrict(account, {'study': False})
    assert account[2].post('/api/breakdown/start', json={}).status_code == 403


def test_breakdown_step_updates_do_not_sync_restricted_plan(account, monkeypatch):
    restrict(account, {'scheduler': False})
    monkeypatch.setattr('ai_provider.ai_available', lambda: False)
    response = account[2].post('/api/breakdown', json={'title': 'Synthetic paper',
        'description': 'Read the article. Write a summary.', 'schedule': False})
    sid = response.json['steps'][0]['id']
    plan = Mock(side_effect=AssertionError('Restricted plan must not be accessed.'))
    monkeypatch.setattr('plan_insert_glue.active_plan_row', plan)
    for done in (True, False):
        assert account[2].patch(f'/api/breakdown/steps/{sid}', json={'done': done}).status_code == 200
        with App.app.app_context():
            assert App.db.session.get(App.AssignmentStep, sid).done is done
    plan.assert_not_called()


def test_breakdown_scheduler_helpers_check_owner_without_login_context(account, monkeypatch):
    restrict(account, {'scheduler': False})
    plan = Mock(side_effect=AssertionError('Restricted owner plan must not be accessed.'))
    monkeypatch.setattr('plan_insert_glue.active_plan_row', plan)
    with App.app.test_request_context():
        assert breakdown._schedule_steps(account[0], None, 'Synthetic', '', '', [], None)['status'] == 'denied'
        breakdown._sync_plan_progress(account[0], None, 'Synthetic')
    plan.assert_not_called()


@pytest.mark.parametrize('settings', [{'tutor': False}, {'assessment_protection': True},
    {'third_party_processing': False}, {'providers': ['groq']}])
def test_text_dissector_discards_output_after_inflight_revocation(account, monkeypatch, settings):
    monkeypatch.setattr(extra_features, 'ai_available', lambda: True)
    def respond(*args, **kwargs):
        App.db.session.get(policy.models()['Organization'], account[1]).policy = settings
        App.db.session.commit()
        return '{"summary":"Must not be published"}'
    monkeypatch.setattr(extra_features, 'ai_chat', respond)
    response = account[2].post('/api/text-dissector', json={'text': 'Synthetic input'})
    assert response.status_code == 403
    assert 'analysis' not in response.json and 'Must not be published' not in response.get_data(as_text=True)


@pytest.mark.parametrize('value', [
    '{"summary":1}', '{"key_points":"x"}', '{"questions":[null]}',
    '{"vocabulary":[null]}', '{"vocabulary":[{"term":4,"meaning":"x"}]}',
])
def test_text_dissector_typed_renderer_fields_fall_back_safely(account, monkeypatch, value):
    monkeypatch.setattr(extra_features, 'ai_available', lambda: True)
    monkeypatch.setattr(extra_features, 'ai_chat', lambda *args, **kwargs: value)
    response = account[2].post('/api/text-dissector', json={'text': 'Synthetic input'})
    assert response.status_code == 200 and response.json['analysis']['_fallback'] is True


def test_text_dissector_skips_provider_when_processing_is_denied(account, monkeypatch):
    restrict(account, {'third_party_processing': False})
    provider = trap_dissector(monkeypatch)
    response = account[2].post('/api/text-dissector', json={'text': 'Synthetic input'})
    assert response.status_code == 200 and response.json['analysis']['_fallback'] is True
    provider.assert_not_called()


def test_breakdown_discards_canvas_fallback_after_model_revocation(account, monkeypatch):
    monkeypatch.setattr('ai_provider.ai_available', lambda: True)
    monkeypatch.setattr(App, 'get_active_account', lambda: {'login_type': 'canvas',
        'canvas_url': 'https://canvas.example.test', 'canvas_token': 'fixture'})
    monkeypatch.setattr('assignment_materials.load_assignment', lambda *args: {
        'description': 'Write the REVOKED_CANVAS_MARKER in your report. Submit the report.', 'materials': []})
    monkeypatch.setattr('ai_firewall.guard', lambda *args, **kwargs: SimpleNamespace(max_output_tokens=900))
    monkeypatch.setattr('ai_firewall.record_tokens', Mock())
    def respond(*args, **kwargs):
        App.db.session.get(policy.models()['Organization'], account[1]).policy = {'canvas': False}
        App.db.session.commit()
        return {'steps': []}
    provider = Mock(side_effect=respond)
    monkeypatch.setattr('ai_provider.chat_json', provider)
    response = account[2].post('/api/breakdown', json={'title': 'Synthetic paper',
        'description': 'Read the LOCAL_ARTICLE carefully. Write a summary paragraph.',
        'id': '1', 'course_id': '2', 'schedule': False})
    assert response.status_code == 200 and 'REVOKED_CANVAS_MARKER' not in response.get_data(as_text=True)
    assert 'LOCAL_ARTICLE' in response.get_data(as_text=True)
    provider.assert_called_once()
    with App.app.app_context():
        saved = App.AssignmentStep.query.filter_by(user_id=account[0]).all()
        assert saved and all('REVOKED_CANVAS_MARKER' not in row.text + (row.evidence or '') for row in saved)


@pytest.mark.parametrize('change', ['personalization', 'providers'])
def test_breakdown_helper_discards_ai_after_permissions_change(account, monkeypatch, change):
    monkeypatch.setattr('ai_provider.ai_available', lambda: True)
    monkeypatch.setattr('ai_firewall.guard', lambda *args, **kwargs: SimpleNamespace(max_output_tokens=900))
    monkeypatch.setattr('ai_firewall.record_tokens', Mock())
    def respond(*args, **kwargs):
        if change == 'personalization':
            App.db.session.execute(update(App.User.__table__).where(App.User.id == account[0]).values(
                ai_personalization_opt_in=False))
        else:
            App.db.session.get(policy.models()['Organization'], account[1]).policy = {'providers': ['groq']}
        App.db.session.commit()
        if change == 'personalization':
            assert loaded.ai_personalization_opt_in is True
        return {'steps': []}
    provider = Mock(side_effect=respond)
    monkeypatch.setattr('ai_provider.chat_json', provider)
    with App.app.test_request_context():
        loaded = App.db.session.get(App.User, account[0])
        login_user(loaded)
        session = App.db.session()
        previous_expiration = session.expire_on_commit
        session.expire_on_commit = False
        try:
            with pytest.raises(PermissionError):
                breakdown._ai_breakdown('Synthetic', '', {'description': 'Read.'}, 30, 2)
        finally:
            session.expire_on_commit = previous_expiration
    provider.assert_called_once()


@pytest.mark.parametrize(('method', 'path'), [
    ('GET', '/api/today'), ('POST', '/api/today/refresh'),
    ('GET', '/api/today/explain?component=health'),
    ('GET', '/api/next-action'), ('GET', '/api/next-action/explain'),
    ('POST', '/api/next-action/respond'),
    ('POST', '/api/schedule/override'), ('POST', '/api/schedule/override/apply'),
    ('GET', '/api/schedule/feasibility'), ('GET', '/api/schedule/versions'),
])
def test_planner_families_deny_before_any_view_or_cache(account, monkeypatch, method, path):
    restrict(account, {'scheduler': False})
    with App.app.test_request_context(path, method=method):
        endpoint = request.endpoint
    assert endpoint is not None
    view = Mock(side_effect=AssertionError('Restricted planner view/cache must not be reached.'))
    monkeypatch.setitem(App.app.view_functions, endpoint, view)
    response = account[2].open(path, method=method, json={} if method == 'POST' else None)
    assert response.status_code == 403 and response.json['feature'] == 'scheduler'
    view.assert_not_called()
