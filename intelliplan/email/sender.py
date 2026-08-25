"""``send_lifecycle_email`` — the one way a lifecycle email leaves the app.

Everything funnels through here so that the eligibility gate, the
deduplication ledger, the unsubscribe headers and the CAN-SPAM address check
cannot be forgotten by a future campaign. A campaign module decides *who*;
this module decides whether that is allowed and makes it happen once.

Ordering matters and is deliberate:

1. Gate first — no ledger row for someone we were never going to mail.
2. Claim the ledger row *before* calling the provider. If the process dies
   mid-send the row is left ``pending`` and that user never gets that email
   again. A missed welcome is a small loss; a duplicate is a support ticket
   and an unsubscribe.
3. Send, then record the outcome.
"""

from __future__ import annotations

import logging
import os
from typing import Any, NamedTuple

from . import templates
from .eligibility import (
    is_marketing_eligible,
    is_reminder_eligible,
    is_transactional_eligible,
)

logger = logging.getLogger(__name__)

#: Signing salt for unsubscribe tokens. Changing it invalidates every
#: unsubscribe link already sitting in someone's inbox, which would be a
#: CAN-SPAM problem — treat it as permanent.
UNSUBSCRIBE_SALT = "email-unsubscribe"


class SendResult(NamedTuple):
    sent: bool
    #: Stable slug: "ok", or the gate reason, or "already_sent" / "provider_failed".
    reason: str


# ── Unsubscribe tokens ──────────────────────────────────────────────


def _serializer():
    from itsdangerous import URLSafeSerializer
    from App import app

    return URLSafeSerializer(app.config["SECRET_KEY"], salt=UNSUBSCRIBE_SALT)


def make_unsubscribe_token(address: str, scope: str = "marketing") -> str:
    """Sign ``{"email": ..., "scope": ...}``.

    Deliberately ``URLSafeSerializer`` and not ``URLSafeTimedSerializer``:
    these links must never expire. CAN-SPAM requires an unsubscribe
    mechanism that works for at least 30 days after sending, and the
    simplest correct answer to "how long" is forever. A link that has aged
    out is a compliance failure that looks exactly like a broken link.
    """
    return _serializer().dumps({"email": (address or "").strip().lower(), "scope": scope})


def parse_unsubscribe_token(token: str) -> dict | None:
    """Return the payload, or ``None`` if the signature does not verify.

    Any tampering — a flipped character, a hand-rolled payload, a token
    signed with a different key — lands here as ``None``. The caller must
    treat that as "no", never as "unsubscribe whatever address was in it".
    """
    from itsdangerous import BadSignature

    try:
        payload = _serializer().loads(token)
    except (BadSignature, Exception):
        return None
    if not isinstance(payload, dict) or not payload.get("email"):
        return None
    return payload


def unsubscribe_url(address: str) -> str:
    from App import APP_BASE_URL

    base = (APP_BASE_URL or "https://intelliplan.tech").rstrip("/")
    return f"{base}/email/unsubscribe/{make_unsubscribe_token(address)}"


