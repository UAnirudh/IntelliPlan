import json

from assignment_study_map import messages, parse


CONTEXT = {'title': 'Fractions', 'description': 'Compare two fractions using a common denominator.',
           'materials': [{'name': 'worksheet.txt', 'text': 'Explain why one fraction is larger.'}],
           'skipped_count': 1}


def test_map_keeps_only_exact_source_backed_steps():
    raw = json.dumps({'steps': [
        {'focus': 'Find a common denominator', 'source_id': 'directions',
         'evidence': 'using a common denominator'},
        {'focus': 'Invent a required graph', 'source_id': 'file_1',
         'evidence': 'Create a bar graph'},
        {'focus': 'Explain the comparison', 'source_id': 'file_1',
         'evidence': 'Explain why one fraction is larger.'},
    ], 'first_question': 'How could you compare the two fractions?'})
    result = parse(raw, CONTEXT)
    assert [step['focus'] for step in result['steps']] == [
        'Find a common denominator', 'Explain the comparison']
    assert result['steps'][1]['source'] == 'worksheet.txt'
    assert result['files_skipped'] == 1


def test_map_falls_back_without_inventing_a_plan():
    result = parse('{invalid', CONTEXT)
    assert result['status'] == 'limited'
    assert result['steps'] == []
    assert 'directions' in result['first_question']


def test_map_accepts_fenced_json_but_rejects_unhashable_source_ids():
    raw = '```json\n' + json.dumps({'steps': [
        {'focus': 'Fake', 'source_id': ['directions'], 'evidence': 'Compare two fractions.'},
        {'focus': 'Explain', 'source_id': 'file_1', 'evidence': 'Explain why one fraction is larger.'},
    ]}) + '\n```'
    result = parse(raw, CONTEXT)
    assert [step['focus'] for step in result['steps']] == ['Explain']


def test_prompt_contains_grade_and_explicit_untrusted_source_boundary():
    prompt = messages(CONTEXT, 'Grade 7')
    assert 'untrusted assignment data' in prompt[0]['content']
    assert 'Grade 7' in prompt[1]['content']
    assert 'Compare two fractions' in prompt[1]['content']
