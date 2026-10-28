"""Persistent branded surfaces use solid fills instead of gradients."""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("template", "selector"),
    [
        ("gradebook.html", r"\.gp-panel::before"),
        ("landing.html", r"\.cta-outer \.cta-section"),
        ("landing.html", r'\[data-theme="dark"\] \.cta-outer \.cta-section'),
        ("deep_study.html", r"\.ds-call-shell"),
        ("deep_study.html", r"\.ds-call-avatar"),
    ],
)
def test_persistent_brand_surfaces_do_not_use_gradient_fills(template, selector):
    source = (ROOT / "Main_Project" / "templates" / template).read_text(encoding="utf-8")
    rule = re.search(selector + r"\s*\{([^}]*)\}", source)
    assert rule, f"Expected {selector} to remain a defined surface"
    assert not re.search(r"(?:linear|radial|conic)-gradient\s*\(", rule.group(1), re.I)
