"""May we email this person?

Every lifecycle send passes through here first. The bar is deliberately set
so that anything unknown reads as "no": a missing birth year is a child, an
opt-in without a timestamp is not evidenced consent, an address we cannot
parse is not an address. Getting a false negative means someone misses a
newsletter. Getting a false positive means marketing mail in a 12-year-old's
inbox, which is a COPPA violation and — while IntelliPlan is in a district
Digital Resource Review — a materially bad thing to explain.

Three gates, sharing a base:

* ``is_transactional_eligible`` — the welcome email and the onboarding
  sequence. They explain a service the user signed up for, so they do not
  require marketing consent, but they still respect age and suppression.
* ``is_reminder_eligible`` — nudges about the student's own unfinished
  work. Adds ``email_reminders_opt_in``, the switch the student set
  themselves.
* ``is_marketing_eligible`` — everything else. Adds consent, consent
  evidence, and the student-only restriction.

Both return ``(eligible, reason)``. ``reason`` is a stable machine-readable
slug for logging and for counting skips in a cron summary; it is never shown
to a user.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

#: Deliberately permissive. This is not RFC 5322 — it exists to catch the
#: empty string, the obvious typo, and the placeholder, not to adjudicate
#: exotic-but-legal addresses. Rejecting a real address here means a student
#: silently never hears from us, which is worse than a bounce.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

#: Under this age, marketing requires verifiable parental consent.
COPPA_AGE = 13


def looks_like_email(address: str | None) -> bool:
    """Cheap syntactic check. See ``_EMAIL_RE`` for what this is not."""
    if not address:
        return False
    address = address.strip()
    return bool(address) and len(address) <= 255 and bool(_EMAIL_RE.match(address))


def is_suppressed(address: str | None) -> bool:
    """True when the address is on the suppression list.

    Keyed on the address rather than a user id on purpose — see the
    ``EmailSuppression`` model. Case-insensitive, because a student who
    unsubscribed as ``Sam@example.com`` must not be reachable as
    ``sam@example.com``.
    """
    if not address:
        return False
    from App import EmailSuppression

    normalised = address.strip().lower()
    try:
        return (
            EmailSuppression.query.filter_by(email=normalised).first() is not None
        )
    except Exception:
        # A suppression table we cannot read is not permission to send. This
        # is the one place where a database error must fail closed.
        return True


def suppress(address: str | None, reason: str = "unsubscribe") -> bool:
    """Add an address to the suppression list. Idempotent.

    Returns True when the address is suppressed afterwards, whether this
    call was the one that did it or it was already there — the caller is an
    unsubscribe link, and "you were already unsubscribed" is a success.
    """
    if not address:
        return False
    from App import EmailSuppression, db

    normalised = address.strip().lower()
    existing = EmailSuppression.query.filter_by(email=normalised).first()
    if existing is not None:
        return True
    db.session.add(EmailSuppression(email=normalised, reason=reason))
    try:
        db.session.commit()
        return True
    except Exception:
        # Almost certainly the unique constraint, from a double-click on the
        # unsubscribe link. The address is suppressed either way.
        db.session.rollback()
        return EmailSuppression.query.filter_by(email=normalised).first() is not None


def age_from_birth_year(birth_year: int | None, now: datetime | None = None) -> int | None:
    """Age in whole years, derived the same way the signup form does it.

    Year subtraction only — IntelliPlan collects a birth *year*, not a date,
    so this is off by up to a year for anyone who has not had their birthday
    yet. That skews young, which is the safe direction for a COPPA gate.
    """
    if birth_year is None:
        return None
    now = now or datetime.utcnow()
    return now.year - int(birth_year)


def _base_gates(user: Any, now: datetime | None = None) -> tuple[bool, str]:
    """Age, address, and suppression — the checks that apply to every send,
    transactional included."""
    if user is None:
        return False, "no_user"

    address = (getattr(user, "email", None) or "").strip()
    if not address:
        return False, "no_email"
    if not looks_like_email(address):
        return False, "malformed_email"

    birth_year = getattr(user, "birth_year", None)
    if birth_year is None:
        # Unknown age is treated as a minor. An account with no birth year
        # predates the field or skipped it; either way we cannot show that
        # this person is old enough, and "we didn't know" is not a defence.
        return False, "unknown_age"

    age = age_from_birth_year(birth_year, now)
    if age is not None and age < COPPA_AGE and not getattr(user, "parent_consent_granted", False):
        return False, "under_13_no_parental_consent"

    if is_suppressed(address):
        return False, "suppressed"

    return True, "ok"


def is_transactional_eligible(user: Any, now: datetime | None = None) -> tuple[bool, str]:
    """The welcome email: no marketing consent required, everything else is.

    Deliberately does *not* consult ``marketing_emails_opt_in``. The welcome
    mail explains a service the person just signed up for and drives setup,
    which is transactional. It still carries an unsubscribe link.
    """
    return _base_gates(user, now)


def is_marketing_eligible(user: Any, now: datetime | None = None) -> tuple[bool, str]:
    """The full gate. Return ``(eligible, reason)``.

    ``reason`` is for logging when False.
    """
    ok, reason = _base_gates(user, now)
    if not ok:
        return False, reason

    if getattr(user, "role", "student") != "student":
        # Teachers and parents are on the platform for someone else's
        # education. Marketing to them is a separate consent conversation
        # that v1 does not have.
        return False, "not_a_student"

    if getattr(user, "marketing_emails_opt_in", False) is not True:
        return False, "no_marketing_consent"

    if getattr(user, "marketing_opt_in_at", None) is None:
        # A flag set with no timestamp cannot be evidenced. "When did this
        # person opt in" is the first question asked in any complaint, and
        # the only safe answer to "we don't know" is to not send.
        return False, "consent_not_dated"

    return True, "ok"


def is_reminder_eligible(user: Any, now: datetime | None = None) -> tuple[bool, str]:
    """The gate for reminders about the student's *own* unfinished work.

    A third gate rather than a reuse of either existing one, because this
    class of mail sits between them and both alternatives are wrong.

    It is not marketing: nothing is being sold, and the entire content is
    the student's own data read back to them. Putting it behind
    ``marketing_emails_opt_in`` would mean a planner that cannot remind you
    about the plan you made, which is the product.

    It is also not the welcome mail. That one is a single message answering
    a signup. This is recurring, unprompted, and about work the student may
    have abandoned deliberately — so it requires the reminder opt-in they
    set explicitly, ``email_reminders_opt_in``, rather than riding in on the
    transactional gate. The student's own switch decides, and the default
    for that switch is off.
    """
    ok, reason = _base_gates(user, now)
    if not ok:
        return False, reason

    if getattr(user, "email_reminders_opt_in", False) is not True:
        return False, "no_reminder_consent"

    return True, "ok"
