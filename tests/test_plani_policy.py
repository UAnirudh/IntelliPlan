"""Plani's internal operations must honor the same boundaries as web routes."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from flask_login import login_user

import App
import plani_agent as plani
from intelliplan.learning import policy


@pytest.fixture
def account():
    previous = App.limiter.enabled
    App.limiter.enabled = False
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'plani-{uuid4().hex}@example.test', password_hash='x',
                        ai_personalization_opt_in=True)
        school = policy.models()['Organization'](name='Plani policy test', kind='school', policy={})
        App.db.session.add_all([user, school])
        App.db.session.flush()
        App.db.session.add(policy.models()['Membership'](
            user_id=user.id, organization_id=school.id, role='student'))
        App.db.session.commit()
        uid, oid = user.id, school.id
    yield uid, oid
    App.limiter.enabled = previous


def restrict(account, settings):
    with App.app.app_context():
        App.db.session.get(policy.models()['Organization'], account[1]).policy = settings
        App.db.session.commit()


def tool(account, name, args=None, target=None):
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, account[0]))
        return plani._execute_tool(name, args or {}, target or account[0])


def add_task(account):
    with App.app.app_context():
        App.db.session.add(App.ManualTask(user_id=account[0], title='Manual algebra',
                                         course='Math', due_date='2026-10-20'))
        App.db.session.commit()


def signed_client(account):
    client = App.app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(account[0])
        session['_fresh'] = True
    return client


@pytest.mark.parametrize(('name', 'key'), [
    ('get_schedule', 'scheduler'), ('generate_schedule', 'scheduler'),
    ('get_today_plan', 'scheduler'), ('get_workload', 'scheduler'),
    ('get_briefing', 'scheduler'), ('explain_priority', 'scheduler'),
    ('track_course', 'study'), ('log_course_progress', 'study'),
    ('list_tracked_courses', 'study'), ('save_note', 'uploads'),
])
def test_internal_tools_check_capability_before_helpers(account, monkeypatch, name, key):
    restrict(account, {key: False})
    provider = Mock(side_effect=AssertionError('provider must not run'))
    monkeypatch.setattr('ai_provider.chat', provider)
    assert tool(account, name)['error'] == 'policy_restricted'
    provider.assert_not_called()


@pytest.mark.parametrize('settings', [{'tutor': False}, {'assessment_protection': True}])
def test_all_tools_recheck_tutor_and_assessment_policy(account, settings):
    restrict(account, settings)
    assert tool(account, 'create_task', {'title': 'Forbidden'})['error'] == 'policy_restricted'
    with App.app.app_context():
        assert App.ManualTask.query.filter_by(user_id=account[0]).count() == 0


def test_tools_cannot_select_other_account_or_run_without_login(account):
    assert tool(account, 'create_task', {'title': 'Forbidden'}, target=account[0] + 10000)['error'] == 'authentication_required'
    with App.app.test_request_context():
        assert plani._execute_tool('navigate_to', {'section': 'settings'}, account[0])['error'] == 'authentication_required'


@pytest.mark.parametrize('key', ['canvas', 'studentvue', 'calendar'])
def test_restricted_sources_skip_fetch_and_cached_aggregate_but_keep_manual_tasks(account, monkeypatch, key):
    add_task(account)
    restrict(account, {key: False})
    collector = Mock(side_effect=AssertionError('restricted source must not run'))
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', collector)
    result = tool(account, 'list_tasks')
    assert [task['title'] for task in result['tasks']] == ['Manual algebra']
    assert tool(account, 'get_grades')['error'] == 'source_restricted'
    assert tool(account, 'get_today_plan')['error'] == 'source_restricted'
    collector.assert_not_called()


def test_no_automatic_context_or_account_reads_without_consent(account, monkeypatch):
    with App.app.app_context():
        App.db.session.get(App.User, account[0]).ai_personalization_opt_in = False
        App.db.session.commit()
    identity = Mock()
    collector = Mock()
    monkeypatch.setattr(App, 'build_student_context', identity)
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', collector)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, account[0]))
        assert plani.build_agent_context(account[0]) == ''
    assert tool(account, 'list_tasks')['error'] == 'personalization_required'
    assert tool(account, 'get_grades')['error'] == 'personalization_required'
    assert tool(account, 'navigate_to', {'section': 'settings'})['navigate'] == '/settings'
    assert tool(account, 'create_task', {'title': 'Explicit task'})['status'] == 'ok'
    identity.assert_not_called()
    collector.assert_not_called()


def test_context_omits_restricted_sections_before_reading_helpers(account, monkeypatch):
    restrict(account, {'scheduler': False, 'canvas': False, 'uploads': False, 'study': False})
    collector, grades, service = Mock(), Mock(), Mock()
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', collector)
    monkeypatch.setattr(App, '_fetch_grades_for_personalization', grades)
    monkeypatch.setattr('command_center_glue._build_service', service)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, account[0]))
        context = plani.build_agent_context(account[0])
    assert '[WORKLOAD]' in context
    for label in ['[TODAY]', '[COURSES & GRADES]', '[STUDY SCHEDULE]', '[MEMORIES]', '[MOMENTUM]']:
        assert label not in context
    collector.assert_not_called()
    grades.assert_not_called()
    service.assert_not_called()


def fixture_generation(monkeypatch, real_planning=False):
    provider = Mock(return_value=json.dumps({'schedule': [], 'overview': 'Fixture plan', 'total_study_time': '1 hour'}))
    monkeypatch.setattr('ai_provider.ai_available', lambda: True)
    monkeypatch.setattr('ai_provider.chat', provider)
    monkeypatch.setattr(plani.ai_firewall, 'guard', lambda *a, **kw: SimpleNamespace(max_output_tokens=8000, plan=None))
    monkeypatch.setattr(plani.ai_firewall, 'record_tokens', lambda *a: None)
    monkeypatch.setattr(App, 'enrich_schedule_data', lambda schedule, *a: schedule)
    if not real_planning:
        monkeypatch.setattr(App, 'humanize_schedule', lambda schedule, *a, **kw: schedule)
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [])
    monkeypatch.setattr(App, '_fetch_grades_for_personalization', lambda: [])
    return provider


def test_local_schedule_survives_calendar_denial_without_token_access(account, monkeypatch):
    add_task(account)
    restrict(account, {'calendar': False})
    provider = fixture_generation(monkeypatch, real_planning=True)
    provider.return_value = json.dumps({'schedule': [{
        'date': '2026-10-20', 'day_name': 'Tuesday', 'blocks': [{
            'assignment': 'Manual algebra', 'course': 'Math', 'duration_minutes': 30,
            'time_slot': '4:30 PM', 'is_break': False}], 'total_hours': 1}],
        'overview': 'Fixture plan', 'total_study_time': '1 hour'})
    google, outlook = Mock(), Mock()
    monkeypatch.setattr(App, 'get_google_token', google)
    monkeypatch.setattr(App, 'get_outlook_token', outlook)
    result = tool(account, 'generate_schedule', {'study_start_time': '16:30', 'study_end_time': '18:30'})
    assert result['status'] == 'ok'
    assert result['calendar_exports'] == {}
    assert result['calendar_connected'] is False
    assert result['calendar_export_restricted'] is True
    assert 'restricted' in plani._humanize_action('generate_schedule', {}, result)
    with App.app.app_context():
        assert App.SavedSchedule.query.filter_by(user_id=account[0], is_active=True).count() == 1
        assert App.DayArchive.query.filter_by(user_id=account[0]).count() == 1
    provider.assert_called_once()
    google.assert_not_called()
    outlook.assert_not_called()


def test_revocation_during_generation_prevents_persistence(account, monkeypatch):
    add_task(account)
    provider = fixture_generation(monkeypatch)
    def revoke(*args, **kwargs):
        restrict(account, {'scheduler': False})
        return json.dumps({'schedule': [], 'overview': 'Fixture'})
    provider.side_effect = revoke
    assert tool(account, 'generate_schedule')['error'] == 'policy_restricted'
    with App.app.app_context():
        assert App.SavedSchedule.query.filter_by(user_id=account[0]).count() == 0
        assert App.DayArchive.query.filter_by(user_id=account[0]).count() == 0


@pytest.mark.parametrize('settings', [{'providers': []}, {'third_party_processing': False}])
def test_schedule_checks_provider_permission_before_generation(account, monkeypatch, settings):
    restrict(account, settings)
    provider = fixture_generation(monkeypatch)
    assert tool(account, 'generate_schedule')['error'] == 'provider_restricted'
    provider.assert_not_called()


@pytest.mark.parametrize(('service', 'key'), [('Canvas', 'canvas'), ('studentvue', 'studentvue'),
                                            ('google calendar', 'calendar'), ('outlook', 'calendar')])
def test_connection_tools_do_not_offer_restricted_services(account, service, key):
    restrict(account, {key: False})
    assert tool(account, 'connect_account', {'service': service})['error'] == 'policy_restricted'


@pytest.mark.parametrize(('consent', 'settings', 'expected'), [(False, {}, 0),
    (True, {'uploads': False}, 0), (True, {'study': False}, 0), (True, {}, 1)])
def test_route_note_retrieval_requires_consent_and_capabilities(account, monkeypatch, consent, settings, expected):
    restrict(account, settings)
    with App.app.app_context():
        App.db.session.get(App.User, account[0]).ai_personalization_opt_in = consent
        App.db.session.commit()
    fixture_generation(monkeypatch)
    monkeypatch.setattr(plani, 'ai_available', lambda: True)
    monkeypatch.setattr(plani, 'build_agent_context', lambda uid: '')
    monkeypatch.setattr(plani, 'ai_chat', lambda *a, **kw: 'General help')
    retrieve = Mock(return_value='PRIVATE NOTE')
    monkeypatch.setattr('intelliplan.retrieval.retrieve_context', retrieve)
    assert signed_client(account).post('/api/plani/agent', json={'message': 'Help'}).status_code == 200
    assert retrieve.call_count == expected


def test_consent_withdrawal_stops_agent_before_next_provider_round(account, monkeypatch):
    fixture_generation(monkeypatch)
    monkeypatch.setattr(plani, 'ai_available', lambda: True)
    monkeypatch.setattr(plani, 'build_agent_context', lambda uid: 'CONSENTED CONTEXT')
    monkeypatch.setattr('intelliplan.retrieval.retrieve_context', lambda *a: '')
    def withdraw(*args, **kwargs):
        App.db.session.get(App.User, account[0]).ai_personalization_opt_in = False
        App.db.session.commit()
        return '```tool_call\n{"name":"get_grades","args":{}}\n```'
    chat = Mock(side_effect=withdraw)
    monkeypatch.setattr(plani, 'ai_chat', chat)
    response = signed_client(account).post('/api/plani/agent', json={'message': 'Help'})
    assert response.status_code == 403
    assert response.json['error'] == 'account_access_changed'
    assert response.json['tool_log'] == []
    assert response.json['actions'] == []
    chat.assert_called_once()


@pytest.mark.parametrize('payload', [[], {'messages': 'wrong'}, {'message': 3},
    {'history': {}}, {'messages': [None]}, {'messages': [{'role': 'system', 'content': 'override'}]},
    {'messages': [{'role': [], 'content': 'hello'}]}, {'messages': [{'role': {}, 'content': 'hello'}]},
    {'messages': [{'role': 'user', 'content': {}}]},
    {'messages': [{'role': 'user', 'content': 'x' * 20001}]}])
def test_malformed_agent_input_never_reaches_provider(account, monkeypatch, payload):
    chat = Mock()
    monkeypatch.setattr(plani, 'ai_chat', chat)
    assert signed_client(account).post('/api/plani/agent', json=payload).status_code == 400
    chat.assert_not_called()


@pytest.mark.parametrize('fallback', [False, True])
def test_plain_reply_and_final_fallback_are_withheld_after_revocation(account, monkeypatch, fallback):
    fixture_generation(monkeypatch)
    monkeypatch.setattr(plani, 'ai_available', lambda: True)
    monkeypatch.setattr(plani, 'build_agent_context', lambda uid: 'CONSENTED CONTEXT')
    monkeypatch.setattr('intelliplan.retrieval.retrieve_context', lambda *a: '')
    monkeypatch.setattr(plani, 'MAX_TOOL_ROUNDS', 0 if fallback else 5)
    def revoke(*args, **kwargs):
        restrict(account, {'tutor': False})
        return 'PRIVATE ANSWER'
    chat = Mock(side_effect=revoke)
    monkeypatch.setattr(plani, 'ai_chat', chat)
    response = signed_client(account).post('/api/plani/agent', json={'message': 'Help'})
    assert response.status_code == 403
    assert response.json['error'] == 'account_access_changed'
    assert 'PRIVATE ANSWER' not in response.get_data(as_text=True)
    chat.assert_called_once()


def test_agent_charges_each_round_and_reports_final_failure_truthfully(account, monkeypatch):
    fixture_generation(monkeypatch)
    monkeypatch.setattr(plani, 'ai_available', lambda: True)
    monkeypatch.setattr(plani, 'build_agent_context', lambda uid: '')
    monkeypatch.setattr('intelliplan.retrieval.retrieve_context', lambda *a: '')
    monkeypatch.setattr(plani, 'MAX_TOOL_ROUNDS', 2)
    guard = Mock(return_value=SimpleNamespace(max_output_tokens=1400, plan=None))
    record = Mock()
    monkeypatch.setattr(plani.ai_firewall, 'guard', guard)
    monkeypatch.setattr(plani.ai_firewall, 'record_tokens', record)
    chat = Mock(side_effect=['```tool_call\n{"name":"navigate_to","args":{"section":"settings"}}\n```'] * 2 + [RuntimeError('offline')])
    monkeypatch.setattr(plani, 'ai_chat', chat)
    response = signed_client(account).post('/api/plani/agent', json={'message': 'Help'})
    assert response.status_code == 502
    assert response.json['error'] == 'ai_unavailable'
    assert 'completed' not in response.json['reply']
    assert guard.call_count == 3
    assert record.call_count == 2


@pytest.mark.parametrize('settings', [{'providers': ['groq']}, {'third_party_processing': False},
    {'assessment_protection': True}, {'uploads': False}, {'study': False}])
def test_dense_retrieval_checks_policy_before_gemini_client(account, monkeypatch, settings):
    from intelliplan.retrieval import embeddings
    restrict(account, settings)
    client = Mock()
    monkeypatch.setattr('ai_provider._gemini_client', client)
    with App.app.app_context():
        assert embeddings.dense_embed(['PRIVATE NOTE'], is_query=False, user_id=account[0]) is None
    client.assert_not_called()


def test_dense_retrieval_requires_consent_and_own_request_identity(account, monkeypatch):
    from intelliplan.retrieval import embeddings
    client = Mock()
    monkeypatch.setattr('ai_provider._gemini_client', client)
    with App.app.app_context():
        App.db.session.get(App.User, account[0]).ai_personalization_opt_in = False
        App.db.session.commit()
        assert embeddings.dense_embed(['PRIVATE NOTE'], is_query=False, user_id=account[0]) is None
        assert embeddings.dense_embed(['PRIVATE NOTE'], is_query=False) is None
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, account[0]))
        assert embeddings.dense_embed(['PRIVATE NOTE'], is_query=False, user_id=account[0] + 1) is None
    client.assert_not_called()


@pytest.mark.parametrize('revoke', [False, True])
def test_allowed_background_dense_retrieval_rechecks_after_call(account, monkeypatch, revoke):
    from intelliplan.retrieval import embeddings
    embed = Mock(return_value=SimpleNamespace(embeddings=[SimpleNamespace(values=[1, 0])]))
    monkeypatch.setattr('ai_provider.gemini_available', lambda: True)
    monkeypatch.setattr('ai_provider._gemini_client', lambda: SimpleNamespace(models=SimpleNamespace(embed_content=embed)))
    if revoke:
        def withdraw(**kwargs):
            App.db.session.get(App.User, account[0]).ai_personalization_opt_in = False
            App.db.session.commit()
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1, 0])])
        embed.side_effect = withdraw
    with App.app.app_context():
        result = embeddings.dense_embed(['PRIVATE NOTE'], is_query=False, user_id=account[0])
    assert (result is None) == revoke
    if not revoke:
        assert result[0].tolist() == [1, 0]
    embed.assert_called_once()


def test_note_search_policy_denial_prevents_index_access(account, monkeypatch):
    restrict(account, {'uploads': False})
    sync = Mock()
    monkeypatch.setattr(App._retrieval, 'sync_user', sync)
    response = signed_client(account).get('/api/notes/search?q=algebra')
    assert response.status_code == 403
    assert response.json['feature'] == 'uploads'
    sync.assert_not_called()


def quiet_secondary_sources(monkeypatch):
    monkeypatch.setattr(App, '_classroom_access_token_for', lambda uid: (None, None))
    monkeypatch.setattr(App, '_blackboard_access_token_for', lambda uid: (None, None))
    monkeypatch.setattr('grade_pulse_glue.observe_assignments', lambda *args: None)


@pytest.mark.parametrize(('source', 'key'), [('canvas', 'canvas'), ('studentvue', 'studentvue'),
                                           ('calendar_feed', 'calendar')])
def test_background_collector_checks_policy_before_cache_or_credentials(account, monkeypatch, source, key):
    quiet_secondary_sources(monkeypatch)
    restrict(account, {key: False})
    credentials = Mock(side_effect=AssertionError('must not open denied credentials'))
    monkeypatch.setattr(App.LinkedAccount, 'get_credentials', credentials)
    with App.app.app_context():
        linked = App.LinkedAccount(user_id=account[0], login_type=source, is_active=True)
        linked.set_credentials({})
        App.db.session.add(linked)
        App.db.session.commit()
        App._lms_cache_put(account[0], [{'title': 'RESTRICTED CACHE', 'source': source}])
        assert App.collect_lms_assignments_for_user(account[0]) == []
        assert App._lms_cache_get(account[0]) is None
    credentials.assert_not_called()


def test_background_collector_omits_rows_when_policy_changes_during_fetch(account, monkeypatch):
    quiet_secondary_sources(monkeypatch)
    with App.app.app_context():
        linked = App.LinkedAccount(user_id=account[0], login_type='studentvue', is_active=True)
        linked.set_credentials({'sv_district_url': 'https://example.test',
                                'sv_username': 'fixture', 'sv_password': 'fixture'})
        App.db.session.add(linked)
        App.db.session.commit()
    def revoke(*args):
        restrict(account, {'studentvue': False})
        return [{'title': 'RESTRICTED FETCH', 'due_date': '2026-10-20'}]
    monkeypatch.setattr(App, 'get_sv_assignments', revoke)
    monkeypatch.setattr(App, 'get_missing_assignments', lambda *a: [])
    with App.app.app_context():
        assert App.collect_lms_assignments_for_user(account[0]) == []
        assert App._lms_cache_get(account[0]) is None


def test_policy_recheck_refreshes_organization_already_loaded_in_session(account):
    with App.app.test_request_context():
        user = App.db.session.get(App.User, account[0])
        organization = App.db.session.get(policy.models()['Organization'], account[1])
        assert policy.decision(user, 'scheduler')[0]
        # Core UPDATE represents a concurrent DB change without synchronizing
        # the already-loaded ORM object. A new decision must read the change.
        App.db.session.execute(policy.models()['Organization'].__table__.update().where(
            policy.models()['Organization'].id == account[1]).values(policy={'scheduler': False}))
        App.db.session.commit()
        assert organization is not None
        policy.invalidate_cache()
        assert not policy.decision(user, 'scheduler')[0]


@pytest.mark.parametrize(('settings', 'consent'), [({'canvas': False}, True),
    ({'studentvue': False}, True), ({'providers': []}, True), ({}, False)])
def test_grade_personalization_guard_precedes_credential_lookup(account, monkeypatch, settings, consent):
    restrict(account, settings)
    lookup = Mock()
    monkeypatch.setattr(App, 'get_grade_account', lookup)
    with App.app.test_request_context():
        user = App.db.session.get(App.User, account[0])
        user.ai_personalization_opt_in = consent
        App.db.session.commit()
        login_user(user)
        assert App._fetch_grades_for_personalization() == []
    lookup.assert_not_called()


@pytest.mark.parametrize(('source', 'key'), [('canvas', 'canvas'), ('studentvue', 'studentvue'),
                                          ('calendar_feed', 'calendar'), ('schoology', 'schoology'), ('hac', 'hac')])
def test_legacy_unified_tasks_route_checks_active_source_policy(account, monkeypatch, source, key):
    restrict(account, {key: False})
    with App.app.app_context():
        linked = App.LinkedAccount(user_id=account[0], login_type=source, is_active=True)
        linked.set_credentials({})
        App.db.session.add(linked)
        App.db.session.commit()
    credentials = Mock(side_effect=AssertionError('denied credential access'))
    monkeypatch.setattr(App.LinkedAccount, 'get_credentials', credentials)
    response = signed_client(account).get('/tasks/unified')
    assert response.status_code == 403
    assert response.json['feature'] == key
    credentials.assert_not_called()


def test_calendar_revocation_ignores_memoized_busy_but_preserves_classes(account, monkeypatch):
    google, outlook = Mock(), Mock()
    monkeypatch.setattr(App, 'get_google_token', google)
    monkeypatch.setattr(App, 'get_outlook_token', outlook)
    classes = {'2026-10-20': [(540, 600)]}
    monkeypatch.setattr(App, '_class_busy_by_date', lambda *a: classes)
    with App.app.test_request_context():
        from flask import g
        login_user(App.db.session.get(App.User, account[0]))
        g._planner_busy_by_date = {14: {'2026-10-20': [(1000, 1100)]}}
        restrict(account, {'calendar': False})
        assert App._planner_busy_by_date() == classes
        assert App._calendar_busy_by_date() == {}
    google.assert_not_called()
    outlook.assert_not_called()


@pytest.mark.parametrize('source', ['canvas', 'studentvue', 'calendar'])
def test_source_revocation_during_nested_generation_prevents_save(account, monkeypatch, source):
    add_task(account)
    provider = fixture_generation(monkeypatch)
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [
        {'title': 'Imported secret task', 'source': source, 'course': 'School'}])
    def revoke(*args, **kwargs):
        assert 'Imported secret task' in args[0][0]['content']
        restrict(account, {source: False})
        return json.dumps({'schedule': [], 'overview': 'Restricted data'})
    provider.side_effect = revoke
    assert tool(account, 'generate_schedule')['error'] == 'account_access_changed'
    with App.app.app_context():
        assert App.SavedSchedule.query.filter_by(user_id=account[0]).count() == 0
        assert App.DayArchive.query.filter_by(user_id=account[0]).count() == 0


def test_profile_extras_select_by_owner_even_when_primary_keys_collide(account, monkeypatch):
    monkeypatch.setattr(App, 'build_student_context', lambda **kwargs: '')
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [])
    monkeypatch.setattr(App, '_fetch_grades_for_personalization', lambda: [])
    monkeypatch.setattr('command_center_glue._build_service', Mock(side_effect=RuntimeError('fixture')))
    with App.app.app_context():
        max_identity = App.db.session.query(App.db.func.max(App.UserIdentity.id)).scalar() or 0
        max_user = App.db.session.query(App.db.func.max(App.User.id)).scalar() or 0
        uid = max(max_identity, max_user) + 1
        owner = App.User(id=uid, email=f'collision-{uuid4().hex}@example.test',
                         password_hash='x', ai_personalization_opt_in=True)
        App.db.session.add(owner)
        App.db.session.flush()
        App.db.session.add_all([
            App.UserIdentity(id=uid, user_id=account[0], completed=False, class_schedule='[{}, {}, {}]'),
            App.UserIdentity(id=uid + 1, user_id=uid, completed=True, class_schedule='[]')])
        App.db.session.commit()
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, uid))
        context = plani.build_agent_context(uid)
    assert 'Onboarding questionnaire is not finished' not in context
    assert '3 slots' not in context


@pytest.mark.parametrize(('source', 'path', 'method'), [
    ('schoology', '/login/schoology', 'get'), ('hac', '/login/hac', 'post'),
    ('google_classroom', '/api/lms/connect/google_classroom', 'post'),
    ('blackboard', '/api/lms/connect/blackboard', 'post'),
    ('moodle', '/api/lms/connect/moodle/manual', 'post'),
    ('powerschool', '/api/lms/connect/powerschool', 'post'),
    ('notion', '/notion/tasks', 'get'), ('notion', '/oauth/notion', 'get'),
    ('google_classroom', '/api/lms/callback/google_classroom', 'get'),
    ('blackboard', '/api/lms/callback/blackboard', 'get'),
])
def test_legacy_connector_routes_use_named_institution_policy(account, monkeypatch, source, path, method):
    restrict(account, {source: False})
    network = Mock(side_effect=AssertionError('denied connector must not call vendor'))
    monkeypatch.setattr(App.requests, 'post', network)
    monkeypatch.setattr(App.requests, 'get', network)
    response = getattr(signed_client(account), method)(path)
    assert response.status_code == 403
    assert response.json['feature'] == source
    network.assert_not_called()


@pytest.mark.parametrize('source', ['schoology', 'hac'])
def test_background_active_legacy_source_cannot_open_credentials_or_cached_data(account, monkeypatch, source):
    quiet_secondary_sources(monkeypatch)
    restrict(account, {source: False})
    credentials = Mock(side_effect=AssertionError('denied credential access'))
    monkeypatch.setattr(App.LinkedAccount, 'get_credentials', credentials)
    with App.app.app_context():
        linked = App.LinkedAccount(user_id=account[0], login_type=source, is_active=True)
        linked.set_credentials({})
        App.db.session.add(linked)
        App.db.session.commit()
        App._lms_cache_put(account[0], [{'title': 'PRIVATE CACHE', 'source': source}])
        assert App.collect_lms_assignments_for_user(account[0]) == []
    credentials.assert_not_called()
    assert tool(account, 'get_grades')['error'] == 'source_restricted'
    assert tool(account, 'connect_account', {'service': source})['error'] == 'policy_restricted'


@pytest.mark.parametrize('source', ['google_classroom', 'blackboard', 'moodle'])
def test_independent_sources_skip_token_fetches_and_revocation_stays_available(account, monkeypatch, source):
    restrict(account, {source: False})
    classroom, blackboard, moodle = Mock(), Mock(), Mock()
    monkeypatch.setattr(App, '_classroom_access_token_for', classroom)
    monkeypatch.setattr(App, '_blackboard_access_token_for', blackboard)
    monkeypatch.setattr(App, '_moodle_fetch_assignments', moodle)
    # Other permitted sources are disconnected, so each returns an empty pair.
    classroom.return_value = (None, None)
    blackboard.return_value = (None, None)
    with App.app.app_context():
        models = {'google_classroom': App.ClassroomIntegration,
                  'blackboard': App.BlackboardIntegration, 'moodle': App.MoodleIntegration}
        model = models[source]
        values = {'user_id': account[0]}
        if source == 'moodle':
            values.update(moodle_url='https://example.test', ws_token='fixture')
        else:
            values.update(access_token='fixture', refresh_token='fixture')
            if source == 'blackboard':
                values['institution_url'] = 'https://example.test'
        App.db.session.add(model(**values))
        App.db.session.commit()
        App._lms_cache_put(account[0], [{'title': 'PRIVATE CACHE', 'source': source}])
        assert App.collect_lms_assignments_for_user(account[0]) == []
    {'google_classroom': classroom, 'blackboard': blackboard, 'moodle': moodle}[source].assert_not_called()
    client = signed_client(account)
    assert client.get('/api/lms/status/' + source).json['connected'] is True
    assert client.post('/api/lms/disconnect/' + source).status_code == 200
    with App.app.app_context():
        assert model.query.filter_by(user_id=account[0]).count() == 0
    with client.session_transaction() as session:
        session.clear()
    assert client.post('/api/lms/disconnect/' + source).status_code == 401


@pytest.mark.parametrize(('source', 'helper', 'model'), [
    ('google_classroom', '_classroom_access_token_for', App.ClassroomIntegration),
    ('blackboard', '_blackboard_access_token_for', App.BlackboardIntegration)])
def test_real_token_helpers_fail_before_refresh_for_denied_or_foreign_users(account, monkeypatch, source, helper, model):
    restrict(account, {source: False})
    refresh = Mock(side_effect=AssertionError('must not refresh denied credentials'))
    monkeypatch.setattr(App, '_classroom_refresh_token' if source == 'google_classroom' else '_blackboard_refresh_token', refresh)
    with App.app.app_context():
        row = model(user_id=account[0], access_token='fixture', refresh_token='fixture')
        if source == 'blackboard':
            row.institution_url = 'https://example.test'
        App.db.session.add(row)
        App.db.session.commit()
        assert getattr(App, helper)(account[0]) == (None, None)
    with App.app.test_request_context():
        assert getattr(App, helper)(account[0]) == (None, None)
        login_user(App.db.session.get(App.User, account[0]))
        assert getattr(App, helper)(account[0] + 1) == (None, None)
    refresh.assert_not_called()


@pytest.mark.parametrize('source', ['schoology', 'hac', 'google_classroom', 'blackboard', 'moodle', 'notion', 'powerschool'])
def test_new_source_restrictions_participate_in_parent_intersection(account, source):
    with App.app.app_context():
        school = App.db.session.get(policy.models()['Organization'], account[1])
        district = policy.models()['Organization'](name='Fixture district', kind='district', policy={source: False})
        App.db.session.add(district)
        App.db.session.flush()
        school.parent_id = district.id
        school.policy = {source: True}
        App.db.session.commit()
        policy.invalidate_cache()
        assert not policy.decision(App.db.session.get(App.User, account[0]), source)[0]


@pytest.mark.parametrize(('source', 'helper', 'model', 'refresh_helper'), [
    ('google_classroom', '_classroom_access_token_for', App.ClassroomIntegration, '_classroom_refresh_token'),
    ('blackboard', '_blackboard_access_token_for', App.BlackboardIntegration, '_blackboard_refresh_token'),
    ('notion', '_refresh_notion_row', App.NotionIntegration, 'refresh_notion_token')])
@pytest.mark.parametrize('revoked', [False, True])
def test_source_refresh_rechecks_before_persisting_tokens(account, monkeypatch, source, helper, model, refresh_helper, revoked):
    def refresh(*args):
        if revoked:
            restrict(account, {source: False})
        return {'access_token': 'new-fixture', 'expires_in': 3600}
    monkeypatch.setattr(App, refresh_helper, refresh)
    with App.app.app_context():
        values = {'user_id': account[0], 'refresh_token': 'fixture'}
        values['token_expires_at'] = App.utcnow() - App.timedelta(minutes=5)
        values['token' if source == 'notion' else 'access_token'] = 'old-fixture'
        if source == 'notion':
            values['database_id'] = 'fixture'
        if source == 'blackboard':
            values['institution_url'] = 'https://example.test'
        row = model(**values)
        App.db.session.add(row)
        App.db.session.commit()
        result = getattr(App, helper)(row if source == 'notion' else account[0])
        if revoked:
            assert result == (None if source == 'notion' else (None, None))
        else:
            assert result == ('new-fixture' if source == 'notion' else ('new-fixture', row))
        App.db.session.expire_all()
        saved = model.query.filter_by(user_id=account[0]).one()
        assert getattr(saved, 'token' if source == 'notion' else 'access_token') == (
            'old-fixture' if revoked else 'new-fixture')


def test_hac_revocation_filters_regular_and_missing_assignment_tags(account, monkeypatch):
    quiet_secondary_sources(monkeypatch)
    with App.app.app_context():
        linked = App.LinkedAccount(user_id=account[0], login_type='hac', is_active=True)
        linked.set_credentials({})
        App.db.session.add(linked)
        App.db.session.commit()
    def revoke(*args, **kwargs):
        restrict(account, {'hac': False})
        return [{'title': 'PRIVATE REGULAR', 'source': 'hac'},
                {'title': 'PRIVATE MISSING', 'source': 'hac_missing'}]
    monkeypatch.setattr(App, '_hac_tasks', revoke)
    observed = Mock()
    monkeypatch.setattr('grade_pulse_glue.observe_assignments', observed)
    with App.app.app_context():
        assert App.collect_lms_assignments_for_user(account[0]) == []
    assert policy.source_capability('hac_missing') == 'hac'
    assert observed.call_args.args[1] == []


def test_denied_notion_sync_preserves_local_task_creation_and_update(account, monkeypatch):
    restrict(account, {'notion': False})
    monkeypatch.setattr(App, 'NOTION_AVAILABLE', True)
    create, update, refresh = Mock(), Mock(), Mock()
    monkeypatch.setattr(App, 'create_notion_task', create)
    monkeypatch.setattr(App, 'update_notion_task', update)
    monkeypatch.setattr(App, 'refresh_notion_token', refresh)
    with App.app.app_context():
        App.db.session.add(App.NotionIntegration(user_id=account[0], token='fixture',
                                                database_id='fixture', refresh_token='fixture'))
        App.db.session.commit()
    client = signed_client(account)
    created = client.post('/tasks/manual/create', json={'title': 'Local task', 'sync_notion': True})
    assert created.status_code == 200
    tid = created.json['id']
    with App.app.app_context():
        App.db.session.get(App.ManualTask, tid).notion_page_id = 'fixture-page'
        App.db.session.commit()
    assert client.post('/tasks/manual/update', json={'id': tid, 'title': 'Changed locally'}).status_code == 200
    with App.app.app_context():
        assert App.db.session.get(App.ManualTask, tid).title == 'Changed locally'
    create.assert_not_called()
    update.assert_not_called()
    refresh.assert_not_called()


@pytest.mark.parametrize('source', ['google_classroom', 'blackboard', 'moodle', 'powerschool'])
def test_generic_sync_route_checks_named_source_before_provider(account, monkeypatch, source):
    restrict(account, {source: False})
    with App.app.app_context():
        App.db.session.add(App.LMSToken(user_id=account[0], provider=source, tokens_json='{"access_token":"fixture"}'))
        App.db.session.commit()
    provider = Mock()
    monkeypatch.setattr('intelliplan.api.lms_sync.get_provider', provider)
    client = signed_client(account)
    response = client.post(f'/api/lms/{source}/sync')
    assert response.status_code == 403
    assert response.json['feature'] == source
    provider.assert_not_called()
    assert client.post(f'/api/lms/{source}/disconnect').status_code == 200
    with App.app.app_context():
        assert App.LMSToken.query.filter_by(user_id=account[0], provider=source).count() == 0


@pytest.mark.parametrize('revoke_stage', ['refresh', 'sync', 'none'])
def test_generic_sync_rechecks_after_vendor_operations_before_persisting(account, monkeypatch, revoke_stage):
    source = 'google_classroom'
    with App.app.app_context():
        App.db.session.add(App.LMSToken(user_id=account[0], provider=source, tokens_json='{"access_token":"old-fixture"}'))
        App.db.session.commit()
    provider = Mock()
    def refresh(**kwargs):
        if revoke_stage == 'refresh':
            restrict(account, {source: False})
        return {'access_token': 'new-fixture'}
    def sync(**kwargs):
        if revoke_stage == 'sync':
            restrict(account, {source: False})
        return [object(), object()]
    provider.refresh_tokens.side_effect = refresh
    provider.sync_all.side_effect = sync
    monkeypatch.setattr('intelliplan.api.lms_sync.get_provider', lambda key: provider)
    response = signed_client(account).post(f'/api/lms/{source}/sync')
    assert response.status_code == (200 if revoke_stage == 'none' else 403)
    with App.app.app_context():
        row = App.LMSToken.query.filter_by(user_id=account[0], provider=source).first()
        assert row.last_sync_count == (2 if revoke_stage == 'none' else 0)
        assert json.loads(row.tokens_json)['access_token'] == ('new-fixture' if revoke_stage == 'none' else 'old-fixture')
    if revoke_stage == 'refresh':
        provider.sync_all.assert_not_called()


@pytest.mark.parametrize('source', ['google_classroom', 'blackboard', 'moodle', 'notion'])
def test_unified_fetch_filters_revoked_sources_before_grade_pulse_and_response(account, monkeypatch, source):
    from datetime import timedelta
    add_task(account)
    with App.app.app_context():
        App.db.session.get(App.User, account[0]).lms_preferences = json.dumps({'assignment_sources': [source, 'manual']})
        App.db.session.commit()
    monkeypatch.setattr(App, 'get_active_account', lambda: None)
    observed = Mock()
    monkeypatch.setattr('grade_pulse_glue.observe_assignments', observed)
    def revoke(*args, **kwargs):
        restrict(account, {source: False})
        # The caller must label provenance itself; providers cannot bypass it.
        return [{'title': 'PRIVATE SOURCE TASK', 'source': 'manual', 'due_date': '2026-10-20'}]
    with App.app.app_context():
        if source == 'google_classroom':
            App.db.session.add(App.ClassroomIntegration(user_id=account[0], access_token='fixture',
                token_expires_at=App.utcnow() + timedelta(hours=1)))
            monkeypatch.setattr(App, '_classroom_fetch_assignments', revoke)
        elif source == 'blackboard':
            App.db.session.add(App.BlackboardIntegration(user_id=account[0], access_token='fixture',
                institution_url='https://example.test', token_expires_at=App.utcnow() + timedelta(hours=1)))
            monkeypatch.setattr(App, '_blackboard_fetch_assignments', revoke)
        elif source == 'moodle':
            App.db.session.add(App.MoodleIntegration(user_id=account[0], moodle_url='https://example.test', ws_token='fixture'))
            monkeypatch.setattr(App, '_moodle_fetch_assignments', revoke)
        else:
            monkeypatch.setattr(App, 'NOTION_AVAILABLE', True)
            App.db.session.add(App.NotionIntegration(user_id=account[0], token='fixture', database_id='fixture'))
            monkeypatch.setattr(App, 'get_notion_tasks', revoke)
        App.db.session.commit()
    response = signed_client(account).get('/tasks/unified')
    assert response.status_code == 200
    assert 'PRIVATE SOURCE TASK' not in response.get_data(as_text=True)
    assert 'Manual algebra' in response.get_data(as_text=True)
    assert [task['title'] for task in observed.call_args.args[1]] == ['Manual algebra']
