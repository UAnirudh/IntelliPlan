"""
intelliplan_mcp.py — Model Context Protocol server for IntelliPlan

Exposes IntelliPlan tools to MCP-aware AI clients (Claude Desktop, Cursor,
Claude Code, etc.) by wrapping the public REST API in intelliplan_api.py.

Setup
-----
1. Install:  pip install mcp httpx
2. Get an API token at https://intelliplan.tech/api/v1/auth/token
   (POST {email, password} → {token}).
3. Export INTELLIPLAN_API_BASE (default https://intelliplan.tech) and
   INTELLIPLAN_API_TOKEN.
4. Wire it into your MCP-aware client. Example (Claude Desktop config):

   {
     "mcpServers": {
       "intelliplan": {
         "command": "python",
         "args": ["intelliplan_mcp.py"],
         "env": {
           "INTELLIPLAN_API_BASE": "https://intelliplan.tech",
           "INTELLIPLAN_API_TOKEN": "<paste-token>"
         }
       }
     }
   }

Tools exposed
-------------
* list_assignments              → all unified assignments (Canvas, StudentVue,
                                  Schoology, Notion, manual)
* create_task                   → add a manual task / homework item
* dismiss_assignment            → mark an assignment done
* restore_assignment            → un-dismiss
* list_tests                    → all assignments marked as tests
* mark_as_test / unmark_test    → toggle test status
* generate_schedule             → AI-built study plan
* get_streak                    → current streak, sparks, level
* get_profile / update_profile  → student identity (grade, focus, goals)

Run standalone:  python intelliplan_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

try:
    import httpx
except ImportError:
    print("Please `pip install httpx` to use the IntelliPlan MCP server.", file=sys.stderr)
    raise

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Please `pip install \"mcp[cli]\"` to use the IntelliPlan MCP server.", file=sys.stderr)
    raise


API_BASE = os.getenv("INTELLIPLAN_API_BASE", "https://intelliplan.tech").rstrip("/")
API_TOKEN = os.getenv("INTELLIPLAN_API_TOKEN", "")

if not API_TOKEN:
    print(
        "WARNING: INTELLIPLAN_API_TOKEN is not set. Most tools will fail with 401.\n"
        "Get a token with:\n"
        f'  curl -X POST {API_BASE}/api/v1/auth/token -H "Content-Type: application/json"'
        ' -d \'{"email":"you@example.com","password":"..."}\'',
        file=sys.stderr,
    )

mcp = FastMCP("intelliplan")

_client: httpx.AsyncClient | None = None


async def _client_get() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=f"{API_BASE}/api/v1",
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "intelliplan-mcp/1.0",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
    return _client


async def _request(method: str, path: str, **kwargs) -> Any:
    client = await _client_get()
    resp = await client.request(method, path, **kwargs)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    if resp.status_code >= 400:
        return {"error": True, "status": resp.status_code, "body": body}
    return body


# ── Assignments ──────────────────────────────────────────────────────
@mcp.tool()
async def list_assignments() -> str:
    """List every assignment IntelliPlan knows about for the authenticated
    student (Canvas, StudentVue, Schoology, Notion, manual tasks). Each
    item includes title, course, due_date, priority, estimated_time, and
    source.
    """
    data = await _request("GET", "/assignments")
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def create_task(
    title: str,
    due_date: str | None = None,
    priority: str = "Medium",
    course: str = "Personal",
    estimated_time: int = 60,
    notes: str = "",
) -> str:
    """Create a manual task / homework item.

    Args:
      title: required, what needs to get done.
      due_date: ISO date (YYYY-MM-DD) or None.
      priority: High | Medium | Low.
      course: course or category name.
      estimated_time: minutes (default 60).
      notes: free-form notes.
    """
    payload = {
        "title": title, "due_date": due_date, "priority": priority,
        "course": course, "estimated_time": estimated_time, "notes": notes,
    }
    return json.dumps(await _request("POST", "/tasks", json=payload), indent=2, default=str)


@mcp.tool()
async def dismiss_assignment(title: str) -> str:
    """Mark an assignment as done."""
    return json.dumps(await _request("POST", "/assignments/dismiss", json={"title": title}), indent=2)


@mcp.tool()
async def restore_assignment(title: str) -> str:
    """Restore a previously dismissed assignment."""
    return json.dumps(await _request("POST", "/assignments/restore", json={"title": title}), indent=2)


# ── Tests ────────────────────────────────────────────────────────────
@mcp.tool()
async def list_tests() -> str:
    """List every assignment the student has flagged as a test."""
    return json.dumps(await _request("GET", "/tests"), indent=2, default=str)


@mcp.tool()
async def mark_as_test(title: str, course: str = "", due_date: str | None = None) -> str:
    """Flag an assignment as a test so it appears in the Tests tab."""
    payload = {"title": title, "course": course, "due_date": due_date}
    return json.dumps(await _request("POST", "/tests", json=payload), indent=2)


@mcp.tool()
async def unmark_test(title: str) -> str:
    """Remove the test flag from an assignment."""
    return json.dumps(await _request("DELETE", "/tests", json={"title": title}), indent=2)


# ── Schedule ─────────────────────────────────────────────────────────
@mcp.tool()
async def generate_schedule(
    hours_per_day: float = 2.0,
    preferred_time: str = "evening",
    custom_tasks: list[str] | None = None,
) -> str:
    """Build a personalised study plan with IntelliPlan's AI scheduler.

    Args:
      hours_per_day: target hours, e.g. 2.0
      preferred_time: morning | afternoon | evening
      custom_tasks: list of extra topics to schedule beyond known assignments.
    """
    payload = {
        "hours_per_day": hours_per_day,
        "preferred_time": preferred_time,
        "custom_tasks": custom_tasks or [],
    }
    return json.dumps(await _request("POST", "/schedule/generate", json=payload), indent=2, default=str)


# ── Streak / sparks ──────────────────────────────────────────────────
@mcp.tool()
async def get_streak() -> str:
    """Get the student's current streak day count, sparks balance, level,
    longest streak, and weekly quest progress."""
    return json.dumps(await _request("GET", "/streak"), indent=2, default=str)


# ── Identity / profile ───────────────────────────────────────────────
@mcp.tool()
async def get_profile() -> str:
    """Get the student's grade level, focus areas, goals, availability."""
    return json.dumps(await _request("GET", "/identity"), indent=2, default=str)


@mcp.tool()
async def update_profile(
    grade_level: str | None = None,
    focus_areas: list[str] | None = None,
    goals: str | None = None,
    weekly_commitments: str | None = None,
) -> str:
    """Update fields of the student's profile. Only fields you pass are
    changed; pass None to leave a field as-is.

    Args:
      grade_level: e.g. "11th grade".
      focus_areas: list, e.g. ["Math","Physics","Test prep (SAT / ACT / AP)"].
      goals: free text.
      weekly_commitments: free text (extracurriculars, sports, etc.).
    """
    payload: dict[str, Any] = {}
    if grade_level is not None:
        payload["grade_level"] = grade_level
    if focus_areas is not None:
        payload["focus_areas"] = focus_areas
    if goals is not None:
        payload["goals"] = goals
    if weekly_commitments is not None:
        payload["weekly_commitments"] = weekly_commitments
    return json.dumps(await _request("PATCH", "/identity", json=payload), indent=2, default=str)


# ── Health / discovery ───────────────────────────────────────────────
@mcp.tool()
async def api_info() -> str:
    """Return the IntelliPlan API endpoint catalogue and version."""
    return json.dumps(await _request("GET", "/docs"), indent=2)


if __name__ == "__main__":
    mcp.run()
