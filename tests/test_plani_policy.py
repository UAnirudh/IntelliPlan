"""Plani's internal operations must honor the same boundaries as web routes."""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from flask_login import login_user

import App
import plani_agent as plani
from intelliplan.learning import policy


@pytest.fixture(autouse=True)
def planning_morning(monkeypatch):
    """Keep fixed-date tasks ahead of the planner's shared request clock.

    The shortest one-sided window ends at 17:00 on the task's due day.
    Letting UTC wall time pass that window turns a valid-configuration test
    into an expired-window test. Clock-specific tests can override this seam.
    """
    monkeypatch.setattr('intelliplan.scheduler_clock.utcnow',
                        lambda: datetime(2026, 10, 20, 8))
    # These tests isolate tool authorization/planning from remote classification.
    # Safety gate and side-effect ordering have their own test_plani_safety suite.
    monkeypatch.setattr('chatbot_api._moderation_response', lambda *a, **k: None)


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


def test_chat_planner_describes_calendar_outage_without_saving(account, monkeypatch):
    from intelliplan.services.calendar_availability import CalendarUnavailable
    add_task(account)
    def outage(*args, **kwargs):
        raise CalendarUnavailable(('google',))
    monkeypatch.setattr(App, '_build_planner_schedule', outage)
    result = tool(account, 'generate_schedule')
    assert result['error'] == 'calendar_unavailable'
    assert result['retryable'] is True
    assert 'calendar' in result['message']
    with App.app.app_context():
        assert App.SavedSchedule.query.filter_by(user_id=account[0]).count() == 0


def signed_client(account):
    client = App.app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(account[0])
        session['_fresh'] = True
    return client


@pytest.mark.parametrize('owner', ['another-account', ''])
def test_chat_rejects_stale_account_before_provider_work(account, monkeypatch, owner):
    provider = Mock(side_effect=AssertionError('provider must not receive stale history'))
    monkeypatch.setattr(plani, 'ai_available', provider)
    response = signed_client(account).post('/api/plani/agent',
        headers={'X-IntelliPlan-Chat-Owner': owner},
        json={'messages': [{'role': 'user', 'content': 'Private old account conversation'}]})
    assert response.status_code == 409
    assert response.get_json()['error'] == 'account_changed'
    assert 'Private old account' not in response.get_data(as_text=True)
    provider.assert_not_called()


@pytest.mark.parametrize('include_owner', [True, False])
def test_chat_current_owner_and_legacy_clients_keep_normal_gates(account, monkeypatch, include_owner):
    provider = Mock(return_value=False)
    monkeypatch.setattr(plani, 'ai_available', provider)
    headers = {'X-IntelliPlan-Chat-Owner': str(account[0])} if include_owner else {}
    response = signed_client(account).post('/api/plani/agent', headers=headers, json={'message': 'Help'})
    assert response.status_code == 503
    provider.assert_called_once()


