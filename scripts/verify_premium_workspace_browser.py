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
OUT = Path(os.getenv('PREMIUM_BROWSER_OUTPUT', str(ROOT / 'output/playwright/premium-workspace')))
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
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block', timezone_id='America/Los_Angeles')
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

    def open_page(path, *, guest=False):
        global active_client
        active_client = guest_client if guest or path in {'/', '/about', '/login'} or path.startswith(('/register', '/login/account')) else client
        errors.clear()
        response = page.goto('https://workspace.test' + path, wait_until='domcontentloaded', timeout=90000)
        assert response.status == 200, (path, response.status)
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_timeout(750)


    for theme in ('light', 'dark'):
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            page.evaluate("theme => localStorage.setItem('theme', theme)", theme) if page.url.startswith('https://workspace.test') else None
            for path in ('/study', '/learn', '/study-and-learn', '/learning', '/command-center'):
                open_page(path)
                page.evaluate("theme => setThemeMode(theme, {instant: true})", theme)
                assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()") == ('#111413' if theme == 'dark' else '#f7f8f7')
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (path, width, theme, 'overflow')
                assert not errors, (path, width, theme, errors)
                page.screenshot(path=str(OUT / f'{path.strip("/")}-{theme}-{width}.png'), full_page=True)
                report.append({'path': path, 'width': width, 'theme': theme, 'errors': []})
    for guest in (False, True):
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            open_page('/learn', guest=guest)
            for tab in ('flashcards', 'review', 'mastery', 'progress', 'quests', 'shop', 'learn'):
                page.locator('#tab-' + tab).click()
                pane = '#tab' + tab.capitalize()
                page.wait_for_function('(selector) => document.querySelector(selector).offsetHeight > 0', arg=pane)
                assert page.locator('#tab-' + tab).evaluate("el => el.classList.contains('active')")
                assert page.locator('#studyApp > div[id^="tab"]:visible').count() == 1
                assert not errors, (guest, width, tab, errors)
            report.append({'flow': 'all visible study navigation tabs render without errors',
                           'guest': guest, 'width': width, 'passed': True})
    page.set_viewport_size({'width': 1440, 'height': 900})
    open_page('/learning')
    page.evaluate("setThemeMode('light', {instant:true})")
    prose = '## Reason it through\n\nA **clear explanation** with $x^2$.\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n```json\n{\"schedule\": [1, 2]}\n```\n\n<img src=x onerror=\"window.__xss=true\"><script>window.__xss=true</script>[bad](javascript:alert(1))'
    App.ai_chat = lambda *args, **kwargs: prose
    # The actual Send handler receives a realistic API envelope; no remote provider call.
    def tutor_reply(handler):
        return handler.fulfill(json={'reply': prose, 'conversation_id': 1})
    page.route('**/api/tutor', tutor_reply)
    page.locator('#hub-question').fill('Explain this concept with an example')
    page.locator('#hub-tutor-form button[type=submit]').click()
    page.locator('.learning-message[data-role=assistant] h2').wait_for()
    assert page.locator('.learning-message table').count() == 1
    assert page.get_by_role('table').count() == 1
    assert page.get_by_role('region', name='Response table').count() == 1
    assert page.locator('.learning-message .katex').count() == 1
    assert page.locator('.learning-message script,.learning-message img,.learning-message a[href^="javascript:"]').count() == 0
    assert page.evaluate('window.__xss !== true')
    assert page.locator('.ip-code-toolbar').inner_text() == 'json\nCopy code'
    page.evaluate("() => {window.__copied = ''; Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async text => {window.__copied=text;}}});}")
    page.get_by_role('button', name='Copy json code').click()
    assert page.evaluate('window.__copied') == '{"schedule": [1, 2]}'
    assert page.locator('.ip-code-toolbar [role=status]').inner_text() == 'Code copied to clipboard.'
    assert page.evaluate("value => IPRichText.plainText(value)", prose).count('Copy code') == 0
    envelopes = [
        {'choices': [{'message': {'content': prose}}]},
        {'candidates': [{'content': {'parts': [{'text': prose}]}}]},
        {'output': [{'content': [{'type': 'output_text', 'text': prose}]}]},
        {'content': [{'type': 'text', 'text': prose}]},
    ]
    for envelope in envelopes:
        assert page.evaluate('value => IPRichText.normalize(value)', envelope) == prose
    mixed = [{'type':'text','text':'Explanation'}, {'type':'image','url':'do not discard'}]
    assert 'do not discard' in page.evaluate('value => IPRichText.normalize(value)', mixed)
    for structured in ({'answer':42, 'explanation':'Because', 'steps':[1,2]},
        {'output':[{'type':'function_call','arguments':'keep me'}]}):
        normalized = page.evaluate('value => IPRichText.normalize(value)', structured)
        assert json.loads(normalized.removeprefix('```json\n').removesuffix('\n```')) == structured
    page.screenshot(path=str(OUT / 'learning-rich-response.png'), full_page=True)
    report.append({'flow':'Actual Learn send, Markdown, math, table, sanitized HTML, code copy and four provider envelopes', 'passed':True})
    # Clipboard denial offers a keyboard-copy selection, never a silent success.
    page.evaluate("() => {navigator.clipboard.writeText = async () => {throw new Error('denied');};}")
    page.get_by_role('button', name='Copy json code').click()
    assert page.evaluate('getSelection().toString()') == '{"schedule": [1, 2]}'
    report.append({'flow':'Clipboard denial selects code and announces recovery', 'passed':True})
    # Drive the actual legacy quiz UI, including controlled delayed requests.
    open_page('/learn')
    page.locator('.mc[data-mode=serious]').focus()
    page.keyboard.press('Enter')
    assert page.locator('.mc[data-mode=serious]').get_attribute('aria-pressed') == 'true'
    page.locator('.mc[data-mode=casual]').click()
    questions = [{'id':1,'type':'recall','question':'What does insulin do?', 'answer':'It lowers blood glucose.'},
                 {'id':2,'type':'conceptual','question':'Why does it matter?', 'answer':'It regulates energy availability.'}]
    page.route('**/study/generate', lambda handler: handler.fulfill(json={'status':'ok','data':{
        'title':'Insulin','questions':questions,'key_concepts':[],'mode':'casual'}}))
    grade_requests, sparks_requests = [], []
    page.route('**/study/evaluate', lambda handler: grade_requests.append(handler))
    page.route('**/study/sparks/update', lambda handler: sparks_requests.append(handler))
    page.locator('#pasteContent').fill('Insulin lowers blood glucose and regulates energy availability. ' * 3)
    page.locator('#startBtn').click()
    page.locator('#ansInput').wait_for()
    page.locator('#ansInput').fill('It lowers blood glucose.')
    page.evaluate('() => {void submitAnswer(); void submitAnswer();}')
    page.wait_for_function('document.querySelector("#submitBtn").disabled')
    page.wait_for_timeout(100)
    assert len(grade_requests) == 1
    grade_requests.pop().fulfill(status=503, json={'status':'error','message':'Temporary outage'})
    page.get_by_role('button', name='Try evaluation again').wait_for()
    assert page.locator('#ansInput').input_value() == 'It lowers blood glucose.'
    assert page.evaluate('G.sesAnswered') == 0 and not sparks_requests
    report.append({'flow':'Quiz keyboard mode selection, double-submit guard and failed grading preserves draft without credit', 'passed':True})
    page.locator('#submitBtn').click()
    page.wait_for_timeout(100)
    assert len(grade_requests) == 1
    evaluation = {'verdict':'correct','score':90,'critique':'Correct relationship.'}
    grade_requests.pop().fulfill(json={'status':'ok','evaluation':evaluation})
    page.wait_for_timeout(100)
    assert len(sparks_requests) == 1
    page.evaluate('() => {IP.confirm = async () => true; void exitSession();}')
    page.locator('#setupView').wait_for(state='visible')
    page.locator('#startBtn').click()
    page.locator('#ansInput').wait_for()
    sparks_requests.pop().fulfill(json={'status':'ok','awarded':2,'total_points':2})
    page.wait_for_timeout(100)
    assert page.evaluate('G.sesAnswered === 0 && G.sesCorrect === 0 && G.sesPts === 0')
    assert page.locator('#nextBtn').is_hidden()
    report.append({'flow':'Late Sparks response after exiting cannot mutate the next quiz session', 'passed':True})
    page.locator('#ansInput').fill('It lowers blood glucose.')
    page.locator('#submitBtn').click()
    page.wait_for_timeout(100)
    grade_requests.pop().fulfill(status=200, content_type='text/html', body='Temporary server page')
    page.get_by_role('button', name='Try evaluation again').wait_for()
    assert page.evaluate('G.sesAnswered') == 0
    page.locator('#submitBtn').click()
    page.wait_for_timeout(100)
    grade_requests.pop().fulfill(json={'status':'ok','evaluation':{'verdict':'incorrect','score':10}})
    page.locator('#nextBtn').wait_for(state='visible')
    assert page.evaluate('G.sesAnswered === 1 && G.sesCorrect === 0')
    report.append({'flow':'Non-JSON grading response recovers and a retry records exactly one attempt', 'passed':True})
    # A delayed grader response must also be ignored after leaving.
    page.locator('#nextBtn').click()
    page.locator('#ansInput').fill('It regulates energy.')
    page.locator('#submitBtn').click()
    page.wait_for_timeout(100)
    page.evaluate('() => {void exitSession();}')
    page.locator('#setupView').wait_for(state='visible')
    grade_requests.pop().fulfill(json={'status':'ok','evaluation':evaluation})
    page.wait_for_timeout(100)
    assert not sparks_requests
    assert page.evaluate('G.sesAnswered') == 1
    report.append({'flow':'Late evaluation after exit is ignored', 'passed':True})
    with App.app.app_context():
        reviews = App.StudyMastery.query.filter_by(user_id=uid).all()
        assert len(reviews) == 1 and reviews[0].times_seen == 1 and reviews[0].times_correct == 0
    report.append({'flow':'First real mastery review initializes and persists exactly one attempt', 'passed':True})
    # Exercise extreme mode without requiring OS fullscreen access in headless CI.
    page.evaluate('() => { G.mode = "extreme"; launchExtreme(); }')
    page.locator('#xAnsInput').fill('It lowers blood glucose.')
    page.locator('#xSubmitBtn').click()
    page.wait_for_timeout(100)
    grade_requests.pop().fulfill(status=503, json={'status':'error','message':'Unavailable'})
    page.locator('#xSelfCheck').wait_for(state='visible')
    page.evaluate('() => {void xMark(false); void xMark(false);}')
    page.locator('#xNextBtn').wait_for(state='visible')
    assert page.evaluate('G.sesAnswered') == 2
    page.evaluate('() => {void exitExtreme();}')
    report.append({'flow':'Extreme-mode unavailable grader reveals self-check and counts one double-clicked self-report', 'passed':True})
    page.locator('#startBtn').click()
    page.locator('#ansInput').wait_for()
    for index in range(2):
        page.locator('#ansInput').fill('An incorrect answer')
        page.locator('#submitBtn').click()
        page.wait_for_timeout(100)
        grade_requests.pop().fulfill(json={'status':'ok','evaluation':{'verdict':'incorrect','score':10}})
        page.locator('#nextBtn').wait_for(state='visible')
        page.locator('#nextBtn').click()
    page.wait_for_timeout(100)
    assert len(sparks_requests) == 1
    page.evaluate('() => {void completeSession();}')
    page.wait_for_timeout(100)
    assert len(sparks_requests) == 1, 'Duplicate completion must not request another bonus'
    page.evaluate('() => {void exitSession();}')
    page.locator('#setupView').wait_for(state='visible')
    page.locator('#startBtn').click()
    page.locator('#ansInput').wait_for()
    sparks_requests.pop().fulfill(json={'status':'ok','awarded':10,'total_points':10})
    page.wait_for_timeout(100)
    assert not sparks_requests
    assert page.evaluate('G.sesAnswered === 0 && G.sesPts === 0')
    assert page.locator('#questionArea').is_visible() and page.locator('#completeCard').is_hidden()
    report.append({'flow':'Completion is single-flight and late bonus cannot complete or reward a new quiz', 'passed':True})
    assert page.evaluate('localStudyDate(new Date("2026-10-27T06:30:00Z"))') == '2026-10-26'
    report.append({'flow':'Study streak/review date follows Los Angeles local date across UTC midnight', 'passed':True})
    # Finish a complete five-question session against real ephemeral reward,
    # streak and session endpoints; only the model grader remains a fixture.
    page.unroute('**/study/sparks/update')
    page.evaluate('() => {void exitSession();}')
    page.locator('#setupView').wait_for(state='visible')
    page.unroute('**/study/generate')
    five_questions = [dict(id=index+1, type='recall', question=f'Practice question {index+1}?', answer='An explanation.') for index in range(5)]
    page.route('**/study/generate', lambda handler: handler.fulfill(json={'status':'ok','data':{
        'title':'Five-question review','questions':five_questions,'key_concepts':[],'mode':'casual'}}))
    page.locator('#startBtn').click()
    page.locator('#ansInput').wait_for()
    for index in range(5):
        page.locator('#ansInput').fill('Not the expected answer')
        page.locator('#submitBtn').click()
        page.wait_for_timeout(100)
        grade_requests.pop().fulfill(json={'status':'ok','evaluation':{'verdict':'incorrect','score':10}})
        page.locator('#nextBtn').wait_for(state='visible')
        page.locator('#nextBtn').click()
    page.locator('#completeCard').wait_for(state='visible')
    page.wait_for_timeout(200)
    with App.app.app_context():
        points = App.get_study_profile(uid, None)
        assert points.total_sessions == 1
        assert points.total_points >= 30 and points.spark_balance >= 30
        assert points.streak_count >= 1
        saved = json.loads(points.session_history)
        assert len(saved) == 1 and saved[0]['questions'] == 5 and saved[0]['correct'] == 0
    report.append({'flow':'Complete quiz persists real session history, rewards and streak through actual endpoints', 'passed':True})
    assert not errors, errors
    (OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    browser.close()
layout_count = sum('path' in item for item in report)
print(json.dumps({'layouts':layout_count, 'flows':len(report)-layout_count, 'report':str(OUT / 'report.json')}))
