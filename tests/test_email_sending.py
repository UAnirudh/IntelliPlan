"""The send path: backwards compatibility, HTML, unsubscribe, dedup.

Nothing here touches the network. The Resend call is patched at
``urllib.request.urlopen`` — the same seam the real code uses — so the
payload that would have gone over the wire is captured and asserted on.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import App as app_module
from intelliplan.email import campaigns, sender, templates


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        yield


@pytest.fixture
def resend(monkeypatch):
    """Capture what would have been POSTed to Resend, and return a fake 200.

    Yields the list of decoded request bodies.
    """
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


def make_user(ctx_unused=None, **overrides):
    """A committed, eligible adult student."""
    defaults = {
        "email": f"send-{uuid.uuid4().hex[:10]}@example.test",
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
    return user


def newsletter_content(payload=None):
    """Just the body variables the newsletter template reads.

    ``normalise_newsletter`` also returns subject/preheader/email_key, which
    are send-level settings rather than template context — spreading the
    whole thing into build_context collides with its own arguments, exactly
    as ``send_newsletter`` avoids doing.
    """
    full = campaigns.normalise_newsletter(payload or {})
    return {k: full[k] for k in ("issue_label", "headline", "intro", "features", "tip")}


# ── Backwards compatibility ─────────────────────────────────────────


def test_three_positional_args_still_work(ctx, resend):
    """notifications_glue calls _send_email(address, title, body) positionally.
    That call must keep working unchanged."""
    result = app_module._send_email("someone@example.test", "Subject", "Body text")
    assert result
    assert len(resend) == 1
    assert resend[0]["text"] == "Body text"
    assert resend[0]["subject"] == "Subject"
    # No html key at all on a plain-text send — Resend rejects a null one.
    assert "html" not in resend[0]
    assert "headers" not in resend[0]


def test_the_notifications_glue_call_shape_specifically(ctx, resend):
    """The exact call from notifications_glue._send_email, as written."""
    assert app_module._send_email("a@example.test", "IntelliPlan", "body\n\nurl\n\n— IntelliPlan")
    assert len(resend) == 1


def test_html_and_headers_are_added_when_given(ctx, resend):
    app_module._send_email(
        "someone@example.test",
        "Subject",
        "Plain fallback",
        html="<p>Rich</p>",
        headers={"List-Unsubscribe": "<https://example.test/u/abc>"},
    )
    assert resend[0]["html"] == "<p>Rich</p>"
    assert resend[0]["text"] == "Plain fallback"
    assert resend[0]["headers"]["List-Unsubscribe"] == "<https://example.test/u/abc>"


def test_the_provider_message_id_comes_back(ctx, resend):
    assert app_module._send_email("someone@example.test", "S", "B") == "msg_test_00000000"


# ── Templates ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["welcome", "feedback", "newsletter"])
def test_templates_render_with_no_name(ctx, monkeypatch, name):
    """Many accounts arrive via Google OAuth with no name set, so None is the
    common case, not the edge case."""
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "1 Test Way")
    context = templates.build_context(
        user=None,
        unsubscribe_url="https://example.test/u/tok",
        preheader="Preview text",
        **newsletter_content(),
    )
    assert context["user_name"] is None
    rendered = templates.render(name, "Subject", context)
    assert rendered.html.strip().startswith("<!DOCTYPE html>")
    assert "None" not in rendered.text, "a None leaked into the plain-text body"
    assert "there" in rendered.text.lower()


def test_a_named_user_is_greeted_by_name(ctx, monkeypatch):
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "1 Test Way")
    context = templates.build_context(
        user=make_user(name="Alex"), unsubscribe_url="https://x.test/u", preheader="p"
    )
    rendered = templates.render("welcome", "S", context)
    assert "Alex" in rendered.html
    assert "Alex" in rendered.text


def test_every_template_carries_the_unsubscribe_link(ctx, monkeypatch):
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "1 Test Way")
    url = "https://example.test/email/unsubscribe/tok123"
    for name in ("welcome", "feedback", "newsletter"):
        context = templates.build_context(
            user=None, unsubscribe_url=url, preheader="p", **newsletter_content()
        )
        rendered = templates.render(name, "S", context)
        assert url in rendered.html, f"{name}.html has no unsubscribe link"
        assert url in rendered.text, f"{name}.txt has no unsubscribe link"


def test_the_postal_address_reaches_the_commercial_footers(ctx, monkeypatch):
    """CAN-SPAM: the newsletter and feedback emails are commercial."""
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "IntelliPlan, 1 Test Way, Testville")
    for name in ("feedback", "newsletter"):
        context = templates.build_context(
            user=None, unsubscribe_url="u", preheader="p", **newsletter_content()
        )
        rendered = templates.render(name, "S", context)
        assert "1 Test Way" in rendered.html, f"{name}.html has no postal address"


def test_the_preheader_is_rendered(ctx, monkeypatch):
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "1 Test Way")
    context = templates.build_context(
        user=None, unsubscribe_url="u", preheader="A distinctive preview line"
    )
    rendered = templates.render("welcome", "S", context)
    assert "A distinctive preview line" in rendered.html


# ── Unsubscribe tokens ──────────────────────────────────────────────


def test_token_round_trip(ctx):
    token = sender.make_unsubscribe_token("Person@Example.test")
    payload = sender.parse_unsubscribe_token(token)
    assert payload == {"email": "person@example.test", "scope": "marketing"}


def test_a_tampered_token_is_rejected(ctx):
    token = sender.make_unsubscribe_token("victim@example.test")
    # Flip a character in the payload half, leaving the structure intact.
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    assert sender.parse_unsubscribe_token(tampered) is None


def test_a_hand_rolled_token_is_rejected(ctx):
    """An unsigned payload someone assembled themselves must not work — that
    would be an unsubscribe-anyone endpoint."""
    import base64

    forged = base64.urlsafe_b64encode(b'{"email":"victim@example.test"}').decode().strip("=")
    assert sender.parse_unsubscribe_token(forged) is None
    assert sender.parse_unsubscribe_token("") is None
    assert sender.parse_unsubscribe_token("garbage") is None


def test_tokens_do_not_expire(ctx):
    """CAN-SPAM requires the link to work for at least 30 days. This asserts
    the serializer is the untimed one — a timed token would carry a
    timestamp and could be aged out."""
    from itsdangerous import URLSafeSerializer

    assert isinstance(sender._serializer(), URLSafeSerializer)
    assert not hasattr(sender._serializer(), "loads_unsafe_with_age")


# ── The unsubscribe route ───────────────────────────────────────────


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    try:
        app_module.limiter.enabled = False
    except Exception:
        pass
    with app_module.app.test_client() as c:
        yield c
    try:
        app_module.limiter.enabled = True
    except Exception:
        pass


def test_unsubscribe_works_without_logging_in(ctx, client):
    """Someone who cannot get into their account must still get off the list."""
    user = make_user()
    token = sender.make_unsubscribe_token(user.email)
    response = client.get(f"/email/unsubscribe/{token}")
    assert response.status_code == 200
    with app_module.app.app_context():
        from intelliplan.email.eligibility import is_suppressed

        assert is_suppressed(user.email) is True
        refreshed = app_module.User.query.get(user.id)
        assert refreshed.marketing_emails_opt_in is False


def test_unsubscribe_does_not_touch_deadline_reminders(ctx, client):
    """The transactional/marketing boundary, from the unsubscribe side."""
    user = make_user()
    user.email_reminders_opt_in = True
    user.sms_reminders_opt_in = True
    app_module.db.session.commit()

    client.get(f"/email/unsubscribe/{sender.make_unsubscribe_token(user.email)}")

    refreshed = app_module.User.query.get(user.id)
    assert refreshed.email_reminders_opt_in is True, "unsubscribing killed deadline reminders"
    assert refreshed.sms_reminders_opt_in is True


def test_a_bad_unsubscribe_token_is_a_400_not_a_crash(ctx, client):
    assert client.get("/email/unsubscribe/not-a-real-token").status_code == 400


def test_one_click_post_returns_json(ctx, client):
    """List-Unsubscribe-Post sends a POST and wants a bare 200."""
    user = make_user()
    response = client.post(f"/email/unsubscribe/{sender.make_unsubscribe_token(user.email)}")
    assert response.status_code == 200
    assert response.get_json()["unsubscribed"] is True


# ── Suppression is honoured by the sender ───────────────────────────


def test_sending_to_a_suppressed_address_is_a_clean_no_op(ctx, resend):
    from intelliplan.email.eligibility import suppress

    user = make_user()
    suppress(user.email)
    result = sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome", subject="S", marketing=False
    )
    assert result.sent is False
    assert result.reason == "suppressed"
    assert resend == [], "a suppressed address was still sent to"


# ── Deduplication ───────────────────────────────────────────────────


def test_the_welcome_sweep_sends_exactly_once(ctx, resend):
    """Two cron fires must not mean two emails."""
    user = make_user()
    campaigns.sweep_welcome()
    campaigns.sweep_welcome()

    mine = [p for p in resend if p["to"] == [user.email]]
    assert len(mine) == 1, f"sent {len(mine)} times, expected exactly 1"
    # Deliberately no assertion on the aggregate `sent` count: the sweep is
    # global and other tests in this suite add users to it.

    row = app_module.EmailSend.query.filter_by(user_id=user.id, email_key="welcome").all()
    assert len(row) == 1, "the ledger grew a second row for the same user and key"


def test_a_racing_second_send_is_refused_by_the_ledger(ctx, resend):
    """The sweep's SQL filter is the first line of defence and catches the
    ordinary repeat run. This covers the second line: two workers that both
    pass the filter and both try to claim. Only one may send."""
    user = make_user()

    first = sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome", subject="S", marketing=False
    )
    second = sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome", subject="S", marketing=False
    )

    assert first.sent is True
    assert second.sent is False
    assert second.reason == "already_sent"
    assert len([p for p in resend if p["to"] == [user.email]]) == 1


def test_the_ledger_records_the_send(ctx, resend):
    user = make_user()
    sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome", subject="S", marketing=False
    )
    row = app_module.EmailSend.query.filter_by(user_id=user.id, email_key="welcome").first()
    assert row is not None
    assert row.status == "sent"
    assert row.provider_message_id == "msg_test_00000000"


def test_a_second_claim_on_the_same_key_is_refused(ctx, resend):
    user = make_user()
    assert sender._claim(user.id, "feedback_v1") is not None
    assert sender._claim(user.id, "feedback_v1") is None


# ── The postal-address refusal ──────────────────────────────────────


def test_marketing_refuses_to_send_without_a_postal_address(ctx, resend, monkeypatch):
    monkeypatch.delenv("MARKETING_POSTAL_ADDRESS", raising=False)
    user = make_user()
    result = sender.send_lifecycle_email(
        user=user, email_key="feedback_v1", template_name="feedback", subject="S", marketing=True
    )
    assert result.sent is False
    assert result.reason == "no_postal_address"
    assert resend == []


def test_the_welcome_email_sends_without_a_postal_address(ctx, resend, monkeypatch):
    """It is transactional, so CAN-SPAM's commercial-email rule does not
    apply — and blocking it would mean a missing postal address silently
    breaks onboarding."""
    monkeypatch.delenv("MARKETING_POSTAL_ADDRESS", raising=False)
    user = make_user()
    result = sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome", subject="S", marketing=False
    )
    assert result.sent is True


# ── Activity gate ───────────────────────────────────────────────────


def test_the_feedback_sweep_skips_users_who_never_came_back(ctx, resend):
    user = make_user()
    user.created_at = datetime.utcnow() - timedelta(days=14, hours=6)
    app_module.db.session.commit()

    summary = campaigns.sweep_feedback()

    assert summary["skipped_inactive"] >= 1
    assert [p for p in resend if p["to"] == [user.email]] == []


def test_the_feedback_sweep_mails_a_user_with_a_linked_account(ctx, resend):
    user = make_user()
    user.created_at = datetime.utcnow() - timedelta(days=14, hours=6)
    app_module.db.session.add(
        app_module.LinkedAccount(user_id=user.id, login_type="canvas", credentials="{}")
    )
    app_module.db.session.commit()

    campaigns.sweep_feedback()

    assert [p for p in resend if p["to"] == [user.email]], "an active user was skipped"


# ── The settings toggle ─────────────────────────────────────────────


def test_turning_marketing_on_stamps_the_consent_date(ctx):
    from notifications_glue import _apply_preferences

    user = make_user(marketing_emails_opt_in=False, marketing_opt_in_at=None)
    _apply_preferences(user, {"marketing_emails_opt_in": True})

    assert user.marketing_emails_opt_in is True
    assert user.marketing_opt_in_at is not None


def test_resaving_does_not_move_the_consent_date(ctx):
    """The value of the timestamp is that it records when they actually
    agreed. A date that drifts on every settings save evidences nothing."""
    from notifications_glue import _apply_preferences

    user = make_user(marketing_emails_opt_in=False, marketing_opt_in_at=None)
    _apply_preferences(user, {"marketing_emails_opt_in": True})
    original = user.marketing_opt_in_at
    _apply_preferences(user, {"marketing_emails_opt_in": True})

    assert user.marketing_opt_in_at == original


def test_turning_marketing_off_keeps_the_consent_record(ctx):
    """The timestamp records a consent that did happen. Erasing it destroys
    the evidence trail for the period when mail was legitimately sent."""
    from notifications_glue import _apply_preferences

    user = make_user()
    stamped = user.marketing_opt_in_at
    _apply_preferences(user, {"marketing_emails_opt_in": False})

    assert user.marketing_emails_opt_in is False
    assert user.marketing_opt_in_at == stamped


def test_the_email_channel_is_not_marketing_consent(ctx):
    """Ticking the transactional email-reminders channel must never enrol
    anyone in the newsletter."""
    from notifications_glue import _apply_preferences

    user = make_user(marketing_emails_opt_in=False, marketing_opt_in_at=None)
    _apply_preferences(user, {"channels": ["email", "sms", "push"]})

    assert user.email_reminders_opt_in is True
    assert user.marketing_emails_opt_in is False
    assert user.marketing_opt_in_at is None


def test_marketing_consent_does_not_enable_reminders(ctx):
    """And the same boundary in the other direction."""
    from notifications_glue import _apply_preferences

    user = make_user(marketing_emails_opt_in=False)
    user.email_reminders_opt_in = False
    _apply_preferences(user, {"marketing_emails_opt_in": True})

    assert user.email_reminders_opt_in is False


def test_re_opting_in_clears_a_previous_suppression(ctx):
    """Without this the toggle silently does nothing for anyone who ever
    clicked unsubscribe: the flag flips on and the gate keeps refusing."""
    from intelliplan.email.eligibility import is_marketing_eligible, suppress
    from notifications_glue import _apply_preferences

    user = make_user()
    suppress(user.email)
    assert is_marketing_eligible(user)[0] is False

    user.marketing_emails_opt_in = False
    _apply_preferences(user, {"marketing_emails_opt_in": True})
    app_module.db.session.commit()

    ok, reason = is_marketing_eligible(user)
    assert ok is True, f"still refused after re-opting in: {reason}"


# ── Retry after a transient provider failure ────────────────────────


def test_a_provider_failure_is_retried_on_the_next_attempt(ctx, monkeypatch):
    """A thirty-second Resend outage must not permanently cost a user their
    welcome email. `failed` means the provider returned a definite falsy
    result, so we know the mail did not go out and a retry cannot duplicate.

    Driven through send_lifecycle_email rather than the sweep: stubbing the
    provider to fail during a *global* sweep marks every other test's user
    failed as a side effect, which made this suite flaky.
    """
    user = make_user()

    monkeypatch.setattr(app_module, "_send_email", lambda *a, **k: False)
    first = sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome",
        subject="S", marketing=False,
    )
    assert first.sent is False
    row = app_module.EmailSend.query.filter_by(user_id=user.id, email_key="welcome").one()
    assert row.status == "failed"

    sent_to = []

    def working_send(addr, *a, **k):
        sent_to.append(addr)
        return "msg_retry_0001"

    monkeypatch.setattr(app_module, "_send_email", working_send)
    second = sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome",
        subject="S", marketing=False,
    )

    assert second.sent is True, "the failed send was never retried"
    assert sent_to == [user.email]
    app_module.db.session.refresh(row)
    assert row.status == "sent"
    assert app_module.EmailSend.query.filter_by(
        user_id=user.id, email_key="welcome"
    ).count() == 1, "the retry created a duplicate ledger row"


def test_a_pending_row_is_never_retried(ctx, resend):
    """`pending` means a process died mid-send and we do not know whether
    the message went out. That is the one case where under-delivery is the
    correct call."""
    user = make_user()
    app_module.db.session.add(
        app_module.EmailSend(user_id=user.id, email_key="welcome", status="pending")
    )
    app_module.db.session.commit()

    result = sender.send_lifecycle_email(
        user=user, email_key="welcome", template_name="welcome", subject="S", marketing=False
    )
    assert result.sent is False
    assert result.reason == "already_sent"
    assert [p for p in resend if p["to"] == [user.email]] == []


# ── Reply-To ────────────────────────────────────────────────────────


def test_lifecycle_email_carries_a_reply_to(ctx, resend):
    """Every template says "reply to this email", but From is a no-reply
    sender. Without Reply-To the answer lands in a mailbox nobody reads."""
    sender.send_lifecycle_email(
        user=make_user(), email_key="welcome", template_name="welcome",
        subject="S", marketing=False,
    )
    assert resend[0]["reply_to"] == "founder@intelliplan.tech"


def test_reply_to_is_overridable(ctx, resend, monkeypatch):
    monkeypatch.setenv("MARKETING_REPLY_TO", "hello@intelliplan.tech")
    sender.send_lifecycle_email(
        user=make_user(), email_key="welcome", template_name="welcome",
        subject="S", marketing=False,
    )
    assert resend[0]["reply_to"] == "hello@intelliplan.tech"


def test_a_plain_send_has_no_reply_to(ctx, resend):
    """The existing 3-arg callers must not suddenly grow a Reply-To."""
    app_module._send_email("x@example.test", "S", "B")
    assert "reply_to" not in resend[0]


# ── Configurable addresses ──────────────────────────────────────────


def test_the_templates_print_no_hardcoded_addresses(ctx, monkeypatch):
    """A hardcoded address on a domain with no MX is a reply into the void.
    Both must come from config so they can point at a mailbox that works."""
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "1 Test Way")
    monkeypatch.setenv("MARKETING_REPLY_TO", "replies@example.test")
    monkeypatch.setenv("SUPPORT_EMAIL", "help@example.test")

    for name in ("welcome", "feedback", "newsletter"):
        context = templates.build_context(
            user=None, unsubscribe_url="u", preheader="p", **newsletter_content()
        )
        rendered = templates.render(name, "S", context)
        for blob in (rendered.html, rendered.text):
            assert "founder@intelliplan.tech" not in blob, f"{name} hardcodes founder@"
            assert "support@intelliplan.tech" not in blob, f"{name} hardcodes support@"

    context = templates.build_context(user=None, unsubscribe_url="u", preheader="p")
    welcome = templates.render("welcome", "S", context)
    assert "help@example.test" in welcome.html
    assert "help@example.test" in welcome.text

    feedback = templates.render("feedback", "S", context)
    assert "replies@example.test" in feedback.html
    assert "mailto:replies@example.test" in feedback.html


def test_support_email_falls_back_to_reply_to(ctx, monkeypatch):
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    monkeypatch.setenv("MARKETING_REPLY_TO", "only@example.test")
    assert templates.support_email() == "only@example.test"