@pytest.mark.parametrize(('name', 'key'), [
    ('get_schedule', 'scheduler'), ('generate_schedule', 'scheduler'),
    ('adjust_schedule', 'scheduler'),
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
    provider.assert_not_called()
    google.assert_not_called()
    outlook.assert_not_called()


def test_revocation_during_generation_prevents_persistence(account, monkeypatch):
    add_task(account)
    fixture_generation(monkeypatch)
    def revoke(*args, **kwargs):
        restrict(account, {'scheduler': False})
        return {'schedule': [], 'overview': 'Fixture'}
    monkeypatch.setattr(App, '_build_planner_schedule', revoke)
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


def test_chat_schedule_uses_real_planner_when_provider_is_down(account, monkeypatch):
    add_task(account)
    restrict(account, {'calendar': False})
    provider = fixture_generation(monkeypatch)
    monkeypatch.setattr('ai_provider.ai_available', lambda: False)
    result = tool(account, 'generate_schedule', {
        'hours_per_day': 0.5, 'study_start_time': '16:30', 'study_end_time': '18:30'})
    assert result['status'] == 'ok'
    blocks = [block for day in result['schedule']['schedule']
              for block in day['blocks'] if not block.get('is_break')]
    assert blocks
    assert all('Manual algebra' in block['assignment'] for block in blocks)
    assert all(block.get('task_id') for block in blocks)
    provider.assert_not_called()


def test_chat_schedule_preserves_canvas_morning_deadline_in_student_zone(account, monkeypatch):
    from datetime import datetime
    provider = fixture_generation(monkeypatch)
    monkeypatch.setattr(App, 'get_google_token', lambda: None)
    monkeypatch.setattr(App, 'get_outlook_token', lambda: None)
    monkeypatch.setattr('intelliplan.scheduler_clock.utcnow', lambda: datetime(2026, 10, 13, 15))
    monkeypatch.setattr(App, '_planner_busy_by_date', lambda **kwargs: {})
    with App.app.app_context():
        App.db.session.add(App.UserStreak(user_id=account[0], timezone='America/Los_Angeles'))
        App.db.session.commit()
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [{
        'id': 'morning', 'source': 'canvas', 'title': 'Morning quiz', 'course': 'Math',
        'estimated_time': 60, 'due_date': '2026-10-14', 'due_at': '2026-10-14T16:00:00Z'}])
    result = tool(account, 'generate_schedule', {'preferred_time': 'evening'})
    assert result.get('status') == 'ok', result
    blocks = [b for day in result['schedule']['schedule'] for b in day['blocks'] if not b.get('is_break')]
    assert blocks
    assert all(b['deadline_at'] == '2026-10-14T09:00:00' for b in blocks)
    assert all(datetime.fromisoformat(b['end_iso']) <= datetime(2026, 10, 14, 9) for b in blocks)
    assert all(datetime.fromisoformat(b['start_iso']).date().isoformat() == '2026-10-13' for b in blocks)
    with App.app.app_context():
        saved = App.SavedSchedule.query.filter_by(user_id=account[0], is_active=True).one()
        assert json.loads(saved.schedule_data) == result['schedule']
    provider.assert_not_called()


@pytest.mark.parametrize('window', [
    {'study_start_time': '4:30 PM'}, {'study_end_time': '8:00 PM'}])
@pytest.mark.parametrize('hours', [2, 0.5])
def test_chat_planner_accepts_one_sided_windows(account, monkeypatch, window, hours):
    add_task(account)
    restrict(account, {'calendar': False})
    fixture_generation(monkeypatch)
    result = tool(account, 'generate_schedule', {**window, 'hours_per_day': hours})
    assert result.get('status') == 'ok', result
    assert result['schedule']['planned_at_local'] == '2026-10-20T08:00:00'
    assert any(day['blocks'] for day in result['schedule']['schedule'])


def test_chat_planner_keeps_colliding_manual_and_lms_ids_distinct(account, monkeypatch):
    add_task(account)
    fixture_generation(monkeypatch)
    monkeypatch.setattr(App, 'get_google_token', lambda: None)
    monkeypatch.setattr(App, 'get_outlook_token', lambda: None)
    monkeypatch.setattr(App, '_planner_busy_by_date', lambda **kwargs: {})
    with App.app.app_context():
        manual_id = App.ManualTask.query.filter_by(user_id=account[0]).one().id
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [
        {'id': manual_id, 'source': 'canvas', 'title': 'Canvas essay',
         'course': 'English', 'estimated_time': 30, 'due_date': '2026-10-20'}])
    result = tool(account, 'generate_schedule')
    assert result['status'] == 'ok'
    blocks = [block for day in result['schedule']['schedule']
              for block in day['blocks'] if not block.get('is_break')]
    assert {block['task_id'] for block in blocks} == {
        f'manual:{manual_id}', f'canvas:{manual_id}'}
    assert any('Manual algebra' in block['assignment'] for block in blocks)
    assert any('Canvas essay' in block['assignment'] for block in blocks)
    with App.app.app_context():
        saved = App.SavedSchedule.query.filter_by(user_id=account[0], is_active=True).one()
        assert json.loads(saved.schedule_data) == result['schedule']


