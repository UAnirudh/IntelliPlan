from datetime import date, datetime
from types import SimpleNamespace

import App
import chatbot_api
import pytest

from intelliplan.learning import policy
from intelliplan.services.test_prep import prep_candidates


def task(title, due='2026-11-27', *, course='Geometry', done=False):
    return SimpleNamespace(title=title, course=course, due_date=due, done=done)


def test_window_dedupes_and_uses_student_local_and_all_day_dates():
    events = [
        {'title': 'Geometry test', 'start': '2026-11-27'},
        {'title': 'Chemistry exam', 'start': '2026-11-28T01:00:00Z',
         'description': 'never show this', 'id': 'private external id'},
        {'title': 'Spanish quiz', 'start': '2026-11-28T02:00:00-05:00'},
        {'title': 'History final', 'start': '2026-11-27', 'status': 'cancelled'},
        {'title': 'Blood test', 'start': '2026-11-27'},
        {'title': 'AP Exam review', 'start': '2026-11-27'},
        {'title': 'Study: Chemistry exam', 'start': '2026-11-27'},
    ]
    result = prep_candidates(date(2026, 11, 24), tasks=[
        task('Geometry test'), task('Vocabulary quiz', '2026-11-26'),
        task('Algebra test', '2026-11-25'), task('French quiz', '2026-11-24'),
        task('English midterm', '2026-11-28'), task('Geometry mock test'),
        task('Complete proof worksheet'), task('Old quiz', '2026-11-23'),
    ], events=events, tz_name='America/Los_Angeles')
    assert [(row['title'], row['days_away'], row['suggest_today']) for row in result] == [
        ('French quiz', 0, False),
        ('Algebra test', 1, False),
        ('Vocabulary quiz', 2, True),
        ('Chemistry exam', 3, True),
        ('Geometry test', 3, True),
        ('Spanish quiz', 3, True),
    ]
    assert result[3]['minutes'] == 20
    assert result[3]['date'] == '2026-11-27'
    assert set(result[3]) == {'id', 'title', 'course', 'date', 'days_away', 'minutes', 'suggest_today'}


def test_saved_plan_assessment_uses_due_date_not_study_date_and_ignores_done_tasks():
    plan = {
        'schedule': [{'date': '2026-11-25', 'blocks': [
            {'assignment': 'Step 1', 'parent_title': 'Chapter 4 exam', 'course': 'Algebra',
             'kind': 'exam', 'due_date': '2026-11-27'},
            {'assignment': 'Unit test', 'course': 'Geometry', 'kind': 'test', 'due_date': '2026-11-27'},
        ]}],
        'deferred': [
            {'title': 'Chapter 4 exam', 'course': 'Algebra', 'kind': 'exam', 'due_date': '2026-11-27'}
        ],
    }
    result = prep_candidates(date(2026, 11, 25), tasks=[task('Unit test', done=True)], plan=plan)
    assert [(row['title'], row['course'], row['days_away']) for row in result] == [
        ('Chapter 4 exam', 'Algebra', 2),
    ]


@pytest.fixture
def assessment_clients(monkeypatch):
    App.app.config['TESTING'] = True
    monkeypatch.setattr(App.limiter, 'enabled', False)
    monkeypatch.setattr(chatbot_api, 'utcnow', lambda: datetime(2026, 11, 24, 22))
    with App.app.app_context():
        App.db.create_all()
        first, second = (App.User(email=f'test-prep-{n}@example.test', password_hash='x', birth_year=2000)
                         for n in (1, 2))
        App.db.session.add_all([first, second])
        App.db.session.flush()
        first_id, second_id = first.id, second.id
        App.db.session.add_all([
            App.ManualTask(user_id=first_id, title='My geometry test', due_date='2026-11-27',
                           course='Geometry', notes='Do not send private notes'),
            App.ManualTask(user_id=second_id, title='Other account test', due_date='2026-11-27'),
            App.ManualTask(user_id=first_id, title='Imported test', due_date='2026-11-27',
                           import_source='canvas'),
        ])
        App.db.session.commit()

    client = App.app.test_client()
    guest = App.app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(first_id)
        session['_fresh'] = True
    yield client, guest
    with App.app.app_context():
        App.ManualTask.query.filter(App.ManualTask.user_id.in_([first_id, second_id])).delete()
        App.User.query.filter(App.User.id.in_([first_id, second_id])).delete()
        App.db.session.commit()


def test_endpoint_is_private_owner_scoped_and_does_not_generate(assessment_clients, monkeypatch):
    client, guest = assessment_clients
    monkeypatch.setattr(chatbot_api, '_llm_chat', lambda **kwargs: pytest.fail('preview invoked AI'))
    monkeypatch.setattr(App, '_outlook_upcoming_events', lambda: [
        {'title': 'Outlook history exam', 'start': '2026-11-27T18:00:00Z'},
    ])
    monkeypatch.setattr(App, 'GCAL_AVAILABLE', True)
    monkeypatch.setattr(App, 'get_google_token', lambda: {'token': 'fixture'})
    monkeypatch.setattr(App, 'has_calendar_scope', lambda _token: True)
    monkeypatch.setattr(App, 'get_upcoming_events', lambda _token: [
        {'title': 'Google chemistry quiz', 'start': '2026-11-27T10:00:00-08:00'},
    ])
    original = policy.decision
    monkeypatch.setattr(policy, 'decision', lambda user, key: (
        (False, 'institution_policy') if key == 'canvas' else original(user, key)))
    assert guest.get('/api/tutor/test-prep').status_code == 401
    result = client.get('/api/tutor/test-prep', headers={'X-Timezone': 'America/Los_Angeles'})
    assert result.status_code == 200
    assert result.headers['Cache-Control'] == 'no-store, private'
    assert [row['title'] for row in result.json['assessments']] == [
        'Google chemistry quiz', 'My geometry test', 'Outlook history exam',
    ]
    assert b'Do not send private notes' not in result.data


def test_endpoint_assessment_protection_stops_before_calendar_reads(assessment_clients, monkeypatch):
    client, _guest = assessment_clients
    monkeypatch.setattr(policy, 'permitted_providers', lambda user: set())
    monkeypatch.setattr(chatbot_api, '_test_prep_calendar', lambda: pytest.fail('read blocked calendar'))
    assert client.get('/api/tutor/test-prep').status_code == 403
