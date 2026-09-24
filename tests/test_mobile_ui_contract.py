"""Guards for the phone UI fixes.

1. The blanket glass rule in ip-base.css used to set
   ``background: var(--glass-btn-bg) !important`` on every <button>. Pages
   style active tabs and primary actions as "accent fill + white label"; the
   blanket rule kept the white label and replaced the fill, so "Basics",
   "Pomodoro 25/5", "Calculate what I need", "Send reset link", "Extract
   tasks" and a dozen more rendered white-on-white (contrast ~1.0:1) on
   desktop and phone. Glass is now a zero-specificity default a page can
   override.

2. On phones the floating feedback bubble moved into the menu. The menu
   entry has to exist and has to be wired, or phone users lose feedback.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_CSS = (ROOT / "static" / "css" / "ip-base.css").read_text(encoding="utf-8")
PHONE_CSS = (ROOT / "static" / "css" / "ip-phone.css").read_text(encoding="utf-8")
BASE_HTML = (ROOT / "Main_Project" / "templates" / "base.html").read_text(encoding="utf-8")


def test_generic_button_glass_cannot_override_a_page_button():
    assert not re.search(r"glass-btn-bg\)\s*!important", BASE_CSS)
    assert not re.search(r"glass-btn-hover\)\s*!important", BASE_CSS)


def test_generic_button_glass_is_zero_specificity():
    rule = BASE_CSS[BASE_CSS.index("Everything else that's a button"):]
    rule = rule[:rule.index("{")]
    assert ":where(" in rule


def test_feedback_is_reachable_from_the_phone_menu():
    assert "data-ip-feedback-open" in BASE_HTML
    assert "querySelectorAll('[data-ip-feedback-open]')" in BASE_HTML
    # Hidden on desktop, where the floating button still exists.
    assert ".nav-feedback { display: none; }" in PHONE_CSS


def test_the_floating_trigger_is_hidden_only_on_phones():
    phone_block = PHONE_CSS[PHONE_CSS.index("THE FLOATING LAYER"):]
    assert "@media (max-width: 768px)" in phone_block
    assert ".ip-fb .ip-fb-trigger" in phone_block