def unsubscribe_headers(address: str) -> dict:
    """``List-Unsubscribe`` plus one-click.

    Gmail and Yahoo require both of these from bulk senders. Without them a
    student's only route off the list is the spam button, which costs
    domain reputation far more than the unsubscribe would have.
    """
    return {
        "List-Unsubscribe": f"<{unsubscribe_url(address)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


# ── The ledger ──────────────────────────────────────────────────────


def _claim(user_id: int, email_key: str) -> Any | None:
    """Insert a ``pending`` row, or return None if this send already happened.

    The unique constraint on (user_id, email_key) is what makes this safe
    against two cron fires racing: both may pass the SELECT, only one
    survives the INSERT, and the loser takes the IntegrityError and skips.

    A row already sitting at ``failed`` is re-claimed rather than refused.
    That status is only written when the provider returned a definite
    falsy result — Resend hands back an id when it accepts a message, and
    the SMTP path raises — so we know the mail was not delivered and a
    retry cannot duplicate it. Without this, a thirty-second provider
    outage permanently burns that user's welcome email.

    ``pending`` is deliberately *not* re-claimed: that means a process died
    mid-send, and we genuinely do not know whether the message went out.
    The retry is bounded by the sweep's own time window, so a persistently
    failing address stops being retried once it ages out rather than being
    attempted forever.
    """
    from sqlalchemy.exc import IntegrityError
    from App import EmailSend, db

    existing = EmailSend.query.filter_by(user_id=user_id, email_key=email_key).first()
    if existing is not None:
        if existing.status != "failed":
            return None
        existing.status = "pending"
        try:
            db.session.commit()
            return existing
        except Exception:
            db.session.rollback()
            return None

    row = EmailSend(user_id=user_id, email_key=email_key, status="pending")
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        # The other worker won. Not an error — this is the constraint doing
        # exactly what it is there for.
        db.session.rollback()
        return None
    return row


def _record(row: Any, status: str, message_id: Any = None) -> None:
    from App import db

    row.status = status
    if isinstance(message_id, str):
        row.provider_message_id = message_id[:128]
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


# ── The entry point ─────────────────────────────────────────────────


def send_lifecycle_email(
    user: Any,
    email_key: str,
    template_name: str,
    subject: str,
    preheader: str = "",
    marketing: bool = True,
    context_extra: dict | None = None,
    dedupe: bool = True,
    gate: str | None = None,
) -> SendResult:
    """Gate, deduplicate, render, and send one lifecycle email.

    ``marketing=False`` selects the transactional gate — used by the welcome
    email and the onboarding sequence, which explain a service the user
    signed up for. It still respects age and suppression, and still carries
    an unsubscribe link.

    ``gate`` names the check explicitly and wins over ``marketing`` when
    given: ``"marketing"``, ``"transactional"``, or ``"reminder"``. The
    boolean was fine while there were two gates and stops being expressive
    at three — ``marketing=False`` cannot say *which* non-marketing gate is
    meant. An unrecognised name falls back to the marketing gate, the
    strictest of the three, so a typo under-sends rather than over-sends.

    ``dedupe=False`` is for admin test sends, which must be repeatable.
    """
    gates = {
        "marketing": is_marketing_eligible,
        "transactional": is_transactional_eligible,
        "reminder": is_reminder_eligible,
    }
    if gate is None:
        gate = "marketing" if marketing else "transactional"
    elif gate not in gates:
        logger.error(
            "unknown gate %r for %s; falling back to the marketing gate", gate, email_key
        )
        gate = "marketing"
    ok, reason = gates[gate](user)
    if not ok:
        return SendResult(False, reason)

    address = (user.email or "").strip()

    if gate == "marketing" and not templates.postal_address():
        # Refusing beats sending a non-compliant commercial email. Loud,
        # because the fix is one environment variable and the failure is
        # otherwise invisible until someone complains.
        logger.error(
            "MARKETING_POSTAL_ADDRESS is unset — refusing to send %s to %s. "
            "CAN-SPAM requires a physical mailing address in commercial email.",
            email_key,
            address,
        )
        return SendResult(False, "no_postal_address")

    row = None
    if dedupe:
        row = _claim(user.id, email_key)
        if row is None:
            return SendResult(False, "already_sent")

    try:
        context = templates.build_context(
            user=user,
            unsubscribe_url=unsubscribe_url(address),
            preheader=preheader,
            **(context_extra or {}),
        )
        rendered = templates.render(template_name, subject, context)
    except Exception as exc:
        logger.exception("could not render %s for user %s: %s", template_name, user.id, exc)
        if row is not None:
            _record(row, "failed")
        return SendResult(False, "render_failed")

    from App import _send_email

    try:
        result = _send_email(
            address,
            rendered.subject,
            rendered.text,
            html=rendered.html,
            headers=unsubscribe_headers(address),
            reply_to=templates.reply_to(),
        )
    except Exception as exc:
        logger.warning("provider raised sending %s to %s: %s", email_key, address, exc)
        result = False

    if row is not None:
        _record(row, "sent" if result else "failed", result)
    return SendResult(bool(result), "ok" if result else "provider_failed")
