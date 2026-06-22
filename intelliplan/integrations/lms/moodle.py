"""Moodle provider (Web Services REST API).

Moodle doesn't use OAuth by default — students paste a personal Web
Services token they generate in their profile, or admins configure the
site-wide token. We treat the per-user token as the "access_token".

Required env vars (optional):
  MOODLE_BASE_URL — e.g. https://moodle.myschool.edu (used as default)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

from intelliplan.integrations.lms.base import LMSAssignment, LMSCourse, LMSProvider


def _base_url(tokens: dict[str, Any] | None = None) -> str:
    if tokens and tokens.get("base_url"):
        return str(tokens["base_url"]).rstrip("/")
    return (os.getenv("MOODLE_BASE_URL", "") or "").rstrip("/")


class MoodleProvider(LMSProvider):
    key = "moodle"
    display_name = "Moodle"
    docs_url = "https://docs.moodle.org/dev/Web_services"

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.getenv("MOODLE_BASE_URL"))

    def get_authorize_url(self, *, user_id: int, redirect_uri: str, state: str) -> str:
        # Moodle Web Services uses a manual token paste, not OAuth redirects.
        # The UI directs users to /user/managetoken.php to mint one.
        return f"{_base_url()}/user/managetoken.php"

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        # `code` here is the manually-pasted token.
        return {"access_token": code, "refresh_token": None, "expires_at": None}

    def _call(self, tokens: dict[str, Any], fn: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{_base_url(tokens)}/webservice/rest/server.php"
        payload = {
            "wstoken": tokens.get("access_token", ""),
            "wsfunction": fn,
            "moodlewsrestformat": "json",
            **(params or {}),
        }
        resp = requests.post(url, data=payload, timeout=15)
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def list_courses(self, *, tokens: dict[str, Any]) -> list[LMSCourse]:
        # core_enrol_get_users_courses needs the user id. We resolve it via
        # core_webservice_get_site_info which returns the calling user.
        info = self._call(tokens, "core_webservice_get_site_info") or {}
        uid = info.get("userid")
        if not uid:
            return []
        courses = self._call(tokens, "core_enrol_get_users_courses", {"userid": uid}) or []
        out: list[LMSCourse] = []
        for c in courses:
            out.append(LMSCourse(
                external_id=f"moodle:{c.get('id')}",
                name=c.get("fullname") or c.get("shortname") or "Course",
                meta={"raw": c},
            ))
        return out

    def list_assignments(self, *, tokens: dict[str, Any], course: LMSCourse) -> list[LMSAssignment]:
        course_id = course.external_id.split(":", 1)[1]
        data = self._call(tokens, "mod_assign_get_assignments", {"courseids[0]": course_id}) or {}
        out: list[LMSAssignment] = []
        for course_entry in data.get("courses", []):
            for a in course_entry.get("assignments", []):
                due_at = None
                if a.get("duedate"):
                    try:
                        due_at = datetime.fromtimestamp(int(a["duedate"]), tz=timezone.utc)
                    except (TypeError, ValueError):
                        due_at = None
                out.append(LMSAssignment(
                    external_id=f"moodle:{course_id}:{a.get('id')}",
                    course_external_id=course.external_id,
                    title=a.get("name", "Assignment"),
                    due_at=due_at,
                    description=a.get("intro"),
                    points_possible=a.get("grade"),
                ))
        return out
