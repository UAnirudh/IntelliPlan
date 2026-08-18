"""The download page must never offer a link it cannot honour.

Everything here is about the failure modes, because the happy path is the
one anyone would test by hand. A download page that 500s when GitHub is
slow, or renders buttons pointing at a release that was deleted, is worse
than one that says the builds are not ready.
"""

from __future__ import annotations

import time
import urllib.error

import pytest

import App
import desktop_releases as dr


@pytest.fixture(autouse=True)
def clear_cache():
    dr._cache.update({"at": 0.0, "value": None, "ok": False})
    yield
    dr._cache.update({"at": 0.0, "value": None, "ok": False})


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


def asset(name, size=1024, url="https://example.test/x"):
    return {"name": name, "size": size, "browser_download_url": url}


def release(tag="desktop-v1.2.3", assets=None, draft=False):
    return {
        "tag_name": tag,
        "draft": draft,
        "published_at": "2026-08-14T04:40:00Z",
        "html_url": "https://example.test/release",
        "assets": assets if assets is not None else [asset("IntelliPlan-Setup-1.2.3-x64.exe")],
    }


# ── Classification ──────────────────────────────────────────────────


@pytest.mark.parametrize("name,platform,arch", [
    ("IntelliPlan-Setup-1.0.0-x64.exe", "windows", "x64"),
    ("IntelliPlan-1.0.0-arm64.dmg", "mac", "arm64"),
    ("IntelliPlan-1.0.0-x64.dmg", "mac", "x64"),
    ("IntelliPlan-1.0.0-arm64.AppImage", "linux", "arm64"),
    ("IntelliPlan-1.0.0-amd64.deb", "linux", "x64"),
    ("IntelliPlan-1.0.0.x86_64.rpm", "linux", "x64"),
])
def test_installers_are_classified(name, platform, arch):
    out = dr._classify(asset(name))
    assert out is not None
    assert out["platform"] == platform
    assert out["arch"] == arch


def test_the_multi_arch_windows_installer_is_labelled_universal():
    """electron-builder suffixes the per-arch NSIS builds and leaves the
    combined one bare. Unlabelled it is the least appealing row on the page
    — an unnamed file twice the size — despite being the one that always
    works."""
    out = dr._classify(asset("IntelliPlan-Setup-1.0.0.exe"))
    assert out["platform"] == "windows"
    assert out["arch"] == "universal"


def test_the_windows_zip_is_not_mistaken_for_a_mac_one():
    """Both platforms ship IntelliPlan-<version>-...-arm64.zip into the same
    release. Without the -win- marker the Windows native ARM64 build would be
    offered to Mac users and absent from the Windows list."""
    win = dr._classify(asset("IntelliPlan-1.0.0-win-arm64.zip"))
    assert win["platform"] == "windows"
    assert win["arch"] == "arm64"

    mac = dr._classify(asset("IntelliPlan-1.0.0-arm64.zip"))
    assert mac["platform"] == "mac"


def test_the_windows_installer_still_outranks_the_windows_zip():
    """The zip exists because ARM64 has no installer, not because it is a
    better download. An installer must stay the lead option."""
    exe = dr._classify(asset("IntelliPlan-Setup-1.0.0-x64.exe"))
    zip_ = dr._classify(asset("IntelliPlan-1.0.0-win-arm64.zip"))
    assert exe["rank"] < zip_["rank"]


def test_other_platforms_keep_an_empty_arch_when_it_is_unknown():
    """Only Windows ships a combined installer. Guessing 'universal' for a
    dmg or a deb would put a claim on the page we cannot support."""
    assert dr._classify(asset("IntelliPlan-1.0.0.dmg"))["arch"] == ""
    assert dr._classify(asset("IntelliPlan-1.0.0.AppImage"))["arch"] == ""


@pytest.mark.parametrize("name", [
    "IntelliPlan-Setup-1.0.0-x64.exe.blockmap",
    "latest.yml",
    "latest-mac.yml",
    "checksums.sha256",
    "IntelliPlan.exe.sig",
    "README.md",
])
def test_updater_metadata_is_not_offered_as_a_download(name):
    """These sit beside the installers in every release. Listing them gives
    a student a file that does nothing when they open it."""
    assert dr._classify(asset(name)) is None


# ── Choosing a release ──────────────────────────────────────────────


def test_the_newest_desktop_release_wins(monkeypatch):
    # GitHub returns releases newest-first, so the first usable one wins.
    payload = [release(tag="desktop-v2.0.0"), release(tag="desktop-v1.0.0")]
    monkeypatch.setattr(dr, "_fetch_latest", lambda: _select(payload))
    assert dr.latest_release()["version"] == "2.0.0"


def _select(payload):
    """Mirror of _fetch_latest's selection over a literal payload."""
    for rel in payload:
        tag = rel.get("tag_name") or ""
        if not tag.startswith(dr.TAG_PREFIX) or rel.get("draft"):
            continue
        assets = [a for a in (dr._classify(a) for a in rel.get("assets") or []) if a]
        if not assets:
            continue
        assets.sort(key=lambda a: (a["platform"], a["rank"], a["name"]))
        return {"version": tag[len(dr.TAG_PREFIX):], "tag": tag,
                "published_at": rel.get("published_at"),
                "notes_url": rel.get("html_url"), "assets": assets}
    return {"version": None, "tag": None, "published_at": None,
            "notes_url": None, "assets": []}


