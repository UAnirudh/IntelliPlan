"""Bounded contracts for generated study material and formative AI feedback.

These checks establish shape and score consistency, not factual correctness.
Generated study feedback is never independent, reviewed mastery evidence.
"""
import json
import re


def _text(value, maximum, *, optional=False):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or len(value) > maximum or (not optional and not value.strip()):
        raise ValueError('Invalid study text.')
    return value.strip()


def parse_response(raw):
    if not isinstance(raw, str) or len(raw) > 120000:
        raise ValueError('Invalid study response.')
    raw = raw.strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', raw, re.IGNORECASE)
    value = json.loads(fence.group(1) if fence else raw)
    if not isinstance(value, dict):
        raise ValueError('Study response must be an object.')
    return value


def generation(raw, maximum_questions=20):
    value = parse_response(raw)
    title = _text(value.get('title'), 200)
    questions, concepts = value.get('questions'), value.get('key_concepts', [])
    if not isinstance(questions, list) or not 1 <= len(questions) <= maximum_questions:
        raise ValueError('Invalid study question count.')
    if not isinstance(concepts, list) or len(concepts) > 12:
        raise ValueError('Invalid study concepts.')
    clean_questions, seen = [], set()
    for question in questions:
        if not isinstance(question, dict) or question.get('type') not in ('recall', 'conceptual', 'short-answer'):
            raise ValueError('Invalid study question.')
        prompt = _text(question.get('question'), 2000)
        if prompt.casefold() in seen:
            raise ValueError('Duplicate study question.')
        seen.add(prompt.casefold())
        clean_questions.append(dict(id=len(clean_questions) + 1, type=question['type'], question=prompt,
            answer=_text(question.get('answer'), 6000), hint=_text(question.get('hint'), 500, optional=True)))
    clean_concepts = []
    for concept in concepts:
        if not isinstance(concept, dict):
            raise ValueError('Invalid study concept.')
        clean_concepts.append(dict(term=_text(concept.get('term'), 200),
                                   definition=_text(concept.get('definition'), 2000)))
    return dict(title=title, questions=clean_questions, key_concepts=clean_concepts)


def evaluation(raw):
    value = parse_response(raw)
    verdict, score = value.get('verdict'), value.get('score')
    ranges = {'correct': (70, 100), 'partial': (40, 69), 'incorrect': (0, 39)}
    if not isinstance(verdict, str) or verdict not in ranges or type(score) is not int:
        raise ValueError('Invalid study evaluation.')
    low, high = ranges[verdict]
    if not low <= score <= high:
        raise ValueError('Study score conflicts with its verdict.')
    result = dict(verdict=verdict, score=score)
    for field in ('what_was_right', 'what_was_missing', 'critique', 'memory_anchor', 'better_answer'):
        result[field] = _text(value.get(field), 4000, optional=True)
    return result
