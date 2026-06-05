from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://intelliplan.tech/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    bad = page.locator("a").evaluate_all("""(els) => els.filter(a => {
      if (a.closest('.trustpilot-widget, [data-tp-widget], iframe, [class*="trustpilot"]')) return false;
      const txt = (a.innerText || '').trim();
      const al = (a.getAttribute('aria-label') || '').trim();
      const tt = (a.getAttribute('title') || '').trim();
      return !txt && !al && !tt;
    }).map(a => ({ href: a.href, html: a.outerHTML.slice(0, 150) }))""")
    print("bad count", len(bad))
    for b in bad[:15]:
        print(b)
    page.goto("https://intelliplan.tech/login", wait_until="domcontentloaded")
    print("canvas imgs", page.locator('img[src*="/static/logos/canvas.svg"]').count())
    browser.close()
