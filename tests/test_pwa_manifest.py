import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pwa_manifest_screenshots_reference_existing_static_assets():
    manifest = json.loads((ROOT / "static" / "manifest.json").read_text(encoding="utf-8"))

    for screenshot in manifest.get("screenshots", []):
        src = screenshot.get("src", "")
        assert src.startswith("/static/"), f"Screenshot is outside the static asset tree: {src!r}"
        asset_path = ROOT / src.lstrip("/")
        assert asset_path.is_file(), f"PWA screenshot is missing from the static asset tree: {src}"
