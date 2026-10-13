"""Local synthetic Command Center screenshots and scoped axe checks."""
import os
import sys
import tempfile
import threading
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
scratch = tempfile.TemporaryDirectory(prefix='intelliplan-command-ui-')
os.environ.update(DATABASE_URL='sqlite:///' + (Path(scratch.name) / 'fixture.db').as_posix(),
                  SECRET_KEY='synthetic-command-ui-only', FLASK_ENV='testing',
                  RESEND_API_KEY='', SENTRY_DSN='', BILLING_ENABLED='false',
                  FOLLOWTHROUGH_INPROCESS_CRON='0', NOTIFICATIONS_INPROCESS_CRON='0')
import App  # noqa: E402 - disposable configuration must precede application boot
import policy_versions  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

output = ROOT / '.codex/verification/school-learning-hub'
output.mkdir(parents=True, exist_ok=True)
App.limiter.enabled = False
with App.app.app_context():
    user = App.User(email='command-ui@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.flush()
    uid = user.id
    identity = App._get_or_create_identity(uid)
    identity.completed = True
    App.db.session.add(App.ManualTask(user_id=uid, title='Math practice', done=False))
    App.db.session.commit()
with App.app.test_client() as client:
    with client.session_transaction() as session:
        session['_user_id'], session['_fresh'] = str(uid), True
    for doc in ('privacy', 'terms'):
        response = client.post('/api/policy/acknowledge', json={'doc': doc, 'version': policy_versions.current_version(doc)})
        assert response.status_code == 200
    cookie = client.get_cookie(App.app.config.get('SESSION_COOKIE_NAME', 'session'))
server = make_server('127.0.0.1', 5099, App.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
checks = []
errors = []
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE'])
        context = browser.new_context()
        context.add_cookies([dict(name=cookie.key, value=cookie.value, domain='127.0.0.1', path='/')])
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        for theme in ('light', 'dark'):
            for width in (320, 768, 1440):
                page.set_viewport_size({'width': width, 'height': 900})
                page.goto('http://127.0.0.1:5099/command-center')
                page.evaluate("theme => { localStorage.setItem('theme', theme); document.documentElement.setAttribute('data-theme', theme); }", theme)
                assert page.locator('html').get_attribute('data-theme') == theme
                page.wait_for_selector('.command-chat__prompts')
                reminders = page.locator('#commandRemind')
                reminders.wait_for(state='visible')
                assert not reminders.evaluate('(element) => element.open')
                reminders.locator('summary').click()
                page.locator('#commandRemindOn').wait_for(state='visible')
                reminders.locator('summary').click()
                page.locator('#commandRemindOn').wait_for(state='hidden')
                consent = page.get_by_role('button', name='Essential only', exact=True)
                if consent.is_visible():
                    consent.click()
                    page.wait_for_function("!document.querySelector('#ipCookieBanner') || !document.querySelector('#ipCookieBanner').getClientRects().length")
                page.wait_for_timeout(500)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.add_script_tag(path=os.environ['AXE_CORE_PATH'])
                report = page.evaluate("async () => {const r=await axe.run('.command-chat', {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}}); return r.violations;}")
                assert not report, report
                page.screenshot(path=str(output / f'command-center-{theme}-{width}.png'))
                # UI success state only: no provider call, notification permission
                # prompt, or real preference write is made by this fixture.
                page.evaluate("window.IPReminders.enable = () => Promise.resolve({saved:true,push:false,pushMessage:''})")
                reminders.locator('summary').click()
                page.locator('#commandRemindOn').click()
                page.wait_for_function("document.querySelector('#commandRemind summary').textContent === 'Reminders are on'")
                reminders.locator('summary').click()
                page.wait_for_function("!document.querySelector('#commandRemind').open")
                checks.append({'theme': theme, 'width': width, 'axe_violations': report,
                               'overflow': False, 'reminder_success_mock': True})
        assert not errors, errors
        browser.close()
finally:
    server.shutdown()
    server.server_close()
    with App.app.app_context():
        App.db.session.remove()
        App.db.engine.dispose()
    try:
        (output / 'command-ui-results.json').write_text(json.dumps({'checks': checks, 'javascript_errors': errors}, indent=2))
    finally:
        scratch.cleanup()
print(json.dumps({'checks': len(checks), 'javascript_errors': errors}))
