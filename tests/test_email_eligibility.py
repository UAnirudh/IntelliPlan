"""The gate that decides whether a lifecycle email may be sent.

This is the function that must not have bugs. A false negative means a
student misses a newsletter; a false positive means marketing mail in a
12-year-old's inbox, which is a COPPA violation.

So the tests worth writing are the ones asserting the gate stays *shut*:
no birth year, a child without parental consent, an opt-in flag with no
date on it, a suppressed address. The happy path gets one test and the
refusals get the rest.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

import App as app_module
from intelliplan.email import eligibility


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        yield


def make_user(**overrides):
    """An adult student with dated marketing consent — eligible unless a
    test breaks one specific thing. Not committed; the gate reads attributes
    and only touches the database for the suppression check."""
    defaults = {
        "email": f"gate-{uuid.uuid4().hex[:10]}@example.test",
        "birth_year": 2000,
        "parent_consent_granted": True,
        "role": "student",
        "marketing_emails_opt_in": True,
        "marketing_opt_in_at": datetime(2026, 1, 1),
    }
    defaults.update(overrides)
    return app_module.User(**defaults)


# ── The happy path ──────────────────────────────────────────────────


def test_an_adult_student_with_dated_consent_is_eligible(ctx):
    ok, reason = eligibility.is_marketing_eligible(make_user())
    assert ok is True
    assert reason == "ok"


# ── Age ─────────────────────────────────────────────────────────────


def test_an_unknown_birth_year_is_treated_as_a_minor(ctx):
    """The branch most likely to be got wrong. An account with no birth year
    predates the field or skipped it — either way we cannot show this person
    is old enough, and "we didn't know" is not a defence."""
    ok, reason = eligibility.is_marketing_eligible(make_user(birth_year=None))
    assert ok is False
    assert reason == "unknown_age"


def test_a_child_without_parental_consent_is_refused(ctx):
    child = make_user(
        birth_year=datetime.utcnow().year - 9,
        parent_consent_granted=False,
    )
    ok, reason = eligibility.is_marketing_eligible(child)
    assert ok is False
    assert reason == "under_13_no_parental_consent"


def test_a_child_with_parental_consent_passes_the_age_gate(ctx):
    """The other half of the COPPA branch. Verifiable parental consent is
    exactly what the regulation asks for, so this must not be refused —
    otherwise the consent flow is decorative."""
    child = make_user(
        birth_year=datetime.utcnow().year - 9,
        parent_consent_granted=True,
    )
    ok, reason = eligibility.is_marketing_eligible(child)
    assert ok is True, f"refused with {reason}"


def test_a_twelve_year_old_is_under_the_gate_and_a_thirteen_year_old_is_not(ctx):
    """Pins the boundary so an off-by-one in the age maths gets caught."""
    year = datetime.utcnow().year
    twelve = make_user(birth_year=year - 12, parent_consent_granted=False)
    thirteen = make_user(birth_year=year - 13, parent_consent_granted=False)
    assert eligibility.is_marketing_eligible(twelve)[0] is False
    assert eligibility.is_marketing_eligible(thirteen)[0] is True


# ── Consent ─────────────────────────────────────────────────────────


def test_no_opt_in_is_refused(ctx):
    ok, reason = eligibility.is_marketing_eligible(
        make_user(marketing_emails_opt_in=False)
    )
    assert ok is False
    assert reason == "no_marketing_consent"


def test_an_undated_opt_in_is_refused(ctx):
    """Consent you cannot date is consent you cannot evidence."""
    ok, reason = eligibility.is_marketing_eligible(
        make_user(marketing_emails_opt_in=True, marketing_opt_in_at=None)
    )
    assert ok is False
    assert reason == "consent_not_dated"


def test_reminders_consent_does_not_imply_marketing_consent(ctx):
    """The transactional/marketing boundary, asserted from the gate's side.
    A student who asked for deadline reminders has not joined a newsletter."""
    user = make_user(marketing_emails_opt_in=False)
    user.email_reminders_opt_in = True
    ok, reason = eligibility.is_marketing_eligible(user)
    assert ok is False
    assert reason == "no_marketing_consent"


# ── Role ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_only_students_are_marketed_to_in_v1(ctx, role):
    ok, reason = eligibility.is_marketing_eligible(make_user(role=role))
    assert ok is False
    assert reason == "not_a_student"


# ── Address ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "address", ["", None, "not-an-email", "no@tld", "two@@at.example", "spaces in@x.example"]
)
def test_an_unusable_address_is_refused(ctx, address):
    ok, reason = eligibility.is_marketing_eligible(make_user(email=address))
    assert ok is False
    assert reason in {"no_email", "malformed_email"}


def test_no_user_at_all_is_refused(ctx):
    ok, reason = eligibility.is_marketing_eligible(None)
    assert ok is False
    assert reason == "no_user"


# ── Suppression ─────────────────────────────────────────────────────


def test_a_suppressed_address_is_refused(ctx):
    user = make_user()
    eligibility.suppress(user.email, reason="unsubscribe")
    ok, reason = eligibility.is_marketing_eligible(user)
    assert ok is False
    assert reason == "suppressed"


def test_suppression_ignores_address_case(ctx):
    """Someone who unsubscribed as Sam@ must not be reachable as sam@."""
    address = f"Mixed-{uuid.uuid4().hex[:8]}@Example.test"
    eligibility.suppress(address)
    assert eligibility.is_suppressed(address.lower()) is True
    assert eligibility.is_suppressed(address.upper()) is True


def test_suppressing_twice_is_not_an_error(ctx):
    """The unsubscribe link gets double-clicked. Both clicks must succeed."""
    address = f"double-{uuid.uuid4().hex[:8]}@example.test"
    assert eligibility.suppress(address) is True
    assert eligibility.suppress(address) is True


# ── The transactional gate ──────────────────────────────────────────


def test_the_welcome_gate_does_not_require_marketing_consent(ctx):
    """The welcome email is transactional — it explains a service the person
    just signed up for."""
    user = make_user(marketing_emails_opt_in=False, marketing_opt_in_at=None)
    ok, reason = eligibility.is_transactional_eligible(user)
    assert ok is True, f"refused with {reason}"


def test_the_welcome_gate_still_refuses_a_child_without_consent(ctx):
    child = make_user(
        birth_year=datetime.utcnow().year - 9,
        parent_consent_granted=False,
        marketing_emails_opt_in=False,
    )
    ok, reason = eligibility.is_transactional_eligible(child)
    assert ok is False
    assert reason == "under_13_no_parental_consent"


def test_the_welcome_gate_still_refuses_a_suppressed_address(ctx):
    user = make_user(marketing_emails_opt_in=False)
    eligibility.suppress(user.email)
    ok, reason = eligibility.is_transactional_eligible(user)
    assert ok is False
    assert reason == "suppressed"


def test_the_welcome_gate_still_mails_a_teacher(ctx):
    """The role restriction is a marketing rule, not a safety one. A teacher
    who signs up should still be told how to set the product up."""
    ok, _ = eligibility.is_transactional_eligible(make_user(role="teacher"))
    assert ok is True
