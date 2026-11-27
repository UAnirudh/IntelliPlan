"""Isolated signup/setup/sidebar acceptance. Never contacts production."""
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urljoin, urlsplit

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
    browser = playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
    context = browser.new_context(viewport={'width':1440,'height':1000}, service_workers='block')
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
            method=req.method, data=req.post_data, content_type=req.headers.get('content-type'),
            follow_redirects=not (parsed.path == '/onboarding' and req.method == 'POST'))
        target = response.headers.get('Location')
        if target and response.status_code in (301,302,303,307,308):
            # Playwright calls a route handler only for the first URL of a
            # redirect chain, so a redirect this mock answers can never be
            # followed: the browser would look up https://ui.test itself and
            # fail with ERR_NAME_NOT_RESOLVED. Answer with a navigation to the
            # app's own target instead -- that is a fresh request, so it comes
            # back through this handler like any other page.
            return handler.fulfill(status=200,headers={'Content-Type':'text/html; charset=utf-8'},
                body=f'<script>location.replace({json.dumps(urljoin(req.url,target))})</script>'.encode())
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
    for width in (1440,768,390):
        page.set_viewport_size({'width':width,'height':1000})
        open_page(guest,'/register')
        assert page.locator('#regEmail').is_visible() and page.locator('#regPassword').is_visible()
        assert not page.locator('input[name=marketing_emails_opt_in]').is_checked()
        assert page.locator('input[name=marketing_emails_opt_in]').is_hidden()
        assert page.locator('.login-card a[href="/oauth/canvas"]').count() == 0
        assert page.locator('#installBanner').count() == 0
        assert page.evaluate("() => { const event = new Event('beforeinstallprompt',{cancelable:true}); window.dispatchEvent(event); return !event.defaultPrevented; }")
        page.locator('#regBirthYear').fill('2020')
        assert page.locator('#regParentEmail').count() == 0
        assert page.locator('#regParentWrap').is_visible()
        assert 'cannot activate an under-13 account' in page.locator('#regParentWrap').inner_text()
        page.locator('#regBirthYear').fill('')
        assert page.locator('#regParentWrap').is_hidden()
        page.get_by_text('Optional email preferences',exact=True).click()
        page.locator('input[name=marketing_emails_opt_in]').check()
        assert page.locator('input[name=marketing_emails_opt_in]').is_checked()
        page.locator('input[name=marketing_emails_opt_in]').uncheck()
        page.get_by_text('Optional email preferences',exact=True).click()
        capture('signup')
        with App.app.app_context():
            App.identities().save(uid, {"completed": False})
        open_page(student,'/onboarding')
        assert page.get_by_role('heading', name='Your week starts here').is_visible()
        assert page.locator('.onb-start form button').count() == 2
        assert page.locator('#mainNav').count() == 0
        assert page.locator('#planiBot').is_hidden()
        assert page.get_by_role('button', name='Log out').is_visible()
        capture('setup-start')
        if width == 390:
            page.get_by_role('button', name='I’ll do this later').click()
            page.wait_for_url('**/command-center')
            page.get_by_role('link', name='Paste assignments').wait_for()
            assert page.locator('#newConversation').is_hidden()
            page.get_by_role('link', name='Paste assignments').click()
            page.wait_for_url('**/scheduler?capture=1')
            assert page.locator('#plannerCapture').evaluate('(details) => details.open')
            page.locator('#plannerCaptureText').fill('Read chapter six due tomorrow 30m')
            page.get_by_role('button', name='Review tasks', exact=True).click()
            page.locator('#plannerCapturePreview').get_by_text('Read chapter six', exact=False).wait_for()
            page.get_by_role('button', name='Add these tasks', exact=True).click()
            page.wait_for_function("document.getElementById('plannerCaptureStatus').textContent.includes('Added')")
        else:
            page.get_by_role('button', name='Connect my school').click()
            page.wait_for_url('**/connect')
            options = page.locator('#cxImportOptions')
            assert not options.evaluate('(details) => details.open')
            options.get_by_text('Other ways to import schoolwork', exact=True).click()
            options.get_by_role('button', name='CSV template', exact=False).click()
            assert options.locator('#ipCsvFile').is_visible()
        with App.app.app_context():
            assert App._get_or_create_identity(uid).completed
            db_user = App.db.session.get(App.User, uid)
            assert not db_user.email_reminders_opt_in and not db_user.push_reminders_opt_in
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        report.append({'flow':'one-action onboarding and a direct route to assignments at '+str(width),'passed':True})
    page.set_viewport_size({'width':1440,'height':1000})
    open_page(student,'/settings')
    for mode in ('expanded','collapsed'):
        page.evaluate('(mode) => IPSidebar.setMode(mode)',mode)
        page.mouse.move(1000,100)
        page.wait_for_timeout(250)
        if mode == 'collapsed':
            page.locator('.app-side').hover(position={'x':25,'y':200})
            page.wait_for_timeout(250)
        metrics = page.evaluate("""() => {
            const read = selector => {
                const row = document.querySelector(selector), icon = row.querySelector('.side-icon');
                const css = getComputedStyle(row), rect = row.getBoundingClientRect(), i = icon.getBoundingClientRect();
                return {fontFamily:css.fontFamily, fontSize:css.fontSize, lineHeight:css.lineHeight, height:rect.height, left:rect.left, width:rect.width, iconX:i.x, iconWidth:i.width};
            };
            return [read('.side-account a[href="/settings"]'),read('.side-account .side-logout')];
        }""")
        first, second = metrics
        for key in ('fontFamily','fontSize','lineHeight'):
            assert first[key] == second[key], (mode,key,metrics)
        for key in ('height','left','width','iconX','iconWidth'):
            assert abs(first[key]-second[key]) < .6, (mode,key,metrics)
        capture('sidebar-'+mode)
    report.append({'flow':'Settings and Logout share font, row and icon alignment in expanded and peek sidebar','passed':True})
    browser.close()
(OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(report),'output':str(OUT)},indent=2))
