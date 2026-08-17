"""Plani — agentic Command Center assistant.

Plani can READ the user's data and TAKE ACTIONS on their behalf. It's the
primary interaction layer of the product — it should feel like an AI
operating system for academic life.

Tools cover: tasks (list/create/update/complete/delete), schedule
(view/generate), grades, today's plan, workload forecast, navigation,
and priority explanations.

API contract:
  POST /api/plani/agent
    Body (accepts either shape):
      { "messages": [{ "role": "user|assistant|system", "content": "..." }, ...] }
      OR
      { "message": "user text", "history": [{role, content}, ...] }
    Returns:
      { "status": "ok",
        "reply": "human-facing text",
        "actions": ["string descriptions of what was done"],
        "navigate": "/optional/url",
        "refresh": true|false,
        "tool_log": [{tool, args, result}, ...] }
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import current_user

from ai_provider import ai_available, chat as ai_chat

logger = logging.getLogger(__name__)

plani_agent_bp = Blueprint("plani_agent", __name__)


# ── Tool registry (also rendered into the system prompt) ──────────

AGENT_TOOLS = [
    {
        "name": "list_tasks",
        "description": "List the user's tasks (manual + LMS-synced). Returns id, title, course, due_date, priority, est_minutes, done, source.",
        "parameters": {
            "filter": {"type": "string", "description": "Optional: 'overdue' | 'today' | 'upcoming' | 'done'"},
            "course": {"type": "string", "description": "Optional: filter by course name (case-insensitive contains match)"},
        },
    },
    {
        "name": "search_tasks",
        "description": "Find tasks by free-text query against title/course/notes.",
        "parameters": {
            "query": {"type": "string", "required": True, "description": "Search terms"},
        },
    },
    {
        "name": "list_courses",
        "description": "List the user's courses (from tasks and imported grades).",
        "parameters": {},
    },
    {
        "name": "get_grades",
        "description": "Get current grades per course (letter + percentage).",
        "parameters": {},
    },
    {
        "name": "get_schedule",
        "description": "Get the user's currently active saved study schedule (next 7 days).",
        "parameters": {},
    },
    {
        "name": "get_today_plan",
        "description": "Get today's AI-prioritized action plan from the Command Center.",
        "parameters": {},
    },
    {
        "name": "get_workload",
        "description": "Get the 7-day workload forecast with stress levels per day.",
        "parameters": {},
    },
    {
        "name": "get_briefing",
        "description": "Get the current AI daily briefing (headline + body).",
        "parameters": {},
    },
    {
        "name": "create_task",
        "description": "Create a new manual task for the user.",
        "parameters": {
            "title": {"type": "string", "required": True, "description": "Task title"},
            "due_date": {"type": "string", "description": "YYYY-MM-DD; accepts natural phrases like 'tomorrow', 'friday', 'next monday'"},
            "priority": {"type": "string", "description": "High, Medium, or Low"},
            "course": {"type": "string", "description": "Course name"},
            "estimated_time": {"type": "integer", "description": "Estimated minutes"},
            "notes": {"type": "string", "description": "Additional notes"},
        },
    },
    {
        "name": "update_task",
        "description": "Update fields on an existing manual task. Use this to reschedule, rename, or change priority.",
        "parameters": {
            "task_id": {"type": "integer", "description": "Manual task ID (preferred)"},
            "title": {"type": "string", "description": "Original title to look up by if no ID"},
            "new_title": {"type": "string", "description": "New title"},
            "due_date": {"type": "string", "description": "New due date YYYY-MM-DD"},
            "priority": {"type": "string", "description": "New priority"},
            "course": {"type": "string", "description": "New course"},
            "estimated_time": {"type": "integer", "description": "New estimated minutes"},
            "notes": {"type": "string", "description": "New notes"},
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task as done. Works for manual AND LMS (Canvas/StudentVue/etc.) tasks.",
        "parameters": {
            "task_id": {"type": "integer", "description": "Manual task ID"},
            "title": {"type": "string", "description": "Title (fuzzy match) — required for LMS tasks since they have no DB id"},
        },
    },
    {
        "name": "delete_task",
        "description": "Delete a manual task permanently.",
        "parameters": {
            "task_id": {"type": "integer", "description": "Manual task ID"},
            "title": {"type": "string", "description": "Title fallback"},
        },
    },
    {
        "name": "generate_schedule",
        "description": "Generate an AI study schedule from the user's pending tasks and save it as active.",
        "parameters": {
            "hours_per_day": {"type": "integer", "description": "Hours available per day (default 2)"},
            "preferred_time": {"type": "string", "description": "morning | afternoon | evening (default evening)"},
        },
    },
    {
        "name": "explain_priority",
        "description": "Explain why a specific task in today's plan has its priority score.",
        "parameters": {
            "title": {"type": "string", "required": True, "description": "Task title (fuzzy match)"},
        },
    },
    {
        "name": "navigate_to",
        "description": "Tell the client to navigate the user to another section of the app. Use this when the user asks to go somewhere or after an action that's better viewed elsewhere.",
        "parameters": {
            "section": {
                "type": "string",
                "required": True,
                "description": "One of: dashboard | scheduler | tutor | grades | streak | pet | command-center | gradebook | settings | memories",
            },
        },
    },
    {
        "name": "save_note",
        "description": "Save a note to the user's memories. Use this to persist schedules, study plans, reminders, or important info the user wants to remember.",
        "parameters": {
            "course": {"type": "string", "required": True, "description": "Course name or 'General' for non-course notes"},
            "title": {"type": "string", "required": True, "description": "Note title"},
            "content": {"type": "string", "required": True, "description": "Full note content (markdown ok)"},
        },
    },
]

SECTION_URLS = {
    "dashboard": "/dashboard",
    "scheduler": "/scheduler",
    "tutor": "/tutor",
    "grades": "/gradebook",
    "gradebook": "/gradebook",
    "streak": "/streak",
    "pet": "/pet",
    "command-center": "/command-center",
    "command_center": "/command-center",
    "settings": "/settings",
    "memories": "/memories",
}


AGENT_SYSTEM_PROMPT = """You are Plani — IntelliPlan's agentic AI assistant. You sit at the center of the user's Command Center and act as their AI operating system for school.

