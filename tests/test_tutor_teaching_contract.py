"""Teaching contracts and Fish HTTP behavior; no live provider calls."""
from types import SimpleNamespace

import pytest

import ai_provider
from adaptive_tutor.pedagogy import QUESTION_LED_GUIDANCE
from adaptive_tutor.prompt import build_adaptive_prompt
from intelliplan.premium.claude_tutor import TUTOR_SYSTEM


def test_premium_and_adaptive_use_shared_question_led_contract():
    assert QUESTION_LED_GUIDANCE in TUTOR_SYSTEM
    prompt = build_adaptive_prompt({'profile': {'explanation_style': 'detailed', 'explanation_length': 'long'}},
                                   use_voice=True)
    assert QUESTION_LED_GUIDANCE in prompt
    assert 'type="svg-exercise"' in prompt
    assert 'Never emit HTML, JavaScript or external assets' in prompt
    assert 'full step-by-step breakdowns' not in prompt
    assert 'If they request a direct answer, answer clearly' not in prompt
    assert 'read aloud' in prompt


@pytest.fixture
def fish(monkeypatch):
    from intelliplan.learning import enforcement
    monkeypatch.setattr(enforcement, 'permitted_processing', lambda provider: True)
    for name in ('FISH_AUDIO_KEY', 'FISH_AUDIO_API_KEY', 'FISH_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    calls = []
    response = SimpleNamespace(status_code=200, content=b'fake-audio', text='private-provider-body')
    monkeypatch.setattr('requests.post', lambda *args, **kwargs: calls.append((args, kwargs)) or response)
    return calls, response


@pytest.mark.parametrize('name', ['FISH_AUDIO_KEY', 'FISH_AUDIO_API_KEY', 'FISH_API_KEY'])
def test_configured_fish_key_reaches_real_http_adapter(fish, monkeypatch, name):
    calls, _ = fish
    monkeypatch.setenv(name, 'fixture-key')
    audio, mime = ai_provider.speak('What changes when the slope increases?', voice='Sarah')
    assert (audio, mime) == (b'fake-audio', 'audio/mpeg')
    args, kwargs = calls[0]
    assert args == (ai_provider.FISH_AUDIO_URL,)
    assert kwargs['headers']['Authorization'] == 'Bearer fixture-key'
    assert kwargs['headers']['model'] == ai_provider.FISH_AUDIO_MODEL
    assert kwargs['json']['reference_id'] == ai_provider.resolve_voice('Sarah')
    assert kwargs['json']['text'].endswith('?')
    assert kwargs['timeout'] == 60


def test_fish_legacy_key_precedence_and_whitespace(fish, monkeypatch):
    monkeypatch.setenv('FISH_AUDIO_KEY', '  legacy  ')
    monkeypatch.setenv('FISH_AUDIO_API_KEY', 'alias')
    assert ai_provider.fish_audio_key() == 'legacy'
    monkeypatch.setenv('FISH_AUDIO_KEY', '  ')
    assert ai_provider.fish_audio_key() == 'alias'


@pytest.mark.parametrize('status', [402, 429, 500])
def test_fish_failures_do_not_return_error_body_as_audio(fish, monkeypatch, status):
    calls, response = fish
    monkeypatch.setenv('FISH_AUDIO_API_KEY', 'fixture-key')
    response.status_code = status
    with pytest.raises((ai_provider.AIQuotaExhausted, RuntimeError)) as error:
        ai_provider.speak('Try this question.')
    assert 'private-provider-body' not in str(error.value)
    assert len(calls) == 1


def test_fish_policy_denial_does_not_make_http_request(fish, monkeypatch):
    from intelliplan.learning import enforcement
    monkeypatch.setenv('FISH_AUDIO_API_KEY', 'fixture-key')
    monkeypatch.setattr(enforcement, 'permitted_processing', lambda provider: False)
    with pytest.raises(ai_provider.AIUnavailable):
        ai_provider.speak('Question?')
    assert fish[0] == []
