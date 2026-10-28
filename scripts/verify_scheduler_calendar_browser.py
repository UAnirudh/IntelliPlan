"""Rendered Scheduler calendar acceptance checks with synthetic provider replies.

Uses isolated SQLite and a Flask-authenticated student to render the real page.
All browser API calls are intercepted; no provider credentials or writes are used.
"""
import json
import mimetypes
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(
    DATABASE_URL='sqlite:///:memory:', SECRET_KEY='scheduler-browser-fixture-only',
    APP_BASE_URL='https://scheduler.test', FLASK_ENV='testing',
    NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0',
)
import App  # noqa: E402 -- storage must be isolated before application import.

App.app.config['TESTING'] = True
App.limiter.enabled = False
OUT = Path(os.getenv('SCHEDULER_CALENDAR_BROWSER_OUTPUT',
                     str(ROOT / 'output/playwright/scheduler-calendar')))
OUT.mkdir(parents=True, exist_ok=True)

with App.app.app_context():
    App.db.create_all()
    student = App.User(email='scheduler-browser@example.test', name='Student',
                       role='student', birth_year=2005, parent_consent_granted=True,
                       password_hash='fixture-only')
    App.db.session.add(student)
    App.db.session.flush()
    uid = student.id
    App._get_or_create_identity(uid).completed = True
    for doc in App.policy_versions.all_docs():
        App.db.session.add(App.PolicyAcknowledgement(
            user_id=uid, doc=doc, version=App.policy_versions.current_version(doc)))
    App.db.session.commit()

client = App.app.test_client()
with client.session_transaction() as session:
    session.update(_user_id=str(uid), _fresh=True, session_epoch=0)
rendered_response = client.get('/scheduler')
assert rendered_response.status_code == 200, rendered_response.status_code
RENDERED = rendered_response.get_data(as_text=True)

MARKUP = '<img src="https://untrusted.invalid/secret" onerror="window.injected=true">'
PLAN = {
    'overview': 'A confirmed study block with a specific start and end.',
    'total_study_time': '1 hour', 'notes': [],
    'schedule': [{'date': '2026-11-02', 'day_name': 'Monday', 'total_hours': 1,
                  'workload_level': 'light', 'blocks': [
                      {'block_id': 'd1-b1', 'assignment': 'Algebra practice',
                       'course': 'Math', 'time_slot': '2:00 PM - 3:00 PM',
                       'duration_minutes': 60, 'notes': 'Solve and check five equations.'}]}],
}


