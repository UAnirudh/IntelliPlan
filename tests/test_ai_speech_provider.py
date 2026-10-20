"""Speech provider routing without API traffic or student data."""
from types import SimpleNamespace

import pytest

import ai_provider
from intelliplan.learning import enforcement


@pytest.fixture
def groq_speech(monkeypatch):
    calls, checked = [], []
    monkeypatch.setattr(ai_provider, 'fish_audio_key', lambda: None)
    monkeypatch.setattr(ai_provider, 'groq_audio_key', lambda: 'fake-audio-key')
    monkeypatch.setattr(ai_provider, 'GROQ_TTS_VOICE', 'hannah')
    monkeypatch.setattr(ai_provider, 'GROQ_TTS', 'canopylabs/orpheus-v1-english')

    def permitted(provider):
        checked.append(provider)
        return True

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(read=lambda: b'RIFFsynthetic-wave')

    monkeypatch.setattr(enforcement, 'permitted_processing', permitted)
    monkeypatch.setattr(ai_provider, '_groq_client', lambda key: SimpleNamespace(
        audio=SimpleNamespace(speech=SimpleNamespace(create=create))))
    return calls, checked


@pytest.mark.parametrize('voice', [None, 'Ethan', ai_provider.FISH_VOICES[0]['id'], 'other-fish-reference'])
def test_fish_picker_falls_back_to_configured_groq_voice(groq_speech, voice):
    calls, checked = groq_speech
    assert ai_provider.speak('A synthetic audio check.', voice=voice) == (b'RIFFsynthetic-wave', 'audio/wav')
    assert calls[0]['voice'] == 'hannah'
    assert calls[0]['response_format'] == 'wav'
    assert checked == ['groq']


def test_explicit_supported_groq_voice_is_normalized(groq_speech):
    calls, _ = groq_speech
    ai_provider.speak('A synthetic audio check.', voice=' Troy ')
    assert calls[0]['voice'] == 'troy'


def test_fish_selection_honors_deployment_groq_voice(groq_speech, monkeypatch):
    calls, _ = groq_speech
    monkeypatch.setattr(ai_provider, 'GROQ_TTS_VOICE', 'daniel')
    ai_provider.speak('A synthetic audio check.', voice='Ethan')
    assert calls[0]['voice'] == 'daniel'


def test_institutional_provider_restriction_prevents_any_speech_request(groq_speech, monkeypatch):
    calls, _ = groq_speech
    monkeypatch.setattr(enforcement, 'permitted_processing', lambda provider: False)
    with pytest.raises(ai_provider.AIUnavailable, match='institutional policy'):
        ai_provider.speak('A synthetic audio check.')
    assert calls == []


def test_fish_speech_restriction_does_not_silently_route_to_groq(groq_speech, monkeypatch):
    calls, _ = groq_speech
    monkeypatch.setattr(ai_provider, 'fish_audio_key', lambda: 'fake-fish-key')
    monkeypatch.setattr(enforcement, 'permitted_processing', lambda provider: provider == 'groq')
    with pytest.raises(ai_provider.AIUnavailable, match='institutional policy'):
        ai_provider.speak('A synthetic audio check.')
    assert calls == []


def test_groq_limit_is_checked_before_creating_request(groq_speech):
    calls, _ = groq_speech
    with pytest.raises(ValueError, match='2000 characters'):
        ai_provider.speak('x' * 2001)
    assert calls == []


@pytest.mark.parametrize('text,voice,fmt', [(None, None, 'mp3'), ('hello', {}, 'mp3'),
                                         ('hello', None, 'json'), ('   ', None, 'mp3')])
def test_malformed_speech_inputs_do_not_reach_provider(groq_speech, text, voice, fmt):
    calls, _ = groq_speech
    with pytest.raises(ValueError):
        ai_provider.speak(text, voice=voice, fmt=fmt)
    assert calls == []


def test_missing_keys_report_configuration_without_provider_call(groq_speech, monkeypatch):
    calls, _ = groq_speech
    monkeypatch.setattr(ai_provider, 'groq_audio_key', lambda: None)
    with pytest.raises(ai_provider.AIUnavailable, match='No speech backend configured'):
        ai_provider.speak('A synthetic audio check.')
    assert calls == []


def test_capabilities_and_picker_match_groq_provider(groq_speech):
    assert ai_provider.speech_capabilities() == {
        'provider': 'groq', 'configured': True, 'speech_max_chars': 2000,
        'provider_max_chars': 200, 'speech_timeout_ms': 45000}
    voices = ai_provider.speech_voices()
    assert {v['id'] for v in voices} == set(ai_provider._GROQ_ENGLISH_VOICES)
    assert [v['id'] for v in voices if v['default']] == ['hannah']


