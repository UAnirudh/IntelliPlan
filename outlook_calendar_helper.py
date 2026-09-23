"""Microsoft Graph helpers for a student's Outlook calendar.

The module is deliberately small and transport-only: App owns account
identity/token persistence, while this module refreshes tokens and translates
the schedule shape shared by Google and Outlook into Graph events.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("openid", "profile", "offline_access", "User.Read", "Calendars.ReadWrite")


def configured() -> bool:
    return bool(os.getenv("MICROSOFT_CLIENT_ID") and os.getenv("MICROSOFT_CLIENT_SECRET")
                and os.getenv("MICROSOFT_REDIRECT_URI"))


def get_auth_url(state: str) -> str:
    params = {
        "client_id": os.environ["MICROSOFT_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": os.environ["MICROSOFT_REDIRECT_URI"],
        "response_mode": "query",
        "scope": " ".join(SCOPES),
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTHORITY}/authorize?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    return _token_request({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": os.environ["MICROSOFT_REDIRECT_URI"]})


def refresh_token(token: dict) -> dict:
    refreshed = _token_request({"grant_type": "refresh_token",
                                "refresh_token": token.get("refresh_token", "")})
    return {**token, **refreshed,
            "refresh_token": refreshed.get("refresh_token") or token.get("refresh_token", "")}


def _token_request(payload: dict) -> dict:
    response = requests.post(f"{AUTHORITY}/token", data={
        "client_id": os.environ["MICROSOFT_CLIENT_ID"],
        "client_secret": os.environ["MICROSOFT_CLIENT_SECRET"],
        "scope": " ".join(SCOPES), **payload,
    }, timeout=15)
    response.raise_for_status()
    return response.json()


def graph_get(token: dict, path: str, params: dict | None = None) -> dict:
    response = requests.get(f"{GRAPH}{path}", params=params, headers=_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


def graph_post(token: dict, path: str, body: dict) -> dict:
    response = requests.post(f"{GRAPH}{path}", json=body, headers=_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


def _headers(token: dict) -> dict:
    return {"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"}


def profile(token: dict) -> dict:
    return graph_get(token, "/me", {"$select": "displayName,mail,userPrincipalName"})


def busy_minutes_by_date(token: dict, start_date: date, days: int = 14,
                         utc_offset_minutes: int = 0) -> dict[date, list[tuple[int, int]]]:
    """Read the calendar view once and return local wall-clock busy ranges."""
    offset = timedelta(minutes=int(utc_offset_minutes or 0))
    start = datetime.combine(start_date, datetime.min.time())
    end = start + timedelta(days=max(1, min(60, int(days or 1))))
    data = graph_get(token, "/me/calendarView", {
        "startDateTime": start.isoformat(), "endDateTime": end.isoformat(),
        "$select": "start,end,isAllDay", "$top": "1000",
    })
    out: dict[date, list[tuple[int, int]]] = {}
    for event in data.get("value", []):
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
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def add_schedule_to_calendar(token: dict, schedule_data: dict, timezone_name: str = "Pacific Standard Time") -> list[str]:
    created: list[str] = []
    for day in schedule_data.get("schedule", []):
        for block in day.get("blocks", []):
            if block.get("is_break") or " - " not in str(block.get("time_slot", "")):
                continue
            try:
                start = datetime.strptime(f"{day['date']} {block['time_slot'].split(' - ')[0].strip()}", "%Y-%m-%d %I:%M %p")
            except ValueError:
                continue
            end = start + timedelta(minutes=int(block.get("duration_minutes") or 30))
            event = graph_post(token, "/me/events", {
                "subject": f"Study: {block.get('assignment') or 'Study'}",
                "body": {"contentType": "text", "content": f"Course: {block.get('course') or 'General'}\nCreated by IntelliPlan"},
                "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
                "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
            })
            if event.get("id"):
                created.append(event["id"])
    return created