def run_width(browser, width):
    with App.app.app_context():
        App.SavedSchedule.query.filter_by(user_id=uid).delete()
        App.db.session.commit()
    context = browser.new_context(viewport={'width': width, 'height': 960},
                                  timezone_id='Asia/Tokyo', reduced_motion='reduce',
                                  service_workers='block')
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    context.add_init_script("localStorage.setItem('ip_tour_done','1');")
    page = context.new_page()
    page.clock.install(time=datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc))
    errors, availability, exports, generated, requests, hostile_requests = [], [], [], [], [], []
    submission_writes = []
    state = {'auto': 'outlook', 'export': 'partial', 'submitted': False}
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(handler):
        req = handler.request
        parsed = urlsplit(req.url)
        if parsed.hostname != 'scheduler.test':
            if parsed.hostname == 'untrusted.invalid':
                hostile_requests.append(req.url)
            return handler.abort()
        requests.append({'method': req.method, 'path': parsed.path})
        if parsed.path == '/scheduler':
            return handler.fulfill(content_type='text/html', body=RENDERED)
        if parsed.path.startswith('/static/'):
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(),
                                       content_type=mimetypes.guess_type(path)[0]
                                       or 'application/octet-stream')
            return handler.fulfill(status=404)
        if parsed.path == '/calendar/free-slot':
            availability.append({'method': req.method, 'query': parse_qs(parsed.query)})
            mode = state['auto']
            if mode == 'outage':
                return handler.fulfill(status=503, json={'error': MARKUP})
            if mode == 'disconnected':
                return handler.fulfill(json={'connected': False})
            slot = {'date': '2026-11-02', 'available': mode != 'full',
                    'slot': MARKUP if mode == 'markup' else '2:00 PM',
                    'free_hours': 3.5}
            return handler.fulfill(json={'connected': True, 'providers': ['outlook'],
                                         'suggestions': [slot], **slot})
        if parsed.path == '/calendar/events':
            return handler.fulfill(json={'connected': True, 'events': [],
                                         'providers': {'outlook': {'connected': True}}})
        if parsed.path == '/calendar/export':
            exports.append({'method': req.method, 'body': json.loads(req.post_data)})
            if state['export'] == 'network':
                return handler.abort('failed')
            if state['export'] == 'error':
                return handler.fulfill(status=503, json={'status': 'error', 'message': MARKUP})
            return handler.fulfill(json={'status': 'ok', 'created': 1,
                'providers': {'outlook': {'created': 1}, 'google': {'error': 'temporarily unavailable'}}})
        if parsed.path == '/live':
            actionable = {'source': 'canvas', 'course': 'Math', 'due_date': '2026-10-23',
                          'estimated_time': 30, 'submission_status': 'needs_action',
                          'requires_action': True, 'awaiting_grade': False,
                          'submission_verified': False, 'status_label': 'Needs your attention',
                          'submission': {'workflow_state': 'unsubmitted', 'submitted_at': None}}
            algebra = {**actionable, 'id': '11', 'title': 'Algebra practice'}
            essay = {**actionable, 'id': '12', 'title': 'History essay'}
            if state['submitted']:
                essay.update(submission_status='student_reported', requires_action=False,
                             awaiting_grade=True, student_submission_override=True,
                             status_label='You marked submitted · waiting for grade')
            waiting = {**actionable, 'id': '13', 'title': 'Submitted biology lab ' + MARKUP,
                       'submission': {'workflow_state': 'submitted', 'submitted_at': '2026-10-22T12:00:00Z'},
                       'submission_status': 'awaiting_grade', 'requires_action': False,
                       'awaiting_grade': True, 'submission_verified': True,
                       'status_label': 'Submitted · waiting for grade'}
            assert parse_qs(parsed.query).get('include_waiting') == ['1']
            return handler.fulfill(json=[algebra, essay, waiting])
        if parsed.path == '/api/assignment/submission':
            body = json.loads(req.post_data)
            submission_writes.append(body)
            assert req.method == 'POST' and body['assignment']['id'] == '12'
            state['submitted'] = body['action'] == 'submitted'
            return handler.fulfill(json={'status': 'ok', 'submission_status':
                                         'student_reported' if state['submitted'] else 'needs_action'})
        if parsed.path == '/generate_schedule':
            generated.append(json.loads(req.post_data))
            return handler.fulfill(json={'status': 'ok', 'data': PLAN})
        if parsed.path in ('/schedule/save', '/schedule/saved'):
            response = client.open(parsed.path, method=req.method, data=req.post_data,
                                   content_type=req.headers.get('content-type'))
            return handler.fulfill(status=response.status_code, json=response.get_json())
        if parsed.path == '/archive/context':
            return handler.fulfill(json={'custom_tasks': []})
        if parsed.path == '/schedule/progress':
            return handler.fulfill(json={'status': 'ok', 'id': 1})
        if parsed.path == '/api/schedule/forecast':
            return handler.fulfill(json={'status': 'ok', 'risk': {'tasks': []}})
        if parsed.path == '/api/schedule/autopilot':
            return handler.fulfill(json={'status': 'ok', 'changed': False})
        return handler.fulfill(json={})

    page.route('**/*', route)
    checks = []

    def capture(name):
        assert not errors, errors
        assert not hostile_requests, hostile_requests
        assert not page.evaluate('Boolean(window.injected)'), 'Server markup executed'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
        page.screenshot(path=str(OUT / f'{name}-{width}.png'), full_page=True)

    def auto(mode):
        state['auto'] = mode
        before = len(availability)
        with page.expect_response(lambda res: urlsplit(res.url).path == '/calendar/free-slot'):
            page.locator('#autoTimeBtn').click()
        expect(page.locator('#autoTimeBtn')).to_be_enabled()
        assert len(availability) == before + 1
        assert availability[-1] == {'method': 'GET', 'query': {
            'date': ['2026-11-02'], 'days': ['3'], 'tz': ['Asia/Tokyo']}}, availability[-1]

    try:
        page.goto('https://scheduler.test/scheduler', wait_until='networkidle')
        cookie = page.get_by_role('button', name='Essential only', exact=True)
        if cookie.is_visible():
            cookie.click()
        expect(page.locator('#hoursPerDay')).to_be_visible()
        page.wait_for_function('allAssignments.length === 2')
        page.locator('#assignmentQueue summary').click()
        expect(page.locator('#assignmentQueueCount')).to_contain_text('2 to do · 1 waiting')
        expect(page.locator('.assignment-queue-row')).to_have_count(2)
        page.locator('#assignmentWaitingFilter').click()
        expect(page.locator('.assignment-queue-row')).to_have_count(1)
        expect(page.locator('.assignment-queue-row')).to_contain_text('Submitted biology lab ' + MARKUP)
        assert page.locator('#assignmentQueue img').count() == 0
        page.locator('#assignmentActionableFilter').click()
        page.get_by_role('button', name='I’ve submitted this: History essay', exact=True).click()
        expect(page.locator('#assignmentQueueStatus')).to_contain_text('Marked submitted by you')
        expect(page.locator('#assignmentWaitingFilter')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#assignmentQueueCount')).to_contain_text('1 to do · 2 waiting')
        expect(page.locator('.assignment-queue-row')).to_have_count(2)
        checks.append('Waiting-grade filter and manual submission preserve submitted work without scheduling it')
        capture('waiting-for-grade-filter')
        auto('outlook')
        expect(page.locator('#preferredTime')).to_have_value('afternoon')
        expect(page.locator('#hoursPerDay')).to_have_value('3.5')
        expect(page.locator('#autoTimeHint')).to_contain_text('2:00 PM')
        checks.append('Outlook-only confirmed slot updates preferences in browser local date/timezone')
        capture('outlook-confirmed-slot')

        for mode, message in [('full', 'no 30-minute opening'),
                              ('outage', 'temporarily unavailable')]:
            page.locator('#hoursPerDay').fill('4.5')
            page.locator('#preferredTime').select_option('morning')
            auto(mode)
            expect(page.locator('#autoTimeHint')).to_contain_text(message)
            expect(page.locator('#hoursPerDay')).to_have_value('4.5')
            expect(page.locator('#preferredTime')).to_have_value('morning')
            checks.append(f'{mode} preserves both existing preferences and shows truthful message')
            capture('availability-' + mode)

        auto('disconnected')
        expect(page.locator('#autoTimeHint a[href="/oauth/google"]')).to_be_visible()
        expect(page.locator('#autoTimeHint a[href="/oauth/outlook"]')).to_be_visible()
        checks.append('No connection offers both Google and Outlook')
        auto('markup')
        expect(page.locator('#autoTimeHint')).to_contain_text(MARKUP)
        assert page.locator('#autoTimeHint img').count() == 0
        checks.append('Availability server markup is escaped and inert')

        with page.expect_response(lambda res: urlsplit(res.url).path == '/generate_schedule'):
            page.locator('#generateBtn').click()
        expect(page.locator('#scheduleResults')).to_be_visible()
        expect(page.locator('.day-card .block-title')).to_have_text('Algebra practice')
        expect(page.locator('.day-card .time-slot')).to_contain_text('2:00 PM')
        expect(page.locator('#calExportBtn')).to_have_text('Export to calendar')
        assert len(generated) == 1
        assert [row['id'] for row in generated[0]['assignments']] == ['11'], generated
        capture('generated-study-plan')
        checks.append('Real Generate and renderSchedule render timed study block and export action')
        with page.expect_response(lambda res: urlsplit(res.url).path == '/schedule/save') as saved:
            page.get_by_role('button', name='Save to dashboard', exact=True).click()
        assert saved.value.status == 200 and saved.value.json()['status'] == 'ok'
        expect(page.locator('[onclick="saveSchedule()"]')).to_contain_text('Saved!')
        with App.app.app_context():
            active = App.SavedSchedule.query.filter_by(user_id=uid, is_active=True).one()
            assert json.loads(active.schedule_data)['schedule'][0]['blocks'][0]['assignment'] == 'Algebra practice'
        stored = client.get('/schedule/saved').get_json()
        assert stored['status'] == 'ok'
        assert stored['data']['schedule'][0]['blocks'][0]['assignment'] == 'Algebra practice'
        checks.append('Save to dashboard persists the rendered plan through real save and read endpoints')

        with page.expect_response(lambda res: urlsplit(res.url).path == '/calendar/export'):
            page.locator('#calExportBtn').click()
        notice = page.locator('.ip-toast__text').filter(has_text='Some calendars could not be updated')
        expect(notice).to_be_visible()
        expect(notice).to_contain_text('Some calendars could not be updated: Google Calendar')
        expect(notice).to_contain_text('other calendar exports succeeded')
        assert len(exports) == 1
        assert exports[0]['method'] == 'POST'
        assert exports[0]['body']['skip_overlaps'] is True
        assert exports[0]['body']['timezone'] == 'Asia/Tokyo'
        assert exports[0]['body']['schedule_data'] == PLAN
        capture('partial-provider-export')
        checks.append('One export POST includes overlap protection and timezone; partial error is visible')

        state['export'] = 'error'
        page.locator('#calExportBtn').click()
        notice = page.locator('.ip-toast__text').filter(has_text='Export failed: ' + MARKUP)
        expect(notice).to_be_visible()
        assert page.locator('.ip-toast img').count() == 0
        capture('export-provider-error')
        state['export'] = 'network'
        page.locator('#calExportBtn').click()
        notice = page.locator('.ip-toast__text').filter(has_text='could not be confirmed.')
        expect(notice).to_be_visible()
        expect(notice).to_contain_text('could not be confirmed. Check your calendar before retrying.')
        # Advance retry timers without a wall-clock sleep: neither failure may
        # schedule a silent second POST to an endpoint that creates events.
        page.clock.fast_forward(30000)
        page.wait_for_load_state('networkidle')
        assert len(exports) == 3, exports
        checks.append('Provider/network export failures stay visible, inert, and never auto-retry POST')
        capture('export-network-failure')
        page.get_by_role('button', name='Undo submitted: History essay', exact=True).click()
        expect(page.locator('#assignmentQueueStatus')).to_contain_text('Moved back to work that needs attention')
        expect(page.locator('#assignmentActionableFilter')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#assignmentQueueCount')).to_contain_text('2 to do · 1 waiting')
        expect(page.locator('.assignment-queue-row')).to_have_count(2)
        page.wait_for_function('allAssignments.length === 2')
        assert [write['action'] for write in submission_writes] == ['submitted', 'reopen']
        checks.append('Undo submitted restores assignment to actionable work without changing LMS')
        capture('submission-undo-restored')
        return {'width': width, 'passed': True, 'checks': checks,
                'availability_calls': availability, 'export_posts': len(exports),
                'browser_errors': errors, 'horizontal_overflow': False}
    finally:
        context.tracing.stop(path=str(OUT / f'trace-{width}.zip'))
        context.close()


if __name__ == '__main__':
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None)
        results = [run_width(browser, width) for width in (1280, 390)]
        browser.close()
    report = {'passed': True, 'rendered_signed_in_scheduler': True,
              'source': 'synthetic provider replies; isolated SQLite; no live calendar writes',
              'results': results}
    (OUT / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
