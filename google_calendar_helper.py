from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp
import httplib2
from datetime import datetime, timedelta, timezone
import time
from time_utils import utcnow
import os
import requests as http_requests
import secrets
import hashlib
import base64
import json

LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
]

# The file-picker flow asks for access to individual files the student
# selects. Do not add this to the ordinary calendar connection: file access
# is requested only when the student chooses Drive in Integrations.
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
]

SCOPES = CALENDAR_SCOPES + LOGIN_SCOPES
CALENDAR_HTTP_TIMEOUT_SECONDS = 15

def _redirect_uri(redirect_uri=None):
    return redirect_uri or os.getenv("GOOGLE_REDIRECT_URI") or "https://intelliplan.tech/oauth2callback"

def scopes_for(purpose):
    """The scopes one consent screen asks for.

    Drive is requested through this same OAuth client rather than a second
    Google app: one client, one consent screen to get verified, one redirect
    URI. ``include_granted_scopes`` (below) makes it incremental, so a student
    who already linked Calendar is only asked about Drive, and the token that
    comes back covers both.
    """
    if purpose == "login":
        return list(LOGIN_SCOPES)
    if purpose == "drive":
        # Read per call so GOOGLE_DRIVE_SCOPE_MODE takes effect without a
        # restart; the drive.file-only default matches DRIVE_SCOPES above.
        from google_drive_helper import drive_scopes
        return drive_scopes() + LOGIN_SCOPES
    return list(SCOPES)


def get_auth_url(state, purpose="calendar", redirect_uri=None):
    """Generate Google OAuth URL with PKCE for secure flow."""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()
    
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": _redirect_uri(redirect_uri),
        "response_type": "code",
        "scope": " ".join(scopes_for(purpose)),
        "access_type": "offline",
        "prompt": "select_account" if purpose == "login" else "consent",
        "state": state,
        "include_granted_scopes": "true",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    import urllib.parse
    base_url = "https://accounts.google.com/o/oauth2/v2/auth"
    return f"{base_url}?{urllib.parse.urlencode(params)}", code_verifier

def exchange_code_for_token(code, code_verifier=None, redirect_uri=None):
    """Exchange authorization code for tokens — with PKCE."""
    data = {
        "code": code,
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": _redirect_uri(redirect_uri),
        "grant_type": "authorization_code"
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    # A token exchange with no ceiling can hold a worker open indefinitely
    # if Google stops answering mid-connection, which takes the whole site
    # down rather than this one sign-in.
    resp = http_requests.post("https://oauth2.googleapis.com/token",
                              data=data, timeout=15)
    data = resp.json()
    if "error" in data:
        raise Exception(f"Token exchange failed: {data}")
    granted_scopes = data.get("scope", "")
    return {
        "token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        # Recorded so a client can renew *before* a request fails rather than
        # discovering the expiry as a 401 halfway through a search.
        "expires_at": time.time() + int(data.get("expires_in") or 3600),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "scopes": granted_scopes.split() if granted_scopes else SCOPES
    }

def merge_token_data(existing_token, new_token):
    """Preserve refresh_token/client metadata when Google omits it on later grants."""
    merged = {**(existing_token or {}), **(new_token or {})}
    if existing_token and not new_token.get("refresh_token"):
        merged["refresh_token"] = existing_token.get("refresh_token")
    merged.setdefault("token_uri", "https://oauth2.googleapis.com/token")
    merged.setdefault("client_id", os.getenv("GOOGLE_CLIENT_ID"))
    merged.setdefault("client_secret", os.getenv("GOOGLE_CLIENT_SECRET"))
    return merged

def has_calendar_scope(token_dict):
    scopes = set(token_dict.get("scopes") or [])
    return bool(scopes.intersection(CALENDAR_SCOPES))

def has_drive_scope(token_dict):
    scopes = set(token_dict.get("scopes") or [])
    return bool(scopes.intersection(DRIVE_SCOPES))

def refresh_access_token(token_dict):
    """Refresh an expired access token."""
    resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
        timeout=15,
        data={
            "refresh_token": token_dict["refresh_token"],
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "grant_type": "refresh_token"
        }
    )
    data = resp.json()
    if "error" in data:
        raise Exception(f"Token refresh failed: {data}")
    token_dict["token"] = data["access_token"]
    token_dict["expires_at"] = time.time() + int(data.get("expires_in") or 3600)
    return token_dict

