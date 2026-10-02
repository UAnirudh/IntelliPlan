"""ORM models for the class timetable.

Two additive tables:

* ``class_meetings`` — one row per class on a student's timetable: course,
  clock times, room, teacher, and which weekdays / rotation days it meets.
* ``timetable_settings`` — one row per student: how their school's days
  rotate, the anchor that pins the rotation to the calendar, the no-school
  days, and the bell schedule (period → times) that untimed classes inherit.

The rotation math lives in :mod:`intelliplan.domain.timetable`; these rows
only convert to and from its types. Registration follows the same
``register(db)`` pattern as ``intelliplan.models.model_priors`` so
``db.create_all()`` at boot builds both tables.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from time_utils import utcnow

from intelliplan.domain.timetable import (
    Meeting,
    Rotation,
    parse_clock,
    parse_int_list,
    parse_weekdays,
)


def _utcnow() -> datetime:
    return utcnow()


def _json(raw: str | None, fallback: Any) -> Any:
    try:
        value = json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def register(db: Any) -> tuple[type, type]:
    """Define ``ClassMeeting`` and ``TimetableSettings``. Idempotent."""
    registry = getattr(db.Model, "registry", None)
    found: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            name = getattr(cls, "__tablename__", "")
            if name in ("class_meetings", "timetable_settings"):
                found[name] = cls
    if len(found) == 2:
        return found["class_meetings"], found["timetable_settings"]

    class ClassMeeting(db.Model):
        __tablename__ = "class_meetings"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        course = db.Column(db.String(256), nullable=False)
        period = db.Column(db.String(32), default="")
        room = db.Column(db.String(64), default="")
        teacher = db.Column(db.String(128), default="")
        #: "HH:MM", 24-hour, local wall clock. Empty → take it from the bell.
        start_time = db.Column(db.String(5), default="")
        end_time = db.Column(db.String(5), default="")
        #: "Mon,Wed,Fri"; empty = every school day.
        weekdays = db.Column(db.String(32), default="")
        #: "1,3"; A = 1, B = 2. Empty = every rotation day.
        rotation_days = db.Column(db.String(32), default="")
        color = db.Column(db.String(16), default="")
        #: manual | studentvue | schoology | photo
        source = db.Column(db.String(16), default="manual")
        external_id = db.Column(db.String(64), default="")
        created_at = db.Column(db.DateTime, default=_utcnow)
        updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

        def to_meeting(self) -> Meeting:
            return Meeting(
                id=self.id,
                course=self.course or "",
                period=self.period or "",
                room=self.room or "",
                teacher=self.teacher or "",
                start_minute=parse_clock(self.start_time),
                end_minute=parse_clock(self.end_time),
                weekdays=parse_weekdays(self.weekdays),
                rotation_days=parse_int_list(self.rotation_days),
                color=self.color or "",
                source=self.source or "manual",
            )

    class TimetableSettings(db.Model):
        __tablename__ = "timetable_settings"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
        rotation_kind = db.Column(db.String(8), default="none")
        cycle_length = db.Column(db.Integer, default=1)
        anchor_date = db.Column(db.Date, nullable=True)
        anchor_day = db.Column(db.Integer, default=1)
        school_weekdays = db.Column(db.String(32), default="Mon,Tue,Wed,Thu,Fri")
        skip_days_json = db.Column(db.Text, default="[]")
        bell_json = db.Column(db.Text, default="{}")
        last_import_source = db.Column(db.String(16), default="")
        last_import_at = db.Column(db.DateTime, nullable=True)
        updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

        def rotation(self) -> Rotation:
            return Rotation.from_dict({
                "kind": self.rotation_kind or "none",
                "length": self.cycle_length or 1,
                "anchor_date": self.anchor_date,
                "anchor_day": self.anchor_day or 1,
                "school_weekdays": self.school_weekdays or "",
                "skip_days": _json(self.skip_days_json, []),
            })

        def bell(self) -> dict[str, list[str]]:
            raw = _json(self.bell_json, {})
            return {
                str(k)[:32]: [str(v[0]), str(v[1])]
                for k, v in raw.items()
                if isinstance(v, (list, tuple)) and len(v) >= 2
            }

    return ClassMeeting, TimetableSettings
