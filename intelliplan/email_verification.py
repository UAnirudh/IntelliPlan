"""One-time proof of password-account email ownership.

The account may be used before verification. Only owner-verified school-domain
entitlements wait for this proof. Raw bearer tokens are never stored or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
from datetime import timedelta

from flask import current_app

from time_utils import utcnow

TOKEN_TTL = timedelta(hours=24)
RESEND_COOLDOWN = timedelta(minutes=15)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def send_verification(user, *, now=None, bypass_cooldown=False) -> tuple[bool, str]:
    """Issue and send one short-lived token; return (sent, internal reason).

    Transactional eligibility deliberately keeps the existing age and parent
    consent rules. A missing age or unconsented under-13 account is not sent
    mail. The public resend endpoint always returns the same response either
    way, so this internal reason is never exposed to unauthenticated callers.
    """
    if user is None or not getattr(user, "id", None):
        return False, "no_user"
    if getattr(user, "email_verified", False) is True:
        return False, "already_verified"
    address = (getattr(user, "email", "") or "").strip().lower()
    if "@" not in address or len(address) > 255:
        return False, "invalid_email"

    from intelliplan.email.eligibility import is_transactional_eligible
    allowed, reason = is_transactional_eligible(user, now=now)
    if not allowed:
        return False, reason

    from App import db
    now = now or utcnow()
    last_sent = getattr(user, "email_verification_sent_at", None)
    if (not bypass_cooldown and last_sent is not None
            and now - last_sent < RESEND_COOLDOWN):
        return False, "cooldown"

    secret = secrets.token_urlsafe(32)
    token = f"{int(user.id)}.{secret}"
    user.email_verification_hash = _digest(secret)
    user.email_verification_email_hash = _digest(address)
    user.email_verification_expires_at = now + TOKEN_TTL
    user.email_verification_sent_at = now
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return False, "database_error"

    base = (current_app.config.get("APP_BASE_URL") or
            __import__("os").getenv("APP_BASE_URL", "https://intelliplan.tech")).rstrip("/")
    link = f"{base}/verify-email?token={token}"
    name = html.escape((getattr(user, "name", "") or "there").strip() or "there")
    message = (
        f"Hi {getattr(user, 'name', '') or 'there'},\n\n"
        "Confirm that you own this email address to access any verified "
        "school benefits associated with it.\n\n"
        f"Confirm your email: {link}\n\n"
        "This link expires in 24 hours and can only be used once. If you did "
        "not request this, you can ignore this message.\n"
    )
    markup = (
        "<!doctype html><html><body style=\"font-family:Arial,sans-serif;color:#172033;line-height:1.55\">"
        f"<p>Hi {name},</p><p>Confirm that you own this email address to access any verified "
        f"school benefits associated with it.</p><p><a href=\"{html.escape(link, quote=True)}\" "
        "style=\"display:inline-block;padding:12px 18px;background:#3478f6;color:#fff;text-decoration:none;border-radius:8px\">Confirm email</a></p>"
        "<p>This link expires in 24 hours and can only be used once. If you did not request this, ignore this message.</p>"
        "</body></html>"
    )
    try:
        from App import _send_email
        if not _send_email(address, "Confirm your IntelliPlan email", message, html=markup):
            return False, "delivery_failed"
    except Exception as exc:
        # Do not include the token or provider response in logs.
        current_app.logger.warning("verification email delivery failed (%s)", type(exc).__name__)
        return False, "delivery_failed"
    return True, "sent"


def consume_verification(token: str, *, now=None) -> bool:
    """Consume a valid token once and apply only a matching school domain."""
    if not isinstance(token, str) or len(token) > 256 or "." not in token:
        return False
    user_id_text, secret = token.split(".", 1)
    if not user_id_text.isdecimal() or not secret:
        return False
    from App import User, db
    try:
        user = db.session.get(User, int(user_id_text))
    except (TypeError, ValueError):
        return False
    if user is None or getattr(user, "email_verified", False) is True:
        return False

    now = now or utcnow()
    expected_token = getattr(user, "email_verification_hash", None) or ""
    expected_email = getattr(user, "email_verification_email_hash", None) or ""
    expiry = getattr(user, "email_verification_expires_at", None)
    address = (getattr(user, "email", "") or "").strip().lower()
    if (not expected_token or not expected_email or expiry is None or expiry <= now
            or not hmac.compare_digest(expected_token, _digest(secret))
            or not hmac.compare_digest(expected_email, _digest(address))):
        return False

    user.email_verified = True
    user.email_verification_hash = None
    user.email_verification_email_hash = None
    user.email_verification_expires_at = None
    try:
        from intelliplan.learning.entitlements import add_verified_school_membership
        add_verified_school_membership(user, address)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return False
    return True
