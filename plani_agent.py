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

from intelliplan.services.calendar_availability import CalendarUnavailable

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import current_user

from ai_provider import (AIQuotaExhausted, AIUnavailable, ai_available,
                         chat as ai_chat)
import ai_firewall
from ai_firewall import AIBlocked

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
        "description": "Generate a study schedule from pending tasks, save it as active, and add it to connected calendars.",
        "parameters": {
            "hours_per_day": {"type": "integer", "description": "Hours available per day (default 2)"},
            "preferred_time": {"type": "string", "description": "morning | afternoon | evening (default evening)"},
            "study_start_time": {"type": "string", "description": "Optional daily start, e.g. 16:30 or 4:30 PM. May be given alone"},
            "study_end_time": {"type": "string", "description": "Optional daily end, e.g. 21:00. May be omitted"},
        },
    },
    {
        "name": "adjust_schedule",
        "description": "Repair the saved study plan when available time changes. Keeps assignment deadlines unchanged. Saves unless preview is true.",
        "parameters": {
            "action": {"type": "string", "required": True,
                       "description": "skip_day | limit_day | add_time | catch_up"},
            "day": {"type": "string", "description": "Affected local date YYYY-MM-DD; defaults to today"},
            "minutes": {"type": "integer", "description": "Available minutes for limit_day; additional minutes for add_time"},
            "preview": {"type": "boolean", "description": "True to show changes without saving"},
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
                "description": "One of: dashboard | scheduler | tutor | grades | streak | pet | command-center | gradebook | settings | memories | courses",
            },
        },
    },
    {
        "name": "connect_account",
        "description": "Show the student a button that connects a school system or calendar. Use it when they ask about their assignments, grades or calendar and nothing is connected, or when they ask how to connect something.",
        "parameters": {
            "service": {
                "type": "string",
                "required": True,
                "description": "One of: canvas | studentvue | schoology | hac | google_calendar | outlook | other",
            },
        },
    },
    {
        "name": "list_tracked_courses",
        "description": "List the outside courses (Khan Academy, Coursera, edX, any link) the student is tracking, with minutes done this week, the weekly goal, and whether they are on pace.",
        "parameters": {},
    },
    {
        "name": "track_course",
        "description": "Start tracking an outside course. Needs the course link the student gave you. Never invent a link.",
        "parameters": {
            "url": {"type": "string", "required": True, "description": "The course link, exactly as the student gave it"},
            "title": {"type": "string", "description": "Short name for the course"},
            "weekly_goal_minutes": {"type": "integer", "description": "Minutes per week (default 120)"},
        },
    },
    {
        "name": "log_course_progress",
        "description": "Record time the student says they spent on a tracked course.",
        "parameters": {
            "course": {"type": "string", "required": True, "description": "Tracked course title (fuzzy match)"},
            "minutes": {"type": "integer", "required": True, "description": "Minutes spent"},
        },
    },
    {
        "name": "recommend_courses",
        "description": "Get search links on Khan Academy, Coursera, edX and MIT OpenCourseWare for a subject. Use these links; never write a course URL yourself.",
        "parameters": {
            "subject": {"type": "string", "required": True, "description": "What the student wants to learn"},
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
    "courses": "/my-courses",
}

#: Where each "connect" button goes. Fixed here so the model picks a key
#: and never writes a URL.
CONNECT_LINKS = {
    "canvas": ("Connect Canvas", "/oauth/canvas"),
    "studentvue": ("Connect StudentVue", "/login/studentvue"),
    "schoology": ("Connect Schoology", "/login/schoology"),
    "hac": ("Connect Home Access Center", "/login/hac"),
    "google_calendar": ("Connect Google Calendar", "/login/google"),
    "outlook": ("Connect Outlook", "/oauth/outlook"),
    "other": ("See every way to connect", "/connect"),
}


AGENT_SYSTEM_PROMPT = """You are Plani — IntelliPlan's agentic AI assistant. You sit at the center of the user's Command Center and act as their AI operating system for school.

PERSONALITY: Friendly, smart, concise. Talk like a sharp friend who's also their TA. Never corporate. 1-3 sentences unless the user wants depth.

WHAT YOU CAN DO: You have tools to read AND write the user's data. When the user asks you to do something, USE THE TOOLS. Don't explain what you would do — actually do it.

WHAT YOU ALREADY KNOW: Account context is supplied only when AI
personalization is enabled and school policy allows its data sources.
Use only the context actually supplied. Missing context does not mean the
account is empty. Tool permissions are checked independently; explain a
denial and never claim that a denied action succeeded. Do not invent
courses, assignments, numbers, or access to unavailable account data.

INTELLIGENT BEHAVIOR:
- If the user says "mark X done", call complete_task with title="X".
- Changing study time does not change an assignment's deadline. For "I can't
  study tonight", use adjust_schedule with skip_day. For "only 30 minutes",
  use limit_day. For "I'm behind", use catch_up. Only change due_date when
  the user explicitly says the assignment deadline has changed. If they
  ask to move one sitting, explain the supported day-level repair or link
  to /scheduler; never move its deadline as a substitute.
- adjust_schedule changes the saved study plan. It does not move existing
  external calendar events. Never claim those events were updated; if the
  user requested calendar changes, explain this boundary.
- For "What should I focus on", use supplied context or an allowed
  get_today_plan result. If account access is unavailable, ask the user
  to describe their tasks and offer general planning help.
- If they ask about assignments, grades or their calendar and nothing is
  connected, call connect_account for the system they name (or "other")
  so they get a button right here. Do not send them to Settings in words.
- Outside courses (Khan Academy, Coursera, edX, any link): track_course when
  they give you a link, log_course_progress when they say they worked on
  one, list_tracked_courses to check pace. For "what should I take",
  call recommend_courses and pass on its links unchanged. Time a student
  reports is self-reported; never call it verified.
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


_TOOL_CAPABILITIES = {
    **{name: ('scheduler',) for name in (
        'get_schedule', 'get_today_plan', 'get_workload', 'get_briefing',
        'explain_priority', 'generate_schedule', 'adjust_schedule')},
    **{name: ('study',) for name in (
        'list_tracked_courses', 'track_course', 'log_course_progress', 'recommend_courses')},
    'save_note': ('uploads',),
}
_ACCOUNT_READ_TOOLS = frozenset({
    'list_tasks', 'search_tasks', 'list_courses', 'get_grades', 'get_schedule',
    'get_today_plan', 'get_workload', 'get_briefing', 'explain_priority',
    'generate_schedule', 'adjust_schedule', 'list_tracked_courses', 'log_course_progress',
})


def _tool_user(user_id):
    """Internal tool arguments cannot select a different account."""
    from App import User, db
    if not current_user.is_authenticated or current_user.id != user_id:
        return None
    user = db.session.get(User, user_id)
    if user:
        db.session.refresh(user)
    return user


def _capabilities_allowed(user, *keys):
    from intelliplan.learning import policy
    try:
        policy.invalidate_cache()
        return bool(user) and all(policy.decision(user, key)[0] for key in keys)
    except Exception:
        logger.warning('Plani capability check unavailable', exc_info=True)
        return False


def _source_data_allowed(user):
    # Aggregated grades and Command Center payloads do not carry enough
    # provenance to safely separate restricted sources. Omit the aggregate.
    from intelliplan.learning import policy
    return _capabilities_allowed(user, *policy.SOURCE_CAPABILITIES)


def _permitted_lms_tasks(user_id):
    from App import collect_lms_assignments_for_user
    if not _source_data_allowed(_tool_user(user_id)):
        return []
    return collect_lms_assignments_for_user(user_id) or []


def _tool_denial(name, args, user_id):
    from App import _ai_personalization_enabled
    from intelliplan.learning import policy
    try:
        user = _tool_user(user_id)
    except Exception:
        return {'error': 'policy_unavailable'}
    if not user:
        return {'error': 'authentication_required', 'message': 'Sign in to your own account.'}
    try:
        policy.invalidate_cache()
        if not policy.decision(user, 'tutor')[0] or policy.effective(user)['assessment_protection']:
            return {'error': 'policy_restricted', 'feature': 'tutor'}
        keys = _TOOL_CAPABILITIES.get(name, ())
        if name == 'connect_account':
            service = str(args.get('service') or '').lower().strip().replace(' ', '_')
            keys = (service,) if service in policy.LMS_SOURCES else (
                ('calendar',) if service in {'google_calendar', 'outlook'} else ())
        if not _capabilities_allowed(user, *keys):
            return {'error': 'policy_restricted', 'feature': ','.join(keys)}
        if name in _ACCOUNT_READ_TOOLS and not _ai_personalization_enabled(user):
            return {'error': 'personalization_required',
                    'message': 'Enable AI personalization in Settings to let Plani read stored account data.'}
        if name in {'get_grades', 'get_schedule', 'get_today_plan', 'get_workload', 'get_briefing', 'explain_priority', 'adjust_schedule'} and not _source_data_allowed(user):
            return {'error': 'source_restricted', 'message': 'Your institution has restricted an account data source.'}
        if name == 'generate_schedule' and not policy.permitted_providers(user):
            return {'error': 'provider_restricted'}
    except Exception:
        logger.warning('Plani tool policy unavailable', exc_info=True)
        return {'error': 'policy_unavailable'}
    return None


def _adjust_saved_schedule(args: dict, user_id: int) -> dict[str, Any]:
    """Repair the owner's plan with the same engine as the Scheduler page."""
    from sqlalchemy import update
    from App import SavedSchedule, db, invalidate_schedule_cache
    import followthrough_glue as followthrough
    from intelliplan.api.adjust import _task_ids

    if not set(args) <= {'action', 'day', 'minutes', 'preview'}:
        return {'error': 'invalid_arguments', 'message': 'Use an action, day, minutes, and optional preview.'}
    if args.get('action') not in {'skip_day', 'limit_day', 'add_time', 'catch_up'}:
        return {'error': 'invalid_adjustment', 'message': 'Choose a day-level study-time adjustment.'}
    if 'preview' in args and not isinstance(args['preview'], bool):
        return {'error': 'invalid_arguments', 'message': 'Preview must be true or false.'}
    if 'day' in args:
        try:
            from datetime import date
            date.fromisoformat(args['day'])
        except (TypeError, ValueError):
            return {'error': 'invalid_arguments', 'message': 'Use a local date in YYYY-MM-DD format.'}
    if args['action'] in {'limit_day', 'add_time'}:
        minutes = args.get('minutes')
        maximum = 1440 if args['action'] == 'limit_day' else 960
        minimum = 0 if args['action'] == 'limit_day' else 1
        if type(minutes) is not int or not minimum <= minutes <= maximum:
            return {'error': 'invalid_arguments', 'message': f'Choose {minimum} to {maximum} whole minutes.'}
    if not followthrough.followthrough_enabled(user_id):
        return {'error': 'feature_unavailable'}
    state = _processing_state(user_id)
    if state is None:
        return {'error': 'policy_unavailable'}
    row = followthrough._active_row(user_id, None)
    if row is None:
        return {'error': 'no_saved_plan', 'message': 'Make a study plan first.'}
    row_id, original_data, original_progress = row.id, row.schedule_data, row.progress_json
    try:
        old_data = json.loads(original_data)
        progress = json.loads(original_progress or '{}')
        if not isinstance(old_data, dict) or not old_data.get('schedule') or not isinstance(progress, dict):
            return {'error': 'no_saved_plan', 'message': 'Make a study plan first.'}
        now = followthrough._now()
        reality = followthrough._session_reality(_task_ids(old_data), user_id, None) or {}
        service = followthrough._build_service(user_id, None, args)
        result, data = service.adjust(old_data, progress, args, now=now,
                                      credit=reality.get('abandoned') or {},
                                      finished=tuple(reality.get('finished') or ()))
        data, new_progress = followthrough._finalize(data, old_data, progress, now.date())
        denial = _tool_denial('adjust_schedule', args, user_id)
        if denial:
            return denial
        if _processing_state(user_id) != state:
            return {'error': 'account_access_changed'}
        preview = args.get('preview', False)
        summary = result.summary()
        if not preview:
            followthrough._stage_plan_outcomes(user_id, old_data, progress, now.date())
            # A newer chat, drag, or replan must not be overwritten by this
            # request's earlier snapshot. The active row and both JSON fields
            # are part of the conditional write.
            changed = db.session.execute(update(SavedSchedule).where(
                SavedSchedule.id == row_id, SavedSchedule.user_id == user_id,
                SavedSchedule.is_active.is_(True),
                SavedSchedule.schedule_data == original_data,
                SavedSchedule.progress_json == original_progress,
            ).values(schedule_data=json.dumps(data), progress_json=json.dumps(new_progress))
                .execution_options(synchronize_session=False)).rowcount
            if changed != 1:
                db.session.rollback()
                return {'error': 'plan_changed', 'message': 'Your plan changed while I was adjusting it. Try again.'}
            db.session.commit()
            try:
                invalidate_schedule_cache(user_id=user_id, guest_id=None)
            except Exception:
                logger.warning('Chat plan repair cache invalidation failed', exc_info=True)
        return {'status': 'ok', 'preview': preview, 'saved': not preview,
                'schedule': data, **summary,
                'calendar_updated': False,
                'message': ('This is a preview; your saved plan is unchanged.' if preview else
                            'Your study plan is updated. Assignment deadlines and previously exported calendar events are unchanged.')}
    except CalendarUnavailable as exc:
        return {'error': 'calendar_unavailable', 'message': str(exc), 'retryable': True}
    except ValueError as exc:
        return {'error': 'invalid_adjustment', 'message': str(exc)}
    except Exception:
        db.session.rollback()
        logger.warning('Chat plan repair failed', exc_info=True)
        return {'error': 'adjustment_failed', 'message': 'I could not adjust the plan. Try again.'}


def _execute_tool(name: str, args: dict, user_id: int) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {'error': 'invalid_arguments'}
    denial = _tool_denial(name, args, user_id)
    if denial:
        return denial
    from App import (
        ManualTask, SavedSchedule, ImportedGrade, DismissedAssignment,
        CourseNote, DayArchive, db,
        infer_task_difficulty, PRIORITY_COLORS, build_scheduler_personalization,
        save_dismissed, invalidate_lms_cache_for_user,
    )

    today_str = datetime.now().strftime("%Y-%m-%d")

    if name == "list_tasks":
        manual = ManualTask.query.filter_by(user_id=user_id).order_by(ManualTask.due_date.asc()).all()
        try:
            lms = _permitted_lms_tasks(user_id)
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
        result = _execute_tool("list_tasks", {}, user_id)
        if result.get('error'):
            return result
        all_tasks = result['tasks']
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
            grades = ImportedGrade.query.filter_by(user_id=user_id).all() if _source_data_allowed(_tool_user(user_id)) else []
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
        if not _source_data_allowed(_tool_user(user_id)):
            return {'error': 'source_restricted'}
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

    if name == "adjust_schedule":
        return _adjust_saved_schedule(args, user_id)

    if name == "generate_schedule":
        generation_state = _processing_state(user_id)
        if generation_state is None:
            return {'error': 'policy_unavailable'}
        tasks = ManualTask.query.filter_by(user_id=user_id, done=False).all()
        from command_center_glue import _filter_stored_tasks
        tasks = _filter_stored_tasks(user_id, tasks)
        try:
            synced_tasks = _permitted_lms_tasks(user_id)
        except Exception:
            synced_tasks = []
        if not tasks and not synced_tasks:
            return {"error": "No pending tasks to schedule."}
        try:
            hours = float(args.get("hours_per_day", 2))
            if not 0 < hours <= 12:
                raise ValueError
        except (TypeError, ValueError):
            return {"error": "Choose study hours greater than zero and at most 12 per day."}
        pref = args.get("preferred_time", "evening")
        if pref not in {"morning", "afternoon", "evening"}:
            return {"error": "Choose morning, afternoon, or evening for your study time."}
        assignments = []
        for t in tasks:
            assignments.append({
                "id": f"manual:{t.id}",
                "title": t.title, "course": t.course or "Personal",
                "due_date": t.due_date or "", "priority": t.priority or "Medium",
                "estimated_time": t.estimated_time or 60,
                "difficulty": infer_task_difficulty(None, t.priority or "Medium", t.due_date),
                "color": PRIORITY_COLORS.get(t.priority or "Medium", "#60a5fa"),
            })
        for t in synced_tasks:
            source = t.get('source') or 'lms'
            source_id = t.get('id') or t.get('source_ref') or t.get('external_id')
            if not source_id:
                source_id = json.dumps([t.get('course') or '', t.get('title') or '',
                                        t.get('due_date') or ''], separators=(',', ':'))
            assignments.append({
                "id": f"{source}:{source_id}",
                "title": t.get("title") or "Untitled assignment", "course": t.get("course") or "School",
                "due_date": t.get("due_date") or "", "priority": t.get("priority") or "Medium",
                "due_at": t.get('due_at') or t.get('due_datetime'),
                "deadline_at": t.get('deadline_at'),
                "estimated_time": t.get("estimated_time") or 60,
                "difficulty": t.get("difficulty") or infer_task_difficulty(None, t.get("priority") or "Medium", t.get("due_date")),
                "color": t.get("color") or PRIORITY_COLORS.get(t.get("priority") or "Medium", "#60a5fa"),
            })
        # Same personalization the /generate-schedule endpoint uses, so a plan
        # made in chat lands in the student's real hours just like one made on
        # the Scheduler page.
        import scheduler_engine
        dna, availability, commitments = build_scheduler_personalization(user_id=user_id)
        study_start = _norm_clock(args.get("study_start_time"))
        study_end = _norm_clock(args.get("study_end_time"))
        if (args.get("study_start_time") and not study_start) or (args.get("study_end_time") and not study_end):
            return {"error": "I couldn't read that time. Try something like 4:30 PM or 16:30."}
        # "2 hours a day after 4:30 PM" gives a start and no end. That is the
        # Command Center's own suggested prompt, so it has to work: the
        # window runs long enough for the hours plus breaks, capped at 23:30.
        if study_start and not study_end:
            study_end = _add_minutes(study_start, int(round(hours * 60)) + 45, cap="23:30")
        if study_end and not study_start:
            study_start = _add_minutes(study_end, -(int(round(hours * 60)) + 45), cap="06:00")
        if study_start and study_end and study_end <= study_start:
            return {"error": "Your study end time must be after the start time."}
        if study_start:
            availability = {day: {"start": study_start, "end": study_end} for day in scheduler_engine.DAY_ABBR}
        try:
            denial = _tool_denial(name, args, user_id)
            if denial:
                return denial
            if _processing_state(user_id) != generation_state:
                return {'error': 'account_access_changed'}
            # Chat and the Scheduler page must allocate the same real work.
            # A model may interpret the request, but it cannot invent blocks.
            from App import _build_planner_schedule
            schedule = _build_planner_schedule(
                assignments, [], user_id, None, availability, commitments,
                pref, hours, dna=dna, record_version=False,
            )
            denial = _tool_denial(name, args, user_id)
            if denial:
                return denial
            if _processing_state(user_id) != generation_state:
                return {'error': 'account_access_changed'}
            if not schedule or not any(
                not block.get("is_break")
                for day in schedule.get("schedule", [])
                for block in day.get("blocks", [])
            ):
                return {"error": "No study work fits your available time. Your existing plan is unchanged.",
                        "planning_failed": True}
            if _processing_state(user_id) != generation_state:
                return {'error': 'account_access_changed'}
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
            exports = {}
            # Export after persistence: an unavailable third-party calendar
            # must never discard the plan the student just created.
            try:
                from App import get_google_token
                from google_calendar_helper import add_schedule_to_calendar
                google_token = get_google_token() if _capabilities_allowed(_tool_user(user_id), 'calendar') else None
                if google_token and _capabilities_allowed(_tool_user(user_id), 'calendar'):
                    ids, _new_token, skipped = add_schedule_to_calendar(google_token, schedule)
                    exports["google"] = {"created": len(ids), "skipped": skipped}
            except Exception as exc:
                logger.warning("Google calendar export from Plani failed: %s", exc)
            try:
                from App import _user_tz_name, get_outlook_token, outlook_calendar_helper
                outlook_token = get_outlook_token() if _capabilities_allowed(_tool_user(user_id), 'calendar') else None
                if outlook_token and _capabilities_allowed(_tool_user(user_id), 'calendar'):
                    # The student's own zone, so blocks land at the hour the
                    # plan says rather than at that hour in California. The
                    # export also skips blocks already there, so asking twice
                    # does not double the calendar.
                    result = outlook_calendar_helper.export_schedule(
                        outlook_token, schedule, tz_name=_user_tz_name(user_id))
                    exports["outlook"] = {"created": len(result["created"]),
                                          "skipped": result["skipped"]}
            except Exception as exc:
                logger.warning("Outlook calendar export from Plani failed: %s", exc)
            return {"status": "ok",
                "calendar_connected": bool(exports) or _has_calendar(),
                "message": f"Schedule generated — {schedule.get('total_study_time', '')}. "
                           "It is saved on the Scheduler page, the Dashboard, and Memories.",
                "days": len(schedule.get("schedule", [])),
                "overview": schedule.get("overview", ""),
                "schedule": schedule,
                "calendar_exports": exports,
                "calendar_export_restricted": not _capabilities_allowed(_tool_user(user_id), 'calendar')}
        except CalendarUnavailable as exc:
            return {'error': 'calendar_unavailable', 'message': str(exc), 'retryable': True}
        except AIBlocked as e:
            return {'error': e.reason, 'message': e.message}
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

    if name == "connect_account":
        service = (args.get("service") or "").lower().strip().replace(" ", "_")
        label, url = CONNECT_LINKS.get(service, CONNECT_LINKS["other"])
        return {"status": "ok", "link": {"label": label, "url": url},
                "message": f"Showed the student a '{label}' button. Tell them to press it."}

    if name in ("list_tracked_courses", "track_course", "log_course_progress"):
        import courses_glue
        from App import User

        user = User.query.get(user_id)
        if user is None:
            return {"error": "No account."}
        try:
            if name == "track_course":
                course = courses_glue.add_course(
                    user, args.get("url"), args.get("title"), args.get("weekly_goal_minutes"))
                return {"status": "ok", "title": course.title,
                        "weekly_goal_minutes": course.weekly_goal_minutes,
                        "link": {"label": "Open my courses", "url": "/my-courses"},
                        "message": f"Tracking '{course.title}'. A task for this week is on the plan."}
            if name == "log_course_progress":
                course = courses_glue.find_course(user_id, args.get("course"))
                if course is None:
                    return {"error": "No single tracked course matches that name.",
                            "courses": [c["title"] for c in courses_glue.snapshot(user)]}
                minutes = courses_glue.log_progress(user, course, args.get("minutes"))
                week = next((c["week"] for c in courses_glue.snapshot(user)
                             if c["id"] == course.id), {})
                return {"status": "ok", "title": course.title, "minutes": minutes, "week": week}
        except courses_glue.CourseError as exc:
            return {"error": str(exc)}
        return {"status": "ok", "courses": courses_glue.snapshot(user)}

    if name == "recommend_courses":
        import course_tracking

        results = course_tracking.recommendations(args.get("subject"))
        if not results:
            return {"error": "Say what subject to look for."}
        return {"status": "ok", "results": results,
                "note": "These are search pages. Give the student the links as they are."}

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
        line = f"✓ Generated schedule — {result.get('days', '?')} days"
        exports = result.get("calendar_exports") or {}
        synced = [f"{name.title()} ({v.get('created', 0)} events)"
                  for name, v in exports.items() if isinstance(v, dict)]
        if synced:
            line += " · added to " + ", ".join(synced)
        elif result.get('calendar_export_restricted'):
            line += ' · calendar export restricted by your institution'
        elif result.get("calendar_connected") is False:
            line += " · no calendar connected, so nothing was added to one"
        return line
    if tool == "save_note":
        return f"✓ Saved to memories: {result.get('title', '')}"
    if tool == "adjust_schedule" and result.get('status') == 'ok':
        return ('Previewed study plan changes' if result.get('preview')
                else 'Updated your saved study plan; assignment deadlines are unchanged')
    if tool == "navigate_to":
        return f"→ Opening {result.get('section', '')}"
    if tool == "track_course":
        return f"✓ Tracking {result.get('title', 'course')} · {result.get('weekly_goal_minutes', 0)} min a week"
    if tool == "log_course_progress":
        return f"✓ Logged {result.get('minutes', 0)} min on {result.get('title', 'course')}"
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


def _norm_clock(value) -> str:
    """'4:30 PM', '4pm', '16:30' -> '16:30'. Empty string when unreadable."""
    text = str(value or "").strip().lower().replace(".", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not m:
        return ""
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if not 1 <= h <= 12:
            return ""
        h = h % 12 + (12 if ampm == "pm" else 0)
    if h > 23 or mins > 59:
        return ""
    return f"{h:02d}:{mins:02d}"


def _add_minutes(clock: str, delta: int, cap: str) -> str:
    h, m = map(int, clock.split(":"))
    total = h * 60 + m + delta
    ch, cm = map(int, cap.split(":"))
    limit = ch * 60 + cm
    total = min(total, limit) if delta > 0 else max(total, limit)
    return f"{total // 60:02d}:{total % 60:02d}"


def _has_calendar() -> bool:
    try:
        if not _capabilities_allowed(_tool_user(_get_user_id()), 'calendar'):
            return False
        from App import get_google_token, get_outlook_token
        return bool(get_google_token() or get_outlook_token())
    except Exception:
        return False


def build_agent_context(user_id: int) -> str:
    """Only consented account context permitted by the current policy."""
    from App import (
        ManualTask, SavedSchedule, CourseNote, UserStreak, PlaniPet,
        StudyPoints, StudySession, UserIdentity, db,
        build_student_context, _ai_personalization_enabled,
        _summarize_grade_signals, _fetch_grades_for_personalization,
    )

    user = _tool_user(user_id)
    if not user or not _ai_personalization_enabled(user) or _tool_denial('list_tasks', {}, user_id):
        return ''

    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    parts: list[str] = []

    # ── Who they are ────────────────────────────────────────────────
    def _identity():
        grades_summary = None
        if _source_data_allowed(user):
            grades_summary = _safe(
                lambda: _summarize_grade_signals(_fetch_grades_for_personalization())
            )
        block = build_student_context(
            user_id=user_id, grades_summary=grades_summary,
            depth='full' if _capabilities_allowed(user, 'scheduler') else 'thin'
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
        ui = UserIdentity.query.filter_by(user_id=user_id).first()
        if not ui:
            return []
        out = []
        if not ui.completed:
            out.append("Onboarding questionnaire is not finished — some "
                       "personalisation is missing. Offer to fill the gaps.")
        cls = (_safe(lambda: json.loads(ui.class_schedule or "[]"), []) or []) if _capabilities_allowed(user, 'scheduler') else []
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

    data = (_safe(_today, {}) or {}) if _capabilities_allowed(user, 'scheduler') and _source_data_allowed(user) else {}

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
        lms = _safe(lambda: _permitted_lms_tasks(user_id), []) or []
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

    if _source_data_allowed(user):
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

    if _capabilities_allowed(user, 'scheduler') and _source_data_allowed(user):
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

    if _capabilities_allowed(user, 'uploads'):
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

    if _capabilities_allowed(user, 'study', 'review'):
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


def _processing_state(user_id):
    """Pin consent and school policy for prompts already assembled in this request."""
    from App import _ai_personalization_enabled
    from intelliplan.learning import policy
    try:
        user = _tool_user(user_id)
        if not user:
            return None
        policy.invalidate_cache()
        settings = policy.effective(user)
        if not policy.decision(user, 'tutor')[0] or settings['assessment_protection']:
            return None
        return (_ai_personalization_enabled(user),
                json.dumps(settings, sort_keys=True, default=lambda value: sorted(value)))
    except Exception:
        return None


@plani_agent_bp.route("/api/plani/agent", methods=["POST"])
def plani_agent():
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"status": "error", "error": "auth_required",
                        "reply": "Please log in to use Plani."}), 401

    expected_owner = request.headers.get('X-IntelliPlan-Chat-Owner')
    if expected_owner is not None and expected_owner != str(user_id):
        return jsonify(status='error', error='account_changed',
                       reply='Your account changed. Reload this page before continuing.'), 409

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(status='error', error='invalid_request'), 400

    # Accept both contracts:
    #   { "messages": [...] }
    #   { "message": "text", "history": [...] }
    if isinstance(data.get("messages"), list) and data["messages"]:
        messages = data["messages"]
    else:
        history = data.get("history") or []
        raw_message = data.get('message') or ''
        if not isinstance(raw_message, str) or not isinstance(history, list):
            return jsonify(status='error', error='invalid_messages'), 400
        msg = raw_message.strip()
        if not msg and not history:
            return jsonify({"status": "error",
                "reply": "Send a message to start the conversation."}), 400
        messages = list(history)
        if msg:
            messages.append({"role": "user", "content": msg})

    if (not isinstance(messages, list) or len(messages) > 50 or any(
        not isinstance(m, dict) or not isinstance(m.get('role'), str) or
        m['role'] not in {'user', 'assistant'} or
        not isinstance(m.get('content'), str) or len(m['content']) > 20000
        for m in messages)):
        return jsonify(status='error', error='invalid_messages'), 400
    messages = [{'role': m['role'], 'content': m['content']} for m in messages]
    if not messages:
        return jsonify({"status": "error",
            "reply": "No messages provided."}), 400

    if not ai_available():
        return jsonify({"status": "error",
            "reply": "AI is temporarily unavailable. Try again in a moment."}), 503

    # The agent loop can make MAX_TOOL_ROUNDS model calls for one message, so
    # it is the most expensive surface in the product per request and goes
    # through the same allowance as the tutor.
    try:
        from flask_login import current_user
        decision = ai_firewall.guard(
            current_user,
            prompts=[m.get("content", "") for m in messages if m.get("role") == "user"],
            want_output_tokens=1400,
            feature="agent",
        )
    except AIBlocked as e:
        payload = {"status": "error", "error": e.reason, "reply": e.message}
        if e.retry_after:
            payload["retry_after"] = e.retry_after
        return jsonify(payload), e.status

    from chatbot_api import _moderation_response
    safety_response = _moderation_response(messages)
    if safety_response is not None:
        return safety_response

    import assistant_name

    processing_state = _processing_state(user_id)
    if processing_state is None:
        return jsonify(status='error', error='policy_unavailable'), 403
    system = AGENT_SYSTEM_PROMPT + \
        assistant_name.prompt_line(assistant_name.for_user(user_id)) + \
        "\n\n" + _tool_list_prompt() + \
        f"\n\nToday's date: {datetime.now().strftime('%A, %Y-%m-%d')}." + \
        (build_agent_context(user_id) or "")
    # RAG: ground the answer in passages from the student's own notes that
    # match what they just asked. Empty string when nothing is relevant.
    last_user = next((m.get("content", "") for m in reversed(messages)
                      if m.get("role") == "user"), "")
    if last_user and processing_state[0] and _capabilities_allowed(_tool_user(user_id), 'uploads', 'study'):
        from intelliplan.retrieval import retrieve_context
        system += retrieve_context(user_id, str(last_user)[:1000])
    from plani_safety import context_window
    recent = context_window(messages)
    llm_messages = [{"role": "system", "content": system}] + recent
    actions: list[str] = []
    tool_log: list[dict] = []
    navigate_url: str | None = None
    links: list[dict] = []
    refresh_ui = False
    schedule_preview: dict | None = None

    for _round in range(MAX_TOOL_ROUNDS):
        if _processing_state(user_id) != processing_state:
            return jsonify(status='error', error='account_access_changed',
                           reply='Account access changed. Start a new request.',
                           actions=actions, tool_log=tool_log), 403
        try:
            if _round:
                decision = ai_firewall.guard(_tool_user(user_id),
                    prompts=[m.get('content', '') for m in messages if m.get('role') == 'user'],
                    want_output_tokens=1400, feature='agent')
            generation_window = context_window(llm_messages)
            reply = ai_chat([llm_messages[0]] + generation_window, tier="standard",
                temperature=0.3,
                max_tokens=min(1400, decision.max_output_tokens),
                plan=decision.plan).strip()
            ai_firewall.record_tokens(
                decision,
                sum(len(m.get("content", "")) for m in llm_messages),
                len(reply),
            )
        except AIBlocked as e:
            return jsonify(status='error', error=e.reason, reply=e.message,
                           actions=actions, tool_log=tool_log), e.status
        except AIQuotaExhausted as e:
            wait = getattr(e, "retry_after", None)
            when = f" Try again in about {wait} seconds." if wait else " Try again shortly."
            logger.info("Plani agent out of AI quota: %s", e)
            return jsonify({"status": "error", "error": "ai_quota_exhausted",
                "reply": "Plani has reached today's AI limit." + when,
                "retry_after": wait,
                "actions": actions, "tool_log": tool_log}), 503
        except AIUnavailable as e:
            logger.error("Plani agent has no AI backend: %s", e)
            return jsonify({"status": "error", "error": "ai_unavailable",
                "reply": "Plani's AI is not reachable right now. This is on our side, not yours.",
                "actions": actions, "tool_log": tool_log}), 503
        except Exception as e:
            logger.error("Plani LLM call failed: %s", e)
            return jsonify({"status": "error",
                "reply": "I hit a snag talking to the AI. Try again in a moment.",
                "actions": actions, "tool_log": tool_log}), 502

        if _processing_state(user_id) != processing_state:
            return jsonify(status='error', error='account_access_changed',
                           reply='Account access changed. Start a new request.',
                           actions=actions, tool_log=tool_log), 403
        safety_response = _moderation_response(generation_window, output=reply)
        if safety_response is not None:
            return safety_response
        tool_calls = _parse_tool_calls(reply)
        if not tool_calls:
            clean = re.sub(r"```tool_call.*?```", "", reply, flags=re.DOTALL).strip()
            return jsonify({"status": "ok",
                "reply": clean or "Done.",
                "actions": actions,
                "navigate": navigate_url,
                "links": links,
                "refresh": refresh_ui,
                "schedule": schedule_preview,
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
            # Buttons the tools asked for. URLs come from our own tables,
            # never from the model.
            if isinstance(result, dict) and isinstance(result.get("link"), dict):
                links.append(result["link"])
            if name == "recommend_courses" and isinstance(result, dict):
                links.extend({"label": r["name"], "url": r["url"]}
                             for r in result.get("results") or [])
            if name in {"generate_schedule", "adjust_schedule"} and isinstance(result, dict) and result.get("schedule"):
                schedule_preview = dict(result["schedule"], preview=bool(result.get('preview')))
            # mutations should trigger UI refresh
            if not result.get('error') and name in ("create_task", "update_task", "complete_task",
                        "delete_task", "generate_schedule", "adjust_schedule", "save_note",
                        "track_course", "log_course_progress") and not (
                            name == 'adjust_schedule' and result.get('preview')):
                refresh_ui = True
            results_text.append(f"[{name}] → {json.dumps(result, default=str)}")

        llm_messages.append({"role": "user",
            "content": "TOOL RESULTS:\n" + "\n".join(results_text) +
                       "\n\nNow give the user a brief natural-language reply."})

    # Fell through: too many tool rounds
    if _processing_state(user_id) != processing_state:
        return jsonify(status='error', error='account_access_changed',
                       reply='Account access changed. Start a new request.',
                       actions=actions, tool_log=tool_log), 403
    try:
        decision = ai_firewall.guard(_tool_user(user_id),
            prompts=[m.get('content', '') for m in messages if m.get('role') == 'user'],
            want_output_tokens=600, feature='agent')
        generation_window = context_window(llm_messages)
        final = ai_chat([llm_messages[0]] + generation_window, tier="standard",
            temperature=0.3, max_tokens=min(600, decision.max_output_tokens),
            plan=decision.plan).strip()
        ai_firewall.record_tokens(decision,
            sum(len(m.get('content', '')) for m in llm_messages), len(final))
        if _processing_state(user_id) != processing_state:
            return jsonify(status='error', error='account_access_changed',
                           reply='Account access changed. Start a new request.',
                           actions=actions, tool_log=tool_log), 403
        final = re.sub(r"```tool_call.*?```", "", final, flags=re.DOTALL).strip()
    except AIBlocked as e:
        return jsonify(status='error', error=e.reason, reply=e.message,
                       actions=actions, tool_log=tool_log), e.status
    except Exception:
        logger.warning('Plani final response unavailable', exc_info=True)
        return jsonify(status='error', error='ai_unavailable',
                       reply='The final reply could not be generated. Check the action results below.',
                       actions=actions, tool_log=tool_log, refresh=refresh_ui), 502
    safety_response = _moderation_response(generation_window, output=final)
    if safety_response is not None:
        return safety_response
    return jsonify({"status": "ok", "reply": final or "Done.",
        "actions": actions, "navigate": navigate_url, "links": links,
        "refresh": refresh_ui, "schedule": schedule_preview, "tool_log": tool_log})
