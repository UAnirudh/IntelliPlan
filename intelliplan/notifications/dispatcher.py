"""Enqueue and deliver notifications.

Two halves, deliberately separate:

* :meth:`Dispatcher.enqueue` writes rows. It is cheap, safe to call from a
  request, and never touches a provider.
* :meth:`Dispatcher.flush` delivers them. It is called by cron, and is the
  only thing that talks to Twilio, SMTP, or WebPush.

Anything that can fail slowly belongs in the second half. A student's page
load should never wait on an SMS gateway.

Failure model
-------------
A send can fail three ways and they are not the same:

* **Transient** (provider 500, timeout, socket error) — retry with backoff.
* **Permanent** (bad number, unsubscribed endpoint, malformed address) —
  do not retry; retrying a wrong phone number five times just costs money.
* **Stale** — the message is past its ``expires_at``. Bin it. A reminder
  that a session starts in fifteen minutes has no value an hour later, and
  delivering it late is worse than not delivering it, because it trains the
  student to distrust the timing of everything else.

Providers signal which by raising :class:`PermanentDeliveryError` or
returning falsey / raising anything else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Sequence

from time_utils import utcnow

from intelliplan.notifications.events import Channel, NotificationEvent, render
from intelliplan.notifications.models import MAX_ATTEMPTS, backoff_for
from intelliplan.notifications.preferences import Preferences

logger = logging.getLogger(__name__)

__all__ = ["Dispatcher", "PermanentDeliveryError", "DeliveryResult"]

#: How long a queued message stays worth sending, per kind of urgency.
#: Session reminders die fast; a "your week doesn't fit" warning is still
#: true tomorrow morning.
DEFAULT_TTL = timedelta(hours=6)
SHORT_TTL = timedelta(minutes=90)
_SHORT_TTL_KINDS = {"session_upcoming"}

#: Rows claimed per flush. Bounded so one sweep cannot run for minutes and
#: overlap the next cron tick.
DEFAULT_BATCH = 100

#: A row stuck in "sending" longer than this had its worker die mid-flight.
CLAIM_TIMEOUT = timedelta(minutes=10)


class PermanentDeliveryError(Exception):
    """The message will never be deliverable — do not retry."""


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    sent: int = 0
    failed: int = 0
    dead: int = 0
    expired: int = 0
    skipped: int = 0

    def merged(self, **deltas: int) -> "DeliveryResult":
        return DeliveryResult(
            sent=self.sent + deltas.get("sent", 0),
            failed=self.failed + deltas.get("failed", 0),
            dead=self.dead + deltas.get("dead", 0),
            expired=self.expired + deltas.get("expired", 0),
            skipped=self.skipped + deltas.get("skipped", 0),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "sent": self.sent,
            "failed": self.failed,
            "dead": self.dead,
            "expired": self.expired,
            "skipped": self.skipped,
        }


class Dispatcher:
    """Owns the outbox. Providers are injected so this stays testable."""

    def __init__(
        self,
        outbox_model: type,
        db_session: Any,
        senders: dict[Channel, Callable[[Any], bool]] | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._model = outbox_model
        self._session = db_session
        self._senders = senders or {}
        self._now = now

    # ── Enqueue ───────────────────────────────────────────────────────

    def enqueue(
        self,
        event: NotificationEvent,
        preferences: Preferences,
    ) -> list[Any]:
        """Queue one event across every channel the student wants.

        Returns the rows actually created. A duplicate — same student, same
        fact, same channel — is silently dropped, which is the point: the
        caller does not have to know whether the sweep already noticed this.
        """
        created: list[Any] = []
        earliest = event.deliver_at or self._now()

        for channel in (Channel.PUSH, Channel.SMS, Channel.EMAIL):
            if not preferences.wants(event.kind, channel):
                continue
            try:
                message = render(event, channel)
            except KeyError:
                logger.error("no template for %s; dropping", event.kind)
                continue

            body = message.as_sms() if channel is Channel.SMS else message.body
            scheduled = preferences.delivery_time(event.kind, earliest)
            ttl = SHORT_TTL if event.kind.value in _SHORT_TTL_KINDS else DEFAULT_TTL

            row = self._model(
                user_id=event.user_id,
                kind=event.kind.value,
                channel=channel.value,
                dedupe_key=event.key_for(channel),
                title=message.title[:256],
                body=body,
                url=message.url[:512],
                state="pending",
                scheduled_for=scheduled,
                expires_at=scheduled + ttl,
            )
            # Each row commits on its own. A batch commit means one
            # duplicate (which is normal and expected) rolls back the
            # siblings that were fine.
            try:
                self._session.add(row)
                self._session.commit()
                created.append(row)
            except Exception as exc:
                self._session.rollback()
                if not _is_duplicate(exc):
                    logger.warning("enqueue failed (%s/%s): %s", event.kind, channel, exc)
        return created

    def enqueue_many(
        self,
        events: Iterable[NotificationEvent],
        preferences: Preferences,
    ) -> int:
        return sum(len(self.enqueue(e, preferences)) for e in events)

    # ── Deliver ───────────────────────────────────────────────────────

    def flush(self, limit: int = DEFAULT_BATCH) -> DeliveryResult:
        """Deliver everything that is due. Safe to run concurrently."""
        self._release_stuck_claims()
        rows = self._claim(limit)
        result = DeliveryResult()

        now = self._now()
        for row in rows:
            # Expiry is judged against the dispatcher's clock, not the wall
            # clock. The model exposes an `is_expired` convenience for the
            # admin views, but using it here would make one step of the
            # sweep disagree with every other step about what time it is.
            if row.expires_at and row.expires_at <= now:
                row.state = "cancelled"
                row.last_error = "expired before delivery"
                self._commit()
                result = result.merged(expired=1)
                continue

            sender = self._senders.get(_channel_of(row))
            if sender is None:
                # No provider configured for this channel — not a failure of
                # the message, so it goes back to pending rather than
                # burning a retry.
                row.state = "pending"
                row.claimed_at = None
                self._commit()
                result = result.merged(skipped=1)
                continue

            result = self._attempt(row, sender, result)
        return result

    def _attempt(self, row: Any, sender: Callable[[Any], bool], result: DeliveryResult) -> DeliveryResult:
        row.attempts = (row.attempts or 0) + 1
        try:
            ok = bool(sender(row))
            error = None if ok else "provider reported failure"
        except PermanentDeliveryError as exc:
            row.state = "dead"
            row.last_error = str(exc)[:512]
            row.claimed_at = None
            self._commit()
            return result.merged(dead=1)
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"

        if ok:
            row.state = "sent"
            row.sent_at = self._now()
            row.last_error = None
            row.claimed_at = None
            self._commit()
            return result.merged(sent=1)

        row.last_error = (error or "unknown")[:512]
        row.claimed_at = None
        if row.attempts >= MAX_ATTEMPTS:
            row.state = "dead"
            self._commit()
            return result.merged(dead=1)

        row.state = "pending"
        row.scheduled_for = self._now() + backoff_for(row.attempts)
        self._commit()
        return result.merged(failed=1)

    # ── Claiming ──────────────────────────────────────────────────────

    def _claim(self, limit: int) -> list[Any]:
        """Mark due rows as ours before sending any of them.

        Claiming first means a second dispatcher starting mid-sweep sees
        "sending" and skips them, rather than sending everything twice.
        """
        now = self._now()
        rows = (
            self._model.query.filter(
                self._model.state == "pending",
                self._model.scheduled_for <= now,
            )
            .order_by(self._model.scheduled_for.asc())
            .limit(max(1, limit))
            .all()
        )
        if not rows:
            return []
        for row in rows:
            row.state = "sending"
            row.claimed_at = now
        self._commit()
        return rows

    def _release_stuck_claims(self) -> int:
        """Return rows whose worker died back to the queue.

        Without this a crashed dispatcher strands its batch in "sending"
        forever, and the student silently stops receiving anything.
        """
        cutoff = self._now() - CLAIM_TIMEOUT
        rows = self._model.query.filter(
            self._model.state == "sending",
            self._model.claimed_at < cutoff,
        ).all()
        for row in rows:
            row.state = "pending"
            row.claimed_at = None
        if rows:
            self._commit()
        return len(rows)

    # ── Introspection ─────────────────────────────────────────────────

    def pending_count(self, user_id: int | None = None) -> int:
        query = self._model.query.filter(self._model.state.in_(("pending", "sending")))
        if user_id:
            query = query.filter(self._model.user_id == user_id)
        return query.count()

    def recent(self, user_id: int, limit: int = 20) -> list[Any]:
        return (
            self._model.query.filter(self._model.user_id == user_id)
            .order_by(self._model.created_at.desc())
            .limit(max(1, min(100, limit)))
            .all()
        )

    def sent_today(self, user_id: int, channel: Channel) -> int:
        """Used to enforce the per-student daily cap."""
        since = self._now() - timedelta(days=1)
        return self._model.query.filter(
            self._model.user_id == user_id,
            self._model.channel == channel.value,
            self._model.state == "sent",
            self._model.sent_at >= since,
        ).count()

    def purge_delivered(self, older_than_days: int = 30) -> int:
        """Housekeeping so the table does not grow without bound."""
        cutoff = self._now() - timedelta(days=max(1, older_than_days))
        rows = self._model.query.filter(
            self._model.state.in_(("sent", "cancelled")),
            self._model.created_at < cutoff,
        ).all()
        for row in rows:
            self._session.delete(row)
        if rows:
            self._commit()
        return len(rows)

    # ── Plumbing ──────────────────────────────────────────────────────

    def _commit(self) -> None:
        try:
            self._session.commit()
        except Exception as exc:
            logger.warning("outbox commit failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass


def _channel_of(row: Any) -> Channel:
    try:
        return Channel(row.channel)
    except ValueError:
        return Channel.PUSH


def _is_duplicate(exc: Exception) -> bool:
    """Whether this exception is the unique constraint doing its job.

    Matched on text because the three backends we run against phrase it
    differently and none of them expose a portable code here. A false
    negative only costs a log line, so being generous is the safe side.
    """
    text = str(exc).lower()
    return "unique" in text or "duplicate" in text
