"""Exercise the production declarative renderer in Chromium, without providers."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv('TUTOR_BROWSER_OUTPUT', str(ROOT / '.codex/verification/tutor-browser')))
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / 'static/js/tutor-artifacts.js').read_text(encoding='utf-8')
template = (ROOT / 'Main_Project/templates/tutor.html').read_text(encoding='utf-8')
styles = template[template.index('.tutor-artifact {'):template.index('.tutor-modality {')]

with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None)
    for width in (1280, 390):
        page = browser.new_page(viewport={'width': width, 'height': 900})
        errors, outbound = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: (outbound.append(route.request.url), route.abort()))
        page.set_content('''<!doctype html><html lang="en"><meta charset="utf-8">
          <title>Tutor exercise verification</title>
          <style>*{box-sizing:border-box}:root{--text-primary:#ece7df;--text-secondary:#b5b0a8;
          --border:#555;--border-strong:#777;--bg-elevated:#25221e;--accent:#729ce0}
          body{background:#17150f;color:#ece7df;font:16px system-ui;margin:20px}
          main{max-width:600px;margin:auto}.tutor-artifact{border:1px solid #777;border-radius:12px;margin:16px 0}
          .tutor-artifact-head{padding:12px;border-bottom:1px solid #777}
          </style><main id="exercises"></main></html>''')
        page.add_style_tag(content=styles)
        page.add_script_tag(content=source)

        def create(kind, config, title='Explore'):
            page.evaluate('''segment => document.querySelector('main').appendChild(
                window.IPTutorArtifacts.create(segment))''', {
                    'type': kind, 'title': title,
                    'content': json.dumps(config) if isinstance(config, (dict, list)) else config})

        create('svg-exercise', {'kind': 'linear', 'slope': 1, 'intercept': 0,
                               'question': 'Predict what a steeper slope will change.'}, 'Explore a line')
        slope = page.get_by_role('slider', name='Slope', exact=True)
        line = page.locator('svg line')
        before = line.get_attribute('x2')
        slope.focus()
        slope.press('ArrowRight')
        assert slope.input_value() == '1.5'
        page.wait_for_function("before => document.querySelector('svg line').getAttribute('x2') !== before", arg=before)
        assert line.get_attribute('x2') != before
        assert 'Each step right changes y by 1.5' in page.locator('main').inner_text()
        assert page.locator('svg title').text_content() and page.locator('svg desc').text_content()
        # Rapid input events coalesce to a frame, retaining the same SVG nodes.
        # Screen-reader descriptions update once with the final value as well.
        performance = page.evaluate('''async () => {
          const line=document.querySelector('svg line'), slider=document.querySelector('input[aria-label="Slope"]');
          let mutations=0;const observer=new MutationObserver(records=>mutations+=records.length);
          observer.observe(line,{attributes:true});
          for(let i=0;i<100;i++){slider.value=i%2 ? '2' : '0';slider.dispatchEvent(new Event('input',{bubbles:true}));}
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          observer.disconnect();return {sameNode:line===document.querySelector('svg line'),mutations};
        }''')
        assert performance['sameNode'] and performance['mutations'] <= 10, performance
        assert 'Each step right changes y by 2' in page.locator('main').inner_text()
        create('svg-exercise', {'kind': 'fraction', 'numerator': 3, 'denominator': 4}, 'Explore fractions')
        numerator = page.get_by_role('slider', name='Numerator', exact=True)
        numerator.focus()
        numerator.press('ArrowRight')
        page.get_by_text('4 of 4 equal parts per whole are shaded: 4/4.', exact=True).wait_for()
        numerator.press('ArrowRight')
        page.get_by_text('5 of 4 equal parts per whole are shaded: 5/4.', exact=True).wait_for()
        page.wait_for_function("document.querySelectorAll('svg')[1].querySelectorAll('rect:not([style*=\"display: none\"])').length === 8")
        assert page.locator('svg').nth(1).locator('rect:visible').count() == 8
        create('quiz', {'question': 'Which slope gives a horizontal line?',
                        'options': ['0', '2'], 'correct': 0}, 'Try a prediction')
        page.get_by_role('button', name='2', exact=True).click()
        assert 'Try again' in page.locator('p[role="status"]').first.inner_text()
        page.get_by_role('button', name='0', exact=True).click()
        assert page.get_by_role('button', name='0', exact=True).get_attribute('aria-pressed') == 'true'
        assert page.get_by_role('button', name='2', exact=True).get_attribute('aria-pressed') == 'false'
        assert 'AI-generated check' in page.locator('p[role="status"]').first.inner_text()
        assert 'not a scored mastery check' in page.locator('main').inner_text()
        create('quiz', {'question': 'Long label wrapping', 'options': ['X' * 300, 'Other'], 'correct': 0})
        long_button = page.get_by_role('button', name='X' * 300, exact=True)
        assert long_button.bounding_box()['height'] >= 44
        assert long_button.evaluate('node => node.scrollWidth <= node.clientWidth')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(OUT / f'exercises-{width}.png'), full_page=True)
        # Executable legacy HTML and malicious strings must remain inert text,
        # including iframe navigation, event handlers and foreign SVG content.
        attack = '''<script>window.pwned=1;location='https://leak.test/secret'</script>
          <img src="https://leak.test/image" onerror="window.pwned=2">
          <svg onload="window.pwned=3"><foreignObject><iframe src="https://leak.test/frame"></iframe></foreignObject></svg>'''
        create('html', attack)
        create('svg-exercise', {'kind': 'linear', 'question': attack}, attack)
        create('quiz', {'question': attack, 'options': [attack, 'ok'], 'correct': 0})
        create('svg-exercise', {'kind': 'unsupported'})
        create('quiz', {'options': [], 'correct': 100})
        create('svg-exercise', 'not json')
        page.wait_for_timeout(150)
        assert not page.evaluate('Boolean(window.pwned)')
        assert page.locator('iframe,img,foreignObject').count() == 0
        assert not outbound, outbound
        assert not errors, errors
        print(json.dumps({'width': width, 'passed': True, 'keyboard_svg': True,
                          'fraction_and_quiz': True, 'outbound_requests': len(outbound),
                          'stable_svg_under_100_inputs': performance, 'page_errors': errors}))
        page.close()
    browser.close()
