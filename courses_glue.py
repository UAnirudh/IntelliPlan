"""Courses a student takes outside school: tracked, scheduled, checked on.

Imported by App.py after model setup; every App import is lazy, matching
the other ``*_glue.py`` modules. The rules live in ``course_tracking.py``;
this module owns the routes and the two tables' reads and writes.

How a tracked course reaches the rest of the app
------------------------------------------------
Each active course has one task per week ("Khan Academy: Algebra 1 --
120 min this week", due Sunday). It is an ordinary ``ManualTask``, so the
planner schedules it, the Command Center ranks it, and deadline reminders
fire for it, with no course-specific code in any of them. Logging time
shrinks the task's estimate; meeting the goal completes it.

The task for a new week is made the first time anything reads the
student's courses that week, so there is no cron to keep alive.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, redirect, render_template, request
from flask_login import current_user

import course_tracking as rules
from time_utils import to_local, utcnow

logger = logging.getLogger(__name__)

courses_bp = Blueprint("courses", __name__)

TASK_SOURCE = "course"
#: Weeks of history read for the "weeks in a row" count.
HISTORY_WEEKS = 12


# ── small helpers ─────────────────────────────────────────────────────


def _today_for(user: Any) -> date:
    from App import _user_tz_name

    offset = int(getattr(user, "utc_offset_minutes", 0) or 0)
    return to_local(utcnow(), _user_tz_name(user.id), offset).date()


def _week_bounds_utc(user: Any, today: date) -> tuple[datetime, datetime]:
    """This local week as naive-UTC datetimes, for filtering check-ins."""
    from App import _user_tz_name
    from time_utils import to_utc

    offset = int(getattr(user, "utc_offset_minutes", 0) or 0)
    tz_name = _user_tz_name(user.id)
    start_local = datetime.combine(rules.week_start(today), datetime.min.time())
    return (
        to_utc(start_local, tz_name, offset),
        to_utc(start_local + timedelta(days=7), tz_name, offset),
    )


def _active_courses(user_id: int) -> list[Any]:
    from App import TrackedCourse

    return (
        TrackedCourse.query
        .filter_by(user_id=user_id, archived=False)
        .order_by(TrackedCourse.created_at.asc())
        .all()
    )


def _course_for(user_id: int, course_id: int) -> Any | None:
    """A course only if it belongs to this user."""
    from App import TrackedCourse

    return TrackedCourse.query.filter_by(id=course_id, user_id=user_id).first()


def _minutes_by_week(user: Any, course_ids: list[int], today: date) -> dict[int, dict[date, int]]:
    """course id -> {local week start -> minutes}, for the recent weeks."""
    from App import CourseCheckin, _user_tz_name

    if not course_ids:
        return {}
    offset = int(getattr(user, "utc_offset_minutes", 0) or 0)
    tz_name = _user_tz_name(user.id)
    since = utcnow() - timedelta(weeks=HISTORY_WEEKS + 1)
    rows = (
        CourseCheckin.query
        .filter(CourseCheckin.user_id == user.id,
                CourseCheckin.course_id.in_(course_ids),
                CourseCheckin.created_at >= since)
        .all()
    )
    out: dict[int, dict[date, int]] = {cid: {} for cid in course_ids}
    for row in rows:
        local_day = to_local(row.created_at, tz_name, offset).date()
        week = rules.week_start(local_day)
        bucket = out.setdefault(row.course_id, {})
        bucket[week] = bucket.get(week, 0) + int(row.minutes or 0)
    return out


def _task_key(course_id: int, week: date) -> str:
    return f"course:{course_id}:{week.isoformat()}"


def _sync_week_task(course: Any, minutes_this_week: int, today: date) -> None:
    """Create or update this week's task for one course. Caller commits."""
    from App import ManualTask, db

    week = rules.week_start(today)
    key = _task_key(course.id, week)
    goal = int(course.weekly_goal_minutes or rules.DEFAULT_WEEKLY_GOAL)
    remaining = max(0, goal - minutes_this_week)
    title = f"{course.title}: {goal} min this week"[:512]

    task = ManualTask.query.filter_by(
        user_id=course.user_id, import_source=TASK_SOURCE, external_id=key).first()
    if task is None:
        if remaining == 0:
            return  # goal already met; nothing to put on the plan
        db.session.add(ManualTask(
            user_id=course.user_id,
            title=title,
            due_date=rules.week_end(today).isoformat(),
            priority="Medium",
            course=course.title[:256],
            estimated_time=max(15, remaining),
            notes=course.url,
            import_source=TASK_SOURCE,
            external_id=key,
        ))
        return
    task.title = title
    task.course = course.title[:256]
    task.notes = course.url
    if remaining == 0:
        task.done = True
    elif not task.done:
        # A task the student ticked off by hand stays ticked off.
        task.estimated_time = max(15, remaining)