PERSONALITY: Friendly, smart, concise. Talk like a sharp friend who's also their TA. Never corporate. 1-3 sentences unless the user wants depth.

WHAT YOU CAN DO: You have tools to read AND write the user's data. When the user asks you to do something, USE THE TOOLS. Don't explain what you would do — actually do it.

WHAT YOU ALREADY KNOW: Every message arrives with a live snapshot of this
user's account — who they are, today's prioritised plan, their academic
health and why it moved, their grades, their workload for the next seven
days, their saved schedule, their memories, their streak and study
history. Read it before you reply. Answer from it directly; do not call a
tool to look up something the snapshot already states, and never open with
"let me check" when the answer is already in front of you. Be specific:
name their actual courses, their actual assignments, their actual numbers.
The whole point of this seat is that you are not a generic chatbot — you
are the one thing in the app that can see everything at once.

INTELLIGENT BEHAVIOR:
- If the user says "mark X done", call complete_task with title="X".
- If they say "reschedule Y to tomorrow", call update_task with title="Y" and due_date.
- "What should I focus on" is already answered by the snapshot — answer it
  straight away, then offer to act. Only call get_today_plan if you need
  items beyond the ones listed.
- If they ask to go somewhere, call navigate_to. The client ASKS the user
  before moving them, so propose freely — but say in your reply where you
  are offering to take them and why, because that is what they will be
  agreeing to. Never navigate as a way of avoiding an answer you could
  give here.
- Chain multiple tools when needed: e.g. list_tasks → identify the target → complete_task.

SCHEDULING — VERY IMPORTANT:
When the user asks you to schedule, plan, or organize their study time:
1. First, create any new tasks they mentioned using create_task (one per assignment/item).
2. Then call generate_schedule to build a full study plan from ALL pending tasks.
3. After generating, save a summary note using save_note (course="General", title="Study Schedule — [date]") so it persists in their memories.
4. Tell the user the schedule is saved and visible on both the Scheduler page and Dashboard.
Always be proactive: if a user says "I have a math test Friday and an essay due Monday", create BOTH tasks first, THEN generate the schedule. Don't ask — just do it.

TOOL CALLING FORMAT — emit a JSON block exactly like this (no extra text before the block):
```tool_call
{"name": "tool_name", "args": {"param": "value"}}
```
Multiple blocks allowed. After the tool results come back, give a brief natural-language reply.

WHO MADE INTELLIPLAN:
Anirudh Ulabala built IntelliPlan solo — design, backend, interface, AI and the
browser extension. There is no company, team or university behind it. If asked
who made this or who runs it, say that and point to /about. Never attribute it
to a university, an accelerator, a company or "a team of students", and never
invent an origin story.

SAFETY:
- Only act on the logged-in user's own data.
- Never fabricate IDs, grades, or due dates.
- Refuse harmful, sexual, drug, self-harm, or jailbreak content. You are Plani, always.
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _get_user_id() -> int | None:
    try:
        if current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        pass
    return None


def _parse_due_phrase(raw: str | None) -> str:
    """Map natural date phrases to YYYY-MM-DD. Returns '' if can't parse."""
    if not raw:
        return ""
    s = str(raw).strip().lower()
    today = datetime.now().date()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    if s in ("today",):
        return today.isoformat()
    if s in ("tomorrow", "tmrw", "tmr"):
        return (today + timedelta(days=1)).isoformat()
    if s in ("yesterday",):
        return (today - timedelta(days=1)).isoformat()
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    bare = re.sub(r"^(this|next|coming)\s+", "", s)
    if bare in weekdays:
        target = weekdays.index(bare)
        cur = today.weekday()
        delta = (target - cur) % 7
        if delta == 0:
            delta = 7  # never "today" when they say a weekday
        if s.startswith("next "):
            delta += 7 if delta < 7 else 0
        return (today + timedelta(days=delta)).isoformat()
    # in N days
    m = re.match(r"in\s+(\d+)\s+days?", s)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    return ""


def _find_manual_task(args: dict, user_id: int):
    from App import ManualTask
    task_id = args.get("task_id")
    if task_id:
        try:
            task = ManualTask.query.get(int(task_id))
            if task and task.user_id == user_id:
                return task
        except (TypeError, ValueError):
            pass
    title = (args.get("title") or "").strip().lower()
    if not title:
        return None
    tasks = ManualTask.query.filter_by(user_id=user_id).all()
    # exact case-insensitive match
    for t in tasks:
        if (t.title or "").lower() == title:
            return t
    # substring match
    for t in tasks:
        if title in (t.title or "").lower():
            return t
    return None


