"""Microsoft Graph helpers for a student's Outlook calendar.

The module is deliberately small and transport-only: App owns account
identity/token persistence, while this module refreshes tokens and translates
the schedule shape shared by Google and Outlook into Graph events.

It is also the one place the Microsoft app registration is read from, so
OneDrive (``onedrive_helper``) reuses the same client id, secret, tenant and
redirect URI rather than a second registration.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("openid", "profile", "offline_access", "User.Read", "Calendars.ReadWrite")
DEFAULT_TIMEZONE = "Pacific Standard Time"


def _authority() -> str:
    """Token/authorize base. ``common`` accepts personal Microsoft accounts
    and school/work accounts alike, which is what students have; a school
    that wants to lock sign-in to its own tenant sets ``MICROSOFT_TENANT``."""
    tenant = (os.getenv("MICROSOFT_TENANT") or "common").strip() or "common"
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"


#: Kept for callers that read it; resolved at import with the default tenant.
AUTHORITY = _authority()


def redirect_uri() -> str:
    """The Microsoft redirect URI.

    Used to be required as its own env var, and ``configured()`` reported
    Outlook as unavailable until it was set -- even with the client id and
    secret in place, so a correctly registered app still showed no Connect
    button. It is fully determined by the app's own base URL, so that is the
    default now; the env var remains as an override for staging hosts.
    """
    explicit = (os.getenv("MICROSOFT_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    base = (os.getenv("APP_BASE_URL") or "https://intelliplan.tech").rstrip("/")
    return f"{base}/oauth/outlook/callback"


def configured() -> bool:
    return bool(os.getenv("MICROSOFT_CLIENT_ID") and os.getenv("MICROSOFT_CLIENT_SECRET"))


def get_auth_url(state: str, scopes=SCOPES) -> str:
    params = {
        "client_id": os.environ["MICROSOFT_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "response_mode": "query",
        "scope": " ".join(scopes),
        "state": state,
        "prompt": "select_account",
    }
    return f"{_authority()}/authorize?{urlencode(params)}"


def exchange_code(code: str, scopes=SCOPES) -> dict:
    return _token_request({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": redirect_uri()}, scopes)


def refresh_token(token: dict, scopes=SCOPES) -> dict:
    refreshed = _token_request({"grant_type": "refresh_token",
                                "refresh_token": token.get("refresh_token", "")}, scopes)
    return {**token, **refreshed,
            "refresh_token": refreshed.get("refresh_token") or token.get("refresh_token", "")}


def _token_request(payload: dict, scopes=SCOPES) -> dict:
    # A refresh must ask for the scopes this token was granted. Asking the
    # OneDrive refresh token for calendar scopes it never consented to is an
    # invalid_grant, which is why the scope set is a parameter.
    response = requests.post(f"{_authority()}/token", data={
        "client_id": os.environ["MICROSOFT_CLIENT_ID"],
        "client_secret": os.environ["MICROSOFT_CLIENT_SECRET"],
        "scope": " ".join(scopes), **payload,
    }, timeout=15)
    response.raise_for_status()
    return response.json()


def graph_get(token: dict, path: str, params: dict | None = None) -> dict:
    url = path if path.startswith("https://") else f"{GRAPH}{path}"
    response = requests.get(url, params=params, headers=_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


def graph_post(token: dict, path: str, body: dict) -> dict:
    response = requests.post(f"{GRAPH}{path}", json=body, headers=_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


def _headers(token: dict) -> dict:
    return {"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json",
            # Ask for UTC explicitly. Without this Graph answers in whatever
            # zone the mailbox is set to, and the offset arithmetic below
            # (which assumes UTC) is wrong by that much for every event.
            "Prefer": 'outlook.timezone="UTC"'}


def profile(token: dict) -> dict:
    return graph_get(token, "/me", {"$select": "displayName,mail,userPrincipalName"})


def _calendar_view(token: dict, start_utc: datetime, end_utc: datetime,
                   select: str = "subject,start,end,isAllDay,showAs,isCancelled",
                   max_pages: int = 5) -> list[dict]:
    """Every event instance in a UTC window, following Graph's paging.

    ``calendarView`` (not ``events``) because it expands recurring series
    into their occurrences -- a weekly practice is one ``event`` but fourteen
    blocks of busy time over two weeks.
    """
    params = {
        "startDateTime": start_utc.replace(microsecond=0).isoformat() + "Z",
        "endDateTime": end_utc.replace(microsecond=0).isoformat() + "Z",
        "$select": select, "$top": "250", "$orderby": "start/dateTime",
    }
    out: list[dict] = []
    data = graph_get(token, "/me/calendarView", params)
    for _ in range(max_pages):
        out.extend(e for e in data.get("value", []) if isinstance(e, dict))
        next_link = data.get("@odata.nextLink")
        if not next_link or not str(next_link).startswith(GRAPH):
            break
        data = graph_get(token, next_link)
    return out


def _is_busy(event: dict) -> bool:
    # "free" and "workingElsewhere" events do not block study time; a
    # cancelled meeting still sits in the calendar but nobody attends it.
    if event.get("isCancelled"):
        return False
    return str(event.get("showAs") or "busy").lower() not in {"free", "workingelsewhere"}


def busy_minutes_by_date(token: dict, start_date: date, days: int = 14,
                         utc_offset_minutes: int = 0) -> dict[date, list[tuple[int, int]]]:
    """Read the calendar view once and return local wall-clock busy ranges.

    The window is the student's local days converted to UTC. It used to send
    local midnight as if it were UTC, so a student at UTC-7 lost the last
    seven hours of the horizon and gained seven hours before it.
    """
    offset = timedelta(minutes=int(utc_offset_minutes or 0))
    local_start = datetime.combine(start_date, datetime.min.time())
    local_end = local_start + timedelta(days=max(1, min(60, int(days or 1))))
    events = _calendar_view(token, local_start - offset, local_end - offset)
    out: dict[date, list[tuple[int, int]]] = {}
    for event in events:
        if not _is_busy(event):
            continue
        try:
            if event.get("isAllDay"):
                cursor, finish = _graph_datetime(event["start"]), _graph_datetime(event["end"])
                while cursor.date() < finish.date():
                    out.setdefault(cursor.date(), []).append((0, 24 * 60))
                    cursor += timedelta(days=1)
                continue
            cursor, finish = _graph_datetime(event["start"]) + offset, _graph_datetime(event["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        while cursor < finish:
            midnight = datetime.combine(cursor.date(), datetime.min.time())
            piece_end = min(finish, midnight + timedelta(days=1))
            a = cursor.hour * 60 + cursor.minute
            b = int((piece_end - midnight).total_seconds() // 60)
            if b > a:
                out.setdefault(cursor.date(), []).append((a, min(b, 24 * 60)))
            cursor = piece_end
    return out


def _graph_datetime(value: dict) -> datetime:
    raw = (value.get("dateTime") or "").replace("Z", "+00:00")
    # Graph sends seven fractional digits ("…:00.0000000"); fromisoformat on
    # older Pythons accepts at most six.
    if "." in raw:
        head, _, tail = raw.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        zone = tail[len(digits):]
        raw = f"{head}.{digits[:6]}{zone}" if digits else f"{head}{zone}"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def get_upcoming_events(token: dict, days: int = 7) -> list[dict]:
    """Next week's events in the same shape ``google_calendar_helper`` returns,
    so the calendar list endpoint can merge the two without special cases."""
    now = datetime.utcnow().replace(microsecond=0)
    out = []
    for event in _calendar_view(token, now, now + timedelta(days=max(1, min(31, days)))):
        if event.get("isCancelled"):
            continue
        try:
            start = _graph_datetime(event["start"]).isoformat() + "Z"
            end = _graph_datetime(event["end"]).isoformat() + "Z"
        except (KeyError, TypeError, ValueError):
            continue
        if event.get("isAllDay"):
            start, end = start[:10], end[:10]
        out.append({"id": event.get("id", ""), "title": event.get("subject") or "Untitled",
                    "start": start, "end": end, "description": "",
                    "source": "outlook_calendar"})
    return out[:50]


def _block_start(date_str: str, time_slot: str) -> datetime | None:
    start_text = time_slot.split(" - ")[0].strip()
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {start_text}", fmt)
        except ValueError:
            continue
    return None


def _subject(block: dict) -> str:
    return f"Study: {block.get('assignment') or 'Study'}"


def _utc_converter(tz_name: str | None, utc_offset_minutes: int | None):
    """A local→UTC function for the student's zone, or None if unknown.

    An IANA name (captured from the browser) wins because it knows about
    daylight saving: a fixed offset recorded in October is an hour wrong for
    every block scheduled after the clocks change in November.
    """
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(str(tz_name))
            return lambda local: local.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    if utc_offset_minutes is not None:
        offset = timedelta(minutes=int(utc_offset_minutes))
        return lambda local: local - offset
    return None


def export_schedule(token: dict, schedule_data: dict, *, utc_offset_minutes: int | None = None,
                    tz_name: str | None = None, timezone_name: str = DEFAULT_TIMEZONE,
                    skip_overlaps: bool = False) -> dict:
    """Write study blocks as Outlook events. ``{"created": [ids], "skipped": n}``.

    Two things the first version did not do, both of which Google's export
    does:

    * **No duplicates.** Asking Plani to "put it on my calendar" twice made
      every block twice. A block whose subject and start already exist in the
      calendar is skipped, so the push is safe to repeat.
    * **Respect existing events** when ``skip_overlaps`` is set, so a study
      block is not laid over a meeting the scheduler did not know about.

    Times are sent in UTC when the student's offset is known, because a
    Windows zone name hardcoded to Pacific put every block at the wrong hour
    for anyone outside California.
    """
    blocks = []
    for day in schedule_data.get("schedule", []) or []:
        for block in day.get("blocks", []) or []:
            if block.get("is_break") or " - " not in str(block.get("time_slot", "")):
                continue
            start = _block_start(str(day.get("date", "")), str(block["time_slot"]))
            if not start:
                continue
            end = start + timedelta(minutes=int(block.get("duration_minutes") or 30))
            blocks.append((start, end, block))
    if not blocks:
        return {"created": [], "skipped": 0}

    converter = _utc_converter(tz_name, utc_offset_minutes)

    def to_utc(local: datetime) -> datetime:
        return converter(local) if converter else local

    existing = []
    try:
        window_start = to_utc(min(b[0] for b in blocks)) - timedelta(days=1)
        window_end = to_utc(max(b[1] for b in blocks)) + timedelta(days=1)
        for event in _calendar_view(token, window_start, window_end):
            try:
                existing.append((_graph_datetime(event["start"]), _graph_datetime(event["end"]),
                                 str(event.get("subject") or ""), _is_busy(event)))
            except (KeyError, TypeError, ValueError):
                continue
    except Exception as exc:
        # Not being able to read the calendar is no reason to refuse to write
        # to it; it only means duplicates cannot be ruled out this time.
        print(f"[outlook] could not read existing events before export: {type(exc).__name__}")

    created: list[str] = []
    skipped = 0
    for start, end, block in blocks:
        subject = _subject(block)
        u_start, u_end = to_utc(start), to_utc(end)
        # Existing events come back in UTC (see _headers). Without a known
        # zone the block times are local, so the comparison is approximate
        # -- still enough to catch a byte-identical repeat push.
        duplicate = any(s == u_start and subj == subject for s, _e, subj, _b in existing)
        overlap = skip_overlaps and any(busy and s < u_end and u_start < e
                                        for s, e, _subj, busy in existing)
        if duplicate or overlap:
            skipped += 1
            continue
        if converter:
            when = {"start": {"dateTime": u_start.isoformat(), "timeZone": "UTC"},
                    "end": {"dateTime": u_end.isoformat(), "timeZone": "UTC"}}
        else:
            when = {"start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
                    "end": {"dateTime": end.isoformat(), "timeZone": timezone_name}}
        notes = str(block.get("notes") or "").strip()
        event = graph_post(token, "/me/events", {
            "subject": subject,
            "body": {"contentType": "text",
                     "content": f"Course: {block.get('course') or 'General'}\n"
                                + (f"{notes}\n" if notes else "") + "Created by IntelliPlan"},
            "categories": ["IntelliPlan"],
            "showAs": "busy",
            **when,
        })
        if event.get("id"):
            created.append(event["id"])
            existing.append((u_start, u_end, subject, True))
    return {"created": created, "skipped": skipped}


def add_schedule_to_calendar(token: dict, schedule_data: dict, timezone_name: str = DEFAULT_TIMEZONE,
                             utc_offset_minutes: int | None = None, tz_name: str | None = None) -> list[str]:
    """Back-compatible wrapper: the ids of the events created."""
    return export_schedule(token, schedule_data, timezone_name=timezone_name,
                           utc_offset_minutes=utc_offset_minutes, tz_name=tz_name)["created"]
