"""Where the desktop download links come from.

The installers are GitHub Release assets, not files in this repo: they are
a couple of hundred megabytes each, they are rebuilt on every release, and
three of the six can only be produced on runners we do not own.

So the download page asks GitHub what the latest release actually contains
rather than hardcoding filenames. Hardcoded ones rot the first time a
version number changes, and a download page with dead links is worse than
one that admits it has nothing to offer yet.

Everything here degrades rather than fails. No network, rate limit, no
release published yet — all produce an empty asset list, and the page says
the builds are not ready instead of 500ing or, worse, rendering buttons
that 404.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

#: The repo the workflow publishes to. Overridable so a fork does not
#: advertise this project's binaries.
GITHUB_REPO = os.getenv("DESKTOP_RELEASE_REPO", "UAnirudh/IntelliPlan")

#: Tags the desktop workflow creates. Release tags for anything else — a
#: web deploy, an extension build — must not be mistaken for an app build.
TAG_PREFIX = "desktop-v"

#: GitHub's unauthenticated rate limit is 60 requests an hour per IP, which
#: a download page can burn through in a minute. One request an hour is
#: plenty for something that changes on release days only.
CACHE_TTL_SECONDS = 3600

#: A failed lookup is cached too, briefly, so an outage does not turn every
#: page load into another timeout.
ERROR_CACHE_TTL_SECONDS = 120

_TIMEOUT = 6

#: Extension → (platform, human label, sort rank). Rank decides which
#: download a platform leads with when it ships more than one format.
_KINDS: dict[str, tuple[str, str, int]] = {
    ".exe":      ("windows", "Windows installer", 0),
    ".dmg":      ("mac",     "macOS disk image", 0),
    ".zip":      ("mac",     "macOS (zip)", 1),
    ".appimage": ("linux",   "Linux AppImage", 0),
    ".deb":      ("linux",   "Debian / Ubuntu", 1),
    ".rpm":      ("linux",   "Fedora / RHEL", 2),
}

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "value": None, "ok": False}


def _arch_of(name: str) -> str:
    """Best-effort architecture from the filename.

    electron-builder puts it there via artifactName. It is a label for the
    student ("Apple silicon" vs "Intel"), not something we branch on, so
    guessing wrong is cosmetic.
    """
    low = name.lower()
    if "arm64" in low or "aarch64" in low:
        return "arm64"
    if "x64" in low or "amd64" in low or "x86_64" in low:
        return "x64"
    return ""


def _classify(asset: dict) -> dict | None:
    name = asset.get("name") or ""
    lower = name.lower()
    # Update metadata and delta blocks are for the auto-updater, not for a
    # person to click.
    if lower.endswith((".blockmap", ".yml", ".yaml", ".sha256", ".sig")):
        return None
    # A .zip is a macOS build by default, but Windows ships one too — it is
    # the native ARM64 build, which has no installer because electron-builder
    # cannot currently produce a working ARM64 NSIS package (see
    # desktop/README.md). Both would otherwise be called
    # IntelliPlan-<version>-arm64.zip and land in the same release, so the
    # Windows one carries a -win- marker and is matched before the table.
    if lower.endswith(".zip") and "-win-" in lower:
        return {
            "name": name,
            "url": asset.get("browser_download_url"),
            "size": int(asset.get("size") or 0),
            "platform": "windows",
            "label": "Windows (zip, no installer)",
            "rank": 1,
            "arch": _arch_of(name),
        }

    for ext, (platform, label, rank) in _KINDS.items():
        if lower.endswith(ext):
            arch = _arch_of(name)
            # electron-builder builds one NSIS installer per architecture
            # *and* a combined one carrying all of them, and only the
            # per-arch names get a suffix. Left unlabelled the combined
            # build reads as the vaguest option on the page — an unnamed
            # file twice the size of the others — when it is in fact the
            # one that runs anywhere. Say so.
            if not arch and platform == "windows":
                arch = "universal"
            return {
                "name": name,
                "url": asset.get("browser_download_url"),
                "size": int(asset.get("size") or 0),
                "platform": platform,
                "label": label,
                "rank": rank,
                "arch": arch,
            }
    return None


def _fetch_latest() -> dict:
    """Ask GitHub for the newest desktop release. Never raises."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=10"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "IntelliPlan-download-page",
    })
    token = os.getenv("GITHUB_TOKEN")
    if token:
        # Only to raise the rate limit. The endpoint is public either way.
        req.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        releases = json.loads(resp.read().decode("utf-8"))

    for release in releases:
        tag = release.get("tag_name") or ""
        if not tag.startswith(TAG_PREFIX) or release.get("draft"):
            continue
        assets = [a for a in (_classify(a) for a in release.get("assets") or []) if a]
        if not assets:
            continue
        assets.sort(key=lambda a: (a["platform"], a["rank"], a["name"]))
        return {
            "version": tag[len(TAG_PREFIX):] or tag,
            "tag": tag,
            "published_at": release.get("published_at"),
            "notes_url": release.get("html_url"),
            "assets": assets,
        }
    return {"version": None, "tag": None, "published_at": None,
            "notes_url": None, "assets": []}


def latest_release(force: bool = False) -> dict:
    """Cached view of the newest desktop release.

    Returns the same shape whether the lookup worked or not; callers check
    ``assets`` and say "not ready yet" when it is empty.
    """
    now = time.time()
    with _lock:
        cached = _cache["value"]
        age = now - _cache["at"]
        ttl = CACHE_TTL_SECONDS if _cache["ok"] else ERROR_CACHE_TTL_SECONDS
        if cached is not None and not force and age < ttl:
            return cached

    try:
        value = _fetch_latest()
        ok = True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, OSError) as e:
        print(f"[desktop] release lookup failed: {e}")
        value = {"version": None, "tag": None, "published_at": None,
                 "notes_url": None, "assets": [], "unavailable": True}
        ok = False

    with _lock:
        # Keep a good answer rather than replacing it with a failure: a
        # blip should not blank out download links that were fine a minute
        # ago.
        if not ok and _cache["ok"] and _cache["value"]:
            _cache["at"] = now - (CACHE_TTL_SECONDS - ERROR_CACHE_TTL_SECONDS)
            return _cache["value"]
        _cache.update({"at": now, "value": value, "ok": ok})
    return value


def by_platform(release: dict) -> dict[str, list[dict]]:
    """Group a release's assets under ``windows`` / ``mac`` / ``linux``."""
    grouped: dict[str, list[dict]] = {"windows": [], "mac": [], "linux": []}
    for asset in release.get("assets") or []:
        grouped.setdefault(asset["platform"], []).append(asset)
    return grouped


def human_size(num_bytes: int) -> str:
    if not num_bytes:
        return ""
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"
