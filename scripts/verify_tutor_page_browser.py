"""Rendered signed-in tutor UI with scripted provider responses, no live calls."""
import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='tutor-ui-fixture-only',
                  APP_BASE_URL='https://tutor.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402 -- isolate storage before application import.

App.limiter.enabled = False
OUT = Path(os.getenv('TUTOR_BROWSER_OUTPUT', str(ROOT / 'output/playwright/plani')))
OUT.mkdir(parents=True, exist_ok=True)
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='tutor-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    App.db.session.commit()

with App.app.test_client() as client:
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    response = client.get('/study-and-learn')
    assert response.status_code == 200
    rendered = response.get_data(as_text=True)
    tools_rendered = client.get('/tools').get_data(as_text=True)
    hub_rendered = client.get('/study-tools').get_data(as_text=True)
    with App.app.app_context():
        models = App.learning_models
        school = models['Organization'](name='Fixture school', kind='school',
                                        policy={'tutor': False, 'review': False, 'focus': False})
        App.db.session.add(school)
        App.db.session.flush()
        App.db.session.add(models['Membership'](user_id=uid, organization_id=school.id, role='student'))
        App.db.session.commit()
    restricted_rendered = client.get('/study-and-learn').get_data(as_text=True)

reply = '''What do you predict will happen when the slope increases?
:::artifact{type="svg-exercise" title="Explore a line"}
{"kind":"linear","slope":1,"intercept":0,"question":"Try changing the slope, then explain what changed."}
:::
'''
audit_results = []


def audit(page, selector, name):
    axe_path = os.getenv('AXE_CORE_PATH')
    if not axe_path:
        return
    if not page.evaluate('Boolean(window.axe)'):
        page.add_script_tag(path=axe_path)
    violations = page.evaluate("""async selector => (await axe.run(selector, {
        runOnly: {type:'tag', values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations""", selector)
    audit_results.append(dict(surface=name, violations=violations))
    (OUT / 'accessibility.json').write_text(json.dumps(audit_results, indent=2), encoding='utf-8')
    assert not violations, violations


