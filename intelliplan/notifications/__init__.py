"""Notification subsystem.

An event-sourced outbox: the app records *what happened* and a separate
sweep decides who hears about it, on which channel, and when.

    scheduler / Active  ──emit──▶  events.NotificationEvent
                                        │
                       preferences.Preferences (channels, quiet hours, tz)
                                        │
                                 dispatcher.enqueue  ──▶  notification_outbox
                                                                │
                                          cron ──▶ dispatcher.flush ──▶ providers

Nothing in here imports Flask or ``App``; providers and the ORM model are
injected. ``notifications_glue.py`` at the repo root wires it up.
"""

from intelliplan.notifications.events import (
    Channel,
    EventKind,
    NotificationEvent,
    render,
)
from intelliplan.notifications.dispatcher import (
    Dispatcher,
    DeliveryResult,
    PermanentDeliveryError,
)
from intelliplan.notifications.preferences import (
    Preferences,
    QuietHours,
    preferences_from_user,
)

__all__ = [
    "Channel",
    "EventKind",
    "NotificationEvent",
    "render",
    "Dispatcher",
    "DeliveryResult",
    "PermanentDeliveryError",
    "Preferences",
    "QuietHours",
    "preferences_from_user",
]
