"""Isolated teacher terminology browser checks; no external accounts or network."""
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='teacher-terms-test-only',
                  APP_BASE_URL='https://terms.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402
from intelliplan.learning import policy  # noqa: E402

OUT = ROOT / 'output/playwright/teacher-terminology'
OUT.mkdir(parents=True, exist_ok=True)
App.limiter.enabled = False
App.app.config['TESTING'] = True
with App.app.app_context():
    App.db.create_all()
    users = {}
    for name, role in [('Rivera', 'teacher'), ('Unassigned', 'teacher'), ('Student', 'student'),
                       ('Schooladmin', 'teacher'), ('Owner', 'student')]:
        row = App.User(email=name.lower() + '@example.test', name=name, role=role,
                       birth_year=1980, parent_consent_granted=True, password_hash='x')
        App.db.session.add(row)
        App.db.session.flush()
        users[name] = row.id
        App._get_or_create_identity(row.id).completed = True
        for doc in App.policy_versions.all_docs():
            App.db.session.add(App.PolicyAcknowledgement(user_id=row.id, doc=doc,
                version=App.policy_versions.current_version(doc)))
    M = policy.models()
    district = M['Organization'](name='District', kind='district', policy={'educational_terms': ['penis', 'puberty']})
    App.db.session.add(district)
    App.db.session.flush()
    school = M['Organization'](name='School', kind='school', policy={}, parent_id=district.id)
    App.db.session.add(school)
    App.db.session.flush()
    classroom = M['Organization'](name='Health · Grade 9', kind='class', policy={}, parent_id=school.id)
    App.db.session.add(classroom)
    App.db.session.flush()
    class_id = classroom.id
    App.db.session.add_all([
        M['Membership'](user_id=users['Rivera'], organization_id=class_id, role='teacher'),
        M['Membership'](user_id=users['Student'], organization_id=class_id, role='student'),
        M['Membership'](user_id=users['Schooladmin'], organization_id=school.id, role='admin'),
    ])
    school_id = school.id
    App.ADMIN_EMAILS = App.ADMIN_EMAILS | {'owner@example.test'}
    App.db.session.commit()
clients = {}
for name, uid in users.items():
    client = App.app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(uid), _fresh=True, session_epoch=0)
    clients[name] = client
active_client = clients['Rivera']
report = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(handler):
        req = handler.request
        parsed = urlsplit(req.url)
        if parsed.hostname != 'terms.test':
            return handler.abort()
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)
        response = active_client.open(parsed.path, method=req.method, data=req.post_data,
                                      content_type=req.headers.get('content-type'), follow_redirects=True)
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
        return handler.fulfill(status=response.status_code, headers=headers, body=response.data)

    page.route('**/*', route)
    # Both the platform owner and an assigned school admin can provision a
    # classroom through real UI controls; a teacher cannot self-enroll.
    for actor in ('Owner', 'Schooladmin'):
        active_client = clients[actor]
        page.goto('https://terms.test/school-admin')
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.locator('#admin-organization option').first.wait_for(state='attached')
        page.locator('#admin-organization').select_option(str(school_id))
        page.locator('#admin-classroom-create').wait_for()
        page.locator('#admin-classroom-name').fill(actor + ' health class')
        page.get_by_role('button', name='Create classroom', exact=True).click()
        page.locator('#admin-status').get_by_text('Classroom created.', exact=False).wait_for()
        assert page.locator('#admin-organization option:checked').inner_text() == actor + ' health class'
        for member, role in [('Rivera', 'teacher'), ('Student', 'student')]:
            page.locator('#admin-user-id').fill(str(users[member]))
            page.locator('#admin-role').select_option(role)
            page.get_by_role('button', name='Add or update member', exact=True).click()
            page.locator('#admin-status').get_by_text('Membership saved.', exact=True).wait_for()
        report.append({'flow': actor + ' creates classroom and assigns teacher/student through institution UI', 'passed': True})
    active_client = clients['Rivera']
    denied = active_client.post('/api/school-admin/organizations', json={
        'kind': 'class', 'name': 'Self assigned', 'parent_id': school_id})
    assert denied.status_code == 403
    page.goto('https://terms.test/teacher/terminology')
    page.locator('#termsForm').wait_for()
    page.locator('#termsClass').select_option(str(class_id))
    page.get_by_label('penis', exact=True).check()
    page.get_by_role('button', name='Save classroom terms', exact=True).click()
    page.locator('#termsStatus').get_by_text('Classroom terms saved.', exact=False).wait_for()
    with App.app.app_context():
        assert App.db.session.get(M['Organization'], class_id).policy['educational_terms'] == ['penis']
        assert M['Audit'].query.filter_by(organization_id=class_id, action='educational_terms_updated').count() == 1
    page.reload()
    page.locator('#termsForm').wait_for()
    page.locator('#termsClass').select_option(str(class_id))
    assert page.get_by_label('penis', exact=True).is_checked()
    assert page.get_by_label('vagina · institution restricted', exact=True).is_disabled()
    for width in (1440, 768, 390):
        page.set_viewport_size({'width': width, 'height': 1000})
        for theme in ('light', 'dark'):
            page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            page.screenshot(path=str(OUT / f'teacher-terms-{theme}-{width}.png'), full_page=True)
            report.append({'width': width, 'theme': theme, 'overflow': False, 'errors': []})
    page.get_by_label('Assigned classroom', exact=True).focus()
    page.keyboard.press('Tab')
    assert page.get_by_label('penis', exact=True).evaluate('(node) => document.activeElement === node')
    page.keyboard.press('Space')
    assert not page.get_by_label('penis', exact=True).is_checked()
    save_pattern = f'**/api/teacher/terminology/{class_id}'
    page.route(save_pattern, lambda handler: handler.fulfill(status=503, content_type='application/json', body='{}'))
    page.get_by_role('button', name='Save classroom terms', exact=True).click()
    page.get_by_role('button', name='Reload classrooms', exact=True).wait_for()
    assert page.get_by_role('button', name='Save classroom terms', exact=True).is_enabled()
    page.unroute(save_pattern)
    page.get_by_role('button', name='Reload classrooms', exact=True).click()
    page.locator('#termsForm').wait_for()
    page.locator('#termsClass').select_option(str(class_id))
    assert page.get_by_label('penis', exact=True).is_checked()
    report.append({'flow': 'Save persists audited choice, inherited restrictions disable terms, keyboard controls work and failed save can reload', 'passed': True})
    active_client = clients['Unassigned']
    page.goto('https://terms.test/teacher/terminology')
    page.locator('#termsStatus').get_by_text('No classroom policy access has been assigned.', exact=False).wait_for()
    assert page.locator('#termsForm').is_hidden()
    assert not errors, errors
    page.screenshot(path=str(OUT / 'teacher-unassigned-390.png'), full_page=True)
    report.append({'flow': 'Unassigned teacher receives setup guidance without policy authority', 'passed': True})
    browser.close()
(OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'checks': len(report), 'report': str(OUT / 'report.json')}, indent=2))
