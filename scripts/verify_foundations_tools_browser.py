"""Exercise study-tool navigation and Foundations against an isolated account.

Uses the real Flask endpoints and reviewed scoring. No live data or AI calls.
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
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='foundations-browser-fixture',
                  APP_BASE_URL='https://practice.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402
from intelliplan.learning import policy  # noqa: E402
from primer.api import _signer  # noqa: E402
from primer.catalog import get_item  # noqa: E402
from primer import store  # noqa: E402

App.limiter.enabled = False
App.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
OUT = ROOT / 'output/playwright/foundations-tools'
OUT.mkdir(parents=True, exist_ok=True)
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='practice-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    M = policy.models()
    district = M['Organization'](name='Practice fixture', kind='district', policy={'writing': False})
    App.db.session.add(district)
    App.db.session.flush()
    App.db.session.add(M['Membership'](user_id=uid, organization_id=district.id, role='student'))
    App.db.session.commit()
client = App.app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(uid)
    session['_fresh'] = True
# Compile the real app's first template before measuring browser navigation.
# This also makes startup failures distinct from an interaction regression.
assert client.get('/study-tools').status_code == 200
print('Isolated Flask fixture ready.', flush=True)

activity = {}
errors = []
report = []
hold_responses = set()
fail_responses = set()
delayed_responses = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    page.set_default_navigation_timeout(90000)
    page.add_init_script("localStorage.setItem('ip_tour_done','1');localStorage.setItem('ip_side_mode','rail');")
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(handler):
        request = handler.request
        parsed = urlsplit(request.url)
        if parsed.hostname != 'practice.test':
            return handler.abort()
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)
        suffix = parsed.path.rsplit('/', 1)[-1]
        if suffix in fail_responses:
            fail_responses.remove(suffix)
            return handler.fulfill(status=503, json={'error': 'Temporary practice connection failure.'})
        response = client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
                               method=request.method, data=request.post_data,
                               content_type=request.headers.get('content-type'))
        if parsed.path.endswith('/grade') and request.method == 'PUT':
            result = response.get_json() or {}
            print('Practice grade response:', response.status_code,
                  result.get('learner', {}).get('grade'), result.get('error', ''), flush=True)
        if parsed.path.endswith('/activity') and response.status_code == 200:
            activity.clear()
            activity.update(response.get_json())
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
        suffix = parsed.path.rsplit('/', 1)[-1]
        if request.method == 'GET' and suffix in hold_responses:
            hold_responses.remove(suffix)
            delayed_responses.append((handler, response.status_code, headers, response.data))
            return
        handler.fulfill(status=response.status_code, headers=headers, body=response.data)

    page.route('**/*', route)

    def visit(path):
        response = page.goto('https://practice.test' + path, wait_until='domcontentloaded')
        assert response.status == 200, (path, response.status)
        consent = page.get_by_role('button', name='Essential only', exact=True)
        if consent.is_visible():
            consent.click()

    def update_grade(grade):
        page.locator('#primerPlacementGrade').select_option(str(grade))
        with page.expect_response(lambda response: response.url.endswith('/grade')
                                  and response.request.method == 'PUT') as pending:
            page.get_by_role('button', name='Update practice grade', exact=True).click()
        response = pending.value
        assert response.status == 200, response.text()
        assert response.json()['learner']['grade'] == grade
        expect(page.locator('#primerGradeLabel')).to_have_text(f'Grade {grade}')

    visit('/study-tools')
    print('Study tools page loaded; checking navigation and practice.', flush=True)
    expect(page.locator('#hubGrid a[href]')).to_have_count(22)
    assert page.locator('#hubGrid a a, #hubGrid a button').count() == 0
    restricted = page.locator('#hubGrid .hub-card').filter(has=page.locator('.hub-card-title', has_text='Writing'))
    expect(restricted).to_have_attribute('aria-disabled', 'true')
    assert restricted.get_attribute('href') is None
    assert client.get('/writing').status_code == 403
    page.locator('#hubToolSearch').fill('Foundations')
    expect(page.locator('#hubGrid .hub-card:visible')).to_have_count(1)
    card = page.locator('#hubGrid').get_by_role('link', name='Foundations', exact=True)
    card.focus()
    expect(card).to_be_focused()
    page.keyboard.press('Enter')
    expect(page).to_have_url('https://practice.test/foundations')
    expect(page.locator('#primerCreate')).to_be_visible()
    expect(page.locator('#primerGrade')).to_have_value('')
    page.locator('#primerNickname').fill('Practice learner')
    page.locator('#primerGrade').select_option('9')
    page.get_by_role('button', name='Start practicing', exact=False).click()
    expect(page.locator('#primerCard')).to_be_visible()
    expect(page.locator('#primerGradeLabel')).to_have_text('Grade 9')
    expect(page.locator('#primerOfflinePractice')).to_be_visible()
    page.locator('#primerOfflinePractice summary').click()
    expect(page.locator('#primerOfflineTask')).not_to_be_empty()
    expect(page.locator('#primerOfflinePractice')).to_contain_text('does not change independent practice evidence')
    page.locator('#primerEvidence summary').click()
    expect(page.locator('#primerPathSummary')).to_contain_text('two correct answers without a clue')
    expect(page.locator('.primer-skill-row[data-locked="true"]').first).to_contain_text('Earlier practice needed')
    page.locator('#primerEvidence summary').click()

    def answer_current(assisted=False, correct=True):
        # The route fixture receives the next token before the browser has
        # replaced the previous inputs. Wait for the new form, not stale DOM.
        expect(page.locator('#primerAnswer')).to_be_visible()
        expect(page.locator('#primerNext')).to_be_hidden()
        expect(page.locator('#primerCheck')).to_be_enabled()
        if assisted:
            page.locator('#primerShowClue').click()
            expect(page.locator('#primerClue')).to_be_visible()
        with App.app.app_context():
            challenge = _signer().loads(activity['token'])
            item = get_item(challenge['item_id'])
            answer = item.answer if correct else next((value for value in item.options if value != item.answer), 'not the answer')
        radios = page.locator('#primerResponse input[type=radio]')
        if radios.count():
            page.locator('#primerResponse input[type=radio]').filter().evaluate_all(
                '(inputs, answer) => { const input = inputs.find(node => node.value === answer); input.click(); }', answer)
        else:
            page.locator('#primerTextAnswer').fill(answer)
        with page.expect_response(lambda response: response.url.endswith('/answer')
                                  and response.request.method == 'POST') as pending:
            page.locator('#primerCheck').click()
        response = pending.value
        assert response.status == 200, response.text()
        assert response.json()['correct'] is correct
        expect(page.locator('#primerFeedback')).to_be_visible()
        expect(page.locator('#primerFeedback')).to_have_attribute('data-correct', str(correct).lower())
        expect(page.locator('#primerNext')).to_be_visible()

    answer_current(assisted=True)
    expect(page.locator('#primerEvidenceNote')).to_contain_text('assisted practice')
    expect(page.locator('#primerSessionSummary')).to_contain_text('0 correct independently')
    page.locator('#primerNext').click()
    expect(page.locator('#primerPrompt')).to_be_focused()
    answer_current()
    expect(page.locator('#primerSessionSummary')).to_contain_text('1 correct independently')
    page.locator('#primerNext').click()
    answer_current()
    expect(page.locator('#primerSessionEnd')).to_be_visible()
    expect(page.locator('#primerSessionResult')).to_contain_text('3 questions checked: 2 correct independently, 1 with a clue')
    page.locator('#primerEvidence summary').click()
    expect(page.locator('#primerSkillMap section')).to_have_count(3)
    expect(page.locator('#primerPracticeCount')).to_contain_text('3 checked answers')
    saved_story = activity['story']['title']
    visit('/foundations')
    expect(page.locator('#primerActivityTitle')).to_have_text(saved_story)
    expect(page.locator('#primerPracticeCount')).to_contain_text('3 checked answers')
    page.locator('.primer-settings summary').click()
    update_grade(10)
    expect(page.locator('#primerPlacementGrade')).to_have_value('10')
    page.get_by_role('button', name='Add learner', exact=True).click()
    page.get_by_role('button', name='Back to my practice', exact=True).click()
    expect(page.locator('#primerWorkspace')).to_be_visible()
    expect(page.locator('#primerStoryChoice')).to_be_visible()
    page.locator('#primerChoiceOptions button').first.click()
    expect(page.locator('#primerCard')).to_be_visible()
    expect(page.locator('#primerPrompt')).to_be_focused()
    repair_skill = activity['skill']['id']
    answer_current(correct=False)
    expect(page.locator('#primerEvidenceNote')).to_contain_text('Read the feedback')
    page.locator('#primerNext').click()
    expect(page.locator('#primerCard')).to_be_visible()
    assert activity['skill']['id'] == repair_skill and activity['story']['repair'] is True
    print('Practice, feedback, saved resume, and grade update passed; checking recovery and races.', flush=True)
    # A failed activity or evidence fetch has a visible recovery control;
    # refreshing either one does not invent or discard saved practice.
    saved_answers = client.get(f"/api/primer/learners/{activity['learner']['id']}/progress").get_json()['total_attempts']
    fail_responses.update({'activity', 'progress'})
    visit('/foundations')
    expect(page.locator('#primerRetry')).to_be_visible()
    page.locator('#primerRetry').click()
    expect(page.locator('#primerCard')).to_be_visible()
    expect(page.locator('#primerPrompt')).to_be_focused()
    page.locator('#primerEvidence summary').click()
    expect(page.locator('#primerProgressRetry')).to_be_visible()
    page.locator('#primerProgressRetry').click()
    expect(page.locator('#primerProgressRetry')).to_be_hidden()
    expect(page.locator('#primerPracticeCount')).to_contain_text(f'{saved_answers} checked answers')
    expect(page.locator('#primerPathSummary')).to_contain_text('ready for review')
    # A rejected answer request must leave the learner's draft and Check
    # button usable. A successful retry contributes exactly one answer.
    inputs = page.locator('#primerResponse input')
    if inputs.first.get_attribute('type') == 'radio':
        inputs.first.check()
        draft = inputs.first.input_value()
    else:
        inputs.first.fill('draft answer')
        draft = 'draft answer'
    fail_responses.add('answer')
    page.locator('#primerCheck').click()
    expect(page.locator('#primerStatus')).to_contain_text('Temporary practice connection failure')
    expect(page.locator('#primerCheck')).to_be_enabled()
    expect(page.locator('#primerAnswer')).to_be_visible()
    assert page.locator('#primerResponse input:checked, #primerResponse input[type=text]').input_value() == draft
    answer_current()
    expect(page.locator('#primerPracticeCount')).to_contain_text(f'{saved_answers + 1} checked answers')
    # Hold actual grade-10 API responses, switch placement, then deliver the
    # older responses after grade 11 is already visible. Neither activity nor
    # practice evidence may move back to the old selection.
    hold_responses.update({'activity', 'progress'})
    visit('/foundations')
    expect(page.locator('#primerStatus')).to_have_text('Loading the next activity…')
    page.locator('.primer-settings summary').click()
    update_grade(11)
    expect(page.locator('#primerCard')).to_be_visible()
    new_prompt = page.locator('#primerPrompt').text_content()
    new_skill_path = page.locator('#primerSkillMap').text_content()
    assert len(delayed_responses) == 2
    while delayed_responses:
        handler, status_code, headers, body = delayed_responses.pop()
        handler.fulfill(status=status_code, headers=headers, body=body)
    page.wait_for_timeout(100)
    expect(page.locator('#primerGradeLabel')).to_have_text('Grade 11')
    expect(page.locator('#primerPrompt')).to_have_text(new_prompt)
    assert page.locator('#primerSkillMap').text_content() == new_skill_path
    page.locator('.primer-settings summary').click()
    update_grade(10)

    # A legacy learner has no declared placement. Switching to it must clear
    # the other learner's note, acknowledgement action, and story history.
    with App.app.app_context():
        learner_id = store.list_learners(uid)[0]['id']
        store.create_nudge(learner_id, 'explore')
        legacy = store.create_learner(uid, 'Unplaced learner', 'space', 0)
        App.db.session.execute(store.PROFILE.delete().where(store.PROFILE.c.learner_id == legacy['id']))
        App.db.session.commit()
    visit('/foundations')
    expect(page.locator('#primerFamilyNote')).to_be_visible()
    expect(page.locator('#primerStoryHistory')).to_be_visible()
    page.get_by_role('button', name='Unplaced learner', exact=True).click()
    expect(page.locator('#primerStatus')).to_contain_text('Choose a practice grade')
    expect(page.locator('#primerFamilyNote')).to_be_hidden()
    expect(page.locator('#primerStoryHistory')).to_be_hidden()
    expect(page.locator('#primerFamilyMessage')).to_have_text('')
    expect(page.locator('#primerStoryHistoryList li')).to_have_count(0)
    page.get_by_role('button', name='Practice learner', exact=True).click()
    expect(page.locator('#primerCard')).to_be_visible()
    for path in ('/foundations', '/study-tools'):
        for width in (1440, 768, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            visit(path)
            if path == '/foundations':
                expect(page.locator('#primerWorkspace')).to_be_visible()
                expect(page.locator('#primerGradeLabel')).to_have_text('Grade 10')
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (path, width)
            assert not errors, errors
            page.screenshot(path=str(OUT / f'{path.strip("/")}-{width}.png'), full_page=True)
            report.append({'path': path, 'width': width, 'horizontal_overflow': False, 'page_errors': []})
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        page.screenshot(path=str(OUT / f'{path.strip("/")}-dark.png'), full_page=True)
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
    browser.close()
(OUT / 'report.json').write_text(json.dumps({'flows': ['whole-card keyboard navigation', 'policy restriction',
    'learner creation', 'hint-assisted scoring', 'independent scoring', 'session pause', 'saved resume',
    'practice-grade update', 'cancel add learner', 'story decision', 'missed-skill repair',
    'out-of-order activity and progress responses', 'legacy learner clears prior note and history',
    'saved-focus offline practice', 'prerequisite and review explanation', 'activity recovery',
    'practice-path recovery', 'failed answer preserves draft and saves once'], 'layouts': report}, indent=2), encoding='utf-8')
print(json.dumps({'passed': True, 'flows': 18, 'responsive_layouts': len(report), 'dark_previews': 2, 'evidence': str(OUT)}))
