"""Browser acceptance for adult accounts with isolated SQL records and no external requests."""
import json
import mimetypes
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='adult-ui-fixture-only',
                  APP_BASE_URL='https://adult.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402
from intelliplan.learning import policy  # noqa: E402

App.limiter.enabled = False
App.app.config.update(TESTING=True)
App.check_recaptcha = lambda action: None
App.send_welcome_email_on_signup = lambda uid: None
App._grant_referral_bonus = lambda user: None
App.app.intelliplan_assignment_fetcher = lambda uid: []
OUT = ROOT / 'output/playwright/adult-access'
OUT.mkdir(parents=True, exist_ok=True)
with App.app.app_context():
    App.db.create_all()
    users = {}
    for name, role, year in [('Owner', 'student', 1980), ('Teacher', 'teacher', 1980),
                              ('Parent', 'parent', 1980), ('Student', 'student', 2010)]:
        user = App.User(email=name.lower() + '@example.test', name=name, role=role,
            birth_year=year, parent_consent_granted=True,
            password_hash=App.bcrypt.generate_password_hash('fixture-password-2026').decode())
        App.db.session.add(user); App.db.session.flush()
        users[name.lower()] = user.id
        App._get_or_create_identity(user.id).completed = True
        for doc in App.policy_versions.all_docs():
            App.db.session.add(App.PolicyAcknowledgement(user_id=user.id, doc=doc,
                version=App.policy_versions.current_version(doc)))
    App.ADMIN_EMAILS = App.ADMIN_EMAILS | {'owner@example.test'}
    App.db.session.add(App.ManualTask(user_id=users['student'], title='Prepare the climate project',
        course='Science', due_date=(date.today() + timedelta(days=2)).isoformat(), estimated_time=30, done=False))
    models = policy.models()
    district = models['Organization'](name='Acceptance district', kind='district', policy={'writing': False})
    App.db.session.add(district); App.db.session.flush()
    App.db.session.add(models['Membership'](user_id=users['student'], organization_id=district.id, role='student'))
    App.db.session.commit()

clients = {name: App.app.test_client() for name in users}
clients['guest'] = App.app.test_client()
for name, uid in users.items():
    with clients[name].session_transaction() as session:
        session.update(_user_id=str(uid), _fresh=True, session_epoch=0)
