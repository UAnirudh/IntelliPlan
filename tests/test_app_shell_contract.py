"""Guards for the phone app shell (glass tab bar + Menu sheet).

The signed-in website on a phone mirrors the mobile app: six tabs in the
app's order and a Menu sheet built from the sidebar. These checks keep the
two from drifting and keep the shell off desktop and off public pages.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "Main_Project" / "templates"
BASE_HTML = (TPL / "base.html").read_text(encoding="utf-8")
TABBAR = (TPL / "_app_tabbar.html").read_text(encoding="utf-8")
SHELL_CSS = (ROOT / "static" / "css" / "ip-app-shell.css").read_text(encoding="utf-8")
SHELL_JS = (ROOT / "static" / "js" / "ip-app-shell.js").read_text(encoding="utf-8")
APP_TABS = (ROOT / "mobile" / "app" / "(tabs)" / "_layout.tsx").read_text(encoding="utf-8")


def _tab_labels(html: str) -> list[str]:
    nav = html[html.index('<nav class="ip-tabbar"'):html.index("</nav>")]
    return re.findall(r"<span>([^<]+)</span>", nav)


def test_web_tabs_match_the_app_tabs_in_order():
    app = re.findall(r'title:\s*"([^"]+)"', APP_TABS)
    assert _tab_labels(TABBAR) == app == ["Today", "Due", "Plan", "Grades", "Plani", "Menu"]


def test_shell_only_renders_for_signed_in_app_pages():
    assert "_app_tabbar = logged_in and active_page in _app_pages" in BASE_HTML
    include = BASE_HTML.index('{% include "_app_tabbar.html" %}')
    guard = BASE_HTML.rfind("{% if _app_tabbar %}", 0, include)
    assert guard != -1 and include - guard < 40


def test_shell_is_phone_only():
    # Everything outside the phone media query only hides the shell.
    head = SHELL_CSS[:SHELL_CSS.index("@media (max-width: 768px)")]
    rules = re.sub(r"/\*.*?\*/", "", head, flags=re.S)
    assert re.fullmatch(r"\s*\.ip-tabbar,\s*\.ip-menu-sheet,\s*\.ip-menu-scrim\s*\{\s*display:\s*none;\s*\}\s*", rules)


def test_menu_is_built_from_the_sidebar_and_keeps_account_actions():
    assert "sideNavList" in SHELL_JS
    for needle in ('href="/settings"', "data-ip-feedback-open", 'action="/logout"'):
        assert needle in TABBAR


def test_shell_honours_reduced_transparency_and_motion():
    assert "prefers-reduced-transparency: reduce" in SHELL_CSS
    assert "prefers-reduced-motion: reduce" in SHELL_CSS


def test_shell_buttons_opt_out_of_the_global_button_skin():
    for tag in re.findall(r"<button[^>]*>", TABBAR):
        assert "no-glass" in tag, tag
