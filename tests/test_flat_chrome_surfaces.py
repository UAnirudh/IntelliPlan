"""Persistent reward and secondary surfaces use the same flat theme tokens."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _rule(source: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source, re.S)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def test_global_reward_chrome_uses_flat_theme_fills():
    source = (ROOT / "Main_Project/templates/base.html").read_text(encoding="utf-8")
    for selector in (
        ".nav-streak-badge",
        ".side-streak-badge",
        ".side-pet-badge",
        ".pet-xp-toast",
        ".pet-evolve-flash",
    ):
        rule = _rule(source, selector)
        assert "gradient(" not in rule, selector

    assert "background: var(--medium-bg);" in _rule(source, ".nav-streak-badge")
    assert "background: var(--ink);" in _rule(source, ".pet-xp-toast")
    assert "color: var(--on-ink);" in _rule(source, ".pet-xp-toast")


def test_primer_and_secondary_surfaces_keep_flat_semantic_tints():
    primer = (ROOT / "static/css/foundations.css").read_text(encoding="utf-8")
    admin = (ROOT / "Main_Project/templates/admin.html").read_text(encoding="utf-8")
    groups = (ROOT / "Main_Project/templates/groups.html").read_text(encoding="utf-8")

    assert "background:var(--bg-elevated)" in _rule(primer, ".primer-scene")
    assert "gradient(" not in _rule(primer, ".primer-scene")
    assert "background:var(--high-bg)" in _rule(admin, ".admin-banner")
    assert "gradient(" not in _rule(admin, ".admin-banner")
    assert "background:var(--accent-soft)" in _rule(groups, ".gr-card.member")
    assert "gradient(" not in _rule(groups, ".gr-card.member")
