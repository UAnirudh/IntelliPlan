"""Opt-in real study tutor browser/provider check with a synthetic account only."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-live', action='store_true')
args = parser.parse_args()
if not args.run_live:
    parser.error('--run-live is required; this makes two external tutor requests')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='study-safety-disposable',
    APP_BASE_URL='https://study.test', FLASK_ENV='testing', RESEND_API_KEY='',
    SENTRY_DSN='', POSTHOG_API_KEY='', NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App
App.limiter.enabled = False
App.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='study-safety@example.test', birth_year=2000, password_hash='x', ai_personalization_opt_in=False)
    App.db.session.add(user)
    App.db.session.flush()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    App.db.session.commit()
client = App.app.test_client()
with client.session_transaction() as session:
    session.update(_user_id=str(uid), _fresh=True, session_epoch=0)
results = []

def route(handler):
    req = handler.request
    url = urlsplit(req.url)
    if url.hostname != 'study.test':
        return handler.abort()
    if url.path.startswith('/static/'):
        file = (ROOT / url.path.lstrip('/')).resolve()
        if file.is_relative_to(ROOT / 'static') and file.is_file():
            return handler.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
        return handler.fulfill(status=404)
    response = client.open(url.path + ('?' + url.query if url.query else ''),
        method=req.method, data=req.post_data, content_type=req.headers.get('content-type'), follow_redirects=True)
    if url.path == '/api/tutor' and req.method == 'POST':
        data = response.get_json() or {}
        results.append({'status':response.status_code, 'error':data.get('error'),
                        'reply_present':bool(data.get('reply')), 'reply':data.get('reply')})
    headers = {k: v for k, v in response.headers.items()
               if k.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
    handler.fulfill(status=response.status_code, headers=headers, body=response.data)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={'width':width, 'height':1000}, service_workers='block')
        page.add_init_script("localStorage.setItem('ip_tour_done','1')")
        page.route('**/*', route)
        page.goto('https://study.test/study-and-learn')
        consent = page.get_by_role('button', name='Essential only', exact=True)
        if consent.is_visible():
            consent.click()
        prompt = ('My biology notes say that plants convert light into stored chemical energy. ' * 75
                  + 'Help me understand photosynthesis. Ask me one question to get started.'
                  if width == 1440 else 'Help me understand a quadratic equation. Ask me a question.')
        page.locator('#tutorInput').fill(prompt)
        with page.expect_response(lambda r: r.url.endswith('/api/tutor') and r.request.method == 'POST', timeout=120000):
            page.locator('#tutorSendBtn').click()
        result = results[-1]
        assert result['status'] == 200 and not result['error'] and result['reply_present'], result
        assert 'safety check is temporarily unavailable' not in result['reply']
        page.wait_for_function("!document.getElementById('tutorSendBtn').disabled")
        bubble = page.locator('.tutor-msg-group.bot .tutor-bubble').last
        assert bubble.inner_text().strip()
        assert 'safety check is temporarily unavailable' not in bubble.inner_text()
        report = {'width':width, 'status':result['status'], 'reply_present':True}
        print(json.dumps(report), flush=True)
        page.close()
    browser.close()
