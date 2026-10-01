"""Pin the Electron update and the renderer's security boundary."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_manifest_and_lockfile_resolve_electron_43_5_0():
    package = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "desktop" / "package-lock.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["electron"] == "^43.5.0"
    assert lock["packages"]["node_modules/electron"]["version"] == "43.5.0"


def test_electron_renderer_keeps_isolation_and_navigation_guards():
    source = (ROOT / "desktop" / "src" / "main.js").read_text(encoding="utf-8")
    assert "contextIsolation: true" in source
    assert "nodeIntegration: false" in source
    assert "sandbox: true" in source
    assert "setWindowOpenHandler" in source
    assert "will-navigate" in source