def _drop_open_week_task(course: Any, today: date) -> None:
    """Remove this week's unfinished task when a course stops being tracked."""
    from App import ManualTask, db

    key = _task_key(course.id, rules.week_start(today))
    task = ManualTask.query.filter_by(
        user_id=course.user_id, import_source=TASK_SOURCE, external_id=key, done=False).first()
    if task is not None:
        db.session.delete(task)


def _invalidate_plan(user_id: int) -> None:
    try:
        from intelliplan.api.command_center import invalidate_today

        invalidate_today(user_id)
    except Exception:
        pass


def _local_day(moment: datetime, user: Any) -> date:
    from App import _user_tz_name

    offset = int(getattr(user, "utc_offset_minutes", 0) or 0)
    return to_local(moment, _user_tz_name(user.id), offset).date()


def _course_dict(course: Any, weeks: dict[date, int], today: date, user: Any) -> dict[str, Any]:
    provider = rules.provider_for(course.provider)
    minutes = weeks.get(rules.week_start(today), 0)
    started = _local_day(course.created_at, user) if course.created_at else None
    status = rules.week_status(minutes, course.weekly_goal_minutes, today, started)
    return {
        "id": course.id,
        "title": course.title,
        "url": course.url,
        "provider": provider.key,
        "provider_name": provider.name,
        "weekly_goal_minutes": course.weekly_goal_minutes,
        "week": {
            "minutes": status.minutes,
            "remaining": status.remaining,
            "percent": status.percent,
            "days_left": status.days_left,
            "state": status.state,
            "message": status.message,
        },
        "weeks_in_a_row": rules.weeks_met_in_a_row(
            weeks.items(), course.weekly_goal_minutes, today),
        "total_minutes": int(course.total_minutes or 0),
        # Only ever set from the extension's reading of the course page.
        "verified_percent": course.verified_percent,
        "verified_at": course.verified_at.isoformat() + "Z" if course.verified_at else None,
    }


def snapshot(user: Any) -> list[dict[str, Any]]:
    """Every active course with this week's standing; rolls the week over.

    The one read path: the page, the API and the agent all come through
    here, so whichever is used first in a new week creates its tasks.
    """
    from App import db

    today = _today_for(user)
    courses = _active_courses(user.id)
    by_course = _minutes_by_week(user, [c.id for c in courses], today)
    out = []
    for course in courses:
        weeks = by_course.get(course.id, {})
        _sync_week_task(course, weeks.get(rules.week_start(today), 0), today)
        out.append(_course_dict(course, weeks, today, user))
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("course week rollover failed for user %s: %s", user.id, exc)
    return out


# ── operations shared by the routes and the agent ─────────────────────


class CourseError(ValueError):
    """A request the student can fix; the message is shown to them."""


