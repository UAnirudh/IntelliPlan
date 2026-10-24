"""Real Chromium UI + isolated Flask APIs; synthetic content, no external calls.

Run: python tests/browser/photo_study_acceptance.py
Requires Playwright and its Chromium browser. Output is kept under ignored output/.
"""
import io
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='photo-browser-ephemeral',
                  APP_BASE_URL='https://photo.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')

import App  # noqa: E402
import ai_provider  # noqa: E402
import plani_safety  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from flashcards import store  # noqa: E402

SOURCE = ('Photosynthesis stores light energy as chemical energy. Carbon dioxide and water form '
          'glucose and oxygen. The equation is \\ce{6CO2 + 6H2O -> C6H12O6 + 6O2}.')
App.app.config['TESTING'] = True
App.limiter.enabled = False
App.app.intelliplan_assignment_fetcher = lambda uid: []
provider_calls = []
safety_fail = False


def safe(messages, **kwargs):
    if safety_fail:
        raise plani_safety.SafetyUnavailable('Synthetic outage')
    return None


def vision(**kwargs):
    assert kwargs['provider'] == 'groq' and kwargs['image_mime'] == 'image/jpeg'
    provider_calls.append('vision')
    return json.dumps({'title': 'Photosynthesis', 'text': SOURCE,
                       'warnings': ['The handwritten margin is unclear; it was excluded.']})


def chat_json(messages, **kwargs):
    data = json.loads(messages[-1]['content'])
    provider_calls.append(data['mode'])
    if data['mode'] == 'quiz':
        return {'title': 'Photosynthesis practice', 'items': [
            {'question': 'What happens to light energy in photosynthesis?',
             'options': ['It is stored as chemical energy.', 'It is destroyed.',
                         'It becomes sound.', 'It turns into soil.'],
             'answer': 0, 'explanation': 'The source says light energy is stored as chemical energy.',
             'source_quote': 'Photosynthesis stores light energy as chemical energy.'},
            {'question': 'Which molecule does \\ce{CO2} represent in these notes?',
             'options': ['[Water](https://example.invalid/definition)', 'Carbon dioxide (\\ce{CO2})', 'Glucose', 'Oxygen'],
             'answer': 1, 'explanation': 'Carbon dioxide and water are the starting materials. \\ce{CO2} is carbon dioxide.',
             'source_quote': 'Carbon dioxide and water form glucose and oxygen.'}]}
    return {'title': 'Photosynthesis cards', 'items': [
        {'front': 'What form of energy does photosynthesis store?',
         'back': 'Chemical energy.', 'source_quote': 'Photosynthesis stores light energy as chemical energy.'},
        {'front': 'Recall the equation for photosynthesis.',
         'back': '\\ce{6CO2 + 6H2O -> C6H12O6 + 6O2}',
         'source_quote': 'The equation is \\ce{6CO2 + 6H2O -> C6H12O6 + 6O2}.'}]}


plani_safety.classify = safe
ai_provider.vision = vision
ai_provider.chat_json = chat_json

with App.app.app_context():
    App.db.create_all()
    store.ensure_tables(App.db)
    user = App.User(email='photo-browser@example.test', name='Photo Student', role='student',
                    birth_year=2005, ai_personalization_opt_in=True,
                    parent_consent_granted=True, password_hash='')
    App.db.session.add(user)
    App.db.session.flush()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc=doc,
            version=App.policy_versions.current_version(doc)))
    App.db.session.commit()

client = App.app.test_client()
with client.session_transaction() as session:
    session.update(_user_id=str(uid), _fresh=True, session_epoch=0)

image = Image.new('RGB', (900, 400), 'white')
ImageDraw.Draw(image).text((25, 25), 'Photosynthesis: carbon dioxide + water -> glucose + oxygen', fill='black')
buffer = io.BytesIO()
image.save(buffer, format='PNG')
OUT = Path(os.environ.get('PHOTO_STUDY_BROWSER_OUTPUT', str(ROOT / 'output/playwright/photo-study')))
OUT.mkdir(parents=True, exist_ok=True)
checks = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
    context.add_init_script("""localStorage.setItem('ip_tour_done','1');localStorage.setItem('ip_side_mode','expanded');
      const originalTimeout = window.setTimeout.bind(window);
      window.setTimeout = (fn, ms, ...args) => originalTimeout(fn, window.__photoTimeoutTest && ms === 60000 ? 30 : ms, ...args);
    """)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    stall_save = False
    stalled_routes = []

    def route(handler):
        request = handler.request
        parsed = urlsplit(request.url)
        if os.environ.get('PHOTO_STUDY_BROWSER_DEBUG'):
            print('route', request.method, parsed.path, flush=True)
        if parsed.hostname != 'photo.test':
            return handler.abort()
        if stall_save and parsed.path == '/api/flashcards/photo/save':
            stalled_routes.append(handler)
            return
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)
        response = client.open(parsed.path + ('?' + parsed.query if parsed.query else ''),
            method=request.method,
            data=request.post_data_buffer, content_type=request.headers.get('content-type'),
            follow_redirects=True)
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {'set-cookie', 'content-length', 'content-encoding'}}
        return handler.fulfill(status=response.status_code, headers=headers, body=response.data)

    page.route('**/*', route)

    def capture(name):
        assert not errors, errors
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), 'Horizontal overflow'
        page.screenshot(path=str(OUT / (name + '.png')), full_page=True)
        checks.append(name)

    for width in (1440, 390):
        print(f'Photo browser: {width}px upload and review', flush=True)
        page.set_viewport_size({'width': width, 'height': 1000})
        response = page.goto('https://photo.test/flashcards/photo', wait_until='domcontentloaded')
        assert response.status == 200
        assert page.locator('#photoStudy').count(), (page.url, page.locator('body').inner_text()[:800])
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        print('Photo browser: page ready', flush=True)
        assert page.locator('#psGenerate').is_disabled()
        page.locator('#psFile').set_input_files({'name': 'notes.png', 'mimeType': 'image/png', 'buffer': buffer.getvalue()})
        assert page.locator('#psPreview').is_visible()
        page.get_by_role('button', name='Read photo', exact=True).click()
        page.wait_for_function('document.querySelector("#psSource").value.includes("Photosynthesis")')
        assert 'excluded' in page.locator('#psWarnings').inner_text()
        page.locator('#psSource').fill(SOURCE + ' Chlorophyll absorbs light.')
        capture(f'photo-review-{width}')
        page.get_by_role('button', name='Create practice', exact=True).click()
        page.locator('#psResults').wait_for(state='visible')
        assert page.locator('#psPractice').get_by_text('Question 1 of 2', exact=True).is_visible()
        assert page.get_by_role('button', name='Next question', exact=True).is_disabled()
        page.locator('.ps-choice').nth(2).click()
        assert 'Not quite' in page.locator('.ps-feedback').inner_text()
        page.locator('.ps-evidence summary').click()
        assert 'Photosynthesis stores light energy' in page.locator('.ps-feedback').inner_text()
        page.get_by_role('button', name='Next question', exact=True).click()
        assert page.locator('.ps-question .katex').count() > 0
        assert page.locator('.ps-choice a, .ps-choice [tabindex]').count() == 0
        assert page.locator('.ps-choice .katex').count() > 0
        assert page.locator('.ps-choice .katex .mord').first.evaluate('(node) => getComputedStyle(node).marginRight') == '0px'
        page.locator('.ps-choice').nth(1).click()
        assert 'That’s right' in page.locator('.ps-feedback').inner_text()
        capture(f'photo-quiz-feedback-{width}')
        page.get_by_role('button', name='See results', exact=True).click()
        assert '1 of 2 correct' in page.locator('.ps-complete').inner_text()
        if width == 1440:
            stall_save = True
            page.evaluate('window.__photoTimeoutTest = true')
            page.get_by_role('button', name='Save questions as flashcards', exact=True).click()
            page.wait_for_function('document.querySelector("#psStatus").textContent.includes("too long")')
            assert page.locator('#psSave').is_enabled()
            assert page.locator('#psSaved').is_hidden()
            assert page.locator('#psResults').is_visible()
            assert len(client.get('/api/flashcards/decks').get_json()['decks']) == 0
            for stalled in stalled_routes:
                stalled.abort()
            stalled_routes.clear()
            stall_save = False
            page.evaluate('window.__photoTimeoutTest = false')
            checks.append('photo-save-timeout-restores-preview-and-controls')
        page.get_by_role('button', name='Save questions as flashcards', exact=True).click()
        page.locator('#psSaved').wait_for(state='visible')
        assert page.locator('#psSave').is_disabled()
        capture(f'photo-quiz-saved-{width}')
        page.get_by_role('button', name='Edit source', exact=True).click()
        assert page.locator('#psResults').is_hidden()
        page.locator('#psMode').select_option('flashcards')
        page.get_by_role('button', name='Create practice', exact=True).click()
        page.locator('#psResults').wait_for(state='visible')
        assert page.locator('#psWorkspace').is_hidden()
        assert page.locator('.ps-card').count() == 2
        assert page.locator('.ps-card-answer').nth(1).is_hidden()
        page.get_by_role('button', name='Reveal answer', exact=True).nth(1).click()
        assert page.locator('.ps-card-answer').nth(1).locator('.katex').count() > 0
        capture(f'photo-flashcards-{width}')
        page.get_by_role('button', name='Save flashcard deck', exact=True).click()
        page.locator('#psSaved').wait_for(state='visible')
        assert 'flashcards saved' in page.locator('#psStatus').inner_text()
        page.get_by_role('button', name='Edit source', exact=True).click()
        safety_fail = True
        page.get_by_role('button', name='Create practice', exact=True).click()
        page.wait_for_function('document.querySelector("#psStatus").dataset.error === "true"')
        assert page.locator('#psResults').is_hidden()
        assert 'safety check' in page.locator('#psStatus').inner_text().lower()
        capture(f'photo-safe-outage-{width}')
        safety_fail = False

    response = page.goto('https://photo.test/flashcards', wait_until='domcontentloaded')
    assert response.status == 200
    assert page.get_by_role('link', name='From a photo', exact=True).is_visible()
    assert page.get_by_text('Photo practice', exact=True).count() == 4
    assert not page.get_by_text('photo:', exact=False).count()
    deck_response = client.get('/api/flashcards/decks').get_json()
    assert len(deck_response['decks']) == 4
    deck_id = deck_response['decks'][0]['id']
    queue = client.get(f'/api/flashcards/study?deck_id={deck_id}').get_json()
    assert len(queue['cards']) == 2
    page.evaluate('(id) => fcStudyDeck(id)', deck_id)
    page.locator('#fcSession:not(.fc-hidden)').wait_for()
    page.get_by_role('button', name='Show answer', exact=True).click()
    capture('photo-saved-review-390')
    assert not errors, errors
    page.unroute_all(behavior='wait')
    context.close()
    browser.close()

print(json.dumps({'checks': len(checks), 'screenshots': str(OUT),
                  'provider_fixture_calls': provider_calls,
                  'flows': ['upload', 'ocr-review', 'edit-source', 'quiz-feedback', 'quiz-score',
                            'math-chemistry', 'save-quiz-as-cards', 'flashcard-reveal', 'private-review', 'safety-outage'],
                  'page_errors': errors}, indent=2))
