"""Tests for the adaptive tutor student model.

Covers the three pieces the tutor's personalization actually depends on:
mastery math, modality routing, and the prompt/analysis plumbing. All LLM
calls are stubbed — these must not consume API quota.
"""

import json

import pytest
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

from adaptive_tutor import analysis, engine, modality, store
from adaptive_tutor.api import adaptive_tutor_bp
from adaptive_tutor.prompt import build_adaptive_prompt


@pytest.fixture
def app():
    application = Flask(__name__)
    application.config.update(
        SECRET_KEY='test-secret',
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    SQLAlchemy(application)
    LoginManager(application)
    application.register_blueprint(adaptive_tutor_bp)

    store._READY = False
    with application.app_context():
        yield application
    store._READY = False


@pytest.fixture
def ctx(app):
    """A request context, so the guest-session owner path is available."""
    with app.test_request_context('/'):
        yield


# ── Modality routing ────────────────────────────────────────────────

def test_reading_mode_disables_voice_and_artifacts():
    active = modality.get_active_modalities('reading', modality.DEFAULT_WEIGHTS)
    assert active == {'use_voice': False, 'use_artifacts': False, 'use_text': True}


def test_auto_mode_enables_voice_only_on_a_clear_auditory_signal():
    weak = {'auditory': 0.38, 'visual': 0.32, 'reading': 0.30}
    strong = {'auditory': 0.70, 'visual': 0.15, 'reading': 0.15}

    assert modality.get_active_modalities('auto', weak)['use_voice'] is False
    assert modality.get_active_modalities('auto', strong)['use_voice'] is True


def test_auto_mode_keeps_artifacts_for_visual_learners():
    visual = {'auditory': 0.10, 'visual': 0.70, 'reading': 0.20}
    assert modality.get_active_modalities('auto', visual)['use_artifacts'] is True


def test_weights_blend_toward_the_newest_reading():
    previous = {'auditory': 1.0, 'visual': 0.0, 'reading': 0.0}
    incoming = {'auditory': 0.0, 'visual': 1.0, 'reading': 0.0}
    blended = modality.blend_weights(previous, incoming)

    assert blended['auditory'] == pytest.approx(0.6)
    assert blended['visual'] == pytest.approx(0.4)
    assert sum(blended.values()) == pytest.approx(1.0)


def test_normalize_weights_falls_back_on_garbage():
    assert modality.normalize_weights('not a dict') == modality.DEFAULT_WEIGHTS
    assert modality.normalize_weights({'auditory': 0, 'visual': 0, 'reading': 0}) == modality.DEFAULT_WEIGHTS


# ── Mastery ─────────────────────────────────────────────────────────

def test_first_attempt_seeds_mastery(ctx):
    profile = store.get_or_create_profile()
    store.update_mastery(profile['id'], 'Math', 'Quadratics', correct=True)

    row = store.list_mastery(profile['id'])[0]
    assert row['mastery_score'] == 60.0
    assert row['total_attempts'] == 1
    assert row['confidence_level'] == 20.0


def test_mastery_uses_a_weighted_moving_average(ctx):
    profile = store.get_or_create_profile()
    store.update_mastery(profile['id'], 'Math', 'Quadratics', correct=True)
    store.update_mastery(profile['id'], 'Math', 'Quadratics', correct=True)

    # 60 * 0.7 + (2/2 * 100) * 0.3 == 72
    row = store.list_mastery(profile['id'])[0]
    assert row['mastery_score'] == pytest.approx(72.0)
    assert row['confidence_level'] == 36.0


def test_wrong_answers_pull_mastery_down(ctx):
    profile = store.get_or_create_profile()
    store.update_mastery(profile['id'], 'Math', 'Quadratics', correct=True)
    before = store.list_mastery(profile['id'])[0]['mastery_score']
    store.update_mastery(profile['id'], 'Math', 'Quadratics', correct=False)
    after = store.list_mastery(profile['id'])[0]['mastery_score']

    assert after < before


# ── Mistake patterns ────────────────────────────────────────────────

def test_repeat_mistakes_increment_frequency_rather_than_duplicating(ctx):
    profile = store.get_or_create_profile()
    mistake = {
        'subject': 'Math', 'topic': 'Quadratics',
        'mistakeType': 'sign error', 'description': 'Dropped the minus sign',
    }
    store.record_mistake(profile['id'], mistake)
    store.record_mistake(profile['id'], mistake)

    rows = store.list_mistakes(profile['id'])
    assert len(rows) == 1
    assert rows[0]['frequency'] == 2


def test_resolved_mistakes_leave_the_active_list(ctx):
    profile = store.get_or_create_profile()
    store.record_mistake(profile['id'], {
        'subject': 'Math', 'topic': 'Quadratics',
        'mistakeType': 'sign error', 'description': 'Dropped the minus sign',
    })
    mistake_id = store.list_mistakes(profile['id'])[0]['id']

    assert store.resolve_mistake(profile['id'], mistake_id) is True
    assert store.list_mistakes(profile['id']) == []


def test_mistakes_without_a_description_are_ignored(ctx):
    profile = store.get_or_create_profile()
    store.record_mistake(profile['id'], {'subject': 'Math', 'description': '   '})
    assert store.list_mistakes(profile['id']) == []


# ── Profile ─────────────────────────────────────────────────────────

def test_profile_round_trips_onboarding_answers(ctx):
    profile = store.get_or_create_profile()
    updated = store.update_profile(profile['id'], {
        'grade_level': '10th grade',
        'subjects': ['Algebra 2', 'Chemistry'],
        'interests': ['basketball'],
        'short_term_goals': 'Pass the unit test',
        'explanation_style': 'detailed',
        'onboarding_completed': True,
    })

    assert updated['grade_level'] == '10th grade'
    assert updated['subjects'] == ['Algebra 2', 'Chemistry']
    assert updated['explanation_style'] == 'detailed'
    assert updated['onboarding_completed'] is True


def test_invalid_choices_fall_back_to_defaults(ctx):
    profile = store.get_or_create_profile()
    updated = store.update_profile(profile['id'], {
        'explanation_style': 'shakespearean',
        'difficulty_level': 'impossible',
    })

    assert updated['explanation_style'] == 'balanced'
    assert updated['difficulty_level'] == 'medium'


def test_the_same_owner_gets_the_same_profile(ctx):
    first = store.get_or_create_profile()
    second = store.get_or_create_profile()
    assert first['id'] == second['id']


# ── Prompt assembly ─────────────────────────────────────────────────

def _context():
    return {
        'profile': {
            'grade_level': '10th grade',
            'subjects': ['Algebra 2'],
            'interests': ['basketball'],
            'short_term_goals': 'Pass the unit test',
            'long_term_goals': None,
            'explanation_style': 'detailed',
            'explanation_length': 'long',
            'difficulty_level': 'adaptive',
        },
        'mastery': [{
            'subject': 'Math', 'topic': 'Quadratics',
            'mastery_score': 41.0, 'confidence_level': 36.0,
        }],
        'mistakes': [{
            'subject': 'Math', 'topic': 'Quadratics',
            'mistake_type': 'sign error', 'description': 'Dropped the minus sign',
            'frequency': 3,
        }],
        'recent_sessions': [{
            'summary_text': 'Worked through factoring.',
            'struggled': ['completing the square'],
            'review_next': ['the quadratic formula'],
            'started_at': None,
        }],
        'learner_memory': {
            'learner_type': 'example-driven learner',
            'confidence': 0.6,
            'summary': 'Learns fastest from worked examples.',
            'strengths': ['pattern spotting'],
            'friction_points': ['abstract notation'],
            'preferred_patterns': [],
            'recommended_strategies': [],
        },
        'memory_imports': [{
            'provider': 'ChatGPT', 'source_label': 'export',
            'extracted_summary': 'Prefers short answers.', 'raw_text': '',
        }],
    }


def test_prompt_carries_every_part_of_the_student_model():
    prompt = build_adaptive_prompt(_context())

    assert '10th grade' in prompt
    assert 'basketball' in prompt
    assert 'Quadratics' in prompt
    assert 'sign error' in prompt
    assert 'completing the square' in prompt
    assert 'example-driven learner' in prompt
    assert 'ChatGPT' in prompt


def test_mastery_is_labelled_by_band():
    prompt = build_adaptive_prompt(_context())
    assert 'needs work' in prompt


def test_artifacts_are_only_described_when_enabled():
    with_artifacts = build_adaptive_prompt(_context(), use_artifacts=True)
    without = build_adaptive_prompt(_context(), use_artifacts=False)

    assert ':::artifact{' in with_artifacts
    assert ':::artifact{' not in without


def test_voice_mode_asks_for_spoken_phrasing():
    spoken = build_adaptive_prompt(_context(), use_voice=True)
    assert 'read aloud' in spoken
    assert 'x squared plus 3x minus 7' in spoken


# ── Analysis passes ─────────────────────────────────────────────────

def _chat_returning(payload):
    def _chat(**kwargs):
        return json.dumps(payload)
    return _chat


def _chat_raising(**kwargs):
    raise RuntimeError('provider down')


TRANSCRIPT = [
    {'role': 'user', 'content': 'Why does the minus sign flip?'},
    {'role': 'assistant', 'content': 'Because you distribute it across both terms.'},
]


def test_session_summary_is_parsed():
    summary = analysis.generate_session_summary(_chat_returning({
        'summaryText': 'Covered sign distribution.',
        'topicsCovered': ['distribution'],
        'understood': ['distribution'],
        'struggled': ['factoring'],
        'reviewNext': ['factoring'],
    }), TRANSCRIPT)

    assert summary['summaryText'] == 'Covered sign distribution.'
    assert summary['struggled'] == ['factoring']


def test_session_summary_degrades_when_the_provider_fails():
    summary = analysis.generate_session_summary(_chat_raising, TRANSCRIPT)
    assert summary == analysis.EMPTY_SUMMARY


def test_mistake_extraction_accepts_the_wrapped_object():
    mistakes = analysis.extract_mistakes(_chat_returning({'mistakes': [{
        'subject': 'Math', 'topic': 'Quadratics',
        'mistakeType': 'sign error', 'description': 'Dropped the minus sign',
    }]}), TRANSCRIPT)

    assert len(mistakes) == 1
    assert mistakes[0]['mistakeType'] == 'sign error'


def test_mistake_extraction_drops_entries_without_a_description():
    mistakes = analysis.extract_mistakes(
        _chat_returning({'mistakes': [{'subject': 'Math'}]}), TRANSCRIPT)
    assert mistakes == []


def test_learner_memory_clamps_confidence():
    result = analysis.analyze_learner_memory(
        _chat_returning({'learnerType': 'visual', 'confidence': 4.2}))
    assert result['confidence'] == 1.0


def test_learner_memory_falls_back_on_provider_failure():
    result = analysis.analyze_learner_memory(_chat_raising)
    assert result['learnerType'] == analysis.FALLBACK_LEARNER_MEMORY['learnerType']


# ── Engine ──────────────────────────────────────────────────────────

def test_subject_tag_is_split_off_the_user_message():
    subject, body = engine.split_subject('[Subject: Chemistry]\nWhat is a mole?')
    assert subject == 'Chemistry'
    assert body == 'What is a mole?'


def test_untagged_messages_default_to_general():
    subject, body = engine.split_subject('What is a mole?')
    assert subject == 'General'
    assert body == 'What is a mole?'


def test_prepare_turn_builds_a_prompt_and_channel_set(ctx):
    turn = engine.prepare_turn('reading')
    assert turn['mode'] == 'reading'
    assert turn['active']['use_artifacts'] is False
    assert 'ADAPTIVE STUDENT MODEL' in turn['prompt']


def test_prepare_turn_rejects_an_unknown_mode(ctx):
    assert engine.prepare_turn('telepathy')['mode'] == 'auto'


def test_summarize_conversation_moves_mastery_and_records_mistakes(ctx, monkeypatch):
    monkeypatch.setattr(analysis, 'generate_session_summary', lambda chat, msgs: {
        'summaryText': 'Worked on quadratics.',
        'topicsCovered': ['Quadratics'],
        'understood': ['Factoring'],
        'struggled': ['Completing the square'],
        'reviewNext': ['Completing the square'],
    })
    monkeypatch.setattr(analysis, 'extract_mistakes', lambda chat, msgs: [{
        'subject': 'Math', 'topic': 'Quadratics',
        'mistakeType': 'sign error', 'description': 'Dropped the minus sign',
    }])

    result = engine.summarize_conversation(_chat_raising, 42, [
        {'role': 'user', 'content': '[Subject: Math]\nHelp with quadratics'},
        {'role': 'assistant', 'content': 'Sure.'},
    ])

    profile = store.get_or_create_profile()
    topics = {row['topic'] for row in store.list_mastery(profile['id'])}

    assert result['subject'] == 'Math'
    assert topics == {'Factoring', 'Completing the square'}
    assert len(store.list_mistakes(profile['id'])) == 1

    summaries = store.list_session_summaries(profile['id'])
    assert summaries[0]['conversation_id'] == 42
    assert summaries[0]['summary_text'] == 'Worked on quadratics.'


def test_summarizing_the_same_conversation_twice_updates_one_row(ctx, monkeypatch):
    monkeypatch.setattr(analysis, 'generate_session_summary', lambda chat, msgs: {
        'summaryText': 'Second pass.', 'topicsCovered': [],
        'understood': [], 'struggled': [], 'reviewNext': [],
    })
    monkeypatch.setattr(analysis, 'extract_mistakes', lambda chat, msgs: [])

    messages = [
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': 'hello'},
    ]
    engine.summarize_conversation(_chat_raising, 7, messages)
    engine.summarize_conversation(_chat_raising, 7, messages)

    profile = store.get_or_create_profile()
    assert len(store.list_session_summaries(profile['id'])) == 1


def test_record_turn_never_raises_when_analysis_fails(ctx):
    turn = engine.prepare_turn('auto')
    # Must not raise — a failed analysis cannot break a tutor reply.
    engine.record_turn(_chat_raising, turn, [], 'question', 'answer')


# ── HTTP surface ────────────────────────────────────────────────────

def test_profile_endpoints_round_trip(app):
    client = app.test_client()

    assert client.get('/api/tutor/adaptive/profile').status_code == 200

    saved = client.post('/api/tutor/adaptive/profile', json={
        'grade_level': '11th grade', 'subjects': ['Physics'],
    })
    assert saved.status_code == 200
    assert saved.get_json()['profile']['grade_level'] == '11th grade'

    reloaded = client.get('/api/tutor/adaptive/profile').get_json()
    assert reloaded['profile']['subjects'] == ['Physics']


def test_modality_endpoint_rejects_unknown_modes(app):
    client = app.test_client()
    assert client.post('/api/tutor/adaptive/modality', json={'mode': 'telepathy'}).status_code == 400
    assert client.post('/api/tutor/adaptive/modality', json={'mode': 'visual'}).status_code == 200


def test_dashboard_reports_stats(app):
    data = app.test_client().get('/api/tutor/adaptive/dashboard').get_json()
    assert data['stats'] == {
        'average_mastery': 0.0,
        'topics_tracked': 0,
        'active_mistakes': 0,
        'sessions_summarized': 0,
    }


def test_summarize_rejects_an_empty_conversation(app):
    res = app.test_client().post('/api/tutor/adaptive/summarize', json={'messages': []})
    assert res.status_code == 400


def test_memory_import_requires_text(app):
    res = app.test_client().post('/api/tutor/adaptive/memory-imports', json={'provider': 'ChatGPT'})
    assert res.status_code == 400
