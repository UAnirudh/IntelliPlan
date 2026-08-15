"""Consent for newsletters and onboarding email.

Marketing consent is the kind of flag that is easy to set by accident and
expensive to have set wrongly: the list gets exported to an external sending
tool, and by the time anyone notices, the mail has gone out. So the tests
that matter here are the ones asserting the flag stays *off* — an unticked
box, an absent field, and above all a child's account.
"""

from __future__ import annotations

import uuid

import pytest

import App as app_module


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    app_module.limiter.enabled = False
    with app_module.app.test_client() as c:
        yield c
    app_module.limiter.enabled = True


def fresh_email():
    return f"optin-{uuid.uuid4().hex[:12]}@example.test"


def signup(client, **overrides):
    """Register an adult unless the caller says otherwise."""
    email = overrides.pop("email", None) or fresh_email()
    form = {
        "email": email,
        "password": "correcthorsebattery1",
        "confirm_password": "correcthorsebattery1",
        "birth_year": "2000",
    }
    form.update(overrides)
    client.post("/register", data=form, follow_redirects=True)
    with app_module.app.app_context():
        return app_module.User.query.filter_by(email=email).first()


# ── Saying yes ──────────────────────────────────────────────────────


def test_ticking_the_box_records_consent(client):
    user = signup(client, marketing_emails_opt_in="1")
    assert user is not None
    assert user.marketing_emails_opt_in is True


def test_consent_is_dated(client):
    """"When did this person opt in" is the first question asked in any
    complaint. Consent you cannot date is consent you cannot evidence."""
    user = signup(client, marketing_emails_opt_in="1")
    assert user.marketing_opt_in_at is not None


# ── Saying nothing, which is not yes ────────────────────────────────


def test_an_unticked_box_is_not_consent(client):
    """An unticked checkbox is simply absent from the POST body."""
    user = signup(client)
    assert user.marketing_emails_opt_in is False
    assert user.marketing_opt_in_at is None


def test_reminders_do_not_imply_marketing(client):
    """Transactional and marketing consent are separate. Opting into the
    deadline reminders the planner exists to send is not permission to put
    someone on a newsletter."""
    user = signup(client, phone="+15551234567", sms_reminders_opt_in="1")
    assert user.sms_reminders_opt_in is True
    assert user.marketing_emails_opt_in is False


# ── COPPA ───────────────────────────────────────────────────────────


def test_a_child_is_never_opted_in_even_if_the_box_is_posted(client):
    """The load-bearing test.

    Marketing to an under-13 needs verifiable parental consent, and the
    consent gate on this form covers using the planner — not being sold to.
    The flag must not sit true waiting to be picked up by a list export, so
    it is refused at write time rather than filtered later.
    """
    from datetime import datetime
    child_birth_year = str(datetime.utcnow().year - 9)
    user = signup(
        client,
        birth_year=child_birth_year,
        parent_email="a-parent@example.test",
        marketing_emails_opt_in="1",
    )
    assert user is not None, "the child account should still be created"
    assert user.marketing_emails_opt_in is False
    assert user.marketing_opt_in_at is None


def test_a_child_account_still_gets_its_parental_consent_gate(client):
    """Guards against a future edit that 'fixes' the line above by
    rejecting the signup outright."""
    from datetime import datetime
    user = signup(
        client,
        birth_year=str(datetime.utcnow().year - 9),
        parent_email="another-parent@example.test",
        marketing_emails_opt_in="1",
    )
    assert user.parent_consent_granted is False
    assert user.parent_email == "another-parent@example.test"


# ── The form itself ─────────────────────────────────────────────────


def test_the_box_is_not_pre_ticked(client):
    """A pre-ticked box is not consent under GDPR. If someone adds
    `checked` to that input, this fails."""
    body = client.get("/register").get_data(as_text=True)
    idx = body.find('name="marketing_emails_opt_in"')
    assert idx != -1, "the opt-in checkbox should be on the signup form"
    tag_start = body.rfind("<input", 0, idx)
    tag = body[tag_start:body.find(">", idx) + 1]
    assert "checked" not in tag.lower()
    assert 'type="checkbox"' in tag
