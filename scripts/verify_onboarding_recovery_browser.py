"""Browser regression: a real onboarding safety outage recovers without lost state."""
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='onboarding-test-only',
    FLASK_ENV='testing', NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App
import plani_safety
App.limiter.enabled = False
App.app.config['TESTING'] = True
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='onboarding@example.test', birth_year=2005, password_hash='x')
    App.db.session.add(user)
    App.db.session.flush()
    uid = user.id
    identity = App._get_or_create_identity(uid)
    identity.grade_level = '9th grade'
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    App.db.session.commit()
client = App.app.test_client()
with client.session_transaction() as session:
    session.update(_user_id=str(uid), _fresh=True, session_epoch=0)

def route(handler):
    req = handler.request
    url = urlsplit(req.url)
    if url.hostname != 'onboarding.test':
        return handler.abort()
    if url.path.startswith('/static/'):
        file = (ROOT / url.path.lstrip('/')).resolve()
        if file.is_relative_to(ROOT / 'static') and file.is_file():
            return handler.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
        return handler.fulfill(status=404)
    with patch.object(App, 'ai_available', return_value=True), patch.object(
            App, 'ai_chat', return_value='{}'), patch.object(
            plani_safety, 'classify', side_effect=plani_safety.SafetyUnavailable('rate limited')):
        response = client.open(url.path + ('?' + url.query if url.query else ''),
            method=req.method, data=req.post_data, content_type=req.headers.get('content-type'), follow_redirects=True)
    headers = {k: v for k, v in response.headers.items()
               if k.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
    handler.fulfill(status=response.status_code, headers=headers, body=response.data)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={'width': width, 'height': 950})
        page.route('**/*', route)
        with client.session_transaction() as session:
            session['onb_messages'] = [{'role':'assistant','content':'What subjects?'},
                                       {'role':'user','content':'Chemistry'}]
            session['onb_step'] = 'intro'
        page.goto('https://onboarding.test/onboarding')
        page.evaluate("ONB.messages = []; onbPickMode('custom')")
        page.locator('#onbQuickNotice').wait_for(state='visible')
        assert page.locator('#onbQuick').is_visible()
        assert page.url == 'https://onboarding.test/onboarding'
        # Exercise mid-conversation as well as bootstrap, using the real endpoint.
        page.evaluate("ONB.messages = [{role:'assistant',content:'What subjects?'}]; onbShow('custom')")
        page.locator('#onbInput').fill('Chemistry')
        page.locator('#onbSendBtn').click()
        page.locator('#onbQuickNotice').wait_for(state='visible')
        page.wait_for_function("ONB.messages.length === 2")
        state = client.get('/api/onboarding/state').get_json()
        assert state['messages'][-1]['content'] == 'Chemistry', state
        page.locator('#onbQuickBtn').click()
        page.locator('#onbImport').wait_for(state='visible')
        page.reload()
        page.locator('#onbImport').wait_for(state='visible')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        print(json.dumps({'width':width, 'bootstrap_recovery':True,
            'mid_chat_preserved':True, 'refresh_resumes_import':True}))
        page.close()
    browser.close()
