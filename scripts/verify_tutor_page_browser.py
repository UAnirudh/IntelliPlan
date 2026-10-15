"""Rendered signed-in tutor UI with scripted provider responses, no live calls."""
import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='tutor-ui-fixture-only',
                  APP_BASE_URL='https://tutor.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402 -- isolate storage before application import.

App.limiter.enabled = False
OUT = Path(os.getenv('TUTOR_BROWSER_OUTPUT', str(ROOT / '.codex/verification/tutor-page')))
OUT.mkdir(parents=True, exist_ok=True)
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='tutor-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    App.db.session.commit()

with App.app.test_client() as client:
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    response = client.get('/tutor')
    assert response.status_code == 200
    rendered = response.get_data(as_text=True)

reply = '''What do you predict will happen when the slope increases?
:::artifact{type="svg-exercise" title="Explore a line"}
{"kind":"linear","slope":1,"intercept":0,"question":"Try changing the slope, then explain what changed."}
:::
'''
with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None)
    for width in (1280, 390):
        context = browser.new_context(viewport={'width': width, 'height': 900})
        page = context.new_page()
        errors, sent, speech = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(handler):
            req = handler.request
            parsed = urlsplit(req.url)
            if parsed.hostname != 'tutor.test':
                return handler.abort()
            if parsed.path == '/tutor':
                return handler.fulfill(status=200, content_type='text/html', body=rendered)
            if parsed.path.startswith('/static/'):
                path = (ROOT / parsed.path.lstrip('/')).resolve()
                if path.is_relative_to(ROOT / 'static') and path.is_file():
                    return handler.fulfill(status=200, body=path.read_bytes(),
                                           content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                return handler.abort()
            if parsed.path.startswith('/api/'):
                body, status = {}, 200
                if parsed.path == '/api/tutor/conversations':
                    body = {'conversations': []}
                elif parsed.path == '/api/tutor/adaptive/profile':
                    body = {'profile': {}, 'modality': {'mode': 'auto'}}
                elif parsed.path == '/api/tutor/adaptive/modality':
                    sent.append((parsed.path, json.loads(req.post_data)))
                elif parsed.path == '/api/tutor':
                    sent.append((parsed.path, json.loads(req.post_data)))
                    body = {'reply': reply, 'clarify': True}
                elif parsed.path == '/api/tutor/speak':
                    speech.append(json.loads(req.post_data))
                    body, status = {'error': 'policy_denied'}, 403
                elif parsed.path == '/api/tutor/voices':
                    body = {'voices': [{'id': 'fixture', 'name': 'Fixture voice', 'default': True}]}
                return handler.fulfill(status=status, content_type='application/json', body=json.dumps(body))
            return handler.abort()

        page.route('**/*', route)
        page.goto('https://tutor.test/tutor', wait_until='networkidle')
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_function("document.querySelectorAll('.tutor-modality-btn').length === 5")
        if width == 390:
            page.get_by_role('button', name='Open chat history', exact=True).click()
        page.get_by_role('button', name='Auditory', exact=True).click()
        page.wait_for_function('ttsEnabled === true')
        if width == 390:
            page.evaluate('closeTutorDrawer()')
        page.locator('#tutorInput').fill('Help me understand slope.')
        page.locator('#tutorSendBtn').click()
        slider = page.get_by_role('slider', name='Slope', exact=True)
        slider.wait_for(state='visible')
        slider.focus()
        slider.press('ArrowRight')
        assert 'Each step right changes y by 1.5' in page.locator('#tutorMessages').inner_text()
        page.wait_for_function("document.querySelector('#tutorAudioStatus').textContent.includes('school settings')")
        assert len(speech) == 1
        assert ':::artifact' not in speech[0]['text']
        assert sent[-1][1]['modality_mode'] == 'auditory'
        page.get_by_role('button', name='Help me start', exact=True).click()
        page.wait_for_function("document.querySelectorAll('.tutor-artifact').length === 2")
        assert 'small hint' in sent[-1][1]['messages'][-1]['content']
        assert not page.get_by_role('button', name='Just answer', exact=True).count()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        page.screenshot(path=str(OUT / f'tutor-page-{width}.png'), full_page=True)
        print(json.dumps({'width': width, 'passed': True, 'rendered_signed_in_template': True,
                          'scripted_provider': True, 'audio_policy_denial': True, 'page_errors': errors}))
        context.close()
    browser.close()
