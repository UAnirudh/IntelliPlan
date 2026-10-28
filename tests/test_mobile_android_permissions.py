"""The mobile photo flow must not request broad Android media access."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_photo_picker_uses_minimum_android_permissions():
    config = json.loads((ROOT / "mobile" / "app.json").read_text(encoding="utf-8"))["expo"]
    android = config["android"]
    permissions = set(android.get("permissions", []))
    blocked = set(android.get("blockedPermissions", []))
    picker = next(plugin[1] for plugin in config["plugins"]
                  if isinstance(plugin, list) and plugin[0] == "expo-image-picker")

    assert "android.permission.CAMERA" in permissions
    assert "android.permission.POST_NOTIFICATIONS" in permissions
    assert not any(permission.startswith("android.permission.READ_MEDIA_") for permission in permissions)
    assert "android.permission.READ_EXTERNAL_STORAGE" in blocked
    assert "android.permission.READ_MEDIA_VIDEO" in blocked
    assert "android.permission.RECORD_AUDIO" in blocked
    assert picker["microphonePermission"] is False

    # Keep the iOS disclosure strings aligned with the camera and photo flows.
    info = config["ios"]["infoPlist"]
    assert info["NSPhotoLibraryUsageDescription"]
    assert info["NSCameraUsageDescription"]
