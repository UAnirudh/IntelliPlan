"""Exercise shared workspace UI against an isolated, in-memory Flask account.

No real student records, connectors, or AI calls. Browser requests to external
hosts are blocked. Screenshots and the report stay in output/playwright/.
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
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='fluid-ui-fixture-only',
                  APP_BASE_URL='https://workspace.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402
from intelliplan.learning import policy  # noqa: E402

App.limiter.enabled = False
App.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
OUT = ROOT / 'output/playwright/fluid-workspace'
OUT.mkdir(parents=True, exist_ok=True)
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='workspace-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App.ADMIN_EMAILS = App.ADMIN_EMAILS | {user.email}
    App._get_or_create_identity(uid).completed = True
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    M = policy.models()
    district = M['Organization'](name='Preview district', kind='district', policy={'writing': False})
    App.db.session.add(district)
    App.db.session.flush()
    school = M['Organization'](name='Preview school', kind='school', parent_id=district.id, policy={})
    App.db.session.add(school)
    App.db.session.flush()
    school_id, district_id = school.id, district.id
    App.db.session.add(M['Membership'](user_id=uid, organization_id=school_id, role='admin'))
    App.db.session.commit()

client = App.app.test_client()
guest_client = App.app.test_client()
active_client = client
with client.session_transaction() as session:
    session['_user_id'] = str(uid)
    session['_fresh'] = True

report = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
    context.add_init_script("localStorage.setItem('ip_tour_done','1'); if (!localStorage.getItem('ip_side_mode')) localStorage.setItem('ip_side_mode','expanded');")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(handler):
        req = handler.request
        parsed = urlsplit(req.url)
        if parsed.hostname != 'workspace.test':
            return handler.abort()
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)
        response = active_client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                               method=req.method, data=req.post_data,
                               content_type=req.headers.get('content-type'))
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
        return handler.fulfill(status=response.status_code, headers=headers, body=response.data)

    page.route('**/*', route)

    def open_page(path):
        global active_client
        active_client = guest_client if path in {'/', '/about', '/login'} else client
        errors.clear()
        response = page.goto('https://workspace.test' + path, wait_until='domcontentloaded')
        assert response.status == 200, (path, response.status)
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_timeout(750)

    # Whole-site coverage, including public pages, both themes and phone widths.
    paths = ['/command-center', '/dashboard?classic=1', '/scheduler', '/tutor', '/settings',
             '/study-and-learn', '/learning', '/flashcards', '/school-admin', '/connect',
             '/', '/about', '/login']
    widths = () if os.getenv('FLUID_BROWSER_INTERACTIONS_ONLY') == '1' else (1440, 1024, 390)
    for width in widths:
        page.set_viewport_size({'width': width, 'height': 1000})
        for path in paths:
            open_page(path)
            overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not overflow, (path, width, 'horizontal overflow')
            assert not errors, (path, width, errors)
            slug = path.split('?')[0].strip('/').replace('/', '-') or 'home'
            page.screenshot(path=str(OUT / f'{slug}-{width}.png'), full_page=True)
            report.append({'path': path, 'width': width, 'overflow': False, 'page_errors': []})

    page.set_viewport_size({'width': 1440, 'height': 1000})
    open_page('/tutor')
    page.get_by_role('button', name='Show more tools', exact=True).click()
    assert page.locator('.app-side [data-nav-item="pet"]').is_visible()
    page.get_by_role('button', name='Show fewer tools', exact=True).click()
    assert page.locator('.app-side [data-nav-item="pet"]').is_hidden()
    toggle = page.get_by_role('button', name='Hide chat history', exact=True)
    toggle.click()
    assert page.locator('#tutorSide').is_hidden()
    assert page.get_by_role('button', name='Show chat history', exact=True).get_attribute('aria-expanded') == 'false'
    open_page('/tutor')
    assert page.locator('#tutorSide').is_hidden(), 'Panel preference did not persist'
    page.get_by_role('button', name='Show chat history', exact=True).click()
    page.locator('#sideCollapseBtn').click()
    assert page.evaluate("document.documentElement.hasAttribute('data-side-rail')")
    page.mouse.move(1100, 500)
    page.screenshot(path=str(OUT / 'tutor-collapsed.png'))
    page.keyboard.press('Control+\\')
    assert not page.evaluate("document.documentElement.hasAttribute('data-side-rail')")
    # The desktop preference must not make mobile history inert or hidden.
    page.get_by_role('button', name='Hide chat history', exact=True).click()
    page.set_viewport_size({'width': 390, 'height': 900})
    page.get_by_role('button', name='Open chat history', exact=True).click()
    assert page.locator('#tutorSide').is_visible()
    assert not page.locator('#tutorSide').evaluate('(el) => el.inert')
    page.evaluate('closeTutorDrawer()')

    open_page('/dashboard?classic=1')
    page.get_by_role('button', name='Hide overview', exact=True).click()
    assert page.locator('#dashboard-overview').is_hidden()
    page.get_by_role('button', name='Show overview', exact=True).click()

    page.set_viewport_size({'width': 1440, 'height': 1000})
    open_page('/school-admin')
    page.select_option('#admin-organization', str(school_id))
    writing = page.locator('#policy-writing')
    assert writing.is_disabled() and not writing.is_checked()
    page.locator('#admin-tool-filter').fill('math')
    assert page.locator('#policy-math').is_visible()
    assert page.locator('#policy-pet').is_hidden()
    page.locator('#admin-tool-filter').fill('')
    page.locator('#policy-math').uncheck()
    page.locator('#admin-save').click()
    page.wait_for_function("document.querySelector('#admin-status').textContent.includes('Policy saved')")
    denied = client.post('/api/math/explain', json={})
    assert denied.status_code == 403 and denied.json['feature'] == 'math'
    page.locator('#admin-reload').click()
    page.wait_for_function("document.querySelector('#admin-status').textContent.includes('Latest policy')")
    assert not page.locator('#policy-math').is_checked()
    page.screenshot(path=str(OUT / 'district-controls-saved.png'), full_page=True)
    # Exercise the native dropdown with the keyboard, preserving native semantics.
    selector = page.locator('#admin-organization')
    selector.scroll_into_view_if_needed()
    selector.focus()
    page.keyboard.press('Space')
    page.screenshot(path=str(OUT / 'district-picker-open.png'))
    page.keyboard.press('Home')
    page.keyboard.press('Enter')
    assert selector.input_value() == str(district_id)
    page.locator('#policy-pet').uncheck()
    page.locator('#admin-save').click()
    page.wait_for_function("document.querySelector('#admin-status').textContent.includes('Policy saved')")
    page.select_option('#admin-organization', str(school_id))
    assert page.locator('#policy-pet').is_disabled()
    assert not page.locator('#policy-pet').is_checked()

    for theme in ('light', 'dark'):
        open_page('/command-center')
        page.evaluate('(theme) => { document.documentElement.dataset.theme = theme; }', theme)
        page.wait_for_timeout(450)  # Let the existing theme-color transitions settle.
        page.screenshot(path=str(OUT / f'command-center-{theme}.png'))
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('.command-chat__btn').first.evaluate('(el) => parseFloat(getComputedStyle(el).transitionDuration) < .001')
    report.append({'panel_persistence': True, 'mobile_drawer': True, 'keyboard_sidebar': True,
                   'district_save_reload': True, 'inherited_denial': True, 'direct_api_denial': True,
                   'native_select_keyboard': True, 'reduced_motion': True})
    browser.close()

(OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'passed': True, 'screens': len(report) - 1, 'output': str(OUT)}))
