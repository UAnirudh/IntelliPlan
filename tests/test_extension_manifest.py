"""The extension manifests parse, and the shipping one declares what Focus
Shield needs.

No browser here: Chrome rejects an unpacked extension whose manifest is not
valid JSON, references a file that is not there, or calls an API it never
asked permission for -- and all three are checkable as plain files.

``extension/Testing/`` is the copy that ships (it has the zip builder and
the district-LMS scrapers); ``extension/`` at the top is the older copy and
is only checked for being loadable.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIPPING = ROOT / "extension" / "Testing"
MANIFESTS = [ROOT / "extension" / "manifest.json", SHIPPING / "manifest.json"]


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_manifest_is_valid_mv3_json(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert re.fullmatch(r"\d+(\.\d+){1,3}", manifest["version"])
    worker = manifest["background"]["service_worker"]
    assert (path.parent / worker).is_file()


def _shipping():
    return json.loads((SHIPPING / "manifest.json").read_text(encoding="utf-8"))


def test_shipping_manifest_declares_focus_shield_permissions():
    manifest = _shipping()
    perms = set(manifest["permissions"])
    # dynamic block rules; the poll + block-edge alarms; the cached plan;
    # sending a tab on a blocked site to the friendly page.
    assert {"declarativeNetRequest", "alarms", "storage", "tabs"} <= perms
    # Not asked for: Focus Shield never reads page content or history.
    assert not perms & {"history", "webRequest", "webRequestBlocking", "<all_urls>"}
    assert "https://intelliplan.tech/*" in manifest["host_permissions"]


def test_shipping_version_was_bumped_past_1_4_0():
    version = tuple(int(x) for x in _shipping()["version"].split("."))
    assert version > (1, 4, 0)


def test_every_file_the_extension_loads_exists():
    manifest = _shipping()
    referenced = [manifest["background"]["service_worker"], manifest["action"]["default_popup"],
                  manifest["options_page"], "blocked.html", "blocked.js"]
    for block in manifest.get("content_scripts", []):
        referenced += block.get("js", [])
    referenced += list(manifest["icons"].values())
    missing = [p for p in referenced if not (SHIPPING / p).is_file()]
    assert not missing, missing


def test_the_zip_builder_packs_the_block_page():
    """The builder's file list is hand-maintained for non-content-script
    files; a block page left out of the zip is a blank tab in production."""
    tree = ast.parse((SHIPPING / "_build_zip.py").read_text(encoding="utf-8"))
    entries = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ENTRIES"
    )
    packed = {name for name, _ in entries}
    assert {"blocked.html", "blocked.js", "background.js", "manifest.json"} <= packed


def test_the_block_page_has_no_inline_script():
    """MV3's extension-page CSP refuses inline <script>; the page would
    render with dead buttons."""
    html = (SHIPPING / "blocked.html").read_text(encoding="utf-8")
    for tag in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        assert not tag.strip()
    assert '<script src="blocked.js"></script>' in html
    assert not re.search(r"\son[a-z]+\s*=", html)


def test_background_uses_dynamic_rules_and_the_current_block_endpoint():
    js = (SHIPPING / "background.js").read_text(encoding="utf-8")
    assert "chrome.declarativeNetRequest.updateDynamicRules" in js
    assert "/extension/focus/current" in js
    # Existing features are still wired.
    assert "checkDueAssignments" in js and "intelliplan_sync_push" in js
