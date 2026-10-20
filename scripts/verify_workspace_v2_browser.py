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
OUT = ROOT / 'output/playwright/workspace-v2'
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
        response = page.goto('https://workspace.test' + path, wait_until='domcontentloaded', timeout=90000)
        assert response.status == 200, (path, response.status)
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_timeout(750)

    paths = ['/tutor', '/study-tools', '/foundations', '/command-center', '/dashboard?classic=1',
             '/scheduler', '/settings', '/study-and-learn', '/learning', '/flashcards',
             '/school-admin', '/admin/accounts', '/connect', '/', '/about', '/login',
             '/register?next=/teacher', '/login/account?next=/teacher']
    interactions_only = os.getenv('WORKSPACE_BROWSER_INTERACTIONS_ONLY') == '1'
    for width in (() if interactions_only else (1440, 1024, 390)):
        page.set_viewport_size({'width': width, 'height': 1000})
        for path in paths:
            open_page(path)
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (path, width, 'overflow')
            assert not errors, (path, width, errors)
            slug = path.split('?')[0].strip('/').replace('/', '-') or 'home'
            page.screenshot(path=str(OUT / f'{slug}-{width}.png'), full_page=True)
            report.append({'path': path, 'width': width, 'overflow': False, 'errors': []})

    page.set_viewport_size({'width': 1440, 'height': 1000})
    open_page('/tutor')
    assert not page.locator('.tutor-practice-drawer').get_attribute('open')
    page.locator('.tutor-practice-drawer > summary').click()
    assert page.locator('.tutor-assignment-bar').is_visible()
    page.locator('.tutor-practice-drawer > summary').click()
    page.locator('[data-appearance-open]').first.click()
    assert page.locator('#workspaceAppearance').is_visible()
    for name, value in {'Width': 'wide', 'Text': 'larger', 'Density': 'compact', 'Surface': 'solid', 'Accent': 'emerald', 'Motion': 'reduce', 'Mascot': 'hidden'}.items():
        page.locator('#appearance' + name).select_option(value)
    page.keyboard.press('Escape')
    assert page.locator('#workspaceAppearance').is_hidden()
    assert page.evaluate("document.activeElement.matches('[data-appearance-open]')")
    open_page('/tutor')
    assert page.evaluate("document.documentElement.dataset.uiWidth === 'wide' && document.documentElement.dataset.uiMotion === 'reduce' && document.documentElement.dataset.uiText === 'larger'")
    assert page.locator('#tutorPlaniWrap').is_hidden()
    page.locator('[data-appearance-open]').first.click()
    page.locator('#appearanceReset').click()
    page.locator('[data-appearance-close]').click()
    assert page.evaluate("document.documentElement.dataset.uiWidth === 'focused'")
    report.append({'flow': 'appearance persist, reset and restore focus', 'passed': True})

    markdown = '#### What to notice\n\nThe slope is **too small**.\n\n- Predict\n- Try a change\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n```python\nprint("# keep code")\n```\n\n<img src=x onerror="window.__xss=true"><script>window.__xss=true</script>[bad](javascript:alert(1))'
    page.evaluate("reply => { window.__xss = false; showChat(); addTutorMsg('bot', reply, false); }", json.dumps({'response': markdown}))
    assert page.locator('.tutor-bubble h4').last.inner_text() == 'What to notice'
    assert page.locator('.tutor-bubble table').count() == 1
    assert '# keep code' in page.locator('.tutor-bubble pre').inner_text()
    assert page.locator('.tutor-bubble script, .tutor-bubble img, .tutor-bubble a[href^="javascript:"]').count() == 0
    assert not page.evaluate('window.__xss')
    assert page.get_by_role('button', name='Read this reply aloud').count() == 1
    page.evaluate("() => addTutorMsg('user', 'A readable question in the light theme.', false)")
    contrast = page.locator('.tutor-msg-group.user .tutor-bubble').last.evaluate('''node => {
      const css = getComputedStyle(node);
      const luminance = color => {
        const rgb = color.match(/[\\d.]+/g).slice(0,3).map(v => Number(v)/255)
          .map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4);
        return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
      };
      const a=luminance(css.color), b=luminance(css.backgroundColor);
      return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    }''')
    assert contrast >= 4.5, contrast
    report.append({'flow':'light user message contrast', 'ratio':contrast, 'passed':True})
    page.screenshot(path=str(OUT / 'tutor-rich-response-light.png'), full_page=True)
    report.append({'flow': 'safe Markdown and wrapped JSON in actual message renderer', 'passed': True})

    artifact = 'Explore the slope.\n:::artifact{type="svg-exercise" title="Explore a line"}\n{"kind":"linear","slope":1,"intercept":0}\n:::'
    page.evaluate("reply => addTutorMsg('bot', reply, false)", artifact)
    perf = page.evaluate('''async () => {
      const artifact = document.querySelector('.tutor-artifact');
      const svg = artifact.querySelector('svg'), nodes = [...svg.querySelectorAll('*')];
      let changes = 0;
      const mo = new MutationObserver(rs => { changes += rs.filter(r => r.type === 'childList').length; });
      mo.observe(svg, {childList: true, subtree: true});
      const slider = artifact.querySelector('input[type=range]'), start = performance.now();
      for (let i = 0; i < 300; i++) { slider.value = String((i % 20) / 2 - 5); slider.dispatchEvent(new Event('input')); }
      const inputMs = performance.now() - start;
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); mo.disconnect();
      return {inputMs, changes, retained: nodes.every(n => n.isConnected), count: nodes.length};
    }''')
    assert perf['changes'] == 0 and perf['retained'], perf
    report.append({'flow': '300 slider inputs preserve SVG nodes', **perf})
    page.evaluate("setThemeMode('dark', {instant: true})")
    page.wait_for_timeout(600)
    report.append({'flow': 'dark theme', 'colors': page.evaluate("({body: getComputedStyle(document.body).backgroundColor, main: getComputedStyle(document.querySelector('.tutor-main')).backgroundColor, text: getComputedStyle(document.querySelector('.tutor-bubble')).color})")})
    page.screenshot(path=str(OUT / 'tutor-rich-response-dark.png'), full_page=True)
    assert not errors, errors

    page.set_viewport_size({'width': 390, 'height': 844})
    open_page('/tutor')
    page.locator('#ipMenuOpen').click()
    page.locator('#ipMenuSheet [data-appearance-open]').click()
    assert page.locator('#ipMenuSheet').is_hidden()
    assert page.locator('#workspaceAppearance').is_visible()
    page.keyboard.press('Escape')
    assert page.evaluate("document.activeElement.id === 'ipMenuOpen'")
    page.locator('#tutorInput').fill('Can you help me understand this concept?')
    box, button = page.locator('#tutorInput').bounding_box(), page.locator('#tutorSendBtn').bounding_box()
    assert box['y'] + box['height'] <= button['y'] + 1, (box, button)
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    content, composer = page.locator('#tutorEmpty').bounding_box(), page.locator('.tutor-input-wrap').bounding_box()
    assert content['y'] + content['height'] <= composer['y'] + 1, (content, composer)
    page.locator('#ipMenuOpen').click()
    page.locator('#ipMenuSheet [data-appearance-open]').click()
    page.locator('#appearanceText').select_option('larger')
    page.keyboard.press('Escape')
    page.evaluate("() => { showChat(); addTutorMsg('bot','## A clear explanation\\n\\nLarger, readable text.\\n\\n```python\\nprint(1)\\n```\\n\\n| x | y |\\n|---|---|\\n| 1 | 2 |',false); }")
    assert page.locator('.tutor-msg-group.bot .tutor-bubble').last.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 20
    assert page.locator('.tutor-bubble h2').last.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 27
    assert page.locator('.tutor-bubble pre code').last.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 17
    assert page.locator('.tutor-bubble table').last.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 18
    report.append({'flow':'extra large reading size on phone', 'passed':True})
    page.screenshot(path=str(OUT / 'tutor-composer-phone.png'), full_page=True)
    report.append({'flow': 'phone appearance, focus and composer', 'passed': True})
    browser.close()

report_path = OUT / ('interactions-report.json' if interactions_only else 'report.json')
report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'checks': len(report), 'report': str(report_path)}, indent=2))