def test_failed_chat_planning_preserves_existing_active_plan(account, monkeypatch):
    add_task(account)
    provider = fixture_generation(monkeypatch)
    with App.app.app_context():
        old = App.SavedSchedule(user_id=account[0], name='Keep my plan',
                                schedule_data='{"schedule": []}', is_active=True)
        App.db.session.add(old)
        App.db.session.commit()
        old_id = old.id
    monkeypatch.setattr(App, '_build_planner_schedule', lambda *a, **kw: None)
    result = tool(account, 'generate_schedule')
    assert result['planning_failed'] is True
    with App.app.app_context():
        saved = App.SavedSchedule.query.filter_by(user_id=account[0], is_active=True).one()
        assert saved.id == old_id
        assert App.DayArchive.query.filter_by(user_id=account[0]).count() == 0
    provider.assert_not_called()


def test_chat_planner_distinguishes_repeated_lms_titles_by_deadline(account, monkeypatch):
    fixture_generation(monkeypatch)
    monkeypatch.setattr(App, 'get_google_token', lambda: None)
    monkeypatch.setattr(App, 'get_outlook_token', lambda: None)
    monkeypatch.setattr(App, '_planner_busy_by_date', lambda **kwargs: {})
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [
        {'source': 'studentvue', 'title': 'Homework', 'course': 'Math',
         'estimated_time': 30, 'due_date': due}
        for due in ('2026-10-20', '2026-10-21')])
    result = tool(account, 'generate_schedule')
    assert result['status'] == 'ok'
    ids = {block['task_id'] for day in result['schedule']['schedule']
           for block in day['blocks'] if not block.get('is_break')}
    assert len(ids) == 2


@pytest.mark.parametrize('hours', [0, -1, 13, 'bad', float('inf'), float('nan')])
def test_invalid_chat_study_hours_do_not_invoke_planner(account, monkeypatch, hours):
    add_task(account)
    fixture_generation(monkeypatch)
    planner = Mock(side_effect=AssertionError('invalid hours must not reach planning'))
    monkeypatch.setattr(App, '_build_planner_schedule', planner)
    assert 'error' in tool(account, 'generate_schedule', {'hours_per_day': hours})
    planner.assert_not_called()


def prepare_repair(account, monkeypatch):
    import followthrough_glue as followthrough
    from intelliplan.services import scheduling
    from datetime import datetime
    monkeypatch.setattr('intelliplan.scheduler_clock.utcnow', lambda: datetime(2026, 10, 13, 8))
    class PlanningClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 13, 8, tzinfo=tz)
    # Placement uses the clock again when serializing the chosen plan.
    monkeypatch.setattr(scheduling, 'datetime', PlanningClock)
    add_task(account)
    fixture_generation(monkeypatch)
    monkeypatch.setattr(App, 'get_google_token', lambda: None)
    monkeypatch.setattr(App, 'get_outlook_token', lambda: None)
    monkeypatch.setattr(App, '_planner_busy_by_date', lambda **kwargs: {})
    monkeypatch.setattr(followthrough, 'followthrough_enabled', lambda uid: True)
    monkeypatch.setattr(followthrough, '_now', lambda: datetime(2026, 10, 13, 8))
    monkeypatch.setattr(App, '_choose_plan', lambda service, rows, uid, **kwargs:
                        (service.plan(rows, now=datetime(2026, 10, 13, 8)), None))
    result = tool(account, 'generate_schedule')
    assert result['status'] == 'ok'
    with App.app.app_context():
        row = App.SavedSchedule.query.filter_by(user_id=account[0], is_active=True).one()
        return row.id, row.schedule_data, row.progress_json