def test_capabilities_and_picker_match_fish_provider(groq_speech, monkeypatch):
    monkeypatch.setattr(ai_provider, 'fish_audio_key', lambda: 'synthetic')
    assert ai_provider.speech_capabilities() == {
        'provider': 'fish_audio', 'configured': True,
        'speech_max_chars': ai_provider.MAX_SPEECH_CHARS,
        'provider_max_chars': ai_provider.MAX_SPEECH_CHARS, 'speech_timeout_ms': 75000}
    assert {v['id'] for v in ai_provider.speech_voices()} == {v['id'] for v in ai_provider.FISH_VOICES}


def test_no_provider_does_not_offer_fake_cloud_voice_names(groq_speech, monkeypatch):
    monkeypatch.setattr(ai_provider, 'groq_audio_key', lambda: None)
    assert ai_provider.speech_capabilities()['configured'] is False
    assert ai_provider.speech_capabilities()['provider'] == 'browser'
    assert ai_provider.speech_voices() == []


def test_provider_denial_is_distinguishable_from_availability(groq_speech, monkeypatch):
    monkeypatch.setattr(enforcement, 'permitted_processing', lambda provider: False)
    with pytest.raises(ai_provider.AIProcessingDenied):
        ai_provider.speak('Read this.')


def wave_clip(frame=b'\x01\x00', rate=24000):
    import io
    import wave
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(frame * 24)
    return output.getvalue()


def test_long_logical_audio_action_splits_provider_calls_and_joins_pcm(groq_speech, monkeypatch):
    import io
    import wave
    calls, _ = groq_speech
    def synthesize(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(read=lambda: wave_clip())
    monkeypatch.setattr(ai_provider, '_groq_client', lambda key: SimpleNamespace(
        audio=SimpleNamespace(speech=SimpleNamespace(create=synthesize))))
    text = 'A meaningful sentence. ' * 80
    audio, mime = ai_provider.speak(text)
    assert mime == 'audio/wav'
    assert len(calls) > 1 and all(len(call['input']) <= 200 for call in calls)
    from collections import Counter
    assert Counter(call['input'] for call in calls) == Counter(ai_provider._speech_chunks(text.strip()))
    with wave.open(io.BytesIO(audio), 'rb') as result:
        assert result.getnframes() == len(calls) * 24
        assert result.getframerate() == 24000


def test_long_words_cannot_inflate_provider_fanout():
    chunks = ai_provider._speech_chunks(('x' * 101 + ' ') * 19)
    assert len(chunks) <= 12
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert ''.join(chunks).replace(' ', '') == 'x' * (101 * 19)


def test_wave_join_preserves_order_and_rejects_changed_format():
    import io
    import wave
    combined = ai_provider._join_speech_waves([wave_clip(b'\x01\x00'), wave_clip(b'\x02\x00')])
    with wave.open(io.BytesIO(combined), 'rb') as result:
        assert result.readframes(48) == b'\x01\x00' * 24 + b'\x02\x00' * 24
    with pytest.raises(RuntimeError, match='incompatible audio'):
        ai_provider._join_speech_waves([wave_clip(), wave_clip(rate=16000)])
    with pytest.raises(RuntimeError, match='incompatible audio'):
        ai_provider._join_speech_waves([b'not a wave'])


def test_http_processing_denial_is_403_and_capabilities_match_picker(groq_speech, monkeypatch):
    from flask import Flask
    from flask_login import LoginManager
    import chatbot_api
    app = Flask(__name__)
    app.secret_key = 'synthetic-speech-contract'
    LoginManager(app)
    app.login_manager.user_loader(lambda user_id: None)
    app.register_blueprint(chatbot_api.chatbot_bp)
    monkeypatch.setattr(chatbot_api.ai_firewall, 'guard', lambda *a, **kw: object())
    def deny(*a, **kw):
        raise ai_provider.AIProcessingDenied('institutional policy')
    monkeypatch.setattr(chatbot_api, 'ai_speak', deny)
    with app.test_client() as client:
        denied = client.post('/api/tutor/speak', json={'text': 'Synthetic explanation'})
        assert denied.status_code == 403
        assert denied.json['error'] == 'processing_denied'
        capabilities = client.get('/api/tutor/voices').json
        assert capabilities['speech_max_chars'] == 2000
        assert capabilities['provider'] == 'groq'
        assert {v['id'] for v in capabilities['voices']} == set(ai_provider._GROQ_ENGLISH_VOICES)