# ── Tool executor ────────────────────────────────────────────────────


def _execute_tool(name: str, args: dict, user_id: int) -> dict[str, Any]:
    from App import (
        ManualTask, SavedSchedule, ImportedGrade, DismissedAssignment,
        CourseNote, DayArchive, db,
        infer_task_difficulty, PRIORITY_COLORS, enrich_schedule_data,
        humanize_schedule, build_student_context,
        _ai_personalization_enabled, _summarize_grade_signals,
        _fetch_grades_for_personalization, build_scheduler_personalization,
        save_dismissed, invalidate_lms_cache_for_user,
        collect_lms_assignments_for_user,
    )
    from ai_provider import ai_available as _ai_available, chat as provider_chat

    today_str = datetime.now().strftime("%Y-%m-%d")

    if name == "list_tasks":
        manual = ManualTask.query.filter_by(user_id=user_id).order_by(ManualTask.due_date.asc()).all()
        try:
            lms = collect_lms_assignments_for_user(user_id) or []
        except Exception:
            lms = []
        items = []
        for t in manual:
            items.append({
                "id": t.id, "source": "manual",
                "title": t.title, "course": t.course or "Personal",
                "due_date": t.done and "" or (t.due_date or ""),
                "priority": t.priority or "Medium",
                "est_minutes": t.estimated_time or 60,
                "done": bool(t.done),
                "overdue": bool(t.due_date and t.due_date < today_str and not t.done),
            })
        for a in lms:
            items.append({
                "id": None, "source": a.get("source", "lms"),
                "title": a.get("title", ""),
                "course": a.get("course", ""),
                "due_date": a.get("due_date", ""),
                "priority": a.get("priority", "Medium"),
                "est_minutes": a.get("estimated_time", 60),
                "done": False,
                "overdue": bool(a.get("due_date") and a["due_date"] < today_str),
            })
        filt = (args.get("filter") or "").lower()
        course_q = (args.get("course") or "").lower().strip()
        if filt == "overdue":
            items = [i for i in items if i["overdue"]]
        elif filt == "today":
            items = [i for i in items if i["due_date"] == today_str]
        elif filt == "upcoming":
            items = [i for i in items if i["due_date"] and i["due_date"] >= today_str and not i["done"]]
        elif filt == "done":
            items = [i for i in items if i["done"]]
        if course_q:
            items = [i for i in items if course_q in (i["course"] or "").lower()]
        return {"tasks": items, "count": len(items)}

    if name == "search_tasks":
        q = (args.get("query") or "").lower().strip()
        if not q:
            return {"error": "query is required"}
        all_tasks = _execute_tool("list_tasks", {}, user_id)["tasks"]
        hits = []
        for t in all_tasks:
            blob = (t["title"] + " " + t["course"]).lower()
            if q in blob:
                hits.append(t)
        return {"tasks": hits, "count": len(hits), "query": q}

    if name == "list_courses":
        tasks = ManualTask.query.filter_by(user_id=user_id).all()
        courses = sorted({(t.course or "Personal") for t in tasks if t.course})
        try:
            grades = ImportedGrade.query.filter_by(user_id=user_id).all()
            courses = sorted(set(courses + [g.course for g in grades if g.course]))
        except Exception:
            pass
        return {"courses": courses, "count": len(courses)}

    if name == "get_grades":
        try:
            grades = ImportedGrade.query.filter_by(user_id=user_id).all()
            return {"grades": [{
                "course": g.course,
                "letter_grade": getattr(g, "letter_grade", None) or "",
                "percentage": float(g.percentage) if g.percentage else None,
            } for g in grades]}
        except Exception:
            return {"grades": [], "note": "Could not load grades"}

    if name == "get_schedule":
        s = SavedSchedule.query.filter_by(user_id=user_id, is_active=True).order_by(
            SavedSchedule.created_at.desc()
        ).first()
        if not s:
            return {"status": "none", "message": "No active schedule. Use generate_schedule to make one."}
        data = json.loads(s.schedule_data)
        return {
            "name": s.name,
            "overview": data.get("overview", ""),
            "total_time": data.get("total_study_time", ""),
            "days": [
                {
                    "date": d.get("date", ""),
                    "day": d.get("day_name", ""),
                    "blocks": [
                        {"assignment": b.get("assignment"), "time_slot": b.get("time_slot"), "duration": b.get("duration_minutes")}
                        for b in (d.get("blocks") or []) if not b.get("is_break")
                    ],
                }
                for d in (data.get("schedule") or [])[:7]
            ],
        }

    if name == "get_today_plan":
        try:
            from command_center_glue import _build_service
            from intelliplan.api.serialize import today_to_dict
            payload = _build_service().build(user_id)
            data = today_to_dict(payload)
            return {
                "headline": data.get("briefing", {}).get("headline", ""),
                "body": data.get("briefing", {}).get("body", ""),
                "health_score": data.get("health", {}).get("score"),
                "health_tier": data.get("health", {}).get("tier"),
                "plan": [{
                    "title": t.get("title"),
                    "course": t.get("course"),
                    "due_date": t.get("due_date"),
                    "priority_score": t.get("priority", {}).get("score"),
                    "priority_tier": t.get("priority", {}).get("tier"),
                    "why_now": t.get("why_now"),
                    "est_minutes": t.get("est_minutes"),
                } for t in (data.get("plan") or [])[:8]],
            }
        except Exception as e:
            return {"error": f"Couldn't load today's plan: {e}"}

    if name == "get_workload":
        try:
            from command_center_glue import _build_service
            from intelliplan.api.serialize import today_to_dict
            data = today_to_dict(_build_service().build(user_id))
            f = data.get("forecast", {})
            return {
                "heaviest_day": f.get("heaviest_day"),
                "summary": f.get("summary"),
                "days": [{
                    "date": d.get("date"),
                    "committed_min": d.get("committed_min"),
                    "available_min": d.get("available_min"),
                    "stress": d.get("stress"),
                } for d in (f.get("days") or [])],
            }
        except Exception as e:
            return {"error": f"Couldn't load workload: {e}"}

    if name == "get_briefing":
        try:
            from command_center_glue import _build_service
            from intelliplan.api.serialize import today_to_dict
            data = today_to_dict(_build_service().build(user_id))
            b = data.get("briefing", {})
            return {"headline": b.get("headline"), "body": b.get("body"), "tone": b.get("tone")}
        except Exception as e:
            return {"error": str(e)}

    if name == "explain_priority":
        title = (args.get("title") or "").strip().lower()
        if not title:
            return {"error": "title is required"}
        try:
            from command_center_glue import _build_service
            from intelliplan.api.serialize import today_to_dict
            data = today_to_dict(_build_service().build(user_id))
            for t in data.get("plan") or []:
                if title in (t.get("title") or "").lower():
                    pr = t.get("priority", {})
                    return {
                        "title": t.get("title"),
                        "priority_score": pr.get("score"),
                        "priority_tier": pr.get("tier"),
                        "why_now": t.get("why_now"),
                        "rationale": pr.get("rationale", []),
                    }
            return {"error": "task not in today's plan"}
        except Exception as e:
            return {"error": str(e)}

    if name == "create_task":
        title = (args.get("title") or "").strip()
        if not title:
            return {"error": "Title is required."}
        due = _parse_due_phrase(args.get("due_date")) or args.get("due_date") or ""
        try:
            est = int(args.get("estimated_time") or 60)
        except (TypeError, ValueError):
            est = 60
        task = ManualTask(
            user_id=user_id, title=title,
            due_date=due[:16] if due else "",
            priority=(args.get("priority") or "Medium")[:16],
            course=(args.get("course") or "Personal")[:128],
            estimated_time=est, notes=(args.get("notes") or "")[:1000],
        )
        db.session.add(task)
        db.session.commit()
        try: invalidate_lms_cache_for_user(user_id)
        except Exception: pass
        return {"status": "ok", "id": task.id, "title": task.title,
                "message": f"Created '{task.title}'" + (f" due {due}" if due else "")}

    if name == "update_task":
        task = _find_manual_task(args, user_id)
        if not task:
            return {"error": "Task not found (only manual tasks are editable)."}
        changed = []
        if args.get("new_title"):
            task.title = args["new_title"]; changed.append("title")
        if args.get("due_date") is not None:
            due = _parse_due_phrase(args["due_date"]) or args["due_date"]
            task.due_date = due[:16] if due else ""
            changed.append("due_date")
        if args.get("priority"):
            task.priority = args["priority"][:16]; changed.append("priority")
        if args.get("course"):
            task.course = args["course"][:128]; changed.append("course")
        if args.get("estimated_time") is not None:
            try: task.estimated_time = int(args["estimated_time"]); changed.append("est")
            except (TypeError, ValueError): pass
        if args.get("notes") is not None:
            task.notes = str(args["notes"])[:1000]; changed.append("notes")
        db.session.commit()
        try: invalidate_lms_cache_for_user(user_id)
        except Exception: pass
        return {"status": "ok", "id": task.id, "title": task.title,
                "changed": changed,
                "message": f"Updated '{task.title}' ({', '.join(changed) or 'no changes'})"}

    if name == "complete_task":
        task = _find_manual_task(args, user_id)
        if task:
            if task.done:
                return {"status": "already_done", "message": f"'{task.title}' is already done."}
            task.done = True
            db.session.commit()
            try: invalidate_lms_cache_for_user(user_id)
            except Exception: pass
            return {"status": "ok", "message": f"Marked '{task.title}' as done."}
        # Manual not found — try LMS dismiss by title (no DB id for LMS tasks)
        title = (args.get("title") or "").strip()
        if not title:
            return {"error": "Task not found."}
        try:
            save_dismissed(title, {})
            invalidate_lms_cache_for_user(user_id)
        except Exception as e:
            return {"error": f"Could not dismiss: {e}"}
        return {"status": "ok", "message": f"Marked '{title}' done (LMS)."}

    if name == "delete_task":
        task = _find_manual_task(args, user_id)
        if not task:
            return {"error": "Task not found."}
        title = task.title
        db.session.delete(task)
        db.session.commit()
        try: invalidate_lms_cache_for_user(user_id)
        except Exception: pass
        return {"status": "ok", "message": f"Deleted '{title}'."}

    if name == "generate_schedule":
        tasks = ManualTask.query.filter_by(user_id=user_id, done=False).all()
        if not tasks:
            return {"error": "No pending tasks to schedule."}
        hours = int(args.get("hours_per_day", 2))
        pref = args.get("preferred_time", "evening")
        assignments = []
        for t in tasks:
            assignments.append({
                "title": t.title, "course": t.course or "Personal",
                "due_date": t.due_date or "", "priority": t.priority or "Medium",
                "estimated_time": t.estimated_time or 60,
                "difficulty": infer_task_difficulty(None, t.priority or "Medium", t.due_date),
                "color": PRIORITY_COLORS.get(t.priority or "Medium", "#60a5fa"),
            })
        overdue = [a for a in assignments if a["due_date"] and a["due_date"] < today_str]
        upcoming = [a for a in assignments if not a["due_date"] or a["due_date"] >= today_str]
        upcoming.sort(key=lambda x: x.get("due_date") or "9999")
        overdue_text = "\nOVERDUE:\n" + "\n".join(f"  - {a['title']} ({a['course']}) was due {a['due_date']}" for a in overdue) if overdue else ""
        upcoming_text = "\nUPCOMING:\n" + "\n".join(f"  - {a['title']} ({a['course']}) due {a['due_date']}, priority {a['priority']}, ~{a['estimated_time']}min" for a in upcoming) if upcoming else ""

        grades_summary = None
        if _ai_personalization_enabled():
            try:
                grades_summary = _summarize_grade_signals(_fetch_grades_for_personalization())
            except Exception:
                pass
        profile_ctx = build_student_context(user_id=user_id, grades_summary=grades_summary, depth="full")
        # Same personalization the /generate-schedule endpoint uses, so a plan
        # made in chat lands in the student's real hours just like one made on
        # the Scheduler page.
        import scheduler_engine
        dna, availability, commitments = build_scheduler_personalization(user_id=user_id)
        week_ctx = scheduler_engine.describe_week(availability, commitments)
        habits_ctx = dna.to_prompt() if _ai_personalization_enabled() else ""

        prompt = f"""Today is {today_str}. Schedule ALL items for this student.
{profile_ctx}{week_ctx}{habits_ctx}{overdue_text}{upcoming_text}
Availability: {hours} hours/day, prefers {pref}.
If REAL WEEK is present, schedule no work on days marked "no study time available".
Write "notes" as the actual next physical action for that assignment, and make
"daily_tip" specific to that day — never filler that would fit any other day.
Return ONLY valid JSON:
{{"schedule": [{{"date": "YYYY-MM-DD", "day_name": "Monday", "total_hours": {hours}, "blocks": [{{"assignment": "title", "course": "name", "duration_minutes": 45, "time_slot": "7:00 PM", "notes": "focus", "is_break": false}}], "daily_tip": "tip"}}], "overview": "summary", "total_study_time": "X hours"}}"""

        if not _ai_available():
            return {"error": "AI temporarily unavailable."}
        try:
            raw = provider_chat([{"role": "user", "content": prompt}], tier="standard",
                temperature=0.3, max_tokens=8000, response_format={"type": "json_object"})
            raw = re.sub(r"```json\n?", "", raw)
            raw = re.sub(r"```\n?", "", raw).strip()
            schedule = json.loads(raw)
            schedule = enrich_schedule_data(schedule, assignments, pref, hours)
            try:
                schedule = humanize_schedule(schedule, pref, hours,
                                             availability=availability,
                                             commitments=commitments, dna=dna)
            except Exception: pass
            sched_name = f"Plani Schedule {datetime.now().strftime('%b %d')}"
            SavedSchedule.query.filter_by(user_id=user_id).update({"is_active": False})
            sched = SavedSchedule(user_id=user_id,
                name=sched_name,
                schedule_data=json.dumps(schedule), is_active=True)
            db.session.add(sched)
            # Deterministically archive to Memories in the same transaction —
            # the user asked once, so it must land everywhere without relying
            # on the model remembering a follow-up save_note call.
            try:
                db.session.add(DayArchive(
                    user_id=user_id,
                    archive_date=datetime.now().date(),
                    item_type="schedule",
                    title=sched_name,
                    payload=json.dumps(schedule),
                ))
            except Exception as _arch_e:
                logger.warning("plani schedule archive skipped: %s", _arch_e)
            db.session.commit()
            return {"status": "ok",
                "message": f"Schedule generated — {schedule.get('total_study_time', '')}. "
                           "It is saved on the Scheduler page, the Dashboard, and Memories.",
                "days": len(schedule.get("schedule", [])),
                "overview": schedule.get("overview", "")}
        except Exception as e:
            logger.error("schedule gen failed: %s", e)
            return {"error": "Schedule generation failed. Try again."}

    if name == "save_note":
        course = (args.get("course") or "General").strip()
        title = (args.get("title") or "").strip()
        content = (args.get("content") or "").strip()
        if not title or not content:
            return {"error": "title and content are required."}
        note = CourseNote(
            user_id=user_id,
            course_name=course,
            note_date=today_str,
            title=title,
            text_content=content,
        )
        db.session.add(note)
        db.session.commit()
        return {"status": "ok", "title": title, "course": course,
                "message": f"Saved note '{title}' to memories."}

    if name == "navigate_to":
        section = (args.get("section") or "").lower().strip()
        url = SECTION_URLS.get(section)
        if not url:
            return {"error": f"Unknown section '{section}'."}
        return {"status": "ok", "navigate": url, "section": section,
                "message": f"Taking you to {section}…"}

    return {"error": f"Unknown tool: {name}"}


