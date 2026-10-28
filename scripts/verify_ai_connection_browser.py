"""Isolated signup/setup/sidebar acceptance. Never contacts production."""
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='ui-cleanup-test-only',
                  APP_BASE_URL='https://ui.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App
from playwright.sync_api import sync_playwright

App.app.config['TESTING'] = True
App.limiter.enabled = False
App.send_welcome_email_on_signup = lambda uid: None
App._grant_referral_bonus = lambda user: None
App.check_recaptcha = lambda action: None
App.app.intelliplan_assignment_fetcher = lambda uid: []
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='cleanup@example.test', name='Student', role='student', birth_year=2005,
                    parent_consent_granted=True, password_hash='')
    App.db.session.add(user)
    App.db.session.flush()
    uid = user.id
    App._get_or_create_identity(uid).completed = False
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    App.db.session.commit()
guest = App.app.test_client()
student = App.app.test_client()
with student.session_transaction() as session:
    session.update(_user_id=str(uid), _fresh=True, session_epoch=0)
active = guest
OUT = ROOT / 'output/playwright/ui-cleanup'
OUT.mkdir(parents=True, exist_ok=True)
report = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={'width':1440,'height':1000}, service_workers='block')
    context.grant_permissions(['clipboard-read', 'clipboard-write'], origin='https://ui.test')
    context.add_init_script("localStorage.setItem('ip_tour_done','1');localStorage.setItem('ip_side_mode','expanded');")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(handler):
        req = handler.request
        parsed = urlsplit(req.url)
        if parsed.hostname != 'ui.test':
            return handler.abort()
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(),content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)
        response = active.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
            method=req.method, data=req.post_data, content_type=req.headers.get('content-type'), follow_redirects=True)
        headers = {k:v for k,v in response.headers.items() if k.lower() not in {'set-cookie','content-length','content-encoding'}}
        handler.fulfill(status=response.status_code,headers=headers,body=response.data)
    page.route('**/*',route)
    def open_page(actor, path):
        global active
        active = actor
        errors.clear()
        response = page.goto('https://ui.test'+path,wait_until='domcontentloaded')
        assert response.status == 200
        essential = page.get_by_role('button',name='Essential only',exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_timeout(200)
    def capture(slug):
        assert not errors, (slug, errors)
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (slug,'overflow')
        page.screenshot(path=str(OUT/f'{slug}-{page.viewport_size["width"]}.png'),full_page=True)
        report.append({'page':slug,'width':page.viewport_size['width'],'errors':[],'overflow':False})
    os.environ['BYOK_ENABLED'] = '1'
    for width in (1440,390):
        page.set_viewport_size({'width':width,'height':1000})
        open_page(student,'/settings/ai')
        page.get_by_role('heading',name='Use your ChatGPT plan',exact=True).wait_for()
        assert 'OpenAI must approve this hosted app' in page.locator('.pai').inner_text()
        assert page.locator('#paiOpenAIRedirect').inner_text() == 'https://intelliplan.tech/auth/openai/callback'
        assert 'not active yet' in page.locator('#paiOpenAIRedirectStatus').inner_text()
        copy_button = page.get_by_role('button',name='Copy redirect URI',exact=True)
        assert copy_button.is_visible()
        assert copy_button.bounding_box()['height'] >= 44
        copy_button.click()
        assert page.evaluate('navigator.clipboard.readText()') == 'https://intelliplan.tech/auth/openai/callback'
        assert 'Copied.' in page.locator('#paiOpenAIRedirectStatus').inner_text()
        assert page.get_by_text('OpenAI API',exact=True).is_visible()
        assert page.get_by_role('button',name='Continue with ChatGPT',exact=True).count() == 0
        capture('chatgpt-availability')
        open_page(guest,'/register')
        page.evaluate('() => { document.activeElement.blur(); window.scrollTo(0,0); }')
        page.wait_for_timeout(300)
        capture('signup-clean')
    browser.close()
print(json.dumps({'checks':len(report),'output':str(OUT)},indent=2))
