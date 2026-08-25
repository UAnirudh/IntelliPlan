"""Blackboard Learn provider (REST + OAuth 2.0).

API reference: https://developer.blackboard.com/portal/displayApi
Required env vars:
  BLACKBOARD_BASE_URL    — e.g. https://blackboard.myschool.edu
  BLACKBOARD_APP_KEY     — from Blackboard Developer Portal
  BLACKBOARD_APP_SECRET  — from Blackboard Developer Portal
"""

from __future__ import annotations

import os
import time
import urllib.parse
from datetime import datetime
from typing import Any

import requests

from intelliplan.integrations.lms.base import LMSAssignment, LMSCourse, LMSProvider


def _base_url() -> str:
    return (os.getenv("BLACKBOARD_BASE_URL", "") or "").rstrip("/")


def _credentials() -> tuple[str, str]:
    """App key/secret, accepting either env naming convention.

    ``App.py`` provisions ``BLACKBOARD_CLIENT_ID`` / ``BLACKBOARD_CLIENT_SECRET``
    (the names used in ``.env.example``); this module originally read
    ``BLACKBOARD_APP_KEY`` / ``BLACKBOARD_APP_SECRET``. A deployment that set
    only one pair reported Blackboard as unconfigured here.
    """
    key = (os.getenv("BLACKBOARD_APP_KEY") or os.getenv("BLACKBOARD_CLIENT_ID") or "").strip()
    secret = (os.getenv("BLACKBOARD_APP_SECRET") or os.getenv("BLACKBOARD_CLIENT_SECRET") or "").strip()
    return key, secret


class BlackboardProvider(LMSProvider):
    key = "blackboard"
    display_name = "Blackboard Learn"
    docs_url = "https://developer.blackboard.com/portal/displayApi"

    @classmethod
    def is_configured(cls) -> bool:
        key, secret = _credentials()
        return bool(_base_url() and key and secret)

    def get_authorize_url(self, *, user_id: int, redirect_uri: str, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": _credentials()[0],
            "redirect_uri": redirect_uri,
            "scope": "read",
            "state": state,
        }
        return f"{_base_url()}/learn/api/public/v1/oauth2/authorizationcode?{urllib.parse.urlencode(params)}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        resp = requests.post(
            f"{_base_url()}/learn/api/public/v1/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=_credentials(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_at": int(time.time()) + int(data.get("expires_in", 0)),
        }

    def _headers(self, tokens: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.get('access_token','')}"}

    def list_courses(self, *, tokens: dict[str, Any]) -> list[LMSCourse]:
        resp = requests.get(
            f"{_base_url()}/learn/api/public/v1/users/me/courses",
            headers=self._headers(tokens),
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        out: list[LMSCourse] = []
        for c in resp.json().get("results", []):
            cid = c.get("courseId") or c.get("id")
            if not cid:
                continue
            out.append(LMSCourse(
                external_id=f"blackboard:{cid}",
                name=c.get("name", "Course"),
                meta={"raw": c},
            ))
        return out

    def list_assignments(self, *, tokens: dict[str, Any], course: LMSCourse) -> list[LMSAssignment]:
        course_id = course.external_id.split(":", 1)[1]
        resp = requests.get(
            f"{_base_url()}/learn/api/public/v1/courses/{course_id}/gradebook/columns",
            headers=self._headers(tokens),
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        out: list[LMSAssignment] = []
        for col in resp.json().get("results", []):
            due_raw = col.get("grading", {}).get("due") if isinstance(col.get("grading"), dict) else None
            due_at = None
            if due_raw:
                try:
                    due_at = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    due_at = None
            out.append(LMSAssignment(
                external_id=f"blackboard:{course_id}:{col.get('id')}",
                course_external_id=course.external_id,
                title=col.get("name", "Assignment"),
                due_at=due_at,
                points_possible=col.get("score", {}).get("possible") if isinstance(col.get("score"), dict) else None,
            ))
        return out