# ── Routing ─────────────────────────────────────────────────────────


def _parse_tool_calls(text: str) -> list[dict]:
    pattern = r"```tool_call\s*\n?(.*?)\n?```"
    matches = re.findall(pattern, text, re.DOTALL)
    calls = []
    for m in matches:
        try:
            parsed = json.loads(m.strip())
            if isinstance(parsed, dict) and "name" in parsed:
                calls.append(parsed)
        except json.JSONDecodeError:
            continue
    return calls


def _tool_list_prompt() -> str:
    lines = ["AVAILABLE TOOLS:"]
    for t in AGENT_TOOLS:
        params = t.get("parameters", {})
        if params:
            param_str = ", ".join(
                f"{k}:{v.get('type','string')}{'*' if v.get('required') else ''}"
                for k, v in params.items())
        else:
            param_str = "none"
        lines.append(f"- {t['name']}({param_str}) — {t['description']}")
    return "\n".join(lines)


def _humanize_action(tool: str, args: dict, result: dict) -> str | None:
    """Render a one-line human description of what Plani did, surfaced as a chip."""
    if not isinstance(result, dict) or result.get("error"):
        return None
    status = result.get("status")
    if tool == "create_task":
        return f"✓ Created task: {result.get('title', '')}"
    if tool == "update_task":
        title = result.get("title", "")
        changed = ", ".join(result.get("changed", []) or [])
        return f"✓ Updated {title}" + (f" ({changed})" if changed else "")
    if tool == "complete_task":
        if status == "already_done":
            return None
        return result.get("message", "✓ Marked done")
    if tool == "delete_task":
        return result.get("message", "✓ Deleted task")
    if tool == "generate_schedule":
        return f"✓ Generated schedule — {result.get('days', '?')} days"
    if tool == "save_note":
        return f"✓ Saved to memories: {result.get('title', '')}"
    if tool == "navigate_to":
        return f"→ Opening {result.get('section', '')}"
    return None


