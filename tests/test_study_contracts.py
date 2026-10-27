import json

import pytest

from intelliplan.learning.study_contracts import evaluation, generation


def material(**overrides):
    value = dict(title='Cells', key_concepts=[], questions=[
        dict(id=999, type='recall', question='What is a cell?', answer='A unit of life.')])
    value.update(overrides)
    return json.dumps(value)


def test_reassigns_question_ids_and_preserves_code_fences_inside_answers():
    result = generation('```json\n' + material(questions=[dict(type='short-answer',
        question='Write a line of Python.', answer='```python\nprint(1)\n```')]) + '\n```')
    assert result['questions'][0]['id'] == 1
    assert result['questions'][0]['answer'] == '```python\nprint(1)\n```'


def test_duplicate_question_text_is_rejected():
    question = dict(type='recall', question='What is a cell?', answer='A unit of life.')
    with pytest.raises(ValueError, match='Duplicate'):
        generation(material(questions=[question, {**question, 'question': ' WHAT is a cell? '}]))


def test_provider_cannot_exceed_requested_question_count():
    with pytest.raises(ValueError, match='count'):
        generation(material(), maximum_questions=0)


@pytest.mark.parametrize('raw', ['null', '[]', 'not JSON', 'x' * 120001], ids=['null','array','invalid-json','oversized'])
def test_malformed_output_is_rejected(raw):
    with pytest.raises(ValueError):
        generation(raw)


def test_evaluation_drops_untrusted_points_and_metadata():
    result = evaluation(json.dumps(dict(verdict='correct', score=90, points_earned=999999,
        school_policy={'override': True})))
    assert 'points_earned' not in result and 'school_policy' not in result


@pytest.mark.parametrize('verdict,score', [('incorrect',0),('incorrect',39),
    ('partial',40),('partial',69),('correct',70),('correct',100)])
def test_valid_score_boundaries(verdict, score):
    assert evaluation(json.dumps(dict(verdict=verdict, score=score)))['score'] == score
