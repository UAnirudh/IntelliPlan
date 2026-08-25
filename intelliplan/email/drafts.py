""""You left this unfinished" — one weekly nudge about the student's own work.

IntelliPlan has no literal "draft" table. What it has is three ways to leave
something half-done, and from the student's side they all feel the same:

* a study session started and never ended — the timer is still notionally
  running, or sitting paused, hours later;
* a plan generated and saved but never touched, so the schedule exists and
  the work in it never started;
* a task added, given a due date, and still open after that date passed.

This module finds those, and sends at most one email a week listing them.

Three rules shape everything here, and each exists because the obvious
version of this feature is obnoxious:

**One email, not one per item.** A student with nine overdue tasks gets one
message listing them, not nine. The ledger key is per-week, not per-item.

**Quiet when there is nothing to say.** No drafts means no email. A weekly
"you have 0 unfinished items" is how a reminder becomes noise.

**Only on request.** The gate is ``email_reminders_opt_in`` — the switch the
student set themselves, defaulting to off. Unfinished work is a sensitive
thing to be chased about; a planner that nags by default is a planner people
delete.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .sender import send_lifecycle_email

logger = logging.getLogger(__name__)

SUBJECT = "You left a few things unfinished"
PREHEADER = "A short list of what's still open in IntelliPlan — pick one and close it out."

#: Seconds between sends in a batch, matching ``campaigns.BATCH_DELAY_SECONDS``.
BATCH_DELAY_SECONDS = 0.1

#: A session is only "left open" once it is implausible that it is still
#: being worked on. Six hours rather than one: a student who pauses over
#: dinner and comes back has not abandoned anything, and telling them they
#: did is the kind of wrong that gets an app muted.
SESSION_STALE_HOURS = 6

#: Beyond this a stale session stops being a useful reminder and starts
#: being archaeology.
SESSION_MAX_AGE_DAYS = 21

#: A saved plan is "never started" only after it has had a fair chance to be.
PLAN_UNTOUCHED_DAYS = 2
PLAN_MAX_AGE_DAYS = 30

#: How long a task stays worth mentioning after its due date passes.
TASK_OVERDUE_MAX_DAYS = 30

#: Never list more than this many items. The point is to make starting easy;
#: a wall of forty rows does the opposite.
MAX_ITEMS = 6


@dataclass(frozen=True)
class Draft:
    """One unfinished thing, in the shape the template wants."""

    #: "session" | "plan" | "task"
    kind: str
    title: str
    #: One line of context: the course, the due date, how long it has sat.
    detail: str
    #: Where to go to finish it, relative to the app root.
    path: str
    #: Whole days since it was left. Drives ordering.
    age_days: int


def weekly_key(now: datetime | None = None) -> str:
    """One ledger key per ISO week.

    The dedupe ledger's unique constraint is on (user_id, email_key), so a
    fixed key would send this exactly once per account, ever. Scoping the key
    to the ISO week turns that same constraint into the weekly cap: a cron
    that fires twice on Monday, or every hour all week, still produces one
    email.
    """
    now = now or datetime.utcnow()
    year, week, _ = now.isocalendar()
    return f"drafts_{year}_w{week:02d}"


# ── Finding the unfinished things ───────────────────────────────────


def _parse_due(raw) -> datetime | None:
    """ManualTask.due_date is a free-form string column.

    Anything that is not a leading ISO date reads as "no due date" rather
    than raising — the column has held blanks, and at least historically
    whatever the client sent.
    """
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many}"


def _ago(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    return f"{days // 7} weeks ago"


def stale_sessions(user_id: int, now: datetime) -> list[Draft]:
    """Study sessions still sitting in ``running`` or ``paused``."""
    from App import ActiveSession, db

    cutoff = now - timedelta(hours=SESSION_STALE_HOURS)
    floor = now - timedelta(days=SESSION_MAX_AGE_DAYS)
    try:
        rows = (
            db.session.query(ActiveSession)
            .filter(
                ActiveSession.user_id == user_id,
                ActiveSession.state.in_(("running", "paused")),
                ActiveSession.started_at <= cutoff,
                ActiveSession.started_at >= floor,
            )
            .order_by(ActiveSession.started_at.desc())
            .limit(MAX_ITEMS)
            .all()
        )
    except Exception:
        logger.exception("drafts: session lookup failed for user %s", user_id)
        return []

    drafts = []
    for row in rows:
        started = row.started_at or now
        age = max(0, (now - started).days)
        done = int(round((row.active_seconds or 0) / 60.0))
        planned = row.planned_minutes or 0
        if planned and done:
            detail = f"{done} of {planned} min done · started {_ago(age)}"
        elif done:
            detail = f"{_plural(done, 'minute', 'minutes')} in · started {_ago(age)}"
        else:
            detail = f"Started {_ago(age)}, never finished"
        if row.course:
            detail = f"{row.course} · {detail}"
        drafts.append(
            Draft(
                kind="session",
                title=row.title or "Study session",
                detail=detail,
                path="/active",
                age_days=age,
            )
        )
    return drafts


def untouched_plans(user_id: int, now: datetime) -> list[Draft]:
    """Saved schedules with nothing checked off.

    ``progress_json`` is written by the Interactive View as blocks get
    ticked. Null, empty, or an object with no truthy ``done`` flag all mean
    the same thing: the plan was made and never started.
    """
    import json

    from App import SavedSchedule, db

    newest = now - timedelta(days=PLAN_UNTOUCHED_DAYS)
    floor = now - timedelta(days=PLAN_MAX_AGE_DAYS)
    try:
        rows = (
            db.session.query(SavedSchedule)
            .filter(
                SavedSchedule.user_id == user_id,
                SavedSchedule.is_active.is_(True),
                SavedSchedule.created_at <= newest,
                SavedSchedule.created_at >= floor,
            )
            .order_by(SavedSchedule.created_at.desc())
            .limit(MAX_ITEMS)
            .all()
        )
    except Exception:
        logger.exception("drafts: plan lookup failed for user %s", user_id)
        return []

    drafts = []
    for row in rows:
        started = False
        raw = row.progress_json
        if raw:
            try:
                progress = json.loads(raw)
                if isinstance(progress, dict):
                    started = any(
                        isinstance(v, dict) and (v.get("done") or v.get("checked"))
                        for v in progress.values()
                    )
            except (ValueError, TypeError):
                # Unreadable progress is not evidence of no progress. Skip
                # the row rather than tell someone they never started a plan
                # they may have finished.
                continue
        if started:
            continue
        age = max(0, (now - (row.created_at or now)).days)
        drafts.append(
            Draft(
                kind="plan",
                title=row.name or "My Schedule",
                detail=f"Built {_ago(age)} · nothing checked off yet",
                path="/schedule",
                age_days=age,
            )
        )
    return drafts


def overdue_tasks(user_id: int, now: datetime) -> list[Draft]:
    """Open tasks whose due date has passed."""
    from App import ManualTask, db

    try:
        rows = (
            db.session.query(ManualTask)
            .filter(ManualTask.user_id == user_id, ManualTask.done.is_(False))
            .order_by(ManualTask.created_at.desc())
            .limit(200)
            .all()
        )
    except Exception:
        logger.exception("drafts: task lookup failed for user %s", user_id)
        return []

    drafts = []
    for row in rows:
        due = _parse_due(row.due_date)
        if due is None or due >= now:
            continue
        age = max(0, (now - due).days)
        if age > TASK_OVERDUE_MAX_DAYS:
            continue
        detail = f"Was due {_ago(age)}"
        if row.course and row.course != "Personal":
            detail = f"{row.course} · {detail}"
        drafts.append(
            Draft(kind="task", title=row.title, detail=detail, path="/tasks", age_days=age)
        )
    return drafts


def find_drafts(user_id: int, now: datetime | None = None) -> list[Draft]:
    """Everything this student has left unfinished, newest first, capped.

    Newest first rather than oldest: the thing abandoned yesterday is the
    one they still remember and are most likely to go back and finish. The
    six-week-old task at the bottom of the pile is not the hook.
    """
    now = now or datetime.utcnow()
    drafts = (
        stale_sessions(user_id, now)
        + untouched_plans(user_id, now)
        + overdue_tasks(user_id, now)
    )
    drafts.sort(key=lambda d: d.age_days)
    return drafts[:MAX_ITEMS]


def summarise(drafts: list[Draft]) -> str:
    """The one-line headline, e.g. "a session and 2 tasks"."""
    counts: dict[str, int] = {}
    for draft in drafts:
        counts[draft.kind] = counts.get(draft.kind, 0) + 1
    names = {
        "session": ("study session", "study sessions"),
        "plan": ("plan", "plans"),
        "task": ("task", "tasks"),
    }
    parts = [
        _plural(counts[kind], *names[kind])
        for kind in ("session", "plan", "task")
        if counts.get(kind)
    ]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


# ── The sweep ───────────────────────────────────────────────────────


def _blank_summary() -> dict:
    return {
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "reasons": {},
        "considered": 0,
        "no_drafts": 0,
    }


def sweep_drafts(
    now: datetime | None = None, limit: int = 500, dry_run: bool = False
) -> dict:
    """Mail everyone who opted into reminders and has something unfinished.

    The candidate query filters on ``email_reminders_opt_in`` so the
    expensive part — three lookups per student — only runs for people who
    could actually receive the result. The gate itself still runs inside
    ``send_lifecycle_email``; this is a narrowing, not a substitute for it.
    """
    from App import User

    now = now or datetime.utcnow()
    key = weekly_key(now)
    summary = _blank_summary()
    summary["email_key"] = key
    summary["dry_run"] = bool(dry_run)

    try:
        candidates = (
            User.query.filter(User.email_reminders_opt_in.is_(True))
            .limit(limit)
            .all()
        )
    except Exception:
        logger.exception("drafts sweep: could not load candidates")
        summary["error"] = "candidate query failed"
        return summary

    summary["considered"] = len(candidates)
    preview = []

    for user in candidates:
        try:
            drafts = find_drafts(user.id, now)
        except Exception:
            logger.exception("drafts sweep: lookup failed for user %s", user.id)
            summary["failed"] += 1
            continue

        if not drafts:
            # Nothing to say, so nothing is said.
            summary["no_drafts"] += 1
            continue

        if dry_run:
            preview.append(
                {"user_id": user.id, "email": user.email, "drafts": len(drafts)}
            )
            continue

        try:
            result = send_lifecycle_email(
                user=user,
                email_key=key,
                template_name="drafts",
                subject=SUBJECT,
                preheader=PREHEADER,
                gate="reminder",
                context_extra={
                    "drafts": [asdict(d) for d in drafts],
                    "draft_summary": summarise(drafts),
                    "draft_count": len(drafts),
                },
            )
        except Exception:
            logger.exception("drafts sweep: send failed for user %s", user.id)
            summary["failed"] += 1
            continue

        if result.sent:
            summary["sent"] += 1
        elif result.reason in {"provider_failed", "render_failed"}:
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
        summary["reasons"][result.reason] = summary["reasons"].get(result.reason, 0) + 1
        time.sleep(BATCH_DELAY_SECONDS)

    if dry_run:
        summary["preview"] = preview[:20]
        summary["would_send"] = len(preview)

    if len(candidates) >= limit:
        logger.warning("drafts sweep hit its limit of %s; remainder deferred", limit)
        summary["truncated"] = True

    logger.info("drafts sweep: %s", summary)
    return summary
