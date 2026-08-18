"""The desktop sign-in handback, which is a credential in transit.

A one-time code travels over intelliplan://, a scheme any local program can
register. So these tests are mostly about what happens when someone who is
not the app gets hold of one: the PKCE binding, the single use, and the
expiry are the only things standing between a stolen deep link and a
stolen account.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import desktop_auth as da


# ── PKCE binding ────────────────────────────────────────────────────


def test_a_verifier_redeems_its_own_challenge():
    verifier = da.new_verifier()
    assert da.verifier_matches(verifier, da.challenge_for(verifier))


def test_a_different_verifier_does_not():
    """The whole point: intercepting the code is not enough."""
    stolen_challenge = da.challenge_for(da.new_verifier())
    assert not da.verifier_matches(da.new_verifier(), stolen_challenge)


def test_the_challenge_is_not_reversible_to_the_verifier():
    verifier = da.new_verifier()
    assert verifier not in da.challenge_for(verifier)


def test_challenges_are_the_shape_pkce_specifies():
    """base64url of a SHA-256, unpadded: 43 characters, no '=' or '+'."""
    challenge = da.challenge_for(da.new_verifier())
    assert len(challenge) == 43
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge


# ── Input validation at the boundary ────────────────────────────────


@pytest.mark.parametrize("bad", [
    None, "", "short", "!" * 43, "a" * 42, "a" * 44,
    "../../etc/passwd", "a" * 43 + "=",
])
def test_malformed_challenges_are_refused(bad):
    """This runs on a query parameter a stranger controls."""
    assert not da.is_valid_challenge(bad)


@pytest.mark.parametrize("bad", [
    None, "", "abc", "a" * 42, "a" * 129, "has spaces", "has/slash",
])
def test_malformed_verifiers_are_refused(bad):
    """A one-character verifier would make the PKCE binding brute-forceable."""
    assert not da.is_valid_verifier(bad)


def test_a_short_verifier_cannot_match_even_a_real_challenge():
    short = "a"
    assert not da.verifier_matches(short, da.challenge_for(short))


# ── Expiry ──────────────────────────────────────────────────────────


def test_a_fresh_code_is_live():
    now = datetime(2026, 8, 15, 12, 0, 0)
    assert not da.is_expired(now, now)


def test_a_code_dies_at_the_ttl():
    """Both sides of the boundary, because an off-by-one here is either a
    code that outlives its window or one that expires before the student
    finishes typing their password."""
    born = datetime(2026, 8, 15, 12, 0, 0)
    assert not da.is_expired(born, born + timedelta(seconds=da.CODE_TTL_SECONDS - 1))
    assert da.is_expired(born, born + timedelta(seconds=da.CODE_TTL_SECONDS))


def test_a_missing_timestamp_counts_as_expired():
    """Failing closed. A row with no created_at must not be eternal."""
    assert da.is_expired(None, datetime(2026, 8, 15, 12, 0, 0))


# ── Storage and transport ───────────────────────────────────────────


def test_codes_are_stored_hashed_not_plain():
    code = da.new_code()
    stored = da.hash_code(code)
    assert code not in stored
    assert len(stored) == 64          # sha256 hex
    assert da.hash_code(code) == stored     # stable


def test_codes_do_not_repeat():
    assert len({da.new_code() for _ in range(200)}) == 200


def test_the_deep_link_matches_the_registered_scheme():
    """If this drifts from desktop/package.json's protocols block, the
    browser hands the code to nothing."""
    link = da.deep_link_for("abc123")
    assert link.startswith("intelliplan://auth?code=")
    assert link.endswith("abc123")
