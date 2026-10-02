"""The secret-URL token behind the live plan calendar feed.

Registered from ``App.py`` after ``db`` exists, so ``create_all()`` builds
it. See ``calendar_feed_glue.py`` for the routes.
"""

from __future__ import annotations

from typing import Any

from time_utils import utcnow

from intelliplan.models.grade_pulse import existing_models


def register(db: Any) -> type:
    """Define ``CalendarFeedToken``. Idempotent."""
    existing = existing_models(db)
    if "calendar_feed_tokens" in existing:
        return existing["calendar_feed_tokens"]

    import secret_box

    class CalendarFeedToken(db.Model):
        """A student's secret calendar-subscription URL.

        The URL is the credential: Apple Calendar, Google and Outlook fetch
        it with no cookie and no header, so anyone holding it can read the
        plan. Two consequences shape this table:

        * Lookup is by SHA-256 of the token, so a read of this table alone
          does not yield working feed URLs.
        * The token is also kept, encrypted with the same secret_box as the
          LMS credentials, so Settings can show the link again. A
          subscribe-by-URL link a student can only see once is a link they
          paste somewhere and then lose.

        Rotating replaces both; revoking stamps ``revoked_at`` and the feed
        404s from then on.
        """

        __tablename__ = "calendar_feed_tokens"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
        token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
        token = db.Column(secret_box.EncryptedText, nullable=True)
        #: "full" shows assignment and course names; "private" shows only
        #: "Study block" / "Assignment due", for a calendar shared with family
        #: or shown on a lock screen. The student picks.
        detail = db.Column(db.String(16), default="full", nullable=False)
        created_at = db.Column(db.DateTime, default=utcnow)
        rotated_at = db.Column(db.DateTime, nullable=True)
        revoked_at = db.Column(db.DateTime, nullable=True)
        last_fetched_at = db.Column(db.DateTime, nullable=True)

    return CalendarFeedToken
