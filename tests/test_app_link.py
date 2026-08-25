"""Tests for the mobile→browser session hand-off primitives.

These cover the decisions in app_link.py that are load-bearing for
security. The module is pure — no Flask, no database, no clock of its own
— so every one of them is checkable without standing up an app, which is
the whole reason it was written that way.

The mirror of tests/test_desktop_auth.py, which does the same for the
hand-off running in the other direction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import app_link


# ── Codes ────────────────────────────────────────────────────────────

def test_new_code_is_unguessable_and_unique():
    codes = {app_link.new_code() for _ in range(200)}
    assert len(codes) == 200
    # 32 bytes urlsafe-base64'd is 43 characters.
    assert all(len(c) >= 43 for c in codes)


def test_code_round_trips_through_its_hash():
    code = app_link.new_code()
    assert app_link.codes_match(code, app_link.hash_code(code))


def test_a_different_code_does_not_match():
    stored = app_link.hash_code(app_link.new_code())
    assert not app_link.codes_match(app_link.new_code(), stored)


def test_the_code_itself_is_never_what_is_stored():
    code = app_link.new_code()
    assert app_link.hash_code(code) != code
    assert len(app_link.hash_code(code)) == 64


@pytest.mark.parametrize("bad", [
    None, "", "short", "../etc/passwd", "has space",
    "has/slash", "has.dot", "x" * 65,
])
def test_malformed_codes_are_rejected_before_any_lookup(bad):
    """The code arrives as a path segment a stranger controls, so shape is
    checked before it is allowed anywhere near the database."""
    assert not app_link.is_valid_code(bad)
    assert not app_link.codes_match(bad, app_link.hash_code("whatever"))


def test_empty_stored_hash_never_matches():
    """A row with no hash must not become a skeleton key."""
    assert not app_link.codes_match(app_link.new_code(), "")


# ── Expiry ───────────────────────────────────────────────────────────

def test_a_fresh_code_is_live():
    now = datetime(2026, 8, 23, 12, 0, 0)
    assert not app_link.is_expired(now, now)


def test_a_code_expires_at_the_ttl():
    now = datetime(2026, 8, 23, 12, 0, 0)
    made = now - timedelta(seconds=app_link.CODE_TTL_SECONDS)
    assert app_link.is_expired(made, now)


def test_one_second_inside_the_ttl_still_works():
    now = datetime(2026, 8, 23, 12, 0, 0)
    made = now - timedelta(seconds=app_link.CODE_TTL_SECONDS - 1)
    assert not app_link.is_expired(made, now)


def test_a_missing_timestamp_counts_as_expired():
    """Failing closed: a row with no created_at must not be eternal."""
    assert app_link.is_expired(None, datetime(2026, 8, 23, 12, 0, 0))


# ── Destinations ─────────────────────────────────────────────────────

@pytest.mark.parametrize("provider,expected", [
    ("canvas", "/oauth/canvas"),
    ("google", "/oauth/google"),
    ("notion", "/oauth/notion"),
    ("CANVAS", "/oauth/canvas"),
    ("  google  ", "/oauth/google"),
])
def test_known_providers_resolve_to_their_oauth_start(provider, expected):
    assert app_link.resolve_next(provider) == expected


@pytest.mark.parametrize("hostile", [
    None, "", "unknown",
    "https://evil.example",
    "//evil.example",
    "/oauth/canvas/../../admin",
    "../admin",
    "http://intelliplan.tech.evil.example",
    "javascript:alert(1)",
])
def test_anything_not_on_the_allow_list_is_refused(hostile):
    """The endpoint redirects *while authenticated*, so a caller-chosen
    destination would be an open redirect that also hands over a live
    session — session fixation in one URL."""
    assert app_link.resolve_next(hostile) is None


def test_every_allowed_destination_is_a_local_path():
    """Nothing on the allow-list may point off-site, however it was added."""
    for provider in list(app_link.LINKABLE) + ["settings", "integrations"]:
        target = app_link.resolve_next(provider)
        if target is None:
            continue
        assert target.startswith("/")
        assert not target.startswith("//")
        assert "://" not in target
        assert ".." not in target


def test_every_linkable_provider_actually_resolves():
    """LINKABLE is what the API advertises; it must not promise a provider
    the allow-list cannot serve."""
    for provider in app_link.LINKABLE:
        assert app_link.resolve_next(provider) is not None


# ── Deep links ───────────────────────────────────────────────────────

def test_deep_link_uses_the_registered_scheme():
    link = app_link.deep_link("connected", provider="canvas")
    assert link == "intelliplan://connected?provider=canvas"


def test_deep_link_without_params_has_no_trailing_question_mark():
    assert app_link.deep_link("connected") == "intelliplan://connected"


def test_deep_link_drops_empty_params():
    assert app_link.deep_link("connected", provider="") == "intelliplan://connected"


def test_deep_link_escapes_its_values():
    link = app_link.deep_link("connected", provider="a b&c=d")
    assert " " not in link
    assert link.count("&") == 0
    assert link.count("=") == 1