def add_course(user: Any, url_raw: object, title_raw: object = "",
               goal_raw: object = None) -> Any:
    from App import TrackedCourse, db

    url = rules.clean_url(url_raw)
    if not url:
        raise CourseError("That does not look like a course link. Paste the full address.")
    active = _active_courses(user.id)
    if len(active) >= rules.MAX_ACTIVE_COURSES:
        raise CourseError(
            f"You are tracking {rules.MAX_ACTIVE_COURSES} courses, which is the limit. "
            "Finish or remove one first.")
    if any(rules.match_key(c.url) == rules.match_key(url) for c in active):
        raise CourseError("You are already tracking that course.")

    course = TrackedCourse(
        user_id=user.id,
        provider=rules.detect_provider(url).key,
        title=rules.clean_title(title_raw, url),
        url=url,
        weekly_goal_minutes=rules.clamp_goal(goal_raw),
    )
    db.session.add(course)
    db.session.flush()
    _sync_week_task(course, 0, _today_for(user))
    db.session.commit()
    _invalidate_plan(user.id)
    return course


def log_progress(user: Any, course: Any, minutes_raw: object, note_raw: object = "") -> int:
    """Record time the student says they spent. Returns the minutes stored."""
    from App import CourseCheckin, db

    try:
        minutes = int(minutes_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise CourseError("Enter how many minutes you spent.")
    if minutes <= 0:
        raise CourseError("Enter how many minutes you spent.")
    minutes = min(minutes, rules.MAX_CHECKIN_MINUTES)

    db.session.add(CourseCheckin(
        course_id=course.id,
        user_id=user.id,
        minutes=minutes,
        note=" ".join(str(note_raw or "").split())[:280],
        source=rules.SOURCE_SELF,
    ))
    course.total_minutes = int(course.total_minutes or 0) + minutes
    db.session.flush()

    today = _today_for(user)
    weeks = _minutes_by_week(user, [course.id], today).get(course.id, {})
    _sync_week_task(course, weeks.get(rules.week_start(today), 0), today)
    db.session.commit()
    _invalidate_plan(user.id)
    return minutes


def find_course(user_id: int, name: object) -> Any | None:
    """The active course whose title best matches ``name`` (for the agent)."""
    wanted = " ".join(str(name or "").lower().split())
    if not wanted:
        return None
    courses = _active_courses(user_id)
    for course in courses:
        if course.title.lower() == wanted:
            return course
    hits = [c for c in courses if wanted in c.title.lower()]
    return hits[0] if len(hits) == 1 else None


# ── pages and API ─────────────────────────────────────────────────────


def _require_user() -> Any | None:
    return current_user if current_user.is_authenticated else None


@courses_bp.route("/my-courses")
def courses_page():
    if not current_user.is_authenticated:
        return redirect("/login?next=/my-courses")
    return render_template("courses.html", active_page="courses")


@courses_bp.route("/api/courses", methods=["GET", "POST"])
def api_courses():
    user = _require_user()
    if user is None:
        return jsonify({"status": "error", "message": "Sign in first."}), 401

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        try:
            add_course(user, body.get("url"), body.get("title"), body.get("weekly_goal_minutes"))
        except CourseError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except Exception as exc:
            from App import db

            db.session.rollback()
            logger.warning("course add failed for user %s: %s", user.id, exc)
            return jsonify({"status": "error",
                            "message": "Could not save that course. Try again."}), 500

    return jsonify({
        "status": "ok",
        "courses": snapshot(user),
        "limit": rules.MAX_ACTIVE_COURSES,
    })


@courses_bp.route("/api/courses/<int:course_id>", methods=["PATCH", "DELETE"])
def api_course(course_id: int):
    from App import db

    user = _require_user()
    if user is None:
        return jsonify({"status": "error", "message": "Sign in first."}), 401
    course = _course_for(user.id, course_id)
    if course is None or course.archived:
        return jsonify({"status": "error", "message": "Course not found."}), 404

    today = _today_for(user)
    if request.method == "DELETE":
        # Archived, not deleted: the logged time is the student's record.
        course.archived = True
        _drop_open_week_task(course, today)
    else:
        body = request.get_json(silent=True) or {}
        if "title" in body:
            course.title = rules.clean_title(body.get("title"), course.url)
        if "weekly_goal_minutes" in body:
            course.weekly_goal_minutes = rules.clamp_goal(body.get("weekly_goal_minutes"))
        weeks = _minutes_by_week(user, [course.id], today).get(course.id, {})
        _sync_week_task(course, weeks.get(rules.week_start(today), 0), today)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("course update failed for user %s: %s", user.id, exc)
        return jsonify({"status": "error", "message": "Could not save that. Try again."}), 500
    _invalidate_plan(user.id)
    return jsonify({"status": "ok", "courses": snapshot(user)})


@courses_bp.route("/api/courses/<int:course_id>/checkin", methods=["POST"])
def api_course_checkin(course_id: int):
    user = _require_user()
    if user is None:
        return jsonify({"status": "error", "message": "Sign in first."}), 401
    course = _course_for(user.id, course_id)
    if course is None or course.archived:
        return jsonify({"status": "error", "message": "Course not found."}), 404
    body = request.get_json(silent=True) or {}
    try:
        log_progress(user, course, body.get("minutes"), body.get("note"))
    except CourseError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        from App import db

        db.session.rollback()
        logger.warning("course check-in failed for user %s: %s", user.id, exc)
        return jsonify({"status": "error", "message": "Could not save that. Try again."}), 500
    return jsonify({"status": "ok", "courses": snapshot(user)})


@courses_bp.route("/api/courses/recommend", methods=["GET"])
def api_courses_recommend():
    if _require_user() is None:
        return jsonify({"status": "error", "message": "Sign in first."}), 401
    return jsonify({
        "status": "ok",
        "results": rules.recommendations(request.args.get("subject", "")),
    })


@courses_bp.route("/api/courses/progress", methods=["GET", "POST"])
def api_courses_progress():
    """The browser extension reports a completion percentage it read.

    GET returns the pages this student tracks (host and path only), so the
    extension can tell whether the page in front of it is one of them
    before it reads anything.

    POST body: ``{"url": "<page the student is on>", "percent": 0-100}``.

    The extension posts cross-origin with no session cookie, so its bearer
    token is the only evidence of who is calling. A reading is applied only
    to a course this student already tracks; the endpoint never creates
    one, so installing the extension does not start recording which sites
    a student visits.
    """
    from App import CourseCheckin, _extension_bearer_token, db, get_extension_user

    user = current_user if current_user.is_authenticated else None
    if user is None:
        user = get_extension_user(_extension_bearer_token())
    if user is None:
        return jsonify({"status": "error", "message": "Sign in first."}), 401

    if request.method == "GET":
        return jsonify({
            "status": "ok",
            "tracked": [rules.match_key(c.url) for c in _active_courses(user.id)],
        })

    body = request.get_json(silent=True) or {}
    seen_url = rules.clean_url(body.get("url"))
    percent = rules.clamp_percent(body.get("percent"))
    if not seen_url or percent is None:
        return jsonify({"status": "error", "message": "Bad payload."}), 400

    course = next(
        (c for c in _active_courses(user.id) if rules.same_course(c.url, seen_url)), None)
    if course is None:
        return jsonify({"status": "ok", "matched": False})

    previous = course.verified_percent
    course.verified_percent = percent
    course.verified_at = utcnow()
    if previous is None or percent > previous:
        # A row per increase, so "did they actually move" has a history.
        # Minutes stay 0: the page shows how far, not how long.
        db.session.add(CourseCheckin(
            course_id=course.id, user_id=user.id, minutes=0,
            note=f"{percent:g}% complete", source=rules.SOURCE_EXTENSION))
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("course progress save failed for user %s: %s", user.id, exc)
        return jsonify({"status": "error", "message": "Could not save."}), 500
    return jsonify({"status": "ok", "matched": True, "course_id": course.id,
                    "percent": percent})


def install(app: Any) -> None:
    app.register_blueprint(courses_bp)
