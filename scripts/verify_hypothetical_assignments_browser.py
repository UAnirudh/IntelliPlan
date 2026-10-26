"""Exercise Grade Modeler additions in rendered desktop and phone pages.

Uses disposable SQLite and intercepted LMS data; never writes to a real account.
Run after `playwright install chromium`.
"""
import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='gradebook-browser-fixture-only',
                  FLASK_ENV='testing', NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402

OUT = Path(os.getenv('GRADEBOOK_BROWSER_OUTPUT', str(ROOT / '.codex/verification/hypothetical-assignments')))
OUT.mkdir(parents=True, exist_ok=True)
App.limiter.enabled = False
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='gradebook-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    App.db.session.commit()

with App.app.test_client() as client:
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    response = client.get('/gradebook')
    assert response.status_code == 200
    rendered = response.get_data(as_text=True)
    assert 'id="addAsgnBtn"' in rendered

ASSIGNMENTS = [
    {'id': 'exam', 'title': 'Real exam', 'category': 'Exams', 'graded': True,
     'points_earned': 80, 'points_possible': 100, 'display_score': '80'},
    {'id': 'homework', 'title': 'Real homework', 'category': 'Homework', 'graded': True,
     'points_earned': 100, 'points_possible': 100, 'display_score': '100'},
]
COURSES = [
    {'course': 'Weighted Biology', 'percentage': 84, 'letter': 'B', 'weighting': 'groups',
     'categories': [{'type': 'Exams', 'weight': 80, 'points': 80, 'points_possible': 100, 'mark': 'B-'},
                    {'type': 'Homework', 'weight': 20, 'points': 100, 'points_possible': 100, 'mark': 'A+'}],
     'assignments': ASSIGNMENTS},
    {'course': 'Points History', 'percentage': 90, 'letter': 'A-', 'weighting': 'points',
     'categories': [{'type': 'Exams', 'weight': 0, 'points': 80, 'points_possible': 100, 'mark': ''},
                    {'type': 'Homework', 'weight': 0, 'points': 100, 'points_possible': 100, 'mark': ''}],
     'assignments': ASSIGNMENTS},
    {'course': 'Empty Canvas', 'percentage': None, 'letter': '', 'weighting': 'groups',
     'categories': [], 'groups': [{'id': 'finals', 'name': 'Finals', 'weight': 100}],
     'assignments': []},
]

