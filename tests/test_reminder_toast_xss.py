"""Reminder toast content comes from user-controlled assignment records."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_TEMPLATE = ROOT / "Main_Project" / "templates" / "base.html"


def test_reminder_toast_renders_dynamic_fields_as_text():
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    start = source.index("function showToast(title, body)")
    end = source.index("\n  function fireNotification(task)", start)
    toast = source[start:end]

    assert ".innerHTML" not in toast
    assert "heading.textContent = title" in toast
    assert "message.textContent = body" in toast
    assert "createElementNS('http://www.w3.org/2000/svg', 'use')" in toast
