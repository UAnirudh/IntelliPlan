"""Render actual pinned chemistry assets with malformed-provider and hostile fixtures."""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv('TUTOR_BROWSER_OUTPUT', str(ROOT / 'output/playwright/chemistry')))
OUT.mkdir(parents=True, exist_ok=True)
scripts = [
    'js/vendor/rich-text/marked.umd.js', 'vendor/learning-content/purify.min.js',
    'vendor/learning-content/markdown-it.min.js', 'vendor/learning-content/katex.min.js',
    'vendor/learning-content/mhchem.min.js', 'js/ip-rich-text.js', 'js/learning_content.js',
]
fixture = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Chemistry rendering</title>' + (
    '<link rel="stylesheet" href="/static/vendor/learning-content/katex.min.css">'
    '<link rel="stylesheet" href="/static/css/rich_math.css">'
    '<link rel="stylesheet" href="/static/css/learning_hub.css">'
    '<style>*{box-sizing:border-box}body{margin:0;background:#f6f7fb;color:#202a3b;font:16px/1.7 system-ui}'
    'main{width:min(720px,100%);padding:24px;margin:auto}.tutor-bubble{min-width:0;margin:16px 0;padding:20px;'
    'border:1px solid #dce3ef;border-radius:18px;background:#fff;overflow-wrap:anywhere}'
    'pre{white-space:pre-wrap}h1{font-size:26px}.learning-content{max-width:100%}</style>'
    '<main id="content"><h1>Chemical equations</h1></main>'
) + ''.join('<script src="/static/' + path + '"></script>' for path in scripts) + '</html>'

results = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    for width in (1280, 390):
        page = browser.new_page(viewport={'width': width, 'height': 900})
        errors, outbound = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(handler):
            parsed = urlsplit(handler.request.url)
            if parsed.hostname != 'chemistry.test':
                outbound.append(handler.request.url)
                return handler.abort()
            if parsed.path == '/':
                return handler.fulfill(body=fixture, content_type='text/html')
            path = (ROOT / parsed.path.lstrip('/')).resolve()
            if path.is_relative_to(ROOT / 'static') and path.is_file():
                return handler.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
            return handler.fulfill(status=404)

        page.route('**/*', route)
        page.goto('https://chemistry.test/', wait_until='networkidle')

        def rich(source):
            return page.evaluate('''source => {
              const host=document.createElement('article'); host.className='tutor-bubble';
              host.innerHTML=IPRichText.render(source); document.querySelector('main').append(host);
              return {math:host.querySelectorAll('.katex').length, source:host.textContent,
                formulas:[...host.querySelectorAll('[data-ip-math]')].map(n=>n.dataset.ipMath)};
            }''', source)

        equation = r'\[\ce{2H2 + O2 -> 2H2O}\]'
        encoded = json.dumps({'response': {'content': [{'type': 'text', 'text': '### Balance atoms\n\n' + equation}]}})
        assert rich(encoded)['math'] == 1
        # Single backslashes from a malformed provider envelope are recovered
        # only for reviewed TeX commands; actual JSON escapes remain unchanged.
        malformed = rich(r'{"reply":"\[\ce{2H2 + O2 -> 2H2O}\]"}')
        assert malformed['math'] == 1 and malformed['formulas'] == [r'\ce{2H2 + O2 -> 2H2O}']
        fraction = rich(r'{"reply":"$$\frac{1}{2}+\frac{1}{2}=1$$"}')
        assert fraction['math'] == 1 and fraction['formulas'] == [r'\frac{1}{2}+\frac{1}{2}=1']
        assert page.evaluate('source => IPRichText.normalize(source)', json.dumps({'reply': 'A line\nA tab\tvalue and $5.'})) == 'A line\nA tab\tvalue and $5.'
        assert rich(r'\ce{NH4+ <=> NH3 + H+}')['math'] == 1
        assert rich(r'Concentration: \(\pu{1.5 mol L-1}\).')['math'] == 1
        multiline = '\\[\n\\begin{aligned}\n\\ce{H2 + Cl2 &-> 2HCl} \\\\\n\\ce{Na+ + Cl- &-> NaCl}\n\\end{aligned}\n\\]'
        assert rich(multiline)['math'] == 1
        long_equation = r'\[\ce{C6H12O6 + 6O2 -> 6CO2 + 6H2O}\quad\ce{2H2 + O2 -> 2H2O}\quad\ce{NH4+ <=> NH3 + H+}\]'
        assert rich(long_equation)['math'] == 1
        assert page.locator('.ip-math-display').last.get_attribute('tabindex') == '0'
        page.locator('.ip-math-display').last.focus()
        assert page.evaluate('document.activeElement.matches(".ip-math-display")')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT / f'chemical-equations-{width}.png'), full_page=True)
        # The separate Learning Hub renderer uses the same vetted extension.
        assert page.evaluate('''source => {
          const host=document.createElement('div');document.querySelector('main').append(host);
          return LearningContent.render(host,source) && host.querySelectorAll('.katex').length===2;
        }''', equation + '\n\n' + r'\ce{Na+ + Cl- -> NaCl}')
        assert page.evaluate('''source => {
          const host=document.createElement('div');document.querySelector('main').append(host);
          LearningContent.render(host,source);
          return host.querySelector('.learning-math-inline[tabindex="0"]') !== null;
        }''', r'\ce{C6H12O6 + 6O2 + 2H2 + O2 + NH4+ + Cl- -> 6CO2 + 6H2O + 2H2O + NH3 + HCl}')
        region = page.locator('.learning-math-inline').last
        region.focus()
        assert page.evaluate('document.activeElement.classList.contains("learning-math-inline")')
        page.keyboard.press('ArrowRight')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        attack = r'\[\htmlClass{pwned}{x}\]\n<img src="https://leak.test/img" onerror="window.pwned=1"><script>window.pwned=1</script>'
        rich(json.dumps({'reply': attack}))
        invalid = rich(r'{"reply":"\qbroken"}')
        assert invalid['math'] == 0 and 'qbroken' in invalid['source']
        assert rich(r'\[\unknownCommand{x}\]')['math'] == 0
        assert rich('\\[' + 'x' * 2001 + '\\]')['math'] == 0
        page.wait_for_timeout(100)
        assert not page.evaluate('Boolean(window.pwned)')
        assert page.locator('img,iframe,.pwned').count() == 0
        assert not outbound and not errors, (outbound, errors)
        results.append({'width': width, 'equations_and_json': True, 'learning_hub': True,
                        'keyboard_and_overflow': True, 'hostile_content_inert': True, 'errors': errors})
        page.close()
    browser.close()
(OUT / 'chemistry-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
