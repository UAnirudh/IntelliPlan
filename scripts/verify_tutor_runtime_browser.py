"""Exercise actual tutor rendering and native audio using isolated fixture WAVs.

The provider is replaced with generated PCM audio; HTMLAudioElement decoding,
playback, real Flask speech authorization, and the client queue remain real.
This proves browser integration, not the sound of a deployed provider voice.
"""
import io
import json
import math
import mimetypes
import os
import struct
import sys
import wave
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='tutor-runtime-fixture',
                  APP_BASE_URL='https://runtime.test', FLASK_ENV='testing', AI_FREE_RPH='100',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402
import chatbot_api  # noqa: E402
from intelliplan.learning import policy  # noqa: E402

App.limiter.enabled = False
App.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
OUT = ROOT / 'output/playwright/tutor-runtime'
OUT.mkdir(parents=True, exist_ok=True)
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='runtime-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    M = policy.models()
    organization = M['Organization'](name='Runtime fixture', kind='district', policy={})
    App.db.session.add(organization)
    App.db.session.flush()
    organization_id = organization.id
    App.db.session.add(M['Membership'](user_id=uid, organization_id=organization_id, role='student'))
    App.db.session.commit()

speech_requests = []
transcription_requests = []


def fixture_speech(text, **_options):
    speech_requests.append(text)
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b''.join(struct.pack('<h', int(1800 * math.sin(2 * math.pi * 440 * index / 16000)))
                                   for index in range(1920)))
    return output.getvalue(), 'audio/wav'


chatbot_api.ai_speak = fixture_speech


def fixture_transcribe(name, data):
    transcription_requests.append({'name': name, 'bytes': len(data)})
    return 'Explain the relationship in my recorded question.'


chatbot_api.ai_transcribe = fixture_transcribe
client = App.app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(uid)
    session['_fresh'] = True

markdown = (r'#### Check the relationship' + '\n\n' + r'Area is \(A=\pi r^2\).'
            + '\n\n' + r'\[\frac{1}{2}+\frac{1}{4}=\frac{3}{4}\]' + '\n\n'
            + 'A notebook costs $5 and a pen costs $10.\n\n'
            + '```python\nprint("$x$")\n```\n\n'
            + '<img src=x onerror="window.__xss=true"><script>window.__xss=true</script>')