with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('GRADEBOOK_BROWSER_CHANNEL') or None)
    for width in (1280, 390):
        context = browser.new_context(viewport={'width': width, 'height': 900})
        page = context.new_page()
        errors, writes = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(handler):
            request = handler.request
            parsed = urlsplit(request.url)
            if parsed.hostname != 'gradebook.test':
                return handler.abort()
            if request.method != 'GET':
                writes.append((parsed.path, request.method))
            if parsed.path == '/gradebook':
                return handler.fulfill(content_type='text/html', body=rendered)
            if parsed.path == '/gradebook/detail':
                return handler.fulfill(json=COURSES)
            if parsed.path.startswith('/static/'):
                path = (ROOT / parsed.path.lstrip('/')).resolve()
                if path.is_relative_to(ROOT / 'static') and path.is_file():
                    return handler.fulfill(body=path.read_bytes(),
                        content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                return handler.abort()
            return handler.fulfill(json={'status': 'ok'})

        page.route('**/*', route)
        page.goto('https://gradebook.test/gradebook', wait_until='networkidle')
        consent = page.get_by_role('button', name='Essential only', exact=True)
        if consent.is_visible():
            consent.click()

        def select(name):
            page.locator('#courseDropBtn').click()
            page.locator('#courseMenu .dropdown-item').filter(has_text=name).click()

        def open_form(title, possible, score=''):
            page.locator('#addAsgnBtn').click()
            expect(page.locator('#newAsgnTitle')).to_be_focused()
            page.locator('#newAsgnTitle').fill(title)
            page.locator('#newAsgnPoss').fill(possible)
            page.locator('#newAsgnEarned').fill(score)

        def submit():
            page.locator('#addAsgnForm').get_by_role('button', name='Add assignment', exact=True).click()

        def cards():
            return page.locator('#assignmentBreakdown .assignment-card')

        def hypothetical():
            return cards().filter(has=page.get_by_role('button', name='Remove hypothetical assignment'))

        select('Weighted Biology')
        add_button = page.get_by_role('button', name='Add hypothetical assignment', exact=True)
        expect(add_button).to_be_visible()
        assert add_button.bounding_box()['height'] >= 48
        expect(add_button).to_have_attribute('aria-describedby', 'addAsgnHelp')
        if width == 390:
            assert add_button.bounding_box()['width'] >= 300
        expect(page.locator('#modeledPct')).to_have_text('84%')
        open_form('', '100')
        submit()
        expect(page.locator('#newAsgnMsg')).to_have_text('Give the assignment a title.')
        page.locator('#newAsgnTitle').fill('Future exam')
        page.locator('#newAsgnPoss').fill('0')
        submit()
        expect(page.locator('#newAsgnMsg')).to_contain_text('greater than 0')
        page.locator('#newAsgnPoss').fill('100')
        page.locator('#newAsgnEarned').fill('101')
        submit()
        expect(page.locator('#newAsgnMsg')).to_contain_text('between 0')
        page.locator('#newAsgnEarned').fill('-1')
        submit()
        expect(cards()).to_have_count(2)
        page.locator('#newAsgnEarned').fill('')
        # Keyboard submission must work without a page reload.
        page.locator('#newAsgnTitle').press('Enter')
        expect(cards()).to_have_count(3)
        expect(hypothetical().get_by_placeholder('Earned')).to_have_value('')
        expect(page.locator('#currentPct')).to_have_text('84%')
        expect(page.locator('#modeledPct')).to_have_text('84%')
        page.locator('#targetInput').fill('90')
        page.get_by_role('button', name='Model It', exact=True).click()
        expect(page.locator('#modelResult')).to_contain_text('1 remaining assignment')
        hypothetical().get_by_placeholder('Earned').fill('100')
        expect(page.locator('#modeledPct')).to_have_text('92%')
        expect(hypothetical()).to_contain_text('hypothetical')
        page.locator('#addAsgnBtn').click()
        assert page.locator('#addAsgnBtn').bounding_box()['height'] <= 80
        page.screenshot(path=str(OUT / f'form-{width}.png'), full_page=True)
        form_fits = page.locator('#addAsgnForm').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        assert form_fits, 'Hypothetical assignment form overflows'
        page.get_by_role('button', name='Cancel', exact=True).click()

        select('Points History')
        expect(cards()).to_have_count(2)
        open_form('Same title', '100', '100')
        submit()
        expect(page.locator('#modeledPct')).to_have_text('93.33%')
        expect(page.locator('#currentPct')).to_have_text('90%')
        select('Weighted Biology')
        expect(cards()).to_have_count(3)
        hypothetical().get_by_placeholder('Earned').fill('100')
        hypothetical().get_by_role('button', name='Remove').click()
        expect(cards()).to_have_count(2)
        expect(page.locator('#modeledPct')).to_have_text('84%')

        # A title remains text both on creation and when editing rerenders it.
        title = '<img src=x onerror="window.titleInjected=true"> O\'Brien'
        open_form(title, '100', '0')
        submit()
        expect(hypothetical().locator('.card-title')).to_contain_text(title)
        hypothetical().get_by_placeholder('Earned').fill('50')
        assert page.evaluate('window.titleInjected === undefined')
        expect(hypothetical().locator('img')).to_have_count(0)
        page.get_by_role('button', name='Reset', exact=True).click()
        expect(cards()).to_have_count(2)
        expect(page.locator('#modeledPct')).to_have_text('84%')
        select('Points History')
        expect(cards()).to_have_count(3)  # Reset only affects the selected course.
        page.get_by_role('button', name='Reset', exact=True).click()
        open_form('Upcoming essay', '100')
        submit()
        page.get_by_role('button', name='Realistic Forecast', exact=True).click()
        expect(hypothetical().get_by_placeholder('Earned')).to_have_value('90')
        expect(page.locator('#modeledPct')).to_have_text('90%')
        # Cancellation must prevent an animation from restoring removed rows or
        # writing scores into another course after Reset/course switching.
        page.evaluate('forecastRealistic(); resetAll()')
        page.wait_for_timeout(700)
        expect(cards()).to_have_count(2)
        assert page.evaluate('Object.keys(modifiedScores).length') == 0

        select('Empty Canvas')
        open_form('Final exam', '100', '75')
        expect(page.locator('#newAsgnCategory')).to_have_value('Finals')
        submit()
        expect(page.locator('#modeledPct')).to_have_text('75%')
        expect(page.locator('#currentPct')).to_have_text('—')
        select('Weighted Biology')
        select('Empty Canvas')
        expect(page.locator('#modeledPct')).to_have_text('75%')
        expect(page.locator('#currentPct')).to_have_text('—')
        page.get_by_role('button', name='Reset', exact=True).click()
        expect(cards()).to_have_count(0)
        expect(page.locator('#currentPct')).to_have_text('—')
        expect(page.locator('#modeledPct')).to_have_text('—')
        # AI prediction inputs retain real provider data, without hypothetical rows.
        assert page.evaluate('gradebookData.map(c => c.assignments.length)') == [2, 2, 0]
        assert not errors, errors
        assert not [w for w in writes if w[0].startswith('/gradebook')], writes
        context.close()
    browser.close()
print(json.dumps({'status': 'passed', 'viewports': [1280, 390], 'output': str(OUT)}))
