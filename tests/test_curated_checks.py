"""Independent verification of trusted fixed checks; no learner code execution."""
from fractions import Fraction
import subprocess
import sys

import pytest

from intelliplan.learning import catalog, courses, curated_checks


@pytest.mark.parametrize('key', list(curated_checks.BANK))
def test_fixed_answer_is_verified_independently(key, tmp_path):
    item = curated_checks.BANK[key]
    quiz, proof = item['quiz'], item['verification']
    kind = proof['kind']
    if kind in {'python_stdout', 'python_assertions'}:
        # Only literal trusted repository examples are executed. -I/-S keep
        # project/user-site imports and Python environment options out. This
        # is not a sandbox for generated code or student submissions.
        if kind == 'python_stdout':
            result = subprocess.run([sys.executable, '-I', '-S', '-c', proof['code']],
                cwd=tmp_path, capture_output=True, text=True, timeout=5, check=True)
            assert result.stdout.strip() == quiz['options'][quiz['answer']]
        else:
            passed = []
            for index, assertion in enumerate(quiz['options']):
                result = subprocess.run([sys.executable, '-I', '-S', '-c', proof['setup'] + '\n' + assertion],
                    cwd=tmp_path, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    passed.append(index)
                else:
                    assert 'AssertionError' in result.stderr
            assert passed == [quiz['answer']]
        return
    if kind == 'linear_value':
        expected = Fraction(proof['slope']) * Fraction(proof['x']) + Fraction(proof['intercept'])
    elif kind == 'linear_solution':
        expected = (Fraction(proof['rhs']) - Fraction(proof['b'])) / Fraction(proof['a'])
        assert Fraction(proof['a']) * expected + Fraction(proof['b']) == Fraction(proof['rhs'])
    elif kind == 'slope':
        first, second = proof['points']
        expected = Fraction(second[1] - first[1], second[0] - first[0])
    elif kind == 'quadrant':
        x, y = proof['x'], proof['y']
        assert x != 0 and y != 0
        expected = {(True, True): 'Quadrant I', (False, True): 'Quadrant II',
                    (False, False): 'Quadrant III', (True, False): 'Quadrant IV'}[x > 0, y > 0]
        assert quiz['options'][quiz['answer']] == expected
        return
    else:
        pytest.fail('Missing an independent verifier for this fixed assessment.')
    options = [Fraction(value) for value in quiz['options']]
    assert len(set(options)) == len(options)
    assert [index for index, value in enumerate(options) if value == expected] == [quiz['answer']]


def test_bank_covers_only_the_exact_supported_curricula_and_validates():
    ids = []
    expected = set()
    for subject, factory in [('python', catalog.python_blueprint), ('algebra', catalog.algebra_blueprint)]:
        for spec in factory('Synthetic learning goal')['lessons']:
            key = (subject, spec['objective'])
            expected.add(key)
            content = curated_checks.content_for(*key)
            ids.append(content['assessment_bank_id'])
            assert courses.validate_lesson(content)['quiz'] == content['quiz']
            assert content['assessment_language'] == 'en'
            assert 'verification' not in content and 'verification' not in content['quiz']
    assert set(curated_checks.BANK) == expected
    assert len(ids) == len(set(ids)) == 12


def test_returned_content_cannot_mutate_the_fixed_bank():
    first = curated_checks.content_for('python', 'variables and strings')
    first['quiz']['answer'] = 0
    first['quiz']['options'].clear()
    fresh = curated_checks.content_for('python', '  VARIABLES   and strings ')
    assert fresh['quiz']['answer'] == 1 and len(fresh['quiz']['options']) == 4


@pytest.mark.parametrize('subject, objective', [
    ('python', 'loops and conditions'), ('algebra', 'variables and strings'),
    ('unsupported', 'loops'), (None, 'loops'), ('python', None)])
def test_unmatched_edited_objectives_do_not_receive_a_misaligned_check(subject, objective):
    assert curated_checks.content_for(subject, objective) is None
