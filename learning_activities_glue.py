"""Tracking for classes, clubs, and extracurricular learning commitments.

Each active commitment contributes one rolling weekly goal task to the
ordinary planner. Time entered by a student is kept as self-reported.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import current_user

import course_tracking as rules
from time_utils import to_local, utcnow

logger = logging.getLogger(__name__)
activities_bp = Blueprint("learning_activities", __name__)
TASK_SOURCE = "learning_activity"
ALLOWED_CATEGORIES = {"online_course", "class", "club", "extracurricular", "activity"}
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class ActivityError(ValueError):
    pass


def _user_today(user: Any) -> date:
    from App import _user_tz_name
    return to_local(utcnow(), _user_tz_name(user.id), int(getattr(user, "utc_offset_minutes", 0) or 0)).date()


def _owned(user_id: int, activity_id: int) -> Any | None:
    from App import LearningActivity
    return LearningActivity.query.filter_by(id=activity_id, user_id=user_id).first()


def _active(user_id: int) -> list[Any]:
    from App import LearningActivity
    return LearningActivity.query.filter_by(user_id=user_id, archived=False).order_by(LearningActivity.created_at.asc()).all()


def _week_minutes(user: Any, activity_ids: list[int], today: date) -> dict[int, int]:
    from App import LearningActivityCheckin, _user_tz_name
    if not activity_ids:
        return {}
    offset = int(getattr(user, "utc_offset_minutes", 0) or 0)
    tz_name = _user_tz_name(user.id)
    start = datetime.combine(rules.week_start(today), datetime.min.time())
    end = start + timedelta(days=7)
    from time_utils import to_utc
    start_utc, end_utc = to_utc(start, tz_name, offset), to_utc(end, tz_name, offset)
    rows = LearningActivityCheckin.query.filter(
        LearningActivityCheckin.user_id == user.id,
        LearningActivityCheckin.activity_id.in_(activity_ids),
        LearningActivityCheckin.created_at >= start_utc,
        LearningActivityCheckin.created_at < end_utc,
    ).all()
    out: dict[int, int] = {}
    for row in rows:
        out[row.activity_id] = out.get(row.activity_id, 0) + int(row.minutes or 0)
    return out


def _weeks_in_a_row(user: Any, activity_id: int, goal: int, today: date) -> int:
    from App import LearningActivityCheckin, _user_tz_name
    from time_utils import to_local
    rows = LearningActivityCheckin.query.filter_by(user_id=user.id, activity_id=activity_id).all()
    offset = int(getattr(user, "utc_offset_minutes", 0) or 0)
    weeks: dict[date, int] = {}
    for row in rows:
        local_day = to_local(row.created_at, _user_tz_name(user.id), offset).date()
        start = rules.week_start(local_day)
        weeks[start] = weeks.get(start, 0) + int(row.minutes or 0)
    return rules.weeks_met_in_a_row(weeks.items(), goal, today)


def _task(activity: Any, minutes: int, today: date) -> None:
    from App import ManualTask, db
    week = rules.week_start(today)
    key = f"activity:{activity.id}:{week.isoformat()}"
    goal = rules.clamp_goal(activity.weekly_goal_minutes)
    remaining = max(0, goal - minutes)
    task = ManualTask.query.filter_by(user_id=activity.user_id, import_source=TASK_SOURCE, external_id=key).first()
    title = f"{activity.title}: {goal} min this week"[:512]
    pref = []
    try:
        days = json.loads(activity.preferred_days or "[]")
        if days:
            pref.append("Preferred days: " + ", ".join(d.title() for d in days))
    except (TypeError, ValueError):
        pass
    if activity.preferred_time:
        pref.append("Preferred time: " + activity.preferred_time)
    if activity.url:
        pref.append("Link: " + activity.url)
    notes = "\n".join(pref)
    if task is None:
        if not remaining:
            return
        task = ManualTask(user_id=activity.user_id, title=title,
                          due_date=rules.week_end(today).isoformat(), priority="Medium",
                          course=activity.title[:256], estimated_time=max(15, remaining),
                          notes=notes, import_source=TASK_SOURCE, external_id=key)
        db.session.add(task)
    else:
        task.title, task.course, task.notes = title, activity.title[:256], notes
        if remaining == 0:
            task.done = True
        elif not task.done:
            task.estimated_time = max(15, remaining)


def _invalidate(user_id: int) -> None:
    try:
        from intelliplan.api.command_center import invalidate_today
        invalidate_today(user_id)
    except Exception:
        pass


def snapshot(user: Any) -> list[dict[str, Any]]:
    from App import db
    activities = _active(user.id)
    today = _user_today(user)
    minutes = _week_minutes(user, [a.id for a in activities], today)
    out = []
    for activity in activities:
        goal = rules.clamp_goal(activity.weekly_goal_minutes)
        done = minutes.get(activity.id, 0)
        remaining = max(0, goal - done)
        percent = min(100, round(done * 100 / goal))
        _task(activity, done, today)
        try:
            days = json.loads(activity.preferred_days or "[]")
        except (TypeError, ValueError):
            days = []
        out.append({"id": activity.id, "title": activity.title, "category": activity.category,
                    "url": activity.url or "", "weekly_goal_minutes": goal,
                    "preferred_days": days, "preferred_time": activity.preferred_time or "",
                    "total_minutes": int(activity.total_minutes or 0),
                    "weeks_in_a_row": _weeks_in_a_row(user, activity.id, goal, today),
                    "week": {"minutes": done, "remaining": remaining, "percent": percent,
                             "state": "done" if not remaining else ("on_track" if done else "not_started"),
                             "message": "This week's goal is met." if not remaining else f"{remaining} min left this week."}})
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("learning activity rollover failed for user %s: %s", user.id, exc)
    return out


def add_activity(user: Any, body: dict[str, Any]) -> Any:
    from App import CourseNote, LearningActivity, db
    title = " ".join(str(body.get("title") or "").split())[:200]
    if not title:
        raise ActivityError("Give this class or activity a name.")
    if len(_active(user.id)) >= 30:
        raise ActivityError("You can track up to 30 active commitments.")
    category = str(body.get("category") or "activity").strip().lower()
    if category not in ALLOWED_CATEGORIES:
        category = "activity"
    raw_url = body.get("url") or ""
    url = rules.clean_url(raw_url) if raw_url else ""
    if raw_url and not url:
        raise ActivityError("That link does not look valid. Use a full web address.")
    raw_days = body.get("preferred_days") or []
    if not isinstance(raw_days, list):
        raw_days = []
    days = list(dict.fromkeys(str(day).strip().lower() for day in raw_days if str(day).strip().lower() in DAYS))
    pref_time = " ".join(str(body.get("preferred_time") or "").split())[:80]
    activity = LearningActivity(user_id=user.id, title=title, category=category, url=url,
                                weekly_goal_minutes=rules.clamp_goal(body.get("weekly_goal_minutes")),
                                preferred_days=json.dumps(days), preferred_time=pref_time)
    db.session.add(activity)
    db.session.flush()
    _task(activity, 0, _user_today(user))
    # Make the new commitment discoverable in Plani's existing knowledge base.
    details = [f"Category: {category.replace('_', ' ')}",
               f"Weekly goal: {activity.weekly_goal_minutes} minutes"]
    if days:
        details.append("Preferred days: " + ", ".join(day.title() for day in days))
    if pref_time:
        details.append("Preferred time: " + pref_time)
    if url:
        details.append("Link: " + url)
    db.session.add(CourseNote(user_id=user.id, course_name=title,
                              course_id=f"activity:{activity.id}", course_source="learning_activity",
                              note_date=_user_today(user).isoformat(), title="Learning commitment",
                              text_content="\n".join(details)))
    db.session.commit()
    _invalidate(user.id)
    return activity


def log_progress(user: Any, activity: Any, minutes_raw: object, note_raw: object = "") -> int:
    from App import LearningActivityCheckin, db
    try:
        minutes = int(minutes_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ActivityError("Enter how many minutes you spent.")
    if minutes <= 0:
        raise ActivityError("Enter how many minutes you spent.")
    minutes = min(minutes, 480)
    db.session.add(LearningActivityCheckin(activity_id=activity.id, user_id=user.id,
                                           minutes=minutes, note=" ".join(str(note_raw or "").split())[:280]))
    activity.total_minutes = int(activity.total_minutes or 0) + minutes
    db.session.flush()
    done = _week_minutes(user, [activity.id], _user_today(user)).get(activity.id, 0)
    _task(activity, done, _user_today(user))
    db.session.commit()
    _invalidate(user.id)
    return minutes


def _response(user: Any, activity: Any | None = None):
    activities = snapshot(user)
    selected = next((item for item in activities if activity and item["id"] == activity.id), None)
    return jsonify({"status": "ok", "activities": activities, **({"activity": selected} if selected else {})})


@activities_bp.route("/api/activities", methods=["GET", "POST"])
def api_activities():
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "Sign in first."}), 401
    if request.method == "POST":
        try:
            activity = add_activity(current_user, request.get_json(silent=True) or {})
        except ActivityError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except Exception:
            from App import db
            db.session.rollback()
            logger.exception("learning activity create failed for user %s", current_user.id)
            return jsonify({"status": "error", "message": "Could not save that activity. Try again."}), 500
        return _response(current_user, activity)
    return _response(current_user)


@activities_bp.route("/api/activities/<int:activity_id>/checkin", methods=["POST"])
def api_activity_checkin(activity_id: int):
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "Sign in first."}), 401
    activity = _owned(current_user.id, activity_id)
    if activity is None or activity.archived:
        return jsonify({"status": "error", "message": "Activity not found."}), 404
    body = request.get_json(silent=True) or {}
    try:
        log_progress(current_user, activity, body.get("minutes"), body.get("note"))
    except ActivityError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception:
        from App import db
        db.session.rollback()
        logger.exception("learning activity check-in failed for user %s", current_user.id)
        return jsonify({"status": "error", "message": "Could not save progress. Try again."}), 500
    return _response(current_user, activity)


@activities_bp.route("/api/activities/<int:activity_id>", methods=["DELETE"])
def api_activity_delete(activity_id: int):
    from App import ManualTask, db
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "Sign in first."}), 401
    activity = _owned(current_user.id, activity_id)
    if activity is None or activity.archived:
        return jsonify({"status": "error", "message": "Activity not found."}), 404
    activity.archived = True
    week_key = f"activity:{activity.id}:{rules.week_start(_user_today(current_user)).isoformat()}"
    ManualTask.query.filter_by(user_id=current_user.id, import_source=TASK_SOURCE,
                               external_id=week_key, done=False).delete()
    db.session.commit()
    _invalidate(current_user.id)
    return _response(current_user)


def install(app: Any) -> None:
    app.register_blueprint(activities_bp)