with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None)
    for width, theme in ((1440, 'light'), (1280, 'dark'), (768, 'light'), (390, 'light'), (390, 'dark')):
        context = browser.new_context(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
        context.add_init_script("localStorage.setItem('ip_tour_done','1');localStorage.setItem('theme', '" + theme + "');")
        page = context.new_page()
        errors, sent, speech = [], [], []
        history_available = False
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(handler):
            req = handler.request
            parsed = urlsplit(req.url)
            if parsed.hostname != 'tutor.test':
                return handler.abort()
            if parsed.path in ('/tutor', '/study-and-learn'):
                return handler.fulfill(status=200, content_type='text/html', body=rendered)
            if parsed.path == '/study-tools':
                return handler.fulfill(status=200, content_type='text/html', body=hub_rendered)
            if parsed.path == '/tools':
                return handler.fulfill(status=200, content_type='text/html',
                                       body=restricted_rendered if parsed.query == 'restricted=1' else tools_rendered)
            if parsed.path.startswith('/static/'):
                path = (ROOT / parsed.path.lstrip('/')).resolve()
                if path.is_relative_to(ROOT / 'static') and path.is_file():
                    return handler.fulfill(status=200, body=path.read_bytes(),
                                           content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                return handler.abort()
            if parsed.path.startswith('/api/'):
                body, status = {}, 200
                if parsed.path == '/api/tutor/conversations':
                    body = {'conversations': [{'id': 101, 'title': 'Saved learning conversation'}] if history_available else []}
                elif parsed.path == '/api/tutor/conversations/101':
                    if req.method == 'DELETE':
                        body, status = {'error': 'institution_policy'}, 403
                    else:
                        body = {'id': 101, 'messages': [{'role': 'assistant', 'content': 'What have you tried in your saved practice?'}]}
                elif parsed.path == '/api/tutor/adaptive/profile':
                    body = {'profile': {}, 'modality': {'mode': 'auto'}}
                elif parsed.path == '/api/tutor/adaptive/modality':
                    sent.append((parsed.path, json.loads(req.post_data)))
                elif parsed.path == '/api/tutor':
                    sent.append((parsed.path, json.loads(req.post_data)))
                    body = {'reply': reply, 'clarify': True}
                elif parsed.path == '/api/tutor/speak':
                    speech.append(json.loads(req.post_data))
                    body, status = {'error': 'policy_denied'}, 403
                elif parsed.path == '/api/tutor/voices':
                    body = {'voices': [{'id': 'fixture', 'name': 'Fixture voice', 'default': True}]}
                return handler.fulfill(status=status, content_type='application/json', body=json.dumps(body))
            return handler.abort()

        page.route('**/*', route)
        page.goto('https://tutor.test/study-and-learn', wait_until='networkidle')
        page.evaluate("document.documentElement.dataset.theme = '" + theme + "'")
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.wait_for_function("document.querySelectorAll('.tutor-modality-btn').length === 5")
        assert page.locator('#tutorInput').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('.tutor-practice-drawer summary').click()
        assert page.locator('#educationPlanOpen').is_visible()
        page.locator('.tutor-practice-drawer summary').click()
        # Start at the beginning of an overflowing welcome, including the mascot.
        assert page.locator('#tutorPlaniWrap').bounding_box()['y'] >= page.locator('.tutor-empty').bounding_box()['y'] - 1
        page.screenshot(path=str(OUT / f'plani-welcome-{width}-{theme}.png'), full_page=True)
        audit(page, '.tutor-shell', f'welcome-{width}-{theme}')
        if width <= 768:
            page.get_by_role('button', name='Open chat history', exact=True).click()
            page.get_by_role('button', name='Close chat history', exact=True).press('Escape')
            assert not page.locator('#tutorSide').evaluate("el => el.classList.contains('is-open')")
            assert page.get_by_role('button', name='Open chat history', exact=True).evaluate('el => document.activeElement === el')
            page.get_by_role('button', name='Open chat history', exact=True).click()
        page.get_by_role('button', name='Auditory', exact=True).click()
        page.wait_for_function('ttsEnabled === true')
        if width <= 768:
            page.evaluate('closeTutorDrawer()')
        page.locator('#tutorInput').fill('Help me understand slope.')
        page.locator('#tutorSendBtn').click()
        slider = page.get_by_role('slider', name='Slope', exact=True)
        slider.wait_for(state='visible')
        slider.focus()
        slider.press('ArrowRight')
        # Diagram descriptions update in the next animation frame.
        expect(page.locator('#tutorMessages')).to_contain_text('Each step right changes y by 1.5')
        page.wait_for_function("document.querySelector('#tutorAudioStatus').textContent.includes('school settings')")
        assert len(speech) == 1
        assert ':::artifact' not in speech[0]['text']
        assert sent[-1][1]['modality_mode'] == 'auditory'
        page.get_by_role('button', name='Help me start', exact=True).click()
        page.wait_for_function("document.querySelectorAll('.tutor-artifact').length === 2")
        assert 'small hint' in sent[-1][1]['messages'][-1]['content']
        assert not page.get_by_role('button', name='Just answer', exact=True).count()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        # The reply and the composer must occupy separate regions of the page.
        boxes = page.locator('.tutor-messages').bounding_box(), page.locator('.tutor-input-wrap').bounding_box()
        assert boxes[0]['y'] + boxes[0]['height'] <= boxes[1]['y'] + 1, boxes
        page.screenshot(path=str(OUT / f'plani-chat-{width}-{theme}.png'), full_page=True)
        audit(page, '.tutor-shell', f'chat-{width}-{theme}')
        history_available = True
        page.evaluate('loadConversations()')
        if width <= 768:
            page.get_by_role('button', name='Open chat history', exact=True).click()
        saved_chat = page.get_by_role('button', name='Saved learning conversation', exact=True)
        saved_chat.focus()
        saved_chat.press('Enter')
        page.wait_for_function("document.querySelector('#tutorMessages').textContent.includes('saved practice')")
        if width <= 768:
            page.get_by_role('button', name='Open chat history', exact=True).click()
        page.evaluate('IP.confirm = async () => true')
        page.get_by_role('button', name='Delete chat: Saved learning conversation', exact=True).click()
        page.wait_for_function("document.querySelector('#tutorHistoryStatus').textContent.includes('Could not delete')")
        assert page.get_by_role('button', name='Saved learning conversation', exact=True).is_visible()
        if width <= 768:
            page.get_by_role('button', name='Close chat history', exact=True).click()
        page.get_by_role('link', name='All study tools', exact=False).click()
        page.locator('#hubToolSearch').fill('flashcards')
        assert page.locator('#hubGrid .hub-card:visible').count() == 2
        assert page.locator('#hubGrid .hub-card:visible').evaluate_all("cards => cards.every(card => card.textContent.toLowerCase().includes('flashcards'))")
        hub_flashcards = page.get_by_role('link', name='Flashcards', exact=True)
        assert hub_flashcards.is_visible() and hub_flashcards.get_attribute('href') == '/flashcards'
        page.locator('#hubToolSearch').fill('no matching tool xyz')
        assert page.locator('#hubEmpty').is_visible()
        page.locator('#hubToolSearch').fill('')
        page.get_by_role('button', name='Practice', exact=True).click()
        assert page.locator('#hubGrid .hub-card:visible').count() > 0
        assert page.locator('#hubGrid .hub-card:visible').evaluate_all("cards => cards.every(card => card.dataset.cat === 'practice')")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.goto('https://tutor.test/tools', wait_until='networkidle')
        page.locator('#studyToolSearch').fill('flashcards')
        assert page.locator('.study-tool:visible').count() == 2
        assert page.locator('.study-tool:visible').evaluate_all("cards => cards.every(card => card.textContent.toLowerCase().includes('flashcards'))")
        assert page.get_by_role('link', name='Flashcards', exact=True).count() >= 1
        photo_tool = page.get_by_role('link', name='Photo practice', exact=True)
        assert photo_tool.is_visible() and photo_tool.get_attribute('href') == '/flashcards/photo'
        page.locator('#studyToolSearch').fill('no matching tool xyz')
        assert page.locator('#studyToolsEmpty').is_visible()
        page.locator('#studyToolSearch').fill('')
        page.get_by_role('button', name='Practice', exact=True).click()
        assert page.locator('.study-tool:visible').count() == 6
        page.get_by_role('button', name='All tools', exact=True).click()
        assert page.locator('.study-tool:visible').count() == 25
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        page.screenshot(path=str(OUT / f'study-tools-{width}-{theme}.png'), full_page=True)
        audit(page, '.study-tools', f'tools-{width}-{theme}')
        page.goto('https://tutor.test/tools?restricted=1', wait_until='networkidle')
        assert page.locator('.study-tool.is-restricted').count() == 9
        assert page.get_by_role('link', name='Photo practice', exact=True).count() == 0
        assert page.locator('.study-tool.is-restricted').filter(has_text='Photo practice').count() == 1
        assert page.locator('.study-tool.is-restricted a').count() == 0
        assert page.locator('#tutorInput').count() == 0
        assert page.locator('.study-tools-notice').first.is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT / f'school-permissions-{width}-{theme}.png'), full_page=True)
        print(json.dumps({'width': width, 'passed': True, 'rendered_signed_in_template': True,
                          'theme': theme, 'scripted_provider': True, 'audio_policy_denial': True,
                          'tool_search_and_filters': True, 'composer_does_not_overlap': True,
                          'school_restrictions': True, 'axe_audits': bool(os.getenv('AXE_CORE_PATH')),
                          'keyboard_history_and_failed_delete': True, 'page_errors': errors}))
        context.close()
    context = browser.new_context(viewport={'width': 390, 'height': 844}, java_script_enabled=False)
    page = context.new_page()
    page.route('**/*', route)
    page.goto('https://tutor.test/tools', wait_until='networkidle')
    assert page.locator('.study-tool').count() == 25
    assert page.get_by_role('link', name='Photo practice', exact=True).get_attribute('href') == '/flashcards/photo'
    assert page.locator('.study-tools-controls').is_hidden()
    assert page.get_by_role('link', name='Writing coach', exact=True).is_visible()
    print(json.dumps({'javascript_disabled': True, 'all_tools_reachable': True, 'passed': True}))
    context.close()
    browser.close()
