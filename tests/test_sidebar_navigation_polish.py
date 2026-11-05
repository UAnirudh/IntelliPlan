"""Regression coverage for the quiet, keyboard-friendly app sidebar."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_explore_destination_is_grouped_with_study_links():
    template = (ROOT / "Main_Project/templates/base.html").read_text(encoding="utf-8")
    sidebar = template.split('<div class="side-nav-list" id="sideNavList">', 1)[1].split(
        "</div>", 1
    )[0]

    assert '<h2 class="side-section-label">Study &amp; explore</h2>' in sidebar
    assert 'href="/features" data-nav-item="features"' in sidebar
    assert "Tools / Connect" not in sidebar


def test_sidebar_uses_canvas_surface_and_has_visible_keyboard_focus():
    finish = (ROOT / "static/css/ip-finish.css").read_text(encoding="utf-8")
    rail = (ROOT / "static/css/ip-sidebar-rail.css").read_text(encoding="utf-8")

    assert "body.has-side .app-side { background: var(--bg); box-shadow: none; }" in finish
    assert "box-shadow: inset 2px 0 0 var(--accent);" in finish
    assert "body.has-side .app-side :is(.side-link, .side-customize-btn, .side-search):focus-visible" in finish
    assert "outline: 2px solid var(--accent);" in finish
    assert "background: var(--bg);" in rail