MAX_TOOL_ROUNDS = 5


# ── Standing context ─────────────────────────────────────────────────
#
# Plani used to start every conversation knowing nothing. The system
# prompt described its tools and then handed it the raw message — so
# "what should I work on?" cost a round-trip to get_today_plan before it
# could say anything, and anything the model did not think to ask about
# (the user's grade level, their streak, what they saved to memories) it
# simply never knew. It read as a chatbot bolted onto the page rather
# than as the centre of the app.
#
# This assembles what the app already knows about the user into the
# system prompt, once, before the first token. Tools remain for depth and
# for writes; this is the standing picture.
#
# Three rules hold it together:
#   1. Every section is independently guarded. This runs on the critical
#      path of every message, and a missing pet row must not cost the
#      user their assistant.
#   2. It is budgeted. Everything is truncated to a line or two — the
#      point is to know *that* something exists so the model can ask the
#      right follow-up tool for detail, not to inline the database.
#   3. It is honest about absence. A section with nothing in it is
#      omitted rather than rendered empty, so the model does not read a
#      blank as a zero.

_CTX_MAX_CHARS = 6000


def _ctx_section(title: str, lines: list[str]) -> str:
    lines = [l for l in lines if l]
    if not lines:
        return ""
    return f"\n[{title}]\n" + "\n".join(f"  {l}" for l in lines)


