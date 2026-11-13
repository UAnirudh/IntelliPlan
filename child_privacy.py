"""Fail-closed child-account controls while verified authorization is unavailable.

No environment switch or legacy email-approval flag supplies COPPA consent.
Reopening under-13 use requires an implemented, reviewed authorization flow.
"""
from datetime import datetime

CHILD_HOLD_MESSAGE = (
    'Independent accounts for children under 13 are unavailable while we complete '
    'a verified parental or school authorization process. An email approval link '
    'does not activate an account. A parent or school may contact '
    'uanirudh0811@gmail.com for review or deletion requests.'
)


def registration_birth_year(raw, now=None):
    """Validate age before storing a new account or issuing a credential."""
    now = now or datetime.utcnow()
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise ValueError('A valid birth year is required. Complete signup on the website.')
    text = str(raw).strip()
    if not text.isascii() or not text.isdigit() or len(text) != 4:
        raise ValueError('Please enter a valid four-digit birth year.')
    year = int(text)
    if year < 1900 or year > now.year:
        raise ValueError('Please enter a valid birth year.')
    if now.year - year - 1 < 13:
        raise ValueError(CHILD_HOLD_MESSAGE)
    return year


def hold_reason(user, now=None, require_known_age=False):
    if user is None:
        return None
    now = now or datetime.utcnow()
    year = getattr(user, 'birth_year', None)
    if year is None:
        return 'age_required' if require_known_age else None
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= now.year:
        return 'age_required'
    if now.year - year - 1 < 13:
        return 'parent_consent_required' if not getattr(user, 'parent_consent_granted', False) else 'child_verification_required'
    if getattr(user, 'parent_email', None) and not getattr(user, 'parent_consent_granted', False):
        return 'parent_consent_required'
    return None
