"""Normalize explicit deadlines without inventing times for date-only work."""
from datetime import date, datetime, timezone

from time_utils import to_local


def local_deadline(value, zone='', offset=0):
    """Return a student-local naive instant, or None for absent/date-only input."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return None
    if isinstance(value, str):
        text = value.strip()
        if len(text) <= 10 or not ('T' in text or ' ' in text):
            return None
        try:
            value = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        try:
            return to_local(value.astimezone(timezone.utc).replace(tzinfo=None), zone, offset)
        except (OverflowError, ValueError):
            return None
    return value