def get_calendar_service(token_dict):
    creds = Credentials(
        token=token_dict.get("token"),
        refresh_token=token_dict.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES
    )
    # A separate transport per service keeps credentials/connections isolated.
    # The same socket timeout covers both API calls and automatic token refresh.
    transport = AuthorizedHttp(
        creds, http=httplib2.Http(timeout=CALENDAR_HTTP_TIMEOUT_SECONDS),
        max_refresh_attempts=1,
    )
    return build("calendar", "v3", http=transport, static_discovery=True), creds

def get_free_busy(token_dict, date_str):
    service, _ = get_calendar_service(token_dict)
    day = datetime.strptime(date_str, "%Y-%m-%d")
    time_min = day.replace(hour=6, minute=0).isoformat() + "Z"
    time_max = day.replace(hour=23, minute=59).isoformat() + "Z"
    body = {"timeMin": time_min, "timeMax": time_max, "items": [{"id": "primary"}]}
    result = service.freebusy().query(body=body).execute()
    return result.get("calendars", {}).get("primary", {}).get("busy", [])

def busy_minutes_by_date(token_dict, start_date, days=14, utc_offset_minutes=0, tz_name=None,
                         require_complete=False):
    """``{date: [(start_minute, end_minute), ...]}`` of committed time.

    The scheduler has always subtracted the weekly commitments a student typed
    into settings, and never looked at their actual calendar — so a plan could
    put an hour of chemistry on top of a dentist appointment that was sitting
    right there in Google Calendar. This is the query that closes that.

    One range query for the whole horizon rather than one per day: fourteen
    round trips on the critical path of generating a plan is the difference
    between a scheduler that feels instant and one nobody waits for.

    IANA zones follow daylight saving across the horizon. Older accounts can
    still use their saved fixed offset when no valid zone is known.
    """
    try:
        offset = int(utc_offset_minutes or 0)
    except (TypeError, ValueError):
        offset = 0
    from time_utils import busy_intervals, to_utc

    span_days = max(1, min(60, int(days or 1)))
    local_start = datetime.combine(start_date, datetime.min.time())
    local_end = local_start + timedelta(days=span_days)
    # Local wall clock to UTC is a subtraction: 17:00 at UTC+2 is 15:00 UTC.
    time_min = to_utc(local_start, tz_name, offset).isoformat() + "Z"
    time_max = to_utc(local_end, tz_name, offset).isoformat() + "Z"

    service, _ = get_calendar_service(token_dict)
    result = service.freebusy().query(body={
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": "primary"}],
    }).execute()
    calendars = result.get('calendars') if isinstance(result, dict) else None
    primary = calendars.get('primary') if isinstance(calendars, dict) else None
    if isinstance(primary, dict) and primary.get('errors'):
        raise ValueError('Google calendar availability is incomplete.')
    if require_complete and (not isinstance(primary, dict) or not isinstance(primary.get('busy'), list)):
        raise ValueError('Google calendar availability is incomplete.')
    periods = (primary or {}).get('busy') or []

    out = {}
    for period in periods:
        try:
            raw_start, raw_end = _parse_rfc3339(period.get('start')), _parse_rfc3339(period.get('end'))
            if raw_end <= raw_start:
                raise ValueError('Invalid calendar interval')
            start = max(raw_start, to_utc(local_start, tz_name, offset))
            end = min(raw_end, to_utc(local_end, tz_name, offset))
        except Exception:
            if require_complete:
                raise ValueError('Google calendar availability is incomplete.') from None
            continue
        for day, intervals in busy_intervals(start, end, tz_name, offset).items():
            out.setdefault(day, []).extend(intervals)
    return out


def _parse_rfc3339(value):
    """Parse one of Google's timestamps into a naive UTC datetime.

    Naive-UTC rather than aware, because everything downstream does arithmetic
    against naive local datetimes and mixing the two raises.
    """
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def find_free_slots(token_dict, date_str):
    busy = get_free_busy(token_dict, date_str)
    day = datetime.strptime(date_str, "%Y-%m-%d")
    current = day.replace(hour=7, minute=0)
    end_of_day = day.replace(hour=23, minute=0)
    busy_ranges = []
    for b in busy:
        start = datetime.fromisoformat(b["start"].replace("Z", ""))
        end = datetime.fromisoformat(b["end"].replace("Z", ""))
        busy_ranges.append((start, end))
    slots = []
    while current + timedelta(minutes=30) <= end_of_day:
        slot_end = current + timedelta(minutes=30)
        is_free = all(not (current < bend and slot_end > bstart) for bstart, bend in busy_ranges)
        if is_free:
            slots.append(current.strftime("%I:%M %p").lstrip("0"))
        current += timedelta(minutes=30)
    if slots:
        evening = [s for s in slots if "PM" in s and int(s.split(":")[0]) >= 6]
        return evening[0] if evening else slots[0]
    return "7:00 PM"

def compute_free_hours(token_dict, date_str):
    """Return total free hours between 7 AM and 11 PM for the given date."""
    try:
        busy = get_free_busy(token_dict, date_str)
        day = datetime.strptime(date_str, "%Y-%m-%d")
        current = day.replace(hour=7, minute=0)
        end_of_day = day.replace(hour=23, minute=0)
        busy_ranges = []
        for b in busy:
            start = datetime.fromisoformat(b["start"].replace("Z", ""))
            end = datetime.fromisoformat(b["end"].replace("Z", ""))
            busy_ranges.append((start, end))
        free_slots = 0
        while current + timedelta(minutes=30) <= end_of_day:
            slot_end = current + timedelta(minutes=30)
            is_free = all(not (current < bend and slot_end > bstart) for bstart, bend in busy_ranges)
            if is_free:
                free_slots += 1
            current += timedelta(minutes=30)
        return round(free_slots * 0.5, 1)
    except Exception:
        return 0

def get_upcoming_events(token_dict):
    service, _ = get_calendar_service(token_dict)
    now = utcnow().isoformat() + "Z"
    end = (utcnow() + timedelta(days=7)).isoformat() + "Z"
    events_result = service.events().list(
        calendarId="primary", timeMin=now, timeMax=end,
        maxResults=50, singleEvents=True, orderBy="startTime"
    ).execute()
    events = events_result.get("items", [])
    result = []
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date", ""))
        end_t = e.get("end", {}).get("dateTime", e.get("end", {}).get("date", ""))
        result.append({
            "id": e["id"],
            "title": e.get("summary", "Untitled"),
            "start": start,
            "end": end_t,
            "description": e.get("description", ""),
            "source": "google_calendar"
        })
    return result

def _export_boundary(value, calendar_zone=None):
    """A Google timed or all-day event boundary, as naive UTC."""
    from time_utils import zone_for
    if isinstance(value, str):  # Legacy get_upcoming_events shape.
        value = {'dateTime': value} if 'T' in value else {'date': value}
    if not isinstance(value, dict):
        raise ValueError('Google calendar availability is incomplete.')
    text = value.get('dateTime') or value.get('date')
    if not isinstance(text, str):
        raise ValueError('Google calendar availability is incomplete.')
    parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    zone = zone_for(value.get('timeZone') or calendar_zone)
    if zone is None:
        raise ValueError('Google calendar availability has an unknown time zone.')
    return parsed.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)


def _export_existing(event, calendar_zone):
    if not isinstance(event, dict):
        raise ValueError('Google calendar availability is incomplete.')
    if event.get('status') == 'cancelled':
        return None
    try:
        start, end = _export_boundary(event.get('start'), calendar_zone), _export_boundary(event.get('end'), calendar_zone)
        if end <= start:
            raise ValueError('Invalid interval')
    except (TypeError, ValueError):
        raise ValueError('Google calendar availability is incomplete.') from None
    busy = event.get('transparency') != 'transparent' and event.get('eventType') != 'workingLocation'
    return (start, end, str(event.get('summary') or event.get('title') or ''), busy, event.get('id'))


def _export_calendar_view(service, start, end):
    """Bounded, complete occurrences for this schedule, not a seven-day preview."""
    events, page_token, calendar_zone = [], None, None
    for page in range(5):
        params = dict(calendarId='primary', timeMin=start.isoformat() + 'Z', timeMax=end.isoformat() + 'Z',
                      maxResults=250, singleEvents=True, orderBy='startTime',
                      fields='items(id,status,summary,start,end,transparency,eventType),nextPageToken,timeZone')
        if page_token:
            params['pageToken'] = page_token
        result = service.events().list(**params).execute()
        if (not isinstance(result, dict) or result.get('error')
                or not isinstance(result.get('items'), list)):
            raise ValueError('Google calendar availability is incomplete.')
        calendar_zone = result.get('timeZone') or calendar_zone
        for item in result['items']:
            existing = _export_existing(item, calendar_zone)
            if existing is not None:
                events.append(existing)
        if len(events) > 1250:
            raise ValueError('Google calendar availability exceeds the safe import limit.')
        page_token = result.get('nextPageToken')
        if not page_token:
            return events, calendar_zone
        if not isinstance(page_token, str) or page == 4:
            raise ValueError('Google calendar availability exceeds the safe import limit.')
    raise ValueError('Google calendar availability is incomplete.')


