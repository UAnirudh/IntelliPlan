"""Screenshot one blog article at desktop and phone width, and report overflow.

Run the preview server first (scripts/preview_blog_server.py), then:

    python scripts/screenshot_blog_article.py <slug> [base_url]

Exits non-zero if either width scrolls horizontally, so a layout regression
is visible in a shell as well as in the image.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "screenshots"
DEFAULT_BASE = "http://127.0.0.1:5063"
VIEWPORTS = (("desktop", 1440, 900), ("phone", 390, 844))

METRICS_JS = """() => ({
  innerWidth: window.innerWidth,
  scrollWidth: document.documentElement.scrollWidth,
  articleWidth: Math.round(document.querySelector('.blog-page')?.getBoundingClientRect().width ?? 0),
  overflowing: [...document.querySelectorAll('body *')]
    .filter(el => el.getBoundingClientRect().right > window.innerWidth + 1)
    .slice(0, 8)
    .map(el => el.tagName + (el.className ? '.' + String(el.className).split(' ')[0] : '')),
})"""


def main(slug: str, base: str) -> int:
    OUT.mkdir(exist_ok=True)
    url = f"{base.rstrip('/')}/blog/{slug}"
    overflowed = False

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, width, height in VIEWPORTS:
            page = browser.new_page(
                viewport={"width": width, "height": height},
                # Reduced motion leaves below-the-fold headings visible: the
                # scroll reveal would otherwise keep them at opacity 0 in a
                # full-page capture.
                reduced_motion="reduce",
            )
            page.goto(url, wait_until="networkidle")
            metrics = page.evaluate(METRICS_JS)
            target = OUT / f"blog-{slug}-{name}.png"
            page.screenshot(path=str(target), full_page=True)
            overflowed |= metrics["scrollWidth"] > metrics["innerWidth"]
            print(f"{name:8} {metrics} -> {target.name}")
            page.close()
        browser.close()

    if overflowed:
        print("horizontal overflow detected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: screenshot_blog_article.py <slug> [base_url]")
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BASE))