def test_a_non_desktop_tag_is_ignored():
    """Web deploys and extension builds get tagged too. One of those must
    never be advertised as the desktop app."""
    out = _select([release(tag="v9.9.9"), release(tag="desktop-v1.0.0")])
    assert out["version"] == "1.0.0"


def test_a_draft_release_is_ignored():
    out = _select([release(tag="desktop-v3.0.0", draft=True),
                   release(tag="desktop-v1.0.0")])
    assert out["version"] == "1.0.0"


def test_a_release_with_no_installers_is_skipped():
    """A tag pushed before the build finished has notes and no binaries."""
    out = _select([release(tag="desktop-v3.0.0", assets=[asset("latest.yml")]),
                   release(tag="desktop-v1.0.0")])
    assert out["version"] == "1.0.0"


# ── Failure handling ────────────────────────────────────────────────


def test_a_github_outage_yields_no_assets_rather_than_an_exception(monkeypatch):
    def boom():
        raise urllib.error.URLError("dns is having a day")
    monkeypatch.setattr(dr, "_fetch_latest", boom)

    out = dr.latest_release()
    assert out["assets"] == []
    assert out["unavailable"] is True


def test_a_good_answer_survives_a_later_failure(monkeypatch):
    """A blip must not blank out links that were working a minute ago."""
    monkeypatch.setattr(dr, "_fetch_latest", lambda: _select([release()]))
    good = dr.latest_release()
    assert good["assets"]

    def boom():
        raise TimeoutError("slow")
    monkeypatch.setattr(dr, "_fetch_latest", boom)
    dr._cache["at"] = 0.0                      # force a re-fetch

    after = dr.latest_release()
    assert after["assets"], "the previous good release should still be served"


def test_the_result_is_cached(monkeypatch):
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return _select([release()])
    monkeypatch.setattr(dr, "_fetch_latest", counted)

    dr.latest_release()
    dr.latest_release()
    dr.latest_release()
    assert calls["n"] == 1


def test_the_cache_expires(monkeypatch):
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return _select([release()])
    monkeypatch.setattr(dr, "_fetch_latest", counted)

    dr.latest_release()
    dr._cache["at"] = time.time() - (dr.CACHE_TTL_SECONDS + 1)
    dr.latest_release()
    assert calls["n"] == 2


# ── The page ────────────────────────────────────────────────────────


def test_the_page_says_so_when_there_is_nothing_to_download(client, monkeypatch):
    monkeypatch.setattr(dr, "_fetch_latest",
                        lambda: {"version": None, "tag": None, "published_at": None,
                                 "notes_url": None, "assets": []})
    body = client.get("/download").get_data(as_text=True)
    assert client.get("/download").status_code == 200
    assert "aren't published yet" in body
    assert "All downloads" not in body


def test_the_page_lists_every_installer(client, monkeypatch):
    monkeypatch.setattr(dr, "_fetch_latest", lambda: _select([release(assets=[
        asset("IntelliPlan-Setup-1.2.3-x64.exe", 96_468_992, "https://example.test/w.exe"),
        asset("IntelliPlan-1.2.3-arm64.dmg", 109_051_904, "https://example.test/m.dmg"),
        asset("IntelliPlan-1.2.3-x64.AppImage", 123_731_968, "https://example.test/l.AppImage"),
        asset("IntelliPlan-Setup-1.2.3-x64.exe.blockmap", 1024, "https://example.test/b"),
    ])]))
    body = client.get("/download").get_data(as_text=True)
    assert "https://example.test/w.exe" in body
    assert "https://example.test/m.dmg" in body
    assert "https://example.test/l.AppImage" in body
    assert "blockmap" not in body


def test_the_page_warns_that_builds_are_unsigned(client, monkeypatch):
    """A student who hits a SmartScreen warning with no warning assumes the
    download is malware."""
    monkeypatch.setattr(dr, "_fetch_latest", lambda: _select([release()]))
    body = client.get("/download").get_data(as_text=True)
    assert "not code-signed" in body
    assert "SmartScreen" in body
    assert "Gatekeeper" in body


def test_the_api_mirrors_the_page(client, monkeypatch):
    monkeypatch.setattr(dr, "_fetch_latest", lambda: _select([release()]))
    body = client.get("/api/desktop/latest").get_json()
    assert body["status"] == "ok"
    assert body["version"] == "1.2.3"
    assert body["platforms"]["windows"][0]["url"] == "https://example.test/x"


def test_the_api_is_honest_when_github_is_unreachable(client, monkeypatch):
    def boom():
        raise urllib.error.HTTPError("u", 503, "nope", {}, None)
    monkeypatch.setattr(dr, "_fetch_latest", boom)
    body = client.get("/api/desktop/latest").get_json()
    assert body["unavailable"] is True
    assert body["platforms"] == {"windows": [], "mac": [], "linux": []}


def test_human_size():
    assert dr.human_size(0) == ""
    assert dr.human_size(96_468_992) == "92 MB"
    assert dr.human_size(2 * 1024 ** 3) == "2.0 GB"
