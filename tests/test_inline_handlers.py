"""Every inline handler must call a function that exists.

Two buttons in this app called functions nobody had written:

  my_stats.html   onclick="ipMsOpenFeedback()"   -- reported by a user
  study.html      onclick="xMark(true|false)"    -- found by this scan

Both threw ReferenceError and did nothing. Nothing in the markup looks
wrong, the page does not error anywhere visible, and no Python test would
ever touch it -- the button simply does not work, and the only way to find
out is for somebody to click it.

So this is a scan rather than a test of one button. The specific cases are
pinned below it, because a scan that silently starts passing for the wrong
reason is worse than no scan.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / "Main_Project" / "templates").rglob("*.html"))
SCRIPTS = sorted((ROOT / "static" / "js").rglob("*.js"))

HANDLER_ATTR = re.compile(
    r'\bon(?:click|change|submit|input|mousedown|mouseup|mouseleave|touchstart|touchend)'
    r'\s*=\s*"([^"]*)"')

#: A bare call: not preceded by "." (a method on something) or by an
#: identifier character (the tail of a longer name).
BARE_CALL = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")

DEFINITION_PATTERNS = (
    re.compile(r"function\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()"),
    re.compile(r"([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?function"),
)

#: Language and platform names that are calls but not ours to define.
BUILTINS = {
    "alert", "confirm", "prompt", "print", "open", "close", "fetch", "Number",
    "String", "Boolean", "Array", "Object", "JSON", "Date", "Math", "Set",
    "Map", "RegExp", "Error", "parseInt", "parseFloat", "isNaN",
    "encodeURIComponent", "decodeURIComponent", "setTimeout", "setInterval",
    "clearTimeout", "clearInterval", "requestAnimationFrame",
    # Keywords that read like calls inside an inline expression.
    "if", "for", "while", "switch", "return", "typeof", "catch", "function",
}


def read(path):
    return path.read_text(errors="replace")


def defined_names():
    names = set()
    for path in TEMPLATES + SCRIPTS:
        text = read(path)
        for pattern in DEFINITION_PATTERNS:
            names |= set(pattern.findall(text))
    return names


def handler_calls():
    """(template name, called function) for every inline handler."""
    for path in TEMPLATES:
        for match in HANDLER_ATTR.finditer(read(path)):
            for name in BARE_CALL.findall(match.group(1)):
                yield path.name, name


def test_no_inline_handler_calls_an_undefined_function():
    known = defined_names()
    missing = {}
    for template, name in handler_calls():
        if name in known or name in BUILTINS:
            continue
        missing.setdefault(name, set()).add(template)

    assert not missing, "inline handlers calling functions that do not exist:\n" + "\n".join(
        f"  {name}()  in  {', '.join(sorted(files))}" for name, files in sorted(missing.items()))


# ── The two that were actually broken ────────────────────────────────


def test_the_my_stats_feedback_button_has_a_handler():
    """The reported bug: 'Send feedback' on My Stats did nothing."""
    assert "ipMsOpenFeedback" in defined_names()


def test_the_feedback_widget_exposes_a_way_to_open_it():
    """ipMsOpenFeedback delegates to this. The widget exposed close and
    hide but never open, so there was nothing correct to call."""
    assert "ipFbOpen" in defined_names()


def test_opening_the_widget_also_unhides_it():
    """The panel lives inside #ipFeedback. For anyone who dismissed the
    floating button with its X, opening the panel alone would open it
    inside a hidden parent -- the same dead button, harder to diagnose."""
    base = read(ROOT / "Main_Project" / "templates" / "base.html")
    body = base.split("window.ipFbOpen = function()", 1)[1].split("};", 1)[0]
    assert "fb.hidden = false" in body


def test_the_self_check_buttons_have_a_handler():
    """study.html's 'Got it' / 'Missed it'."""
    assert "xMark" in defined_names()


def test_the_self_check_row_is_shown_somewhere():
    """It was only ever set to display:none, so the buttons could not be
    reached at all -- which is why a dead handler went unnoticed."""
    study = read(ROOT / "Main_Project" / "templates" / "study.html")
    shows = re.findall(r"getElementById\('xSelfCheck'\)\.style\.display\s*=\s*'([^']+)'", study)
    assert "none" in shows, "expected it to be hidden between questions"
    assert any(v != "none" for v in shows), "nothing ever shows the self-check row"


def test_a_self_graded_miss_still_reaches_the_mastery_record():
    """Otherwise a question the student got wrong never comes back for
    review, which is the whole point of tracking mastery."""
    study = read(ROOT / "Main_Project" / "templates" / "study.html")
    body = study.split("async function xMark(correct)", 1)[1].split("\n}", 1)[0]
    assert "updateMastery" in body
    assert "incorrect" in body


# ── The scan is honest ───────────────────────────────────────────────


def test_the_scan_actually_looks_at_something():
    """A scan over zero files passes forever."""
    assert len(TEMPLATES) > 20
    assert len(SCRIPTS) > 3
    assert len(list(handler_calls())) > 100


def test_the_scan_would_catch_a_reintroduction():
    """Prove the detector fires, rather than trusting that it would."""
    known = defined_names()
    injected = list(BARE_CALL.findall('ipDefinitelyNotDefined(1, this)'))
    assert injected == ["ipDefinitelyNotDefined"]
    assert "ipDefinitelyNotDefined" not in known


@pytest.mark.parametrize("expression,expected", [
    ("foo()", ["foo"]),
    ("obj.method()", []),                       # a method, not ours
    ("document.getElementById('x')", []),
    ("foo(); bar()", ["foo", "bar"]),
    ("return foo(this)", ["foo"]),              # "return" is not a call
    ("if (x) foo()", ["if", "foo"]),            # "if (" looks like one; BUILTINS drops it
])
def test_the_call_detector_tells_calls_from_methods(expression, expected):
    assert BARE_CALL.findall(expression) == expected