@pytest.mark.parametrize('preview', [False, True])
def test_chat_repairs_real_plan_without_changing_deadlines(account, monkeypatch, preview):
    row_id, original, progress = prepare_repair(account, monkeypatch)
    assert any(day['date'] == '2026-10-13' and any(not b.get('is_break') for b in day['blocks'])
               for day in json.loads(original)['schedule'])
    result = tool(account, 'adjust_schedule', {
        'action': 'skip_day', 'day': '2026-10-13', 'preview': preview})
    assert result['status'] == 'ok'
    assert result['saved'] is not preview
    assert not [block for day in result['schedule']['schedule']
                if day['date'] == '2026-10-13' for block in day['blocks']
                if not block.get('is_break')]
    with App.app.app_context():
        row = App.db.session.get(App.SavedSchedule, row_id)
        assert row.is_active
        if preview:
            assert (row.schedule_data, row.progress_json) == (original, progress)
        else:
            assert json.loads(row.schedule_data) == result['schedule']
        assert App.ManualTask.query.filter_by(user_id=account[0]).one().due_date == '2026-10-20'


def test_chat_repairs_available_minutes_with_real_engine(account, monkeypatch):
    prepare_repair(account, monkeypatch)
    result = tool(account, 'adjust_schedule', {
        'action': 'limit_day', 'day': '2026-10-13', 'minutes': 30})
    assert result['status'] == 'ok'
    assert result['saved']
    minutes = sum(block['duration_minutes'] for day in result['schedule']['schedule']
                  if day['date'] == '2026-10-13' for block in day['blocks']
                  if not block.get('is_break'))
    assert minutes <= 30


@pytest.mark.parametrize('args', [
    {'action': 'skip_day', 'day': 'tomorrow'},
    {'action': 'limit_day', 'minutes': True},
    {'action': 'limit_day', 'minutes': -1},
    {'action': 'add_time', 'minutes': 0},
    {'action': 'add_time', 'minutes': 961},
    {'action': 'catch_up', 'preview': 'false'},
    {'action': 'skip_day', 'hours_per_day': 300},
])
def test_invalid_repair_inputs_do_not_read_saved_plan(account, monkeypatch, args):
    import followthrough_glue as followthrough
    load = Mock(side_effect=AssertionError('invalid inputs must not read plans'))
    monkeypatch.setattr(followthrough, '_active_row', load)
    assert tool(account, 'adjust_schedule', args)['error'] == 'invalid_arguments'
    load.assert_not_called()


@pytest.mark.parametrize('source', ['canvas', 'studentvue', 'calendar'])
def test_repair_respects_restricted_saved_sources(account, monkeypatch, source):
    import followthrough_glue as followthrough
    restrict(account, {source: False})
    load = Mock(side_effect=AssertionError('restricted saved aggregate must not be read'))
    monkeypatch.setattr(followthrough, '_active_row', load)
    assert tool(account, 'adjust_schedule', {'action': 'catch_up'})['error'] == 'source_restricted'
    load.assert_not_called()


def test_repair_does_not_overwrite_a_concurrent_saved_plan(account, monkeypatch):
    row_id, _, _ = prepare_repair(account, monkeypatch)
    import followthrough_glue as followthrough
    original_finalize = followthrough._finalize
    newer = json.dumps({'schedule': [], 'overview': 'Newer plan'})
    def concurrent_change(*args):
        row = App.db.session.get(App.SavedSchedule, row_id)
        row.schedule_data = newer
        App.db.session.commit()
        return original_finalize(*args)
    monkeypatch.setattr(followthrough, '_finalize', concurrent_change)
    result = tool(account, 'adjust_schedule', {'action': 'catch_up'})
    assert result['error'] == 'plan_changed'
    with App.app.app_context():
        assert App.db.session.get(App.SavedSchedule, row_id).schedule_data == newer


