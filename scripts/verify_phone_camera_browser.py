"""Real WebRTC video between isolated browsers, with disposable signaling DB.

Chromium supplies synthetic camera frames; no physical phone/network is proven.
No provider credentials, external ICE service or production storage is used.
"""
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='phone-camera-browser-fixture',
                  DATA_ENCRYPTION_KEY=Fernet.generate_key().decode(), APP_BASE_URL='https://camera.test',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402 -- disposable storage before app import.

OUT = Path(os.getenv('PHONE_CAMERA_OUTPUT', str(ROOT / '.codex/verification/phone-camera-browser')))
OUT.mkdir(parents=True, exist_ok=True)
App.limiter.enabled = False
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='phone-camera-browser@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    App.db.session.commit()


def client():
    c = App.app.test_client()
    with c.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    return c


requests = []
phone_external = []
errors = []


def routes(page, api, phone=False):
    def route(handler):
        req = handler.request
        parsed = urlsplit(req.url)
        if parsed.hostname != 'camera.test':
            if phone:
                phone_external.append(req.url)
            return handler.abort()
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(status=200, body=path.read_bytes(),
                                       content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.abort()
        if parsed.path in ('/active', '/study-camera') or parsed.path.startswith('/api/active/') and parsed.path != '/api/active/next':
            headers = {k: v for k, v in req.headers.items() if k.lower() in ('x-camera-key', 'origin')}
            response = api.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                                method=req.method, headers=headers, data=req.post_data,
                                content_type='application/json' if req.method == 'POST' else None)
            if parsed.path.startswith('/api/'):
                requests.append((parsed.path, req.post_data or ''))
            return handler.fulfill(status=response.status_code, body=response.get_data(),
                                   content_type=response.content_type)
        if parsed.path.startswith('/api/'):
            data = {'status': 'ok'}
            if parsed.path == '/api/active/next':
                data = {'next': {'title': 'Synthetic camera study session', 'course': 'Math',
                                 'planned_minutes': 25, 'block_id': 'test', 'task_id': 'test'},
                        'has_plan': True}
            if parsed.path == '/api/focus/enforcement':
                data = {'mode': 'off'}
            return handler.fulfill(status=200, content_type='application/json', body=json.dumps(data))
        return handler.abort()
    page.route('**/*', route)
    page.on('pageerror', lambda err: errors.append(str(err)))


with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None,
                               args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
    receiver_context = browser.new_context(viewport={'width': 1280, 'height': 900})
    phone_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    receiver = receiver_context.new_page()
    phone = phone_context.new_page()
    routes(receiver, client())
    routes(phone, client(), phone=True)
    receiver.goto('https://camera.test/active', wait_until='networkidle')
    essential = receiver.get_by_role('button', name='Essential only', exact=True)
    if essential.is_visible():
        essential.click()
    receiver.locator('#ipaStart').wait_for(state='visible')
    receiver.locator('#ipaCameraMenu summary').click()
    receiver.locator('#ipaCameraSource').select_option('phone')
    receiver.locator('label[for="ipaFocusToggle"]').click()
    receiver.locator('#ipaStart').click()
    receiver.wait_for_function("document.querySelector('#ipaPhoneLink').value.includes('#')")
    invite = receiver.locator('#ipaPhoneLink').input_value()
    assert receiver.evaluate('window.IPFocus.activeCamera() === null')
    phone.goto(invite, wait_until='networkidle')
    assert phone.evaluate('location.hash') == ''
    assert phone.evaluate("document.querySelector('#cameraPreview').srcObject === null")
    phone.locator('#cameraConnect').click()
    receiver.wait_for_function('window.IPFocus.activeCamera() !== null', timeout=30000)
    phone.wait_for_function("document.querySelector('#cameraStatus').textContent.includes('Sharing video')", timeout=30000)
    receiver.wait_for_function('window.IPFocus.activeCamera().video.videoWidth > 0')
    assert receiver.evaluate('window.IPFocus.activeCamera().stream.getAudioTracks().length') == 0
    receiver.wait_for_timeout(4500)
    received = receiver.evaluate('''async () => {
        const camera = window.IPFocus.activeCamera();
        const reports = await camera.remote.pc.getStats();
        return [...reports.values()].filter(r => r.type === 'inbound-rtp' && r.kind === 'video')
          .reduce((total, r) => total + (r.framesDecoded || 0), 0);
    }''')
    assert received > 0, 'must decode real synthetic camera video, not just exchange SDP'
    receiver.screenshot(path=str(OUT / 'paired-desktop.png'), full_page=True)
    phone.screenshot(path=str(OUT / 'sharing-phone.png'), full_page=True)
    phone.locator('#cameraStop').click()
    receiver.wait_for_function('window.IPFocus.activeCamera() === null', timeout=15000)
    receiver.wait_for_function("!document.querySelector('#ipaFocusToggle').checked")
    assert phone.evaluate("document.querySelector('#cameraPreview').srcObject === null")
    assert not phone.locator('#cameraStop').is_visible()
    # The ordinary study timer keeps running after the optional camera stops.
    assert receiver.locator('#ipaPause').is_visible()
    assert not receiver.locator('#ipaPhoneInvite').is_visible()
    receiver.locator('label[for="ipaFocusToggle"]').click()
    receiver.wait_for_function("document.querySelector('#ipaPhoneLink').value.includes('#')")
    new_invite = receiver.locator('#ipaPhoneLink').input_value()
    assert new_invite != invite
    phone.goto(new_invite, wait_until='networkidle')
    phone.locator('#cameraConnect').click()
    try:
        receiver.wait_for_function('window.IPFocus.activeCamera() !== null', timeout=30000)
    except Exception:
        print(json.dumps({'reconnect_receiver_status': receiver.locator('#ipaFocusNote').inner_text(),
                          'reconnect_phone_status': phone.locator('#cameraStatus').inner_text(),
                          'phone_visibility': phone.evaluate('document.visibilityState')}))
        raise
    phone.wait_for_function("document.querySelector('#cameraStatus').textContent.includes('Sharing video')", timeout=30000)
    receiver.locator('label[for="ipaFocusToggle"]').click()
    phone.wait_for_function("document.querySelector('#cameraPreview').srcObject === null", timeout=15000)
    assert receiver.evaluate('window.IPFocus.activeCamera() === null')
    assert receiver.locator('#ipaPause').is_visible()
    with App.app.app_context():
        assert App.StudyCameraPair.query.count() == 0
    assert not phone_external, phone_external
    assert not errors, errors
    assert receiver.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
    for _, body in requests:
        assert all(value not in body for value in ('data:image', 'video/webm', 'm=audio ', 'deviceId'))
    result = {'passed': True, 'decoded_video_frames': received, 'audio_tracks': 0,
              'capture': 'Chromium synthetic camera', 'transport': 'real WebRTC between two isolated contexts',
              'signaling': 'disposable Flask+SQLite', 'phone_stop_revokes_receiver': True,
              'receiver_stop_revokes_phone': True, 'timer_survives_stop': True,
              'phone_external_requests': 0, 'page_errors': errors}
    (OUT / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    receiver_context.close()
    phone_context.close()
    browser.close()
