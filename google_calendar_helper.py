from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from time_utils import utcnow
import os
import requests as http_requests
import json
import secrets
import hashlib
import base64

LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

SCOPES = CALENDAR_SCOPES + LOGIN_SCOPES

def _redirect_uri(redirect_uri=None):
    return redirect_uri or os.getenv("GOOGLE_REDIRECT_URI") or "https://intelliplan.tech/oauth2callback"

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
        "scope": " ".join(LOGIN_SCOPES if purpose == "login" else SCOPES),
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
    resp = http_requests.post("https://oauth2.googleapis.com/token", data=data)
    data = resp.json()
    if "error" in data:
        raise Exception(f"Token exchange failed: {data}")
    granted_scopes = data.get("scope", "")
    return {
        "token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
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

def refresh_access_token(token_dict):
    """Refresh an expired access token."""
    resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
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
    return build("calendar", "v3", credentials=creds), creds

def get_free_busy(token_dict, date_str):
    service, _ = get_calendar_service(token_dict)
    day = datetime.strptime(date_str, "%Y-%m-%d")
    time_min = day.replace(hour=6, minute=0).isoformat() + "Z"
    time_max = day.replace(hour=23, minute=59).isoformat() + "Z"
    body = {"timeMin": time_min, "timeMax": time_max, "items": [{"id": "primary"}]}
    result = service.freebusy().query(body=body).execute()
    return result.get("calendars", {}).get("primary", {}).get("busy", [])

def busy_minutes_by_date(token_dict, start_date, days=14, utc_offset_minutes=0):
    """``{date: [(start_minute, end_minute), ...]}`` of committed time.

    The scheduler has always subtracted the weekly commitments a student typed
    into settings, and never looked at their actual calendar — so a plan could
    put an hour of chemistry on top of a dentist appointment that was sitting
    right there in Google Calendar. This is the query that closes that.

    One range query for the whole horizon rather than one per day: fourteen
    round trips on the critical path of generating a plan is the difference
    between a scheduler that feels instant and one nobody waits for.

    Offsets, not timezone names, because that is what the app stores per user.
    Times come back from Google in UTC and the planner thinks in local
    minute-of-day, so the conversion has to happen somewhere and here is where
    the offset is known.
    """
    try:
        offset = timedelta(minutes=int(utc_offset_minutes or 0))
    except (TypeError, ValueError):
        offset = timedelta(0)

    span_days = max(1, min(60, int(days or 1)))
    local_start = datetime.combine(start_date, datetime.min.time())
    local_end = local_start + timedelta(days=span_days)
    # Local wall clock to UTC is a subtraction: 17:00 at UTC+2 is 15:00 UTC.
    time_min = (local_start - offset).isoformat() + "Z"
    time_max = (local_end - offset).isoformat() + "Z"

    service, _ = get_calendar_service(token_dict)
    result = service.freebusy().query(body={
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": "primary"}],
    }).execute()
    periods = result.get("calendars", {}).get("primary", {}).get("busy", []) or []

    out = {}
    for period in periods:
        try:
            start = _parse_rfc3339(period.get("start")) + offset
            end = _parse_rfc3339(period.get("end")) + offset
        except Exception:
            continue
        if end <= start:
            continue
        # An event spanning midnight is busy time on both days, and clipping it
        # to the first would hand the student a free evening they do not have.
        cursor = start
        while cursor < end:
            day = cursor.date()
            day_end = datetime.combine(day, datetime.min.time()) + timedelta(days=1)
            piece_end = min(end, day_end)
            start_minute = cursor.hour * 60 + cursor.minute
            end_minute = int((piece_end - datetime.combine(day, datetime.min.time())).total_seconds() // 60)
            if end_minute > start_minute:
                out.setdefault(day, []).append((start_minute, min(24 * 60, end_minute)))
            cursor = piece_end
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

def add_schedule_to_calendar(token_dict, schedule_data, existing_events=None):
    service, creds = get_calendar_service(token_dict)
    created_ids = []
    skipped = 0
    existing_events = existing_events or []

    # Build existing event time ranges
    busy_ranges = []
    for e in existing_events:
        start_str = e.get("start", "")
        if "T" in start_str:
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", ""))
                busy_ranges.append(start_dt)
            except Exception:
                pass

    for day in schedule_data.get("schedule", []):
        date_str = day["date"]
        for block in day.get("blocks", []):
            if block.get("is_break"):
                continue
            time_slot = block.get("time_slot", "")
            if " - " not in time_slot:
                continue
            start_str = time_slot.split(" - ")[0].strip()
            try:
                start_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %I:%M %p")
            except Exception:
                try:
                    start_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
                except Exception:
                    continue

            end_dt = start_dt + timedelta(minutes=block.get("duration_minutes", 30))

            # Check overlap
            if busy_ranges:
                overlap = any(
                    abs((start_dt - b).total_seconds()) < 1800
                    for b in busy_ranges
                )
                if overlap:
                    skipped += 1
                    continue

            event = {
                "summary": f"📚 {block.get('assignment', 'Study')}",
                "description": f"Course: {block.get('course', '')}\n{block.get('notes', '')}\n\nCreated by IntelliPlan",
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Los_Angeles"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Los_Angeles"},
                "colorId": "1"
            }
            try:
                created = service.events().insert(calendarId="primary", body=event).execute()
                created_ids.append(created.get("id"))
            except Exception as e:
                print(f"Failed to create event: {e}")

    new_token = creds.token or token_dict.get("token", "")
    return created_ids, new_token, skipped