def _safe(fn, default=None):
    """Run a context probe; never let it break the conversation."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - context is best-effort by design
        logger.debug("Plani context probe failed: %s", e)
        return default


def build_agent_context(user_id: int) -> str:
    """Everything the app knows about this user, as a prompt block."""
    from App import (
        ManualTask, SavedSchedule, CourseNote, UserStreak, PlaniPet,
        StudyPoints, StudySession, UserIdentity, db,
        build_student_context, _ai_personalization_enabled,
        _summarize_grade_signals, _fetch_grades_for_personalization,
        collect_lms_assignments_for_user,
    )

    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    parts: list[str] = []

    # ── Who they are ────────────────────────────────────────────────
    def _identity():
        grades_summary = None
        if _ai_personalization_enabled():
            grades_summary = _safe(
                lambda: _summarize_grade_signals(_fetch_grades_for_personalization())
            )
        block = build_student_context(
            user_id=user_id, grades_summary=grades_summary, depth="full"
        )
        # build_student_context returns its own delimited block; strip the
        # delimiters so it nests inside this one instead of opening a
        # second, competing "context" frame in the prompt.
        return (block or "").replace("=== STUDENT CONTEXT (use to personalize, do NOT echo verbatim) ===", "") \
                            .replace("=== END STUDENT CONTEXT ===", "").strip()

    ident_block = _safe(_identity, "") or ""
    if ident_block:
        parts.append("\n[WHO THEY ARE]\n" + "\n".join(
            "  " + l.strip() for l in ident_block.splitlines() if l.strip()
        ))

    def _profile_extras():
        ui = db.session.get(UserIdentity, user_id) or \
            UserIdentity.query.filter_by(user_id=user_id).first()
        if not ui:
            return []
        out = []
        if not ui.completed:
            out.append("Onboarding questionnaire is not finished — some "
                       "personalisation is missing. Offer to fill the gaps.")
        cls = _safe(lambda: json.loads(ui.class_schedule or "[]"), []) or []
        if cls:
            out.append(f"Has a class schedule on file with {len(cls)} slots.")
        return out

    parts.append(_ctx_section("PROFILE", _safe(_profile_extras, []) or []))

    # ── Today: plan, health, briefing, workload ─────────────────────
    # One service build, reused — this is the expensive probe, so it must
    # not be run once per section.
    def _today():
        from command_center_glue import _build_service
        from intelliplan.api.serialize import today_to_dict
        return today_to_dict(_build_service().build(user_id))

    data = _safe(_today, {}) or {}

    if data:
        brief = data.get("briefing") or {}
        health = data.get("health") or {}
        plan = data.get("plan") or []
        forecast = data.get("forecast") or {}

        lines = []
        if brief.get("headline"):
            lines.append(f"Briefing: {brief['headline']} — {brief.get('body', '')}".strip(" —"))
        if health.get("score") is not None:
            lines.append(
                f"Academic health: {health['score']}/100 ({health.get('tier', '')}), "
                f"{health.get('delta_vs_yesterday', 0):+d} vs yesterday"
            )
        for c in (health.get("components") or [])[:5]:
            lines.append(f"  · {c.get('key')}: {c.get('reason')} ({c.get('impact'):+})")
        parts.append(_ctx_section("TODAY", lines))

        plan_lines = []
        for t in plan[:6]:
            pr = t.get("priority") or {}
            plan_lines.append(
                f"{t.get('title')} ({t.get('course') or 'no course'}) "
                f"due {t.get('due_date') or '—'}, {t.get('est_minutes') or '?'}min, "
                f"priority {pr.get('tier') or '?'} — {t.get('why_now') or ''}".strip()
            )
        if len(plan) > 6:
            plan_lines.append(f"…and {len(plan) - 6} more. Call get_today_plan for the full list.")
        parts.append(_ctx_section("TODAY'S PRIORITISED PLAN", plan_lines))

        f_lines = []
        if forecast.get("summary"):
            f_lines.append(str(forecast["summary"]))
        if forecast.get("heaviest_day"):
            f_lines.append(f"Heaviest day ahead: {forecast['heaviest_day']}")
        for d in (forecast.get("days") or [])[:7]:
            f_lines.append(
                f"{d.get('date')}: {d.get('committed_min', 0)}min committed of "
                f"{d.get('available_min', 0)}min available (stress {d.get('stress')})"
            )
        parts.append(_ctx_section("7-DAY WORKLOAD", f_lines))

    # ── Work on their plate ─────────────────────────────────────────
    def _tasks():
        manual = ManualTask.query.filter_by(user_id=user_id, done=False).all()
        lms = _safe(lambda: collect_lms_assignments_for_user(user_id), []) or []
        overdue = [t for t in manual if t.due_date and t.due_date < today_str]
        overdue += [a for a in lms if a.get("due_date") and a["due_date"] < today_str]
        due_today = [t for t in manual if t.due_date == today_str]
        due_today += [a for a in lms if a.get("due_date") == today_str]
        out = [
            f"{len(manual)} open manual tasks, {len(lms)} synced from connected LMS accounts.",
            f"{len(overdue)} overdue, {len(due_today)} due today.",
        ]
        if overdue:
            titles = [getattr(t, 'title', None) or t.get('title', '') for t in overdue[:4]]
            out.append("Overdue: " + "; ".join(x for x in titles if x))
        return out

    parts.append(_ctx_section("WORKLOAD", _safe(_tasks, []) or []))

    # ── Courses and grades ──────────────────────────────────────────
    def _courses():
        summary = _safe(lambda: _summarize_grade_signals(_fetch_grades_for_personalization()))
        if not summary:
            return []
        out = []
        cg = summary.get("course_grades") or []
        if cg:
            out.append("Grades: " + ", ".join(
                f"{r['course']} {int(r['percent'])}%" if r.get("percent") is not None
                else f"{r['course']} (ungraded)"
                for r in cg[:10]
            ))
        if summary.get("weak"):
            out.append("Struggling in: " + ", ".join(summary["weak"][:4]))
        return out

    parts.append(_ctx_section("COURSES & GRADES", _safe(_courses, []) or []))

    # ── Their saved schedule ────────────────────────────────────────
    def _schedule():
        s = (SavedSchedule.query
             .filter_by(user_id=user_id, is_active=True)
             .order_by(SavedSchedule.created_at.desc()).first())
        if not s:
            return ["No active study schedule saved. generate_schedule creates one."]
        sd = _safe(lambda: json.loads(s.schedule_data), {}) or {}
        days = sd.get("schedule") or []
        blocks = sum(len([b for b in (d.get("blocks") or []) if not b.get("is_break")])
                     for d in days)
        return [
            f'Active schedule "{s.name}" covering {len(days)} days, {blocks} study blocks.',
            f"Overview: {sd.get('overview', '')}"[:300],
        ]

    parts.append(_ctx_section("STUDY SCHEDULE", _safe(_schedule, []) or []))

    # ── What they've told the app to remember ───────────────────────
    def _memories():
        notes = (CourseNote.query
                 .filter_by(user_id=user_id)
                 .order_by(CourseNote.id.desc()).limit(8).all())
        if not notes:
            return []
        out = ["Recent memories (call up detail if relevant):"]
        for n in notes:
            snippet = (n.summary_cache or n.text_content or "").strip().replace("\n", " ")
            out.append(f"  · [{n.note_date}] {n.course_name} — {n.title}: {snippet[:120]}")
        return out

    parts.append(_ctx_section("MEMORIES", _safe(_memories, []) or []))

    # ── Momentum: streak, pet, sparks, study history ────────────────
    def _momentum():
        out = []
        st = UserStreak.query.filter_by(user_id=user_id).first()
        if st:
            out.append(
                f"Task streak: {st.current_streak} days (best {st.longest_streak}), "
                f"{st.freezes_available} freezes left."
            )
        pet = PlaniPet.query.filter_by(user_id=user_id).first()
        if pet:
            out.append(f"Study pet: {getattr(pet, 'name', 'Pet')}, "
                       f"level {getattr(pet, 'level', 1)}.")
        pts = StudyPoints.query.filter_by(user_id=user_id).first()
        if pts:
            out.append(f"Sparks: {pts.spark_balance} balance, level {pts.level}, "
                       f"{pts.streak_count}-day study streak.")
        sessions = (StudySession.query
                    .filter_by(user_id=user_id, completed=True)
                    .order_by(StudySession.id.desc()).limit(10).all())
        if sessions:
            mins = sum((s.duration_seconds or 0) for s in sessions) // 60
            asked = sum((s.questions_total or 0) for s in sessions)
            right = sum((s.questions_correct or 0) for s in sessions)
            acc = f", {round(right / asked * 100)}% accuracy" if asked else ""
            out.append(f"Last {len(sessions)} study sessions: {mins} minutes{acc}.")
        return out

    parts.append(_ctx_section("MOMENTUM", _safe(_momentum, []) or []))

    body = "".join(p for p in parts if p).strip()
    if not body:
        return ""

    if len(body) > _CTX_MAX_CHARS:
        # Truncate on a line boundary — a half-line of data reads as a
        # fact, and a fact cut in half is a wrong fact.
        body = body[:_CTX_MAX_CHARS].rsplit("\n", 1)[0] + \
            "\n  …context truncated; use the read tools for anything not listed."

    return (
        "\n=== WHAT YOU ALREADY KNOW ABOUT THIS USER ===\n"
        "This is live data from their account, assembled just now. Treat it "
        "as true and current — you do NOT need to call a tool to re-read "
        "anything already stated here. Use it to answer immediately and "
        "specifically. Call the read tools only for detail this does not "
        "cover, and never repeat this block back verbatim.\n"
        f"{body}\n"
        "=== END ===\n"
    )


@plani_agent_bp.route("/api/plani/agent", methods=["POST"])
def plani_agent():
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"status": "error", "error": "auth_required",
                        "reply": "Please log in to use Plani."}), 401

    data = request.get_json(silent=True) or {}

    # Accept both contracts:
    #   { "messages": [...] }
    #   { "message": "text", "history": [...] }
    if isinstance(data.get("messages"), list) and data["messages"]:
        messages = data["messages"]
    else:
        history = data.get("history") or []
        msg = (data.get("message") or "").strip()
        if not msg and not history:
            return jsonify({"status": "error",
                "reply": "Send a message to start the conversation."}), 400
        messages = list(history)
        if msg:
            messages.append({"role": "user", "content": msg})

    if not messages:
        return jsonify({"status": "error",
            "reply": "No messages provided."}), 400

    if not ai_available():
        return jsonify({"status": "error",
            "reply": "AI is temporarily unavailable. Try again in a moment."}), 503

    system = AGENT_SYSTEM_PROMPT + "\n\n" + _tool_list_prompt() + \
        f"\n\nToday's date: {datetime.now().strftime('%A, %Y-%m-%d')}." + \
        (build_agent_context(user_id) or "")
    recent = messages[-12:]
    llm_messages = [{"role": "system", "content": system}] + recent
    actions: list[str] = []
    tool_log: list[dict] = []
    navigate_url: str | None = None
    refresh_ui = False

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            reply = ai_chat(llm_messages, tier="standard",
                temperature=0.3, max_tokens=1400).strip()
        except Exception as e:
            logger.error("Plani LLM call failed: %s", e)
            return jsonify({"status": "error",
                "reply": "I hit a snag talking to the AI. Try again in a moment.",
                "actions": actions, "tool_log": tool_log}), 502

        tool_calls = _parse_tool_calls(reply)
        if not tool_calls:
            clean = re.sub(r"```tool_call.*?```", "", reply, flags=re.DOTALL).strip()
            return jsonify({"status": "ok",
                "reply": clean or "Done.",
                "actions": actions,
                "navigate": navigate_url,
                "refresh": refresh_ui,
                "tool_log": tool_log})

        llm_messages.append({"role": "assistant", "content": reply})
        results_text = []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {}) or {}
            result = _execute_tool(name, args, user_id)
            tool_log.append({"tool": name, "args": args, "result": result})
            human = _humanize_action(name, args, result)
            if human:
                actions.append(human)
            # capture navigation directive
            if isinstance(result, dict) and result.get("navigate"):
                navigate_url = result["navigate"]
            # mutations should trigger UI refresh
            if name in ("create_task", "update_task", "complete_task",
                        "delete_task", "generate_schedule", "save_note"):
                refresh_ui = True
            results_text.append(f"[{name}] → {json.dumps(result, default=str)}")

        llm_messages.append({"role": "user",
            "content": "TOOL RESULTS:\n" + "\n".join(results_text) +
                       "\n\nNow give the user a brief natural-language reply."})

    # Fell through: too many tool rounds
    try:
        final = ai_chat(llm_messages, tier="standard",
            temperature=0.3, max_tokens=600).strip()
        final = re.sub(r"```tool_call.*?```", "", final, flags=re.DOTALL).strip()
    except Exception:
        final = "Done — I completed the actions above."
    return jsonify({"status": "ok", "reply": final or "Done.",
        "actions": actions, "navigate": navigate_url,
        "refresh": refresh_ui, "tool_log": tool_log})