reply = '```json\n' + json.dumps({'response': {'content': [{'type': 'text', 'text': markdown}]}}) + '\n```'
report = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=['--autoplay-policy=no-user-gesture-required',
                                              '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000},
                                  permissions=['clipboard-read', 'clipboard-write', 'microphone'], service_workers='block')
    context.add_init_script("""localStorage.setItem('ip_tour_done','1');
      window.__nativePlayed=0; window.__nativeEnded=0; window.__nativeErrors=0; window.__fallbackCalls=0;
      const NativeAudio=window.Audio;
      window.Audio=function(...args){const audio=new NativeAudio(...args);
        audio.addEventListener('playing',()=>window.__nativePlayed++);
        audio.addEventListener('ended',()=>window.__nativeEnded++);
        audio.addEventListener('error',()=>window.__nativeErrors++); return audio;};
      if(window.speechSynthesis){const speak=window.speechSynthesis.speak.bind(window.speechSynthesis);
        window.speechSynthesis.speak=function(value){window.__fallbackCalls++;return speak(value);};}
    """)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(handler):
        request = handler.request
        parsed = urlsplit(request.url)
        if parsed.hostname != 'runtime.test':
            return handler.abort()
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)
        if parsed.path == '/api/tutor' and request.method == 'POST':
            return handler.fulfill(json={'reply': reply})
        if parsed.path == '/api/tutor/voices':
            return handler.fulfill(json={'voices': [], 'speech_max_chars': 2000,
                                         'speech_timeout_ms': 45000, 'provider': 'fixture', 'configured': True})
        response = client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                               method=request.method, data=request.post_data_buffer,
                               content_type=request.headers.get('content-type'))
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
        handler.fulfill(status=response.status_code, headers=headers, body=response.data)

    page.route('**/*', route)
    assert page.goto('https://runtime.test/tutor', wait_until='domcontentloaded').status == 200
    consent = page.get_by_role('button', name='Essential only', exact=True)
    if consent.is_visible():
        consent.click()
    page.locator('#tutorInput').fill('Help me compare these fractions and the area formula.')
    page.locator('#tutorSendBtn').click()
    bubble = page.locator('.tutor-msg-group.bot .tutor-bubble').last
    expect(bubble.locator('h4')).to_have_text('Check the relationship')
    expect(bubble.locator('.katex')).to_have_count(2)
    expect(bubble.locator('pre')).to_contain_text('print("$x$")')
    expect(bubble).to_contain_text('A notebook costs $5 and a pen costs $10.')
    assert bubble.locator('img,script,a[href^="javascript:"]').count() == 0
    page.get_by_role('button', name='Copy this reply', exact=True).click()
    copied = page.evaluate('navigator.clipboard.readText()')
    assert copied.startswith('Check the relationship') and 'print("$x$")' in copied
    assert '<script>' not in copied and 'onerror=' not in copied and '```json' not in copied
    assert page.evaluate("IPRichText.speech('## Heading\\n\\nFirst paragraph.\\n\\n- Second item\\n- Third item')") == 'Heading First paragraph. Second item Third item'
    assert page.evaluate("IPRichText.normalize({choices:[{message:{content:[{type:'text',text:'First'},{type:'text',text:'Second'}]}}]})") == 'First\n\nSecond'
    report.append({'flow': 'send question, fenced nested JSON, math, currency, code, sanitization and copy', 'passed': True})

    text = ('Read this complete explanation. Then check the relationship yourself. ').strip() + ' '
    text = text * 70 + 'The final sentence must be read too.'
    page.evaluate('async text => {ttsEnabled=true; await speakReply(text);}', text)
    page.wait_for_function("document.getElementById('tutorAudioFeedback').textContent === 'Finished reading.'", timeout=30000)
    assert len(speech_requests) > 1 and all(len(part) <= 2000 for part in speech_requests)
    assert ' '.join(speech_requests) == text
    audio_report = page.evaluate('({played:__nativePlayed,ended:__nativeEnded,errors:__nativeErrors,fallback:__fallbackCalls})')
    assert audio_report['ended'] == len(speech_requests) and audio_report['errors'] == 0 and audio_report['fallback'] == 0, audio_report
    report.append({'flow': 'full reply native PCM WAV decode and sequential playback', 'chunks': len(speech_requests), **audio_report})

    before = len(speech_requests)
    page.evaluate('async text => {await speakReply(text); stopSpeaking();}', text)
    page.wait_for_timeout(350)
    assert len(speech_requests) == before + 1
    report.append({'flow': 'Stop cancels native playback and remaining chunks', 'passed': True})

    # Use an actual browser microphone stream/MediaRecorder backed by Chromium's
    # synthetic device. No native SpeechRecognition service is involved.
    page.locator('#tutorInput').fill('')
    page.locator('#micBtn').click()
    expect(page.locator('#micBtn')).to_have_attribute('aria-pressed', 'true')
    page.wait_for_timeout(200)
    page.locator('#micBtn').click()
    expect(page.locator('#tutorInput')).to_have_value('Explain the relationship in my recorded question.')
    assert len(transcription_requests) == 1 and transcription_requests[0]['bytes'] > 0
    assert transcription_requests[0]['name'].endswith(('.webm', '.m4a', '.ogg', '.wav'))
    expect(page.locator('#micBtn')).to_have_attribute('aria-pressed', 'false')
    assert page.evaluate("!mediaRecorder && !isRecording && !transcriptionPending")
    report.append({'flow': 'native MediaRecorder routes microphone input through authorized Flask transcription', **transcription_requests[0]})

    with App.app.app_context():
        organization = App.db.session.get(M['Organization'], organization_id)
        organization.policy = {'tutor': False}
        App.db.session.commit()
    played = page.evaluate('__nativePlayed')
    page.evaluate("speakReply('This must be denied by school policy.')")
    expect(page.locator('#tutorAudioFeedback')).to_contain_text('school settings')
    assert len(speech_requests) == before + 1
    assert page.evaluate('__nativePlayed') == played and page.evaluate('__fallbackCalls') == 0
    report.append({'flow': 'real Flask policy denial produces neither server audio nor browser fallback', 'passed': True})

    for width in (1440, 390):
        page.set_viewport_size({'width': width, 'height': 900})
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        page.screenshot(path=str(OUT / f'tutor-formatted-{width}.png'), full_page=True)
    assert not errors, errors
    browser.close()
(OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'passed': True, 'flows': len(report), 'report': str(OUT / 'report.json')}))
