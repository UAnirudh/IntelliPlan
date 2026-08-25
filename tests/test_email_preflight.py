"""The preflight check.

Its whole job is to fail loudly for configuration that would otherwise
break silently, so these tests assert it does NOT pass a broken setup.
"""

from __future__ import annotations

import pytest

import App as app_module
from intelliplan.email import preflight


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        yield


@pytest.fixture
def clean(monkeypatch):
    for var in ("RESEND_API_KEY", "SMTP_HOST", "MAIL_HOST", "EMAIL_HOST",
                "MARKETING_POSTAL_ADDRESS", "CRON_SECRET", "MARKETING_REPLY_TO",
                "SUPPORT_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://intelliplan.tech")
    monkeypatch.setenv("RESEND_FROM", "IntelliPlan <noreply@intelliplan.tech>")
    return monkeypatch


def status_of(result, name):
    return next((c["status"] for c in result["checks"] if c["name"] == name), None)


def test_a_completely_unconfigured_app_fails(ctx, clean):
    result = preflight.check()
    assert result["ok"] is False
    assert status_of(result, "provider") == "fail"
    assert status_of(result, "postal_address") == "fail"
    assert status_of(result, "cron_secret") == "fail"


def test_missing_postal_address_is_a_failure_not_a_warning(ctx, clean, monkeypatch):
    """It stops the newsletter and feedback sends outright."""
    clean.setenv("RESEND_API_KEY", "re_x")
    clean.setenv("CRON_SECRET", "s")
    # Stub the provider lookup, as every other test here does. Without it,
    # setting RESEND_API_KEY makes check() issue a real HTTPS request to
    # api.resend.com with this fake key on every run — a live third-party
    # call from a unit test, which is slow, flaky, and not this test's
    # subject. It passed either way, because an unreachable provider is
    # correctly only a warning, so nothing ever surfaced it.
    monkeypatch.setattr(preflight, "_resend_domains", lambda key: [])
    assert status_of(preflight.check(), "postal_address") == "fail"


def test_a_fully_configured_app_passes(ctx, clean, monkeypatch):
    clean.setenv("RESEND_API_KEY", "re_x")
    clean.setenv("CRON_SECRET", "s")
    clean.setenv("MARKETING_POSTAL_ADDRESS", "1 Test Way")
    clean.setenv("MARKETING_REPLY_TO", "someone@gmail.example")
    monkeypatch.setattr(
        preflight, "_resend_domains",
        lambda key: [{"name": "intelliplan.tech", "status": "verified"}],
    )
    result = preflight.check()
    assert result["ok"] is True, [c for c in result["checks"] if c["status"] == "fail"]


def test_an_unverified_domain_is_a_failure(ctx, clean, monkeypatch):
    clean.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setattr(
        preflight, "_resend_domains",
        lambda key: [{"name": "intelliplan.tech", "status": "pending"}],
    )
    assert status_of(preflight.check(), "from_domain") == "fail"


def test_a_domain_missing_from_resend_is_a_failure(ctx, clean, monkeypatch):
    clean.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setattr(preflight, "_resend_domains", lambda key: [])
    assert status_of(preflight.check(), "from_domain") == "fail"


def test_an_unreachable_provider_is_a_warning_not_a_failure(ctx, clean, monkeypatch):
    """A network blip is a different problem from an unverified domain and
    must not be reported as one."""
    clean.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setattr(preflight, "_resend_domains", lambda key: None)
    assert status_of(preflight.check(), "from_domain") == "warn"


def test_a_reply_to_on_the_sending_domain_is_flagged(ctx, clean, monkeypatch):
    """Send-only domains have no MX, so replies to them bounce. This is the
    exact trap intelliplan.tech is in today."""
    clean.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setattr(preflight, "_resend_domains", lambda key: [])
    result = preflight.check()
    assert status_of(result, "reply_to") == "warn"
    assert "MX" in next(c["detail"] for c in result["checks"] if c["name"] == "reply_to")


def test_a_reply_to_elsewhere_is_fine(ctx, clean, monkeypatch):
    clean.setenv("RESEND_API_KEY", "re_x")
    clean.setenv("MARKETING_REPLY_TO", "me@gmail.example")
    monkeypatch.setattr(preflight, "_resend_domains", lambda key: [])
    assert status_of(preflight.check(), "reply_to") == "ok"


def test_a_non_https_base_url_is_flagged(ctx, clean):
    clean.setenv("APP_BASE_URL", "http://localhost:3000")
    assert status_of(preflight.check(), "app_base_url") == "warn"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("IntelliPlan <noreply@intelliplan.tech>", "intelliplan.tech"),
        ("noreply@send.intelliplan.tech", "send.intelliplan.tech"),
        ("Name <a@B.COM>", "b.com"),
        ("nonsense", ""),
    ],
)
def test_domain_extraction(value, expected):
    assert preflight._domain_of(value) == expected


def test_a_subdomain_sender_matches_its_parent_domain(ctx, clean, monkeypatch):
    """Sending from send.intelliplan.tech is the recommended setup when the
    apex cannot carry the records. It must not be reported as unregistered."""
    clean.setenv("RESEND_API_KEY", "re_x")
    clean.setenv("RESEND_FROM", "IntelliPlan <noreply@send.intelliplan.tech>")
    monkeypatch.setattr(
        preflight, "_resend_domains",
        lambda key: [{"name": "intelliplan.tech", "status": "verified"}],
    )
    assert status_of(preflight.check(), "from_domain") == "ok"
