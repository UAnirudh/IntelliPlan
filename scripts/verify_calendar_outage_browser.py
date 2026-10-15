"""Actual follow-through UI with controlled API responses; no real accounts."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv('CALENDAR_BROWSER_OUTPUT', str(ROOT / '.codex/verification/calendar-health-browser')))
OUT.mkdir(parents=True, exist_ok=True)
GOOD = {'status': 'ok', 'risk': {'tasks': [{'title': 'Algebra', 'status': 'on_track',
        'on_time_probability': .95, 'percent': 95, 'due_date': '2026-10-30'}]}}
OUTAGE = {'status': 'error', 'error_code': 'calendar_unavailable', 'retryable': True,
          'message': '<img src="https://untrusted.invalid/secret">'}

print('Starting calendar UI verifier', flush=True)
with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None)
    for width in (1280, 390):
        print('Checking calendar UI at', width, flush=True)
        page = browser.new_page(viewport={'width': width, 'height': 900})
        errors, unexpected, held = [], [], []
        mode = ['good']

        def route(handler):
            if '/api/schedule/forecast' in handler.request.url:
                if mode[0] == 'hold':
                    held.append(handler)
                    return
                return handler.fulfill(status=503 if mode[0] == 'outage' else 200,
                                       json=OUTAGE if mode[0] == 'outage' else GOOD)
            if '/api/schedule/autopilot' in handler.request.url:
                return handler.fulfill(status=503, json=OUTAGE)
            unexpected.append(handler.request.url)
            handler.abort()

        page.route('**/*', route)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('https://fixture.test/', lambda route: route.fulfill(content_type='text/html', body='''
          <!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
          <main data-ft-mount></main><script>
          var _lastScheduleData = {schedule:[{date:'2026-10-16',blocks:[]}],autopilot:{enabled:true}};
          var _progressSyncEnabled = true;
          </script>'''))
        page.goto('https://fixture.test/')
        print('Fixture loaded', flush=True)
        page.add_style_tag(path=str(ROOT / 'static/css/followthrough.css'))
        page.add_script_tag(path=str(ROOT / 'static/js/followthrough.js'))
        page.get_by_text('Your deadline is on track', exact=False).wait_for()
        before = page.evaluate('JSON.stringify(_lastScheduleData)')
        mode[0] = 'outage'
        page.evaluate('FT.refresh()')
        warning = page.get_by_role('status').filter(has_text='Plan changes are paused')
        warning.wait_for()
        assert not page.get_by_text('Your deadline is on track', exact=False).count()
        assert page.evaluate('JSON.stringify(_lastScheduleData)') == before
        assert not page.locator('img').count()
        page.screenshot(path=str(OUT / f'calendar-outage-{width}.png'), full_page=True)

        mode[0] = 'good'
        page.evaluate('FT.refresh()')
        page.get_by_text('Your deadline is on track', exact=False).wait_for()
        assert not warning.count()
        mode[0] = 'hold'
        page.evaluate('FT.refresh()')
        page.wait_for_timeout(350)
        assert len(held) == 1
        print('Old forecast held', flush=True)
        mode[0] = 'outage'
        page.evaluate('FT.refresh()')
        warning.wait_for()
        held.pop().fulfill(status=200, json=GOOD)
        page.wait_for_timeout(200)
        assert warning.count()
        assert not page.get_by_text('Your deadline is on track', exact=False).count()

        mode[0] = 'good'
        page.evaluate('FT.refresh()')
        page.get_by_text('Your deadline is on track', exact=False).wait_for()
        mode[0] = 'hold'
        page.evaluate('FT.refresh()')
        page.wait_for_timeout(350)
        assert len(held) == 1
        mode[0] = 'good'
        page.evaluate('FT.refresh()')
        page.wait_for_timeout(350)
        held.pop().fulfill(status=404, json={})
        page.wait_for_timeout(200)
        page.evaluate('FT.refresh()')
        page.wait_for_timeout(350)
        assert page.evaluate('FT.available()')
        page.get_by_text('Your deadline is on track', exact=False).wait_for()
        # Autopilot must invalidate even an already-displayed forecast.
        page.evaluate('FT.assignmentsLoaded()')
        auto_warning = page.get_by_role('status').filter(has_text='Autopilot is paused')
        auto_warning.wait_for()
        assert not page.get_by_text('Your deadline is on track', exact=False).count()
        assert page.evaluate('JSON.stringify(_lastScheduleData)') == before
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not unexpected, unexpected
        assert not errors, errors
        page.close()
    browser.close()
    result = {'passed': True, 'widths': [1280, 390], 'outage_warning': True,
              'old_forecast_cannot_clear_outage': True, 'recovery': True,
              'old_404_cannot_disable_current_plan': True,
              'autopilot_clears_old_forecast': True, 'plan_unchanged': True,
              'untrusted_server_markup_inert': True, 'source': 'scripted API responses'}
    (OUT / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
