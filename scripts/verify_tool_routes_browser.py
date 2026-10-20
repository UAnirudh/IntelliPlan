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
OUT = ROOT / 'output/playwright/tool-audit'
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
                               content_type=req.headers.get('content-type'), follow_redirects=True)
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
        return handler.fulfill(status=response.status_code, headers=headers, body=response.data)

    page.route('**/*', route)

    def open_page(path):
        global active_client
        active_client = guest_client if path in {'/', '/about', '/login'} or path.startswith(('/register', '/login/account')) else client
        errors.clear()
        response = page.goto('https://workspace.test' + path, wait_until='domcontentloaded')
        assert response.status == 200, (path, response.status)
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_timeout(750)

    open_page('/study-tools')
    tools = page.locator('.hub-card[href]').evaluate_all("nodes => nodes.map(n => ({path:n.getAttribute('href'),name:n.querySelector('.hub-card-title').textContent}))")
    for width in (1440, 390):
        page.set_viewport_size({'width': width, 'height': 1000})
        for tool in tools:
            errors.clear()
            try:
                open_page(tool['path'])
                result = {**tool, 'width': width, 'overflow': page.evaluate('document.documentElement.scrollWidth > innerWidth'), 'errors': list(errors)}
                page.screenshot(path=str(OUT / (tool['path'].strip('/').replace('/', '-') + '-' + str(width) + '.png')), full_page=True)
            except Exception as error:
                result = {**tool, 'width': width, 'error_type': type(error).__name__, 'error': str(error)[:300]}
            report.append(result)
            print(json.dumps(result), flush=True)
    browser.close()
(OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
failures = [row for row in report if row.get('overflow') or row.get('errors') or row.get('error_type')]
print(json.dumps({'routes':len(tools),'layouts':len(report),'failures':len(failures)}))
raise SystemExit(bool(failures))
