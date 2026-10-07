"""Replay histories to test recovery, honest evidence and stable spaced reviews."""
from datetime import datetime, timedelta

from adaptive_tutor.evidence import summarize


START = datetime(2026, 1, 1, 12)


def attempt(day=0, correct=True, assisted=False, skill='g7m0'):
    return {'skill_id': skill, 'created_at': START + timedelta(days=day),
            'correct': correct, 'assisted': assisted}


def signal(rows, day=0):
    return summarize(rows, START + timedelta(days=day))['g7m0']


def test_recovery_is_not_buried_by_a_large_history_of_misses():
    rows = [attempt(correct=False) for _ in range(100)] + [attempt() for _ in range(3)]
    result = signal(rows)
    assert result['independent_correct'] == 3 and result['attempts'] == 103
    assert result['next_move'] == 'transfer'
    assert result['recent_independent_correct'] == 3 and result['recent_attempts'] == 6
    assert result['trend'] == 'improving'
    assert result['delayed_successes'] == 0


def test_a_recent_miss_or_hint_matters_despite_older_successes():
    successes = [attempt() for _ in range(100)]
    assert signal(successes + [attempt(correct=False)])['next_move'] == 'repair'
    hinted = signal(successes + [attempt(assisted=True)])
    assert hinted['next_move'] == 'independent'
    assert hinted['review_stage'] == 0
    assert hinted['independent_correct'] == 100


def test_early_repetitions_never_postpone_the_due_review_or_advance_it():
    baseline = signal([attempt()])
    repetitions = signal([attempt()] + [attempt(day=.5) for _ in range(100)], day=.5)
    assert repetitions['review_due_at'] == baseline['review_due_at']
    assert repetitions['review_stage'] == 1 and repetitions['delayed_successes'] == 0
    assert signal([attempt(), attempt(day=.5)], day=1)['next_move'] == 'review'


def test_a_qualified_review_advances_and_extra_practice_preserves_the_new_date():
    rows = [attempt(), attempt(day=1), attempt(day=2)]
    result = signal(rows, day=2)
    assert result['review_due_at'] == (START + timedelta(days=4)).isoformat()
    assert result['review_stage'] == 2 and result['delayed_successes'] == 1
    result = signal(rows + [attempt(day=4)], day=4)
    assert result['review_due_at'] == (START + timedelta(days=11)).isoformat()
    assert result['review_stage'] == 3 and result['delayed_successes'] == 2


def test_assistance_restarts_the_cadence_without_erasing_historical_reviews():
    rows = [attempt(), attempt(day=1), attempt(day=4, assisted=True), attempt(day=4.5)]
    result = signal(rows, day=4.5)
    assert result['review_stage'] == 1 and result['delayed_successes'] == 1
    assert result['review_due_at'] == (START + timedelta(days=5.5)).isoformat()


def test_long_absence_requests_a_fresh_check_instead_of_an_old_failure_label():
    assert signal([attempt(correct=False)], day=30)['next_move'] == 'review'
    assert signal([attempt(assisted=True)], day=30)['next_move'] == 'review'


def test_trend_requires_two_complete_windows_and_skills_stay_separate():
    assert signal([attempt()] * 11)['trend'] == 'building'
    assert signal([attempt()] * 6 + [attempt(correct=False)] * 6)['trend'] == 'revisit'
    result = summarize([attempt(), attempt(correct=False, skill='g7r0')], START)
    assert result['g7m0']['next_move'] == 'diagnose'
    assert result['g7r0']['next_move'] == 'repair'
    assert result['g7m0']['attempts'] == result['g7r0']['attempts'] == 1
