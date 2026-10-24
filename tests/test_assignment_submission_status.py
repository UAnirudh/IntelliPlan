"""Current-student submission facts, rather than absent grades, drive work queues."""
import pytest

from intelliplan.services.assignment_status import annotate_assignment, requires_student_action


@pytest.mark.parametrize('record', [
    {'grade': None}, {'score': None}, {'display_score': 'Not Graded'},
    {'has_submitted_submissions': True},
    {'submission': {'workflow_state': 'unsubmitted', 'score': 0}},
    {'submission': {'workflow_state': 'unsubmitted', 'submitted_at': '2026-01-01T00:00:00Z'}},
    {'submission': {'workflow_state': 'graded', 'redo_request': True}},
    {'submission': {'workflow_state': 'unknown', 'submitted_at': 'invalid'}},
])
def test_incomplete_or_contradicted_evidence_stays_actionable(record):
    assert requires_student_action(record)


@pytest.mark.parametrize('submission,status', [
    ({'workflow_state': 'submitted', 'submitted_at': '2026-01-01T00:00:00Z'}, 'awaiting_grade'),
    ({'workflow_state': 'pending_review'}, 'awaiting_grade'),
    ({'workflow_state': 'graded'}, 'graded'),
    ({'excused': True}, 'excused'),
])
def test_verified_student_state_is_visible_without_new_work(submission, status):
    original = {'title': 'Essay', 'due_date': '2026-01-02', 'submission': submission}
    result = annotate_assignment(original)
    assert result['submission_status'] == status
    assert result['requires_action'] is False
    assert result['due_date'] == original['due_date']
    assert 'submission_status' not in original


def test_a_normalized_waiting_record_remains_waiting_when_rechecked():
    record = annotate_assignment({'submission': {'workflow_state': 'pending_review'}})
    assert not requires_student_action(record)


@pytest.mark.parametrize('status', ['submitted', 'pending_review', 'turned_in', 'awaiting_grade'])
def test_normalized_waiting_synonyms_with_empty_nested_evidence(status):
    row = annotate_assignment({'submission': {}, 'submission_status': status})
    assert row['submission_status'] == 'awaiting_grade'
    assert row['awaiting_grade'] is True
    assert row['requires_action'] is False


def test_explicit_reopening_beats_a_normalized_waiting_flag():
    assert requires_student_action({'submission_status': 'awaiting_grade',
                                    'requires_action': False, 'workflow_state': 'reopened'})


def test_direct_planner_adapter_keeps_unknown_and_redo_and_excludes_waiting():
    from datetime import datetime
    from intelliplan.services.scheduling import SchedulingService, StudentContext
    service = SchedulingService(StudentContext(), decompose_stages=False)
    tasks = service.tasks_from([
        {'id': 'waiting', 'title': 'Submitted essay', 'submission': {'workflow_state': 'submitted'}},
        {'id': 'unknown', 'title': 'Ungraded worksheet', 'grade': None},
        {'id': 'redo', 'title': 'Redo exercise', 'submission': {'workflow_state': 'graded', 'redo_request': True}},
    ], now=datetime(2026, 10, 23, 9))
    assert {task.id for task in tasks} == {'unknown', 'redo'}
