"""Google Classroom provider.

Reuses the Google OAuth credentials already configured for Google
Calendar (``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``). Requires the
extra Classroom scopes — when they're missing, the user is prompted to
re-authorize with the broader scope set.

API reference: https://developers.google.com/classroom/reference/rest
"""

from __future__ import annotations

import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests

from intelliplan.integrations.lms.base import LMSAssignment, LMSCourse, LMSProvider

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
]

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://classroom.googleapis.com/v1"


class GoogleClassroomProvider(LMSProvider):
    key = "google_classroom"
    display_name = "Google Classroom"
    docs_url = "https://developers.google.com/classroom"

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))

    def get_authorize_url(self, *, user_id: int, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_at": int(time.time()) + int(data.get("expires_in", 0)),
            "scope": data.get("scope", ""),
        }

    def refresh_tokens(self, *, tokens: dict[str, Any]) -> dict[str, Any]:
        rt = tokens.get("refresh_token")
        if not rt:
            return tokens
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
                "refresh_token": rt,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            **tokens,
            "access_token": data.get("access_token", tokens.get("access_token")),
            "expires_at": int(time.time()) + int(data.get("expires_in", 0)),
        }

    def _headers(self, tokens: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.get('access_token','')}"}

    def list_courses(self, *, tokens: dict[str, Any]) -> list[LMSCourse]:
        resp = requests.get(
            f"{API_BASE}/courses",
            headers=self._headers(tokens),
            params={"courseStates": "ACTIVE"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        out: list[LMSCourse] = []
        for c in resp.json().get("courses", []):
            out.append(LMSCourse(
                external_id=f"google_classroom:{c.get('id')}",
                name=c.get("name", "Course"),
                section=c.get("section"),
                meta={"raw": c},
            ))
        return out

    def list_assignments(self, *, tokens: dict[str, Any], course: LMSCourse) -> list[LMSAssignment]:
        course_id = course.external_id.split(":", 1)[1]
        resp = requests.get(
            f"{API_BASE}/courses/{course_id}/courseWork",
            headers=self._headers(tokens),
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        out: list[LMSAssignment] = []
        for w in resp.json().get("courseWork", []):
            due_at = None
            d = w.get("dueDate") or {}
            t = w.get("dueTime") or {}
            if d.get("year") and d.get("month") and d.get("day"):
                try:
                    due_at = datetime(
                        year=d["year"], month=d["month"], day=d["day"],
                        hour=t.get("hours", 23), minute=t.get("minutes", 59),
                        tzinfo=timezone.utc,
                    )
                except (TypeError, ValueError):
                    due_at = None
            out.append(LMSAssignment(
                external_id=f"google_classroom:{course_id}:{w.get('id')}",
                course_external_id=course.external_id,
                title=w.get("title", "Assignment"),
                due_at=due_at,
                description=w.get("description"),
                points_possible=w.get("maxPoints"),
                url=w.get("alternateLink"),
            ))
        return out