def add_schedule_to_calendar(token_dict, schedule_data, existing_events=None, *,
                             skip_overlaps=None, tz_name=None, utc_offset_minutes=None):
    """Export after a complete preflight; return (created ids, token, skipped).

    The three original positional arguments remain supported. Explicit caller
    zones/offsets win; the old API's Pacific default is used only when neither
    exists. Network/invalid-provider failures propagate so partial writes are
    never reported as a fully successful export. Deterministic IDs make retries
    safe even when Google's successful insert response was lost.
    """
    from time_utils import to_utc, zone_for
    from googleapiclient.errors import HttpError
    zone = tz_name
    if not zone_for(zone):
        zone = 'America/Los_Angeles' if utc_offset_minutes is None else ''
    offset = int(utc_offset_minutes or 0)
    blocks = []
    for day in schedule_data.get('schedule', []):
        for block in day.get('blocks', []):
            if block.get('is_break'):
                continue
            clock = str(block.get('time_slot') or '').split(' - ')[0].strip()
            start = None
            for fmt in ('%Y-%m-%d %I:%M %p', '%Y-%m-%d %H:%M'):
                try:
                    start = datetime.strptime(f"{day.get('date')} {clock}", fmt)
                    break
                except ValueError:
                    pass
            if start is None:
                raise ValueError('Study block has an invalid date or time.')
            minutes = int(block.get('duration_minutes') or 30)
            if not 0 < minutes <= 1440:
                raise ValueError('Study block has an invalid duration.')
            end = start + timedelta(minutes=minutes)
            blocks.append((to_utc(start, zone, offset), to_utc(end, zone, offset), block))
    if not blocks:
        return [], token_dict.get('token', ''), 0
    if skip_overlaps is None:
        skip_overlaps = bool(existing_events)
    service, creds = get_calendar_service(token_dict)
    try:
        start = min(item[0] for item in blocks) - timedelta(days=1)
        end = max(item[1] for item in blocks) + timedelta(days=1)
        existing, calendar_zone = _export_calendar_view(service, start, end)
        for event in existing_events or []:
            item = _export_existing(event, calendar_zone or zone or 'UTC')
            if item is not None:
                existing.append(item)
        created_ids, skipped = [], 0
        for start, end, block in blocks:
            event = {
                'summary': f"📚 {block.get('assignment', 'Study')}",
                'description': f"Course: {block.get('course', '')}\n{block.get('notes', '')}\n\nCreated by IntelliPlan",
                'start': {'dateTime': start.isoformat() + 'Z', 'timeZone': 'UTC'},
                'end': {'dateTime': end.isoformat() + 'Z', 'timeZone': 'UTC'}, 'colorId': '1',
            }
            identity = dict(event, block=str(block.get('task_key') or block.get('assignment_ref')
                                             or block.get('assignment_id') or block.get('id') or ''))
            event_id = hashlib.sha256(('intelliplan-google-v1:' + json.dumps(
                identity, sort_keys=True, separators=(',', ':'), ensure_ascii=False)).encode()).hexdigest()
            duplicate = any(eid == event_id or (s == start and e == end and title == event['summary'])
                            for s, e, title, _busy, eid in existing)
            overlap = skip_overlaps and any(busy and s < end and start < e
                                            for s, e, _title, busy, _id in existing)
            if duplicate or overlap:
                skipped += 1
                continue
            event['id'] = event_id  # Hex is a valid subset of Google's base32hex IDs.
            event['extendedProperties'] = {'private': {'intelliplan_export': event_id}}
            try:
                created = service.events().insert(calendarId='primary', body=event).execute()
                if not isinstance(created, dict) or created.get('id') != event_id:
                    raise ValueError('Google calendar did not confirm the event identity.')
                created_ids.append(event_id)
            except HttpError as exc:
                if getattr(exc.resp, 'status', None) != 409:
                    raise
                actual = service.events().get(calendarId='primary', eventId=event_id).execute()
                marker = actual.get('extendedProperties') if isinstance(actual, dict) else None
                private = marker.get('private') if isinstance(marker, dict) else None
                if (not isinstance(private, dict) or private.get('intelliplan_export') != event_id
                        or actual.get('id') != event_id or actual.get('status') == 'cancelled'
                        or actual.get('summary') != event['summary'] or actual.get('description') != event['description']
                        or _export_boundary(actual.get('start'), calendar_zone) != start
                        or _export_boundary(actual.get('end'), calendar_zone) != end):
                    raise ValueError('Google calendar event identity could not be verified.') from None
                skipped += 1
            existing.append((start, end, event['summary'], True, event_id))
        return created_ids, creds.token or token_dict.get('token', ''), skipped
    finally:
        close = getattr(service, 'close', None)
        if callable(close):
            close()
