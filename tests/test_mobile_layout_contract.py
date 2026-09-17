"""Phone-layout regressions that are cheap to pin and expensive to notice.

Each assertion here stands for a bug that shipped, all four reported from a
phone:

* the dashboard ran off the side of the screen below "Today's plan"
* the nav drawer was translucent enough to read the page through it
* the theme switch was an unlabelled pill that looked like a stray dot
* Settings was one 18,000-pixel scroll with no way to see what was in it

CSS and markup are not usually worth asserting on. These are, because all
four are invisible to every other test in the suite — the page renders, the
routes answer 200, and the layout is still wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "Main_Project" / "templates"
CSS = ROOT / "static" / "css"


def read(path):
    return path.read_text(encoding="utf-8")


# ── Dashboard: the grid track that ran off the screen ────────────────


def test_the_dashboard_columns_cannot_be_widened_by_their_content():
    """`1fr` is `minmax(auto, 1fr)`, and `auto` floors the track at the
    column's min-content width. One task row with a long, nowrap title
    pushed that floor past the viewport — and since both columns stack into
    the same track on a phone, every card below the plan inherited the
    over-wide track and was clipped by main's overflow:clip."""
    css = read(TEMPLATES / "dashboard.html")
    assert "grid-template-columns: minmax(0, 1fr) 360px;" in css
    assert "grid-template-columns: minmax(0, 1fr); }" in css
    assert re.search(r"\.ipd-columns\s*>\s*\*\s*\{\s*min-width:\s*0;", css)


def test_a_bare_1fr_has_not_crept_back_into_the_dashboard_grid():
    css = read(TEMPLATES / "dashboard.html")
    assert "grid-template-columns: 1fr 360px" not in css
    assert ".ipd-columns { grid-template-columns: 1fr; }" not in css


def test_row_actions_are_reachable_without_a_hover():
    """Edit and Delete were opacity:0 until :hover, which a touch screen
    does not have — so on a phone they did not exist."""
    css = read(TEMPLATES / "dashboard.html")
    assert re.search(r"\.ipd-task-actions\s*\{\s*opacity:\s*1;\s*\}", css)


# ── The drawer ───────────────────────────────────────────────────────


def test_the_drawer_is_not_see_through():
    """A slide-over panel the page shows through is not frosted, it is
    unreadable: every label competes with whatever is behind it."""
    mobile = read(CSS / "mobile.css")
    fill = re.search(r"--drawer-fill:\s*rgba\([^)]*,\s*([0-9.]+)\)", mobile)
    assert fill, "the drawer fill is gone"
    assert float(fill.group(1)) >= 0.95

    base = read(TEMPLATES / "base.html")
    nav_rule = base[base.find("#mainNav {", base.find("@supports")):][:220]
    assert "--drawer-fill" in nav_rule
    assert "--chrome-glass)" not in nav_rule


def test_the_fixed_header_glass_still_reads_as_glass_but_not_as_a_window():
    base = read(TEMPLATES / "base.html")
    for alpha in re.findall(r"--chrome-glass:\s*rgba\([^)]*,\s*([0-9.]+)\)", base):
        assert 0.8 <= float(alpha) < 1.0, "header glass is either a window or a wall"


# ── The theme switch ─────────────────────────────────────────────────


def test_the_theme_switch_is_an_icon_button_on_a_phone():
    """A 46x24 pill with a black dot in it, no caption (the label is hidden
    below 1100px), sitting next to a square menu button."""
    mobile = read(CSS / "mobile.css")
    block = mobile[mobile.find(".header-actions .toggle {"):]
    block = block[:block.find("#mobileMenuBtn {")]
    assert "--ip-icon-sun" in block and "--ip-icon-moon" in block
    assert "mask-image: var(--ip-icon-sun)" in block
    assert "mask-image: var(--ip-icon-moon)" in block
    # The icons are masks so they take the theme's own text colour rather
    # than a colour baked into an image.
    assert "background-color: var(--text-primary)" in block


def test_the_switch_and_the_menu_button_are_the_same_size():
    """They sit side by side; a pair that does not match reads as a stray
    control next to a real one."""
    mobile = read(CSS / "mobile.css")
    start = mobile.find(".header-actions .toggle {")
    toggle = mobile[start:mobile.find("}", start)]
    assert "width: 44px;" in toggle and "height: 44px;" in toggle
    menu_start = mobile.find("#mobileMenuBtn {", start)
    menu = mobile[menu_start:mobile.find("}", menu_start)]
    assert "width: 44px !important;" in menu
    assert "height: 44px !important;" in menu


# ── Settings ─────────────────────────────────────────────────────────


def test_settings_collapses_into_sections_on_a_phone():
    html = read(TEMPLATES / "settings.html")
    assert 'id="settingsJump"' in html
    assert "settingsAccordion" in html
    assert ".settings-head {" in html
    assert "matchMedia('(max-width: 768px)')" in html


def test_the_accordion_is_phone_only():
    """Desktop has the room; collapsing there would only add clicks."""
    html = read(TEMPLATES / "settings.html")
    guard = html[html.find("function settingsAccordion"):][:400]
    assert "if (!phone.matches) return;" in guard


def test_a_link_into_a_section_opens_it():
    """The command palette links /settings#themes, and the a11y prompt links
    #accessibilitySettings. Landing on a collapsed heading is a dead end."""
    html = read(TEMPLATES / "settings.html")
    assert 'id="themes"' in html, "the palette's #themes link still lands nowhere"
    assert "openFromHash" in html
    assert "hashchange" in html


def test_a_focused_field_is_never_left_inside_a_shut_section():
    """Autofill and validation both move focus without a click."""
    html = read(TEMPLATES / "settings.html")
    assert "focusin" in html


def test_the_selected_profile_tab_is_not_white_on_white():
    """A blanket `body button:not(...)` glass rule repainted the active tab
    with a white fill while it kept its white label, so the selected tab was
    an invisible control. [aria-pressed] is on that rule's exclusion list
    and is the honest markup for a tab bar anyway."""
    html = read(TEMPLATES / "settings.html")
    assert 'data-pane="basics" aria-pressed="true"' in html
    assert "tab.setAttribute('aria-pressed', 'true')" in html
    assert 'background: var(--accent) !important;' in html


def test_focus_area_pills_have_a_surface_on_a_phone():
    """backdrop-filter is off on mobile by policy, so a glass pill is a
    white shape on a white card: twelve subjects rendered as blank space."""
    html = read(TEMPLATES / "settings.html")
    block = html[html.find(".focus-pill label {", html.find("@media (max-width: 768px)")):][:400]
    assert "background: var(--bg) !important;" in block
    assert "border-color: var(--border-strong) !important;" in block


@pytest.mark.parametrize("selector", [
    ".settings-section input[type=\"text\"]",
    ".settings-section select",
    ".settings-section textarea",
])
def test_form_controls_do_not_make_ios_zoom(selector):
    """Under 16px, Safari zooms the whole page when the field takes focus
    and does not zoom back out."""
    html = read(TEMPLATES / "settings.html")
    assert selector in html
    block = html[html.find(".settings-section input[type=\"text\"]"):][:700]
    assert "font-size: 16px;" in block