active_client = clients['owner']
report = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={'width':1440, 'height':1000}, service_workers='block')
    context.add_init_script("localStorage.setItem('ip_tour_done','1'); localStorage.setItem('ip_side_mode','expanded');")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(handler):
        req = handler.request
        parsed = urlsplit(req.url)
        if parsed.hostname != 'adult.test':
            return handler.abort()
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)
        response = active_client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
            method=req.method, data=req.post_data, content_type=req.headers.get('content-type'), follow_redirects=True)
        headers = {k:v for k,v in response.headers.items() if k.lower() not in {'set-cookie','content-length','content-encoding'}}
        handler.fulfill(status=response.status_code, headers=headers, body=response.data)

    page.route('**/*', route)

    def open_page(actor, path):
        global active_client
        active_client = clients[actor]
        errors.clear()
        response = page.goto('https://adult.test' + path, wait_until='domcontentloaded')
        assert response.status == 200, (actor, path, response.status)
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_timeout(300)

    def capture(slug):
        assert not errors, (slug, errors)
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (slug, 'overflow')
        width = page.viewport_size['width']
        page.screenshot(path=str(OUT / f'{slug}-{width}.png'), full_page=True)
        report.append({'page':slug,'width':width,'errors':[],'overflow':False})

    for width in (1440, 390):
        page.set_viewport_size({'width':width,'height':1000})
        for role in ('teacher','parent'):
            for kind in ('/login/account','/register'):
                open_page('guest', kind + '?next=/' + role)
                expected = 'Teacher' if role == 'teacher' else 'Family'
                assert expected in page.locator('h2').inner_text()
                assert page.locator('input[name=next]').input_value() == '/' + role
                capture(role + ('-signin' if 'login' in kind else '-signup'))

    page.set_viewport_size({'width':1440,'height':1000})
    open_page('owner','/admin/accounts')
    page.locator('#adminAccountEmail').fill('teacher@example.test')
    page.get_by_role('button', name='Find account', exact=True).click()
    grant = page.get_by_role('button',name='Grant admin access',exact=True)
    grant.wait_for(); grant.click()
    page.get_by_role('button',name='Revoke admin access',exact=True).wait_for()
    with App.app.app_context():
        assert App.is_admin(App.db.session.get(App.User,users['teacher']))
    capture('owner-granted')
    page.set_viewport_size({'width':390,'height':1000}); capture('owner-granted')
    page.get_by_role('button',name='Revoke admin access',exact=True).click()
    page.get_by_role('button',name='Grant admin access',exact=True).wait_for()
    with App.app.app_context():
        assert not App.is_admin(App.db.session.get(App.User,users['teacher']))
    report.append({'flow':'owner searches account then grants and revokes via buttons','passed':True})

    page.set_viewport_size({'width':1440,'height':1000})
    open_page('teacher','/teacher')
    denied = page.evaluate("async uid => (await fetch('/api/admin/accounts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid})})).status", users['teacher'])
    assert denied == 403
    page.locator('#tdInviteEmail').fill('student@example.test')
    page.get_by_role('button',name='Send invite',exact=True).click()
    page.get_by_text('Awaiting consent',exact=True).wait_for()
    report.append({'flow':'unauthorized admin grant denied and teacher invite waits for consent','passed':True})

    open_page('student','/linked-accounts')
    pending = page.locator('#sharingPending .sharing-row').filter(has_text='Teacher')
    pending.get_by_label('Recorded study',exact=True).uncheck()
    pending.get_by_label('Foundations practice',exact=True).uncheck()
    pending.get_by_label('Permitted and restricted tools',exact=True).check()
    capture('student-consent')
    page.set_viewport_size({'width':390,'height':1000}); capture('student-consent')
    pending.get_by_role('button',name='Allow selected summary',exact=True).click()
    page.locator('#sharingActive .sharing-row').filter(has_text='Teacher').wait_for()

    for width in (1440,390):
        page.set_viewport_size({'width':width,'height':1000})
        open_page('teacher','/teacher')
        page.get_by_role('button',name="View Student's overview",exact=True).click()
        page.get_by_role('heading',name='Tools permitted by their institution',exact=True).wait_for()
        assert page.locator('#tdDetailBox').get_by_text('Prepare the climate project',exact=True).is_visible()
        assert 'Writing · Restricted: institution policy' in page.locator('#tdDetailBox').inner_text()
        assert page.evaluate("""() => {
          const box = document.querySelector('#tdDetailBox').getBoundingClientRect();
          return [.25,.5,.75].every(fraction => document.elementFromPoint(box.x + box.width / 2,
            box.y + box.height * fraction)?.closest('#tdDetail'));
        }"""), 'An application layer covers the teacher dialog'
        capture('teacher-shared-overview')
        page.keyboard.press('Tab')
        assert page.evaluate("document.activeElement.closest('#tdDetail') !== null")
        page.keyboard.press('Escape')
        assert page.locator('#tdDetail').is_hidden()
        assert page.get_by_role('button',name="View Student's overview",exact=True).evaluate('(element) => element === document.activeElement')
    open_page('student','/linked-accounts')
    page.locator('#sharingActive .sharing-row').filter(has_text='Teacher').get_by_role('button',name='Revoke access',exact=True).click()
    page.get_by_text('No one can view your summary.',exact=True).wait_for()
    open_page('teacher','/teacher')
    denied = page.evaluate("async uid => (await fetch('/api/roles/student/' + uid + '/overview')).status", users['student'])
    assert denied == 403
    report.append({'flow':'student chooses work/tools; teacher sees actual task and district restriction; revocation denies subsequent overview','passed':True})
    report.append({'flow':'teacher dialog stays above shell layers, traps Tab and restores focus on Escape at both viewport widths','passed':True})

    open_page('parent','/parent')
    page.locator('#familyStudentEmail').fill('student@example.test')
    page.get_by_role('button',name='Request access',exact=True).click()
    page.get_by_text('Waiting for student approval',exact=False).wait_for()
    open_page('student','/linked-accounts')
    pending = page.locator('#sharingPending .sharing-row').filter(has_text='Parent')
    pending.get_by_label('Permitted and restricted tools',exact=True).check()
    pending.get_by_role('button',name='Allow selected summary',exact=True).click()
    page.locator('#sharingActive .sharing-row').filter(has_text='Parent').wait_for()
    for width in (1440,390):
        page.set_viewport_size({'width':width,'height':1000})
        open_page('parent','/parent')
        page.locator('#familyToolsList').get_by_text('Writing: Restricted · institution policy',exact=True).wait_for()
        assert 'Prepare the climate project' in page.locator('#familyUpcomingList').inner_text()
        capture('parent-shared-overview')
    report.append({'flow':'parent requests access and receives only student-approved summary and tools','passed':True})
    browser.close()

(OUT / 'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(report),'report':str(OUT / 'report.json')},indent=2))
