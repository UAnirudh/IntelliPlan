"""Microsoft Graph helpers for a student's Outlook calendar.

The module is deliberately small and transport-only: App owns account
identity/token persistence, while this module refreshes tokens and translates
the schedule shape shared by Google and Outlook into Graph events.

It is also the one place the Microsoft app registration is read from, so
OneDrive (``onedrive_helper``) reuses the same client id, secret, tenant and
redirect URI rather than a second registration.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote, urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid5

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("openid", "profile", "offline_access", "User.Read", "Calendars.ReadWrite")
DEFAULT_TIMEZONE = "Pacific Standard Time"

# Common Windows names emitted by Graph, mapped to CLDR's primary IANA zone.
# https://github.com/unicode-org/cldr/blob/main/common/supplemental/windowsZones.xml
# Unknown names are rejected rather than treating a local time as UTC.
_WINDOWS_TIMEZONES = {
    'UTC': 'UTC',
    'Alaskan Standard Time': 'America/Anchorage',
    'Hawaiian Standard Time': 'Pacific/Honolulu',
    'Pacific Standard Time': 'America/Los_Angeles',
    'Pacific Standard Time (Mexico)': 'America/Tijuana',
    'Mountain Standard Time': 'America/Denver',
    'US Mountain Standard Time': 'America/Phoenix',
    'Central Standard Time': 'America/Chicago',
    'Eastern Standard Time': 'America/New_York',
    'US Eastern Standard Time': 'America/Indianapolis',
    'Atlantic Standard Time': 'America/Halifax',
    'Newfoundland Standard Time': 'America/St_Johns',
    'GMT Standard Time': 'Europe/London',
    'Greenwich Standard Time': 'Atlantic/Reykjavik',
    'W. Europe Standard Time': 'Europe/Berlin',
    'Romance Standard Time': 'Europe/Paris',
    'Central Europe Standard Time': 'Europe/Budapest',
    'FLE Standard Time': 'Europe/Kyiv',
    'GTB Standard Time': 'Europe/Bucharest',
    'Turkey Standard Time': 'Europe/Istanbul',
    'Israel Standard Time': 'Asia/Jerusalem',
    'Arabian Standard Time': 'Asia/Dubai',
    'India Standard Time': 'Asia/Kolkata',
    'Singapore Standard Time': 'Asia/Singapore',
    'China Standard Time': 'Asia/Shanghai',
    'Tokyo Standard Time': 'Asia/Tokyo',
    'Korea Standard Time': 'Asia/Seoul',
    'AUS Eastern Standard Time': 'Australia/Sydney',
    'E. Australia Standard Time': 'Australia/Brisbane',
    'AUS Central Standard Time': 'Australia/Darwin',
    'Cen. Australia Standard Time': 'Australia/Adelaide',
    'W. Australia Standard Time': 'Australia/Perth',
    'New Zealand Standard Time': 'Pacific/Auckland',
}


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
    url = _graph_url(path)
    response = requests.get(url, params=params, headers=_headers(token), timeout=15,
                            allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise ValueError('Outlook calendar returned an unexpected redirect.')
    response.raise_for_status()
    return response.json()


def _graph_url(path: str) -> str:
    """Keep bearer-authenticated reads on this Graph origin and API version."""
    if not isinstance(path, str) or any(ch.isspace() or ord(ch) < 32 for ch in path) or '\\' in path:
        raise ValueError('Outlook calendar returned an invalid URL.')
    url = f"{GRAPH}{path}" if path.startswith('/') and not path.startswith('//') else path
    try:
        parsed = urlsplit(url)
        decoded_path = unquote(parsed.path)
        valid = (parsed.scheme == 'https' and parsed.hostname == 'graph.microsoft.com'
                 and parsed.port in (None, 443) and parsed.username is None
                 and parsed.password is None and not parsed.fragment
                 and parsed.path.startswith('/v1.0/')
                 and not any(segment in ('.', '..') for segment in decoded_path.split('/'))
                 and '\\' not in decoded_path
                 and not any(ord(ch) < 32 for ch in decoded_path))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Outlook calendar returned an invalid URL.')
    return url


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
                   select: str = "subject,start,end,isAllDay,showAs,isCancelled,originalStartTimeZone,originalEndTimeZone",
                   max_pages: int = 5, require_complete: bool = False) -> list[dict]:
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
    for page in range(max_pages):
        if require_complete and (not isinstance(data, dict) or data.get('error')
                                 or not isinstance(data.get('value'), list)
                                 or any(not isinstance(e, dict) for e in data['value'])):
            raise ValueError('Outlook calendar availability is incomplete.')
        out.extend(e for e in data.get("value", []) if isinstance(e, dict))
        next_link = data.get("@odata.nextLink")
        if not next_link:
            break
        try:
            # Validate even when graph_get is replaced by an injected adapter.
            _graph_url(next_link)
        except ValueError:
            if require_complete:
                raise ValueError('Outlook calendar availability is incomplete.') from None
            break
        if page + 1 == max_pages:
            if require_complete:
                raise ValueError('Outlook calendar availability exceeds the safe import limit.')
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
                         utc_offset_minutes: int = 0,
                         tz_name: str | None = None,
                         require_complete: bool = False) -> dict[date, list[tuple[int, int]]]:
    """Read the calendar view once and return local wall-clock busy ranges.

    The window is the student's local days converted to UTC. It used to send
    local midnight as if it were UTC, so a student at UTC-7 lost the last
    seven hours of the horizon and gained seven hours before it.
    """
    from time_utils import busy_intervals, to_utc
    offset = int(utc_offset_minutes or 0)
    local_start = datetime.combine(start_date, datetime.min.time())
    local_end = local_start + timedelta(days=max(1, min(60, int(days or 1))))
    utc_start, utc_end = to_utc(local_start, tz_name, offset), to_utc(local_end, tz_name, offset)
    events = _calendar_view(token, utc_start, utc_end, **({'require_complete': True} if require_complete else {}))
    out: dict[date, list[tuple[int, int]]] = {}
    for event in events:
        if not _is_busy(event):
            continue
        try:
            raw_start, raw_end = _graph_datetime(event['start']), _graph_datetime(event['end'])
            if raw_end <= raw_start:
                raise ValueError('Invalid calendar interval')
            start = max(raw_start, utc_start)
            end = min(raw_end, utc_end)
        except (KeyError, TypeError, ValueError):
            if require_complete:
                raise ValueError('Outlook calendar availability is incomplete.') from None
            continue
        for day, intervals in busy_intervals(start, end, tz_name, offset).items():
            out.setdefault(day, []).extend(intervals)
    return out


def _graph_zone(name):
    from time_utils import zone_for
    name = name or 'UTC'
    if not isinstance(name, str):
        raise ValueError('Outlook calendar returned an invalid time zone.')
    zone = zone_for(_WINDOWS_TIMEZONES.get(name, name))
    if zone is None:
        raise ValueError('Outlook calendar returned an unknown time zone.')
    return zone


def _graph_datetime(value: dict) -> datetime:
    if not isinstance(value, dict) or not isinstance(value.get('dateTime'), str):
        raise ValueError('Outlook calendar returned an invalid date/time.')
    raw = value['dateTime'].replace("Z", "+00:00")
    # Graph sends seven fractional digits ("…:00.0000000"); fromisoformat on
    # older Pythons accepts at most six.
    # Only trim fractional seconds; an explicit +/-HH:MM offset must survive.
    raw = re.sub(r'(\.\d{6})\d+', r'\1', raw)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    # All-day events can retain the mailbox's zone even when UTC is requested.
    # A naive dateTime is meaningful only together with Graph's timeZone field.
    zone = _graph_zone(value.get('timeZone'))
    return parsed.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)


def get_upcoming_events(token: dict, days: int = 7) -> list[dict]:
    """Next week's events in the same shape ``google_calendar_helper`` returns,
    so the calendar list endpoint can merge the two without special cases."""
    now = datetime.utcnow().replace(microsecond=0)
    out = []
    for event in _calendar_view(token, now, now + timedelta(days=max(1, min(31, days)))):
        if event.get("isCancelled"):
            continue
        try:
            start_utc, end_utc = _graph_datetime(event['start']), _graph_datetime(event['end'])
            if event.get('isAllDay'):
                # All-day dates belong to the event's calendar, not UTC's date.
                start_zone = _graph_zone(event.get('originalStartTimeZone') or event['start'].get('timeZone'))
                end_zone = _graph_zone(event.get('originalEndTimeZone') or event['end'].get('timeZone'))
                start = start_utc.replace(tzinfo=timezone.utc).astimezone(start_zone).date().isoformat()
                end = end_utc.replace(tzinfo=timezone.utc).astimezone(end_zone).date().isoformat()
            else:
                start, end = start_utc.isoformat() + 'Z', end_utc.isoformat() + 'Z'
        except (KeyError, TypeError, ValueError):
            continue
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
    if converter is None:
        # Legacy callers still supply a Windows name. Use it for both the
        # preflight and the write so deduplication compares actual UTC instants.
        fallback_zone = _graph_zone(timezone_name)
        def converter(local):
            return local.replace(tzinfo=fallback_zone).astimezone(timezone.utc).replace(tzinfo=None)

    def to_utc(local: datetime) -> datetime:
        return converter(local)

    existing = []
    window_start = to_utc(min(b[0] for b in blocks)) - timedelta(days=1)
    window_end = to_utc(max(b[1] for b in blocks)) + timedelta(days=1)
    # Deduplication needs a complete read even when meeting overlaps are allowed.
    # Unknown calendar time is never permission for a blind, duplicate write.
    for event in _calendar_view(token, window_start, window_end, require_complete=True):
        try:
            event_start, event_end = _graph_datetime(event['start']), _graph_datetime(event['end'])
            if event_end <= event_start:
                raise ValueError('Invalid calendar interval')
            existing.append((event_start, event_end, str(event.get("subject") or ""), _is_busy(event)))
        except (KeyError, TypeError, ValueError):
            raise ValueError('Outlook calendar availability is incomplete.') from None

    created: list[str] = []
    skipped = 0
    for start, end, block in blocks:
        subject = _subject(block)
        u_start, u_end = to_utc(start), to_utc(end)
        # Compare actual UTC instants even for the legacy Windows-zone API.
        duplicate = any(s == u_start and subj == subject for s, _e, subj, _b in existing)
        overlap = skip_overlaps and any(busy and s < u_end and u_start < e
                                        for s, e, _subj, busy in existing)
        if duplicate or overlap:
            skipped += 1
            continue
        when = {"start": {"dateTime": u_start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": u_end.isoformat(), "timeZone": "UTC"}}
        notes = str(block.get("notes") or "").strip()
        identity = {
            'start': u_start.isoformat(), 'end': u_end.isoformat(), 'subject': subject,
            'course': str(block.get('course') or 'General'), 'notes': notes,
            'block': str(block.get('task_key') or block.get('assignment_ref')
                         or block.get('assignment_id') or block.get('id') or ''),
        }
        # Graph deduplicates a repeated POST even when its first response was
        # lost and the newly created event is not visible in calendarView yet.
        # Token refreshes cannot change this per-event identifier.
        transaction_id = str(uuid5(NAMESPACE_URL, 'https://intelliplan.tech/outlook/' +
                                   json.dumps(identity, sort_keys=True, separators=(',', ':'), ensure_ascii=False)))
        event = graph_post(token, "/me/events", {
            "subject": subject,
            "transactionId": transaction_id,
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
