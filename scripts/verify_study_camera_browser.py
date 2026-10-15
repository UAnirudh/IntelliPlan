"""Rendered camera workflows with fake capture and disposable Flask+SQLite.

Runs independently of production credentials or storage. Requires Playwright
and its Chromium runtime; STUDY_CAMERA_BROWSER_CHANNEL may select local Chrome.
"""
import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='camera-browser-fixture-only',
                  APP_BASE_URL='https://camera.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402 -- isolate storage before importing the application.

OUT = Path(os.getenv('STUDY_CAMERA_OUTPUT', str(ROOT / '.codex/verification/study-camera-browser')))
OUT.mkdir(parents=True, exist_ok=True)
App.limiter.enabled = False
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='camera-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    App.db.session.commit()

def html(mobile):
    with App.app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(uid)
            session['_fresh'] = True
        response = client.get('/active?view=' + ('phone' if mobile else 'desktop'))
        assert response.status_code == 200
        return response.get_data(as_text=True)

api_client = App.app.test_client()
with api_client.session_transaction() as session:
    session['_user_id'] = str(uid)
    session['_fresh'] = True

with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None,
                               args=['--use-fake-device-for-media-stream',
                                      '--use-fake-ui-for-media-stream'])
    for mobile in (False, True):
        with App.app.app_context():
            App.ActiveFocusSample.query.delete()
            App.ActiveSession.query.delete()
            App.db.session.commit()
        context = browser.new_context(viewport={'width': 390 if mobile else 1280, 'height': 900})
        # The fake Chromium device can emit native changes during startup. This
        # scenario owns device-change timing so its deliberately reversed lists
        # are not superseded by an unrelated native notification.
        context.add_init_script('''
            const listen = navigator.mediaDevices.addEventListener.bind(navigator.mediaDevices);
            navigator.mediaDevices.addEventListener = (name, callback, options) => {
                if (name === 'devicechange') {
                    listen(name, event => { if (!event.isTrusted) callback(event); }, options);
                } else { listen(name, callback, options); }
            };
        ''')
        page = context.new_page()
        def toggle(enabled):
            if page.locator('#ipaFocusToggle').is_checked() != enabled:
                page.locator('label[for="ipaFocusToggle"]').click()
        rendered = html(mobile)
        assert ('id="ipaControlBar"' in rendered) == mobile
        requests = []
        errors = []
        held_permissions = []
        hold_permission = [True]
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(handler):
            request = handler.request
            parsed = urlsplit(request.url)
            if parsed.hostname != 'camera.test':
                return handler.abort()
            if parsed.path == '/active':
                return handler.fulfill(status=200, content_type='text/html', body=rendered)
            if parsed.path.startswith('/static/'):
                path = (ROOT / parsed.path.lstrip('/')).resolve()
                if path.is_relative_to(ROOT / 'static') and path.is_file():
                    return handler.fulfill(status=200, body=path.read_bytes(),
                                           content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                return handler.abort()
            if parsed.path.startswith('/api/'):
                requests.append((parsed.path, request.post_data))
                if parsed.path == '/api/active/focus-permission' and hold_permission[0]:
                    held_permissions.append(handler)
                    return
                if parsed.path.startswith('/api/active/') and parsed.path != '/api/active/next':
                    response = api_client.open(parsed.path, method=request.method,
                                               data=request.post_data, content_type='application/json')
                    return handler.fulfill(status=response.status_code, content_type='application/json',
                                           body=response.get_data(as_text=True))
                body = {'status': 'ok'}
                plan = {'title': 'Algebra practice', 'course': 'Math', 'planned_minutes': 25,
                        'block_id': 'fixture-block', 'task_id': 'fixture-task'}
                if parsed.path == '/api/active/next':
                    body = {'next': plan, 'has_plan': True}
                if parsed.path == '/api/focus/enforcement':
                    body = {'mode': 'off'}
                return handler.fulfill(status=200, content_type='application/json', body=json.dumps(body))
            return handler.abort()
        page.route('**/*', route)
        page.goto('https://camera.test/active', wait_until='networkidle')
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.locator('#ipaStart').wait_for(state='visible')
        # Wait for the menu's first asynchronous toggle/enumeration to finish
        # before replacing the list provider for the reversed-response case.
        page.evaluate('''() => {
            window.originalCameras = window.IPFocus.cameras;
            window.initialCameraListReady = false;
            window.IPFocus.cameras = async () => {
                const devices = await window.originalCameras();
                window.initialCameraListReady = true;
                return devices;
            };
        }''')
        page.locator('#ipaCameraMenu summary').click()
        page.wait_for_function('window.initialCameraListReady === true')
        page.wait_for_function("document.querySelector('#ipaCameraSource').options.length >= 2")
        assert page.evaluate('window.IPFocus.activeCamera() === null')
        # Delayed, out-of-order device lists must preserve the latest choice.
        page.evaluate('''() => {
            window.cameraLists = [];
            window.IPFocus.cameras = () => new Promise(resolve => window.cameraLists.push(resolve));
            navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
            navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
        }''')
        page.wait_for_function('window.cameraLists.length === 2')
        page.locator('#ipaCameraSource').select_option('rear')
        page.evaluate('''() => {
            window.cameraLists[1]([{deviceId: 'latest', label: 'Latest camera'}]);
        }''')
        page.wait_for_function("document.querySelector('#ipaCameraSource').options.length === 3")
        page.evaluate("window.cameraLists[0]([{deviceId: 'stale', label: 'Stale camera'}])")
        assert page.locator('#ipaCameraSource').input_value() == 'rear'
        assert page.locator('#ipaCameraSource').inner_text().find('Stale camera') == -1
        page.evaluate('window.IPFocus.cameras = window.originalCameras')
        page.locator('#ipaCameraSource').select_option('')
        toggle(True)
        assert page.evaluate('window.IPFocus.activeCamera() === null')
        page.locator('#ipaStart').click()
        page.wait_for_function("document.querySelector('#ipaPause').hidden === false")
        for _ in range(20):
            if held_permissions:
                break
            page.wait_for_timeout(100)
        assert held_permissions
        toggle(False)
        hold_permission[0] = False
        for pending in held_permissions:
            response = api_client.get('/api/active/focus-permission')
            pending.fulfill(status=200, content_type='application/json', body=response.get_data(as_text=True))
        assert page.evaluate('window.IPFocus.activeCamera() === null')
        toggle(True)
        page.wait_for_function('window.IPFocus.activeCamera() !== null')
        page.wait_for_timeout(6500)
        page.locator('#ipaPause').click()
        assert page.evaluate('window.IPFocus.activeCamera() === null')
        assert 'paused' in page.locator('#ipaFocusNote').inner_text()
        before_pause = api_client.get('/api/active/current').get_json()['session']['focus']['samples']
        assert before_pause >= 2, before_pause
        page.locator('#ipaPause').click()
        page.wait_for_function('window.IPFocus.activeCamera() !== null')
        page.wait_for_timeout(4500)
        page.locator('#ipaSelfViewBtn').click()
        page.wait_for_function("document.querySelector('#ipaSelfView').srcObject !== null")
        page.wait_for_function("document.querySelector('#ipaCameraSource').options.length > 2")
        page.locator('#ipaCameraSource').select_option(index=2)
        page.wait_for_function('window.IPFocus.activeCamera() !== null')
        toggle(False)
        page.wait_for_timeout(200)
        after_resume = api_client.get('/api/active/current').get_json()['session']['focus']['samples']
        assert after_resume > before_pause, (before_pause, after_resume)
        page.locator('#ipaCameraMenu summary').click()
        page.wait_for_function('window.IPFocus.activeCamera() === null')
        assert page.evaluate("document.querySelector('#ipaSelfView').srcObject === null")
        toggle(True)
        page.wait_for_function('window.IPFocus.activeCamera() !== null')
        page.evaluate("window.savedCameraTrack = window.IPFocus.activeCamera().stream.getVideoTracks()[0]")
        page.evaluate("window.savedCameraTrack.dispatchEvent(new Event('ended'))")
        page.wait_for_function("!document.querySelector('#ipaFocusToggle').checked")
        assert page.evaluate('window.IPFocus.activeCamera() === null')
        toggle(True)
        page.wait_for_function('window.IPFocus.activeCamera() !== null')
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
        assert page.evaluate('window.IPFocus.activeCamera() === null')
        assert not page.locator('#ipaFocusToggle').is_checked()
        assert 'stopped when you left' in page.locator('#ipaFocusNote').inner_text()
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
        toggle(True)
        page.wait_for_function('window.IPFocus.activeCamera() !== null')
        toggle(False)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        if not page.locator('#ipaCameraMenu').evaluate('element => element.open'):
            page.locator('#ipaCameraMenu summary').click()
        page.screenshot(path=str(OUT / f'camera-{ "phone" if mobile else "desktop" }.png'), full_page=True)
        page.locator('#ipaDone').click()
        page.locator('#ipaFinishSave').click()
        page.wait_for_function("document.querySelector('#ipaFinish').hidden === true")
        with App.app.app_context():
            saved = App.ActiveSession.query.filter_by(user_id=uid).one()
            assert saved.state == 'completed'
            assert saved.active_seconds >= 8, saved.active_seconds
            assert saved.focus_samples >= after_resume
            saved_seconds = saved.active_seconds
        assert page.evaluate('window.IPFocus.activeCamera() === null')
        for _, body in requests:
            assert not body or all(token not in body for token in ('deviceId', 'data:image', 'video/webm'))
        assert not errors, errors
        print(json.dumps({'layout': 'phone' if mobile else 'desktop', 'passed': True,
                          'camera': 'Chromium fake capture', 'api': 'disposable Flask+SQLite',
                          'counts_before_pause': before_pause, 'counts_after_resume': after_resume,
                          'saved_active_seconds': saved_seconds,
                          'page_errors': errors}))
        context.close()
    browser.close()
