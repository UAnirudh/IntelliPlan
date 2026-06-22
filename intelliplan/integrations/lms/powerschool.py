"""PowerSchool SIS provider (OAuth 2.0 client_credentials + per-user impersonation).

API reference: https://support.powerschool.com/developer/
Required env vars:
  POWERSCHOOL_BASE_URL    — e.g. https://yourdistrict.powerschool.com
  POWERSCHOOL_CLIENT_ID
  POWERSCHOOL_CLIENT_SECRET
"""

from __future__ import annotations

import base64
import os
import time
import urllib.parse
from datetime import datetime
from typing import Any

import requests

from intelliplan.integrations.lms.base import LMSAssignment, LMSCourse, LMSProvider


def _base_url() -> str:
    return (os.getenv("POWERSCHOOL_BASE_URL", "") or "").rstrip("/")


class PowerSchoolProvider(LMSProvider):
    key = "powerschool"
    display_name = "PowerSchool"
    docs_url = "https://support.powerschool.com/developer/"

    @classmethod
    def is_configured(cls) -> bool:
        return all([
            os.getenv("POWERSCHOOL_BASE_URL"),
            os.getenv("POWERSCHOOL_CLIENT_ID"),
            os.getenv("POWERSCHOOL_CLIENT_SECRET"),
        ])

    def get_authorize_url(self, *, user_id: int, redirect_uri: str, state: str) -> str:
        # PowerSchool's parent/student portals expose a plain OAuth2 endpoint.
        params = {
            "response_type": "code",
            "client_id": os.getenv("POWERSCHOOL_CLIENT_ID", ""),
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{_base_url()}/oauth/authorize?{urllib.parse.urlencode(params)}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        cid = os.getenv("POWERSCHOOL_CLIENT_ID", "")
        cs = os.getenv("POWERSCHOOL_CLIENT_SECRET", "")
        basic = base64.b64encode(f"{cid}:{cs}".encode("ascii")).decode("ascii")
        resp = requests.post(
            f"{_base_url()}/oauth/access_token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
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
        return {
            "Authorization": f"Bearer {tokens.get('access_token','')}",
            "Accept": "application/json",
        }

    def list_courses(self, *, tokens: dict[str, Any]) -> list[LMSCourse]:
        resp = requests.get(
            f"{_base_url()}/ws/schema/query/com.pearson.core.student.student_sections",
            headers=self._headers(tokens),
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        rows = resp.json().get("record", [])
        out: list[LMSCourse] = []
        for r in rows:
            sid = r.get("sectionid") or r.get("id")
            if not sid:
                continue
            out.append(LMSCourse(
                external_id=f"powerschool:{sid}",
                name=r.get("course_name") or r.get("name", "Section"),
                section=r.get("section_number"),
                teacher_name=r.get("teacher_name"),
                meta={"raw": r},
            ))
        return out

    def list_assignments(self, *, tokens: dict[str, Any], course: LMSCourse) -> list[LMSAssignment]:
        section_id = course.external_id.split(":", 1)[1]
        resp = requests.get(
            f"{_base_url()}/ws/schema/query/com.pearson.core.assignments.by_section",
            headers=self._headers(tokens),
            params={"section_id": section_id},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        out: list[LMSAssignment] = []
        for a in resp.json().get("record", []):
            due_at = None
            due_raw = a.get("duedate")
            if due_raw:
                try:
                    due_at = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    due_at = None
            out.append(LMSAssignment(
                external_id=f"powerschool:{section_id}:{a.get('id')}",
                course_external_id=course.external_id,
                title=a.get("name", "Assignment"),
                due_at=due_at,
                description=a.get("description"),
                points_possible=a.get("pointspossible"),
                score=a.get("score"),
                graded=bool(a.get("score")),
            ))
        return out