def test_repair_discards_in_flight_consent_revocation(account, monkeypatch):
    row_id, original, progress = prepare_repair(account, monkeypatch)
    import followthrough_glue as followthrough
    original_finalize = followthrough._finalize
    def revoke(*args):
        App.db.session.get(App.User, account[0]).ai_personalization_opt_in = False
        App.db.session.commit()
        return original_finalize(*args)
    monkeypatch.setattr(followthrough, '_finalize', revoke)
    result = tool(account, 'adjust_schedule', {'action': 'catch_up'})
    assert result['error'] == 'personalization_required'
    with App.app.app_context():
        row = App.db.session.get(App.SavedSchedule, row_id)
        assert (row.schedule_data, row.progress_json) == (original, progress)


@pytest.mark.parametrize('mode', ['save', 'preview', 'conflict', 'revoked'])
def test_repair_preserves_past_outcomes_only_with_successful_save(account, monkeypatch, mode):
    row_id, _, _ = prepare_repair(account, monkeypatch)
    import followthrough_glue as followthrough
    with App.app.app_context():
        row = App.db.session.get(App.SavedSchedule, row_id)
        data = json.loads(row.schedule_data)
        data['schedule'].insert(0, {
            'date': '2026-10-12', 'day_name': 'Monday', 'blocks': [
                {'task_id': f'past:{key}', 'block_id': key, 'assignment': key,
                 'course': 'Math', 'duration_minutes': 30, 'time_slot': '4:00 PM',
                 'due_date': '2026-10-20', 'is_break': False}
                for key in ('completed', 'missed')]})
        row.schedule_data = json.dumps(data)
        row.progress_json = json.dumps({'completed': {'done': True}})
        App.db.session.commit()
    original_finalize = followthrough._finalize
    def change_during_repair(*args):
        result = original_finalize(*args)
        if mode == 'conflict':
            App.db.session.get(App.SavedSchedule, row_id).schedule_data = '{"schedule":[],"overview":"newer"}'
            App.db.session.commit()
        elif mode == 'revoked':
            App.db.session.get(App.User, account[0]).ai_personalization_opt_in = False
            App.db.session.commit()
        return result
    monkeypatch.setattr(followthrough, '_finalize', change_during_repair)
    result = tool(account, 'adjust_schedule', {'action': 'catch_up', 'preview': mode == 'preview'})
    with App.app.app_context():
        signals = App.StudentSignal.query.filter_by(user_id=account[0], kind=followthrough.OUTCOME_SIGNAL).all()
        outcomes = [outcome for signal in signals for outcome in json.loads(signal.value_json)['rows']]
        if mode == 'save':
            assert result['saved']
            assert {(r['key'].split('|')[1], r['done']) for r in outcomes} == {
                ('past:completed', True), ('past:missed', False)}
        else:
            assert not outcomes


@pytest.mark.parametrize('suffix', ['', '::draft', '::steps:0,1'])
def test_identity_based_enrichment_preserves_duplicate_title_deadlines(account, suffix):
    data = {'schedule': [{'date': '2026-10-14', 'blocks': [
        {'task_id': f'canvas:{number}{suffix}', 'assignment': 'Homework', 'parent_title': 'Homework',
         'duration_minutes': 30, 'time_slot': '4:00 PM', 'is_break': False}
        for number in (1, 2)]}]}
    assignments = [{'id': f'canvas:{number}', 'title': 'Homework', 'due_date': due,
                    'priority': 'Medium', 'difficulty': 'Medium'}
                   for number, due in ((1, '2026-10-20'), (2, '2026-10-21'))]
    with App.app.test_request_context():
        enriched = App.enrich_schedule_data(data, assignments, 'evening', 2)
    assert {b['task_id']: b['due_date'] for b in enriched['schedule'][0]['blocks']} == {
        f'canvas:1{suffix}': '2026-10-20', f'canvas:2{suffix}': '2026-10-21'}


