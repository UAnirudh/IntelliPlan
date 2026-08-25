"""The one-week feedback ask, and the form it points at.

Two changes from v1: it lands at one week rather than two, and it leads with
a form instead of asking for a reply. The form URL is the part that cannot
be fixed after the fact — a dead link in a sent email stays dead — so it is
configurable and asserted on in both the HTML and text parts.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import App as app_module
from intelliplan.email import campaigns, templates


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        yield


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "IntelliPlan, 1 Test Way, Testville CA 94000")
    captured: list[dict] = []

    class FakeResponse:
        def read(self):
            return json.dumps({"id": "msg_test_00000000"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        yield captured


def make_active_user(age_days, **overrides):
    """A committed, marketing-eligible student with real activity on file."""
    defaults = {
        "email": f"fb-{uuid.uuid4().hex[:10]}@example.test",
        "name": "Sam",
        "birth_year": 2000,
        "parent_consent_granted": True,
        "role": "student",
        "marketing_emails_opt_in": True,
        "marketing_opt_in_at": datetime(2026, 1, 1),
        "password_hash": "x",
    }
    defaults.update(overrides)
    user = app_module.User(**defaults)
    app_module.db.session.add(user)
    app_module.db.session.commit()
    user.created_at = datetime.utcnow() - timedelta(days=age_days)
    # The sweep only asks people who actually came back.
    app_module.db.session.add(app_module.LinkedAccount(
        user_id=user.id, login_type="canvas", credentials="{}"))
    app_module.db.session.commit()
    return user


# ── Timing ────────────────────────────────────────────────────────────


def test_the_ask_lands_one_week_in(ctx):
    assert campaigns.FEEDBACK_MIN_DAYS == 7


def test_the_window_stays_open_long_enough_to_survive_a_late_cron(ctx):
    """Exactly one day would mean a cron that slips by an hour skips a whole
    cohort, permanently — they age out and are never asked."""
    assert campaigns.FEEDBACK_MAX_DAYS - campaigns.FEEDBACK_MIN_DAYS >= 1.0


def test_a_week_old_account_is_asked(ctx, resend):
    user = make_active_user(campaigns.FEEDBACK_MIN_DAYS + 0.25)
    campaigns.sweep_feedback()
    assert [p for p in resend if p["to"] == [user.email]], "a week-old account was not asked"


def test_a_two_day_old_account_is_not_asked_yet(ctx, resend):
    user = make_active_user(2)
    campaigns.sweep_feedback()
    assert [p for p in resend if p["to"] == [user.email]] == []


def test_a_month_old_account_has_aged_out(ctx, resend):
    user = make_active_user(30)
    campaigns.sweep_feedback()
    assert [p for p in resend if p["to"] == [user.email]] == []


def test_the_ask_is_sent_once(ctx, resend):
    user = make_active_user(campaigns.FEEDBACK_MIN_DAYS + 0.25)
    campaigns.sweep_feedback()
    campaigns.sweep_feedback()
    assert len([p for p in resend if p["to"] == [user.email]]) == 1


# ── The form link ─────────────────────────────────────────────────────


def test_the_form_url_reaches_both_parts_of_the_email(ctx, resend):
    user = make_active_user(campaigns.FEEDBACK_MIN_DAYS + 0.25)
    campaigns.sweep_feedback()
    payload = [p for p in resend if p["to"] == [user.email]][0]
    assert campaigns.FEEDBACK_FORM_URL in payload["html"]
    assert campaigns.FEEDBACK_FORM_URL in payload["text"]


def test_the_form_url_is_configurable(monkeypatch):
    """Form URLs move. Hardcoding one means a broken link needs a deploy."""
    import importlib

    monkeypatch.setenv("FEEDBACK_FORM_URL", "https://forms.example.test/t/other")
    reloaded = importlib.reload(campaigns)
    try:
        assert reloaded.FEEDBACK_FORM_URL == "https://forms.example.test/t/other"
    finally:
        monkeypatch.delenv("FEEDBACK_FORM_URL", raising=False)
        importlib.reload(campaigns)


def test_replying_is_still_offered_as_an_alternative(ctx, monkeypatch):
    """The form is primary, not compulsory. Someone who would rather write a
    sentence should not have to open a browser to do it."""
    monkeypatch.setenv("MARKETING_REPLY_TO", "replies@example.test")
    context = templates.build_context(
        user=None, unsubscribe_url="u", preheader="p",
        feedback_form_url=campaigns.FEEDBACK_FORM_URL,
    )
    rendered = templates.render("feedback", "S", context)
    assert "mailto:replies@example.test" in rendered.html
    assert "replies@example.test" in rendered.text


def test_the_copy_no_longer_promises_there_is_no_form(ctx):
    """v1 said "No form, no survey link". Leading with a form while still
    saying that would read as a bait and switch."""
    context = templates.build_context(
        user=None, unsubscribe_url="u", preheader="p",
        feedback_form_url=campaigns.FEEDBACK_FORM_URL,
    )
    rendered = templates.render("feedback", "S", context)
    for blob in (rendered.html, rendered.text):
        assert "No form" not in blob
        assert "no survey link" not in blob


def test_the_email_still_carries_its_compliance_furniture(ctx, resend):
    """It is a marketing-gated send, so the unsubscribe link and postal
    address are not optional."""
    user = make_active_user(campaigns.FEEDBACK_MIN_DAYS + 0.25)
    campaigns.sweep_feedback()
    payload = [p for p in resend if p["to"] == [user.email]][0]
    assert "unsubscribe" in payload["html"].lower()
    assert "1 Test Way" in payload["text"]
    assert "List-Unsubscribe" in (payload.get("headers") or {})
