"""Delivering a notification to the phone app.

The web app already pushes through VAPID/pywebpush, addressed by a browser
endpoint URL. A React Native build has no such endpoint: Expo hands the app
a token instead, and delivery goes through Expo's own service, which fans
out to APNs and FCM.

Rather than a parallel notification system, an Expo token is stored as
another ``PushSubscription`` row — one row per device install, which is
exactly what that table already means. The token itself goes in
``endpoint``, and it is self-identifying (``ExponentPushToken[...]``), so
the sender can tell the two kinds apart without a schema change or a
migration. Everything that already sends a reminder therefore reaches the
phone the moment a token is registered, with no change at the call sites.

This module is pure transport: it knows how to talk to Expo and nothing
about students, tasks, or when a reminder is due.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

#: Expo's push endpoint. Documented and stable; the token format is the
#: contract, not this URL.
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

#: Expo accepts up to 100 messages per request. Batching matters because a
#: student with a phone and a tablet should not cost two round-trips.
MAX_BATCH = 100

_TIMEOUT = 10


def is_expo_token(value: str | None) -> bool:
    """Is this an Expo push token rather than a browser endpoint?

    Both live in ``PushSubscription.endpoint``, so this is what keeps a
    phone token out of pywebpush — which would otherwise fail confusingly
    on a string that is not a URL.
    """
    if not value:
        return False
    v = value.strip()
    return v.startswith("ExponentPushToken[") or v.startswith("ExpoPushToken[")


def build_messages(tokens, payload: dict) -> list[dict]:
    """Turn our internal payload into Expo's message shape.

    Kept separate from sending so the mapping can be tested without a
    network call — the field names differ from ours in ways that are easy
    to get subtly wrong.
    """
    title = payload.get("title") or "IntelliPlan"
    body = payload.get("body") or ""
    data = {k: v for k, v in payload.items() if k not in ("title", "body")}
    return [
        {
            "to": token,
            "title": str(title)[:120],
            "body": str(body)[:400],
            # Carried through to the app so a tap can open the right screen,
            # the same way the web push payload carries a url.
            "data": data,
            "sound": "default",
            # Expo's default priority can be deprioritised by the OS; a
            # deadline reminder that arrives an hour late is not a reminder.
            "priority": "high",
        }
        for token in tokens
        if is_expo_token(token)
    ]


def send(tokens, payload: dict, *, opener=None) -> dict:
    """Push to every Expo token given.

    Returns ``{"sent": n, "invalid": [tokens]}``. Invalid tokens are the
    ones Expo says will never work again — the app was uninstalled, or the
    token was rotated — and the caller is expected to delete those rows so
    the table does not accumulate dead devices.

    Never raises. A notification that cannot be delivered must not take
    down the cron sweep that was delivering it.
    """
    messages = build_messages(tokens, payload)
    if not messages:
        return {"sent": 0, "invalid": []}

    sent = 0
    invalid: list[str] = []
    request_opener = opener or _post

    for start in range(0, len(messages), MAX_BATCH):
        batch = messages[start:start + MAX_BATCH]
        try:
            body = request_opener(EXPO_PUSH_URL, batch)
        except Exception as e:                      # noqa: BLE001 - see docstring
            print(f"[expo-push] send failed: {e}")
            continue

        receipts = (body or {}).get("data")
        if not isinstance(receipts, list):
            continue
        for message, receipt in zip(batch, receipts):
            if not isinstance(receipt, dict):
                continue
            if receipt.get("status") == "ok":
                sent += 1
                continue
            # DeviceNotRegistered is the only error that means "stop trying
            # forever". Everything else — rate limits, Expo hiccups — is
            # worth retrying on the next sweep rather than dropping a real
            # device over.
            details = receipt.get("details") or {}
            if details.get("error") == "DeviceNotRegistered":
                invalid.append(message["to"])
            else:
                print(f"[expo-push] rejected: {receipt.get('message')}")

    return {"sent": sent, "invalid": invalid}


def _post(url: str, batch: list[dict]) -> dict:
    data = json.dumps(batch).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Expo compresses responses only when asked; the receipts are
            # small either way, so ask for plain to keep parsing simple.
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))