def test_finalized_repair_preserves_duplicate_title_deadlines(account):
    import followthrough_glue as followthrough
    from copy import deepcopy
    from datetime import date
    old = {'schedule': [{'date': '2026-10-14', 'blocks': [
        {'task_id': f'canvas:{number}', 'assignment': 'Homework', 'parent_title': 'Homework',
         'due_date': due, 'duration_minutes': 30, 'time_slot': '4:00 PM', 'is_break': False}
        for number, due in ((1, '2026-10-20'), (2, '2026-10-21'))]}]}
    with App.app.test_request_context():
        repaired, _ = followthrough._finalize(deepcopy(old), old, {}, date(2026, 10, 13))
    assert {b['task_id']: b['due_date'] for b in repaired['schedule'][0]['blocks']} == {
        'canvas:1': '2026-10-20', 'canvas:2': '2026-10-21'}


@pytest.mark.parametrize('preview', [False, True])
def test_real_chat_route_returns_repaired_plan_with_accurate_preview_state(account, monkeypatch, preview):
    row_id, original, progress = prepare_repair(account, monkeypatch)
    monkeypatch.setattr(plani, 'ai_available', lambda: True)
    monkeypatch.setattr(plani, 'build_agent_context', lambda uid: '')
    monkeypatch.setattr('intelliplan.retrieval.retrieve_context', lambda *a: '')
    request = json.dumps({'name': 'adjust_schedule', 'args': {
        'action': 'skip_day', 'day': '2026-10-13', 'preview': preview}})
    chat = Mock(side_effect=['```tool_call\n' + request + '\n```', 'Your plan is ready.'])
    monkeypatch.setattr(plani, 'ai_chat', chat)
    response = signed_client(account).post('/api/plani/agent', json={
        'message': 'Show a preview for skipping tonight' if preview else 'I cannot study tonight; move my work.'})
    assert response.status_code == 200
    assert response.json['schedule']['preview'] is preview
    assert response.json['refresh'] is not preview
    assert response.json['tool_log'][0]['result']['saved'] is not preview
    with App.app.app_context():
        saved = App.db.session.get(App.SavedSchedule, row_id)
        if preview:
            assert (saved.schedule_data, saved.progress_json) == (original, progress)
        else:
            assert json.loads(saved.schedule_data)['schedule'] == response.json['schedule']['schedule']


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
    fixture_generation(monkeypatch)
    monkeypatch.setattr(App, 'collect_lms_assignments_for_user', lambda uid: [
        {'title': 'Imported secret task', 'source': source, 'course': 'School'}])
    def revoke(*args, **kwargs):
        assert any(row['title'] == 'Imported secret task' for row in args[0])
        restrict(account, {source: False})
        return {'schedule': [], 'overview': 'Restricted data'}
    monkeypatch.setattr(App, '_build_planner_schedule', revoke)
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
        if App.db.engine.dialect.name == 'postgresql':
            # Explicit IDs reproduce the ownership bug; advance sequences so
            # later synthetic fixtures can insert without reusing these IDs.
            from sqlalchemy import text
            for model in (App.User, App.UserIdentity):
                App.db.session.execute(text(
                    f"SELECT setval(pg_get_serial_sequence(:table, 'id'), (SELECT MAX(id) FROM {model.__tablename__}), true)"),
                    {'table': model.__tablename__})
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


def test_chat_adjustment_explains_calendar_outage_and_retains_plan(account, monkeypatch):
    from intelliplan.services.calendar_availability import CalendarUnavailable
    row_id, original, progress = prepare_repair(account, monkeypatch)
    def outage(*args, **kwargs):
        raise CalendarUnavailable(('outlook',))
    monkeypatch.setattr(App, '_planner_busy_by_date', outage)
    result = tool(account, 'adjust_schedule', {'action': 'catch_up'})
    assert result['error'] == 'calendar_unavailable'
    assert result['retryable'] is True
    with App.app.app_context():
        row = App.db.session.get(App.SavedSchedule, row_id)
        assert (row.schedule_data, row.progress_json) == (original, progress)
