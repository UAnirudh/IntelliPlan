"""Bad schedule input must be rejected before calendar or persistence work."""
import pytest

import App
from intelliplan.services.schedule_input import validate_schedule_request


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(App.app.config, 'TESTING', True)
    monkeypatch.setattr(App.limiter, 'enabled', False)
    def unexpected_calendar_read(**kwargs):
        raise AssertionError('invalid input reached the calendar')
    monkeypatch.setattr(App, '_planner_busy_by_date', unexpected_calendar_read)
    return App.app.test_client()


@pytest.mark.parametrize('payload', [
    [{'title': 'Algebra'}],
    {'assignments': ['Algebra'], 'skip_clarify': True},
    {'assignments': {}, 'skip_clarify': True},
    {'custom_tasks': [42], 'skip_clarify': True},
    {'custom_tasks': 'Algebra', 'skip_clarify': True},
    {'custom_tasks': ['Algebra'], 'hours_per_day': 'bad', 'skip_clarify': True},
    {'custom_tasks': ['Algebra'], 'hours_per_day': 'NaN', 'skip_clarify': True},
    {'custom_tasks': ['Algebra'], 'hours_per_day': True, 'skip_clarify': True},
    {'custom_tasks': ['Algebra'], 'hours_per_day': 25, 'skip_clarify': True},
])
def test_invalid_request_returns_400_before_reading_calendar(client, payload):
    response = client.post('/generate_schedule', json=payload)
    assert response.status_code == 400
    assert response.is_json and response.get_json()['status'] == 'error'


@pytest.mark.parametrize('hours', [0, -1, 25, None, True, 'bad', '', 'NaN', 'Infinity', float('inf'), float('nan'), 10 ** 1000])
def test_invalid_hours_never_reach_the_planner(hours):
    with pytest.raises(ValueError, match='Study hours'):
        validate_schedule_request({'custom_tasks': ['Algebra'], 'hours_per_day': hours})


@pytest.mark.parametrize('body', [None, [], 'tasks', {'assignments': None}, {'custom_tasks': None}])
def test_invalid_envelopes_and_explicit_null_collections_are_rejected(body):
    with pytest.raises(ValueError):
        validate_schedule_request(body)


def test_provider_metadata_and_clarified_tasks_are_preserved_without_mutating_input():
    assignment = {'id': 123, 'title': 'Algebra', 'course': 'Math', 'due_at': None,
                  'subtasks': [{'text': 'Read question', 'done': False}], 'points_possible': 10}
    clarified = {'title': 'Read chapter', 'course': 'English', 'due_date': '2026-11-01',
                 'estimated_time': 35, 'estimate_source': 'student'}
    body = {'assignments': [assignment], 'custom_tasks': ['Review notes', clarified],
            'hours_per_day': '1.5', 'study_start_time': '16:30', 'skip_clarify': True}
    normalized = validate_schedule_request(body)
    assert normalized == {**body, 'hours_per_day': 1.5}
    assert body['hours_per_day'] == '1.5'
    assert normalized['assignments'][0] is assignment
    assert normalized['custom_tasks'][1] is clarified


def test_optional_defaults_and_whole_day_boundary_are_supported():
    assert validate_schedule_request({}) == {'assignments': [], 'custom_tasks': [], 'hours_per_day': 2.0}
    assert validate_schedule_request({'hours_per_day': 24})['hours_per_day'] == 24.0
