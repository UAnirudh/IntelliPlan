from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os
import json

SCOPES = ['https://www.googleapis.com/auth/calendar']
CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")]
    }
}

def get_flow():
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI")
    )
    flow.code_challenge_method = None
    flow.oauth2session._client.code_challenge_method = None
    return flow

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
    """Get busy times for a given date to find free slots."""
    service, _ = get_calendar_service(token_dict)
    day = datetime.strptime(date_str, "%Y-%m-%d")
    time_min = day.replace(hour=6, minute=0).isoformat() + "Z"
    time_max = day.replace(hour=23, minute=59).isoformat() + "Z"
    
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": "primary"}]
    }
    result = service.freebusy().query(body=body).execute()
    busy = result.get("calendars", {}).get("primary", {}).get("busy", [])
    return busy

def find_free_slots(token_dict, date_str, duration_minutes=120):
    """Find free time slots on a given date."""
    busy = get_free_busy(token_dict, date_str)
    day = datetime.strptime(date_str, "%Y-%m-%d")
    
    # Build timeline from 7am to 11pm
    slots = []
    current = day.replace(hour=7, minute=0)
    end_of_day = day.replace(hour=23, minute=0)
    
    busy_ranges = []
    for b in busy:
        start = datetime.fromisoformat(b["start"].replace("Z", ""))
        end = datetime.fromisoformat(b["end"].replace("Z", ""))
        busy_ranges.append((start, end))
    
    while current + timedelta(minutes=30) <= end_of_day:
        slot_end = current + timedelta(minutes=30)
        is_free = all(
            not (current < bend and slot_end > bstart)
            for bstart, bend in busy_ranges
        )
        if is_free:
            slots.append(current.strftime("%I:%M %p"))
        current += timedelta(minutes=30)
    
    # Find best contiguous block
    if slots:
        # Prefer evening slots (6pm+)
        evening = [s for s in slots if int(s.split(":")[0]) >= 6 and "PM" in s]
        return evening[0] if evening else slots[0]
    return "7:00 PM"

def get_upcoming_events(token_dict, days=7):
    """Get events from Google Calendar for the next N days."""
    service, _ = get_calendar_service(token_dict)
    now = datetime.utcnow().isoformat() + "Z"
    end = (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"
    
    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        maxResults=50,
        singleEvents=True,
        orderBy="startTime"
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

def add_event_to_calendar(token_dict, title, date_str, start_time_str, duration_minutes, description=""):
    """Add a study block to Google Calendar."""
    service, creds = get_calendar_service(token_dict)
    
    try:
        start_dt = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %I:%M %p")
    except:
        start_dt = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M")
    
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    
    event = {
        "summary": f"📚 {title}",
        "description": description or f"Study block created by IntelliPlan",
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "America/Los_Angeles"
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "America/Los_Angeles"
        },
        "colorId": "1"
    }
    
    created = service.events().insert(calendarId="primary", body=event).execute()
    # Save updated token
    return created.get("id"), creds.token

def add_schedule_to_calendar(token_dict, schedule_data):
    """Add an entire generated schedule to Google Calendar."""
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
                continue
            
            end_dt = start_dt + timedelta(minutes=block.get("duration_minutes", 30))
            
            event = {
                "summary": f"📚 {block.get('assignment', 'Study')}",
                "description": f"Course: {block.get('course', '')}\n{block.get('notes', '')}\n\nCreated by IntelliPlan",
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": "America/Los_Angeles"
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": "America/Los_Angeles"
                },
                "colorId": "1"
            }
            
            created = service.events().insert(calendarId="primary", body=event).execute()
            created_ids.append(created.get("id"))
    
    return created_ids, creds.token or token_dict.get("token", "")