"""Exercise Command Center -> Tutor test prep in Chromium with fixture providers.

Usage: python scripts/verify_test_prep_browser.py
Set PLAYWRIGHT_CHROMIUM_EXECUTABLE to use a compatible system Chromium.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='test-prep-fixture-only',
                  APP_BASE_URL='https://prep.test', FLASK_ENV='testing',
                  NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
import App  # noqa: E402
import chatbot_api  # noqa: E402

App.limiter.enabled = False
chatbot_api.utcnow = lambda: datetime(2026, 11, 24, 22)
App._outlook_upcoming_events = lambda: [
    {'title': 'History exam', 'start': '2026-11-27T18:00:00Z', 'description': 'private details'},
]
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='prep-fixture@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.flush()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    App.db.session.add_all([
        App.ManualTask(user_id=uid, title='Geometry test', course='Geometry', due_date='2026-11-26'),
        App.ManualTask(user_id=uid, title='Spelling quiz', course='English', due_date='2026-11-25'),
    ])
    App.db.session.commit()

with App.app.test_client() as client, sync_playwright() as playwright:
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    pages = {}
    for path in ('/command-center', '/study-and-learn'):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.headers.get('Location'))
        pages[path] = response.get_data(as_text=True)

    chrome = playwright.chromium.launch(executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
    for width in (390, 1280):
        context = chrome.new_context(viewport={'width': width, 'height': 900}, timezone_id='America/Los_Angeles')
        context.add_init_script("localStorage.setItem('ip_tour_done','1');")
        page = context.new_page()
        errors, sent, loads = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(handler):
            req = handler.request
            url = urlsplit(req.url)
            if url.hostname != 'prep.test':
                return handler.abort()
            if url.path in pages:
                return handler.fulfill(status=200, content_type='text/html', body=pages[url.path])
            if url.path.startswith('/static/'):
                response = client.get(url.path)
                return handler.fulfill(status=response.status_code, content_type=response.content_type,
                                       body=response.data)
            body, status = {}, 200
            if url.path == '/api/tutor/test-prep':
                response = client.get(url.path, headers={'X-Timezone': req.headers.get('x-timezone', '')})
                return handler.fulfill(status=response.status_code, content_type='application/json', body=response.data)
            if url.path == '/api/tutor/conversations':
                body = {'conversations': [{'id': 101, 'title': 'Old practice chat'}]}
            elif url.path == '/api/tutor/conversations/101':
                loads.append(url.path)
                body = {'id': 101, 'messages': [{'role': 'assistant', 'content': 'Previous chat'}]}
            elif url.path == '/api/tutor/adaptive/profile':
                body = {'profile': {}}
            elif url.path == '/api/tutor':
                sent.append(json.loads(req.post_data))
                body = {'reply': ('Prove that the lines are parallel.' if len(sent) == 1
                                 else 'You earned 3/4. Retry the missing justification.')}
            return handler.fulfill(status=status, content_type='application/json', body=json.dumps(body))

        page.route('**/*', route)
        page.goto('https://prep.test/command-center')
        card = page.get_by_role('region', name='Upcoming test prep')
        expect(card.get_by_role('link', name='Prepare for History exam')).to_be_visible()
        expect(card.get_by_text('Test in 2 days · 20 minutes of practice')).to_be_visible()
        expect(card.get_by_text('Spelling quiz')).to_have_count(0)
        card.get_by_role('link', name='Prepare for Geometry test').click()
        expect(page.get_by_label('Test or topic')).to_have_value('Geometry test')
        expect(page.get_by_label('Course (optional)', exact=True)).to_have_value('Geometry')
        page.get_by_label('Teacher’s question format or rubric').fill('Two-column proofs only. Grade out of 4.')
        page.get_by_role('button', name='Draft my practice session').click()
        box = page.get_by_role('textbox', name='Your question')
        expect(box).to_have_value(re.compile('20-minute test prep'))
        assert not sent and not loads
        page.get_by_role('button', name='Send message').click()
        expect(page.get_by_text('Prove that the lines are parallel.')).to_be_visible()
        assert sent[0]['conversation_id'] is None
        assert 'Two-column proofs only. Grade out of 4.' in sent[0]['messages'][0]['content']
        assert 'past mistakes' in sent[0]['messages'][0]['content']
        assert 'one focused retry' in sent[0]['messages'][0]['content']
        box.fill('Triangles congruent by SAS, then CPCTC and converse alternate interior angles.')
        page.get_by_role('button', name='Send message').click()
        expect(page.get_by_text('You earned 3/4. Retry the missing justification.')).to_be_visible()
        assert len(sent) == 2 and not errors, errors
        context.close()
    chrome.close()
print('Command Center -> Tutor test prep passed at 390px and 1280px.')
