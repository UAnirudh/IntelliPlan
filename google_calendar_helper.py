from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os
import requests as http_requests
import json
import secrets
import hashlib
import base64

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_auth_url(state):
    """Generate Google OAuth URL without PKCE."""
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI"),
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/calendar",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true"
    }
    base_url = "https://accounts.google.com/o/oauth2/v2/auth"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base_url}?{query}"

def exchange_code_for_token(code):
    """Exchange authorization code for tokens — no PKCE."""
    resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI"),
            "grant_type": "authorization_code"
        }
    )
    data = resp.json()
    if "error" in data:
        raise Exception(f"Token exchange failed: {data}")
    return {
        "token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "scopes": SCOPES
    }

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

def get_upcoming_events(token_dict):
    service, _ = get_calendar_service(token_dict)
    now = datetime.utcnow().isoformat() + "Z"
    end = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"
    events_result = service.events().list(
        calendarId="primary", timeMin=now, timeMax=end,
        maxResults=50, singleEvents=True, orderBy="startTime"
    ).execute()
    events = events_result.get("items", [])
    result = []
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date", ""))
        result.append({
            "id": e["id"],
            "title": e.get("summary", "Untitled"),
            "start": start,
            "description": e.get("description", ""),
            "source": "google_calendar"
        })
    return result

def add_schedule_to_calendar(token_dict, schedule_data):
    service, creds = get_calendar_service(token_dict)
    created_ids = []
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
            except:
                try:
                    start_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
                except:
                    continue
            end_dt = start_dt + timedelta(minutes=block.get("duration_minutes", 30))
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
    return created_ids, new_token