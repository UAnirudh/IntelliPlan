"""Render the student's plan as a subscribable iCalendar feed.

``ics_feed.py`` reads calendar feeds *in*; ``/schedule/export.ics`` writes a
one-shot file *out*. Neither puts the plan where most students actually look
-- the Calendar app that came with their phone -- and keeps it there. A
subscription does: Apple Calendar, Google Calendar and Outlook re-fetch a
subscribed URL on their own schedule, so every reflow, autopilot run or
new assignment Grade Pulse slots in reaches the phone without the student
doing anything.

Pure: schedule JSON and due dates in, RFC 5545 text out. The token, the
route and the database live in ``calendar_feed_glue.py``.

Choices that differ from the one-shot export, and why
-----------------------------------------------------
* **Stable UIDs.** The export stamps UIDs with the export time, which is
  fine for a file imported once. A subscription is re-read every hour, and
  a UID that changes every read makes some clients delete and re-create
  every event -- alarms and all. UIDs here derive from what the event *is*.
* **UTC when the timezone is known.** Plan times are the student's wall
  clock. With their IANA zone they are converted to UTC (``...Z``), which
  every client places correctly, including after the student travels. With
  no zone recorded they stay floating, which is still the right wall-clock
  time -- converting through a guessed zone would make it wrong.
* **Nothing the student did not put in the plan.** No name, no email, no
  grades, no notes. Titles and course names appear only in "full" mode; in
  "private" mode every event is generic, for a calendar that is shared with
  family or visible on a lock screen.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["build_plan_calendar", "ics_escape", "fold_line", "content_etag"]

CAL_NAME = "IntelliPlan study plan"
PRODID = "-//IntelliPlan//Live Plan Feed//EN"
UID_DOMAIN = "intelliplan.tech"
#: Suggested re-fetch interval. Clients treat it as a hint (Google largely
#: ignores it), but Apple and Outlook honour it.
REFRESH = "PT1H"
#: Due dates further out than this are noise in a phone calendar; older
#: than the lookback are history.
DUE_LOOKAHEAD_DAYS = 180
DUE_LOOKBACK_DAYS = 14


def ics_escape(text: Any) -> str:
    """RFC 5545 TEXT escaping: backslash, semicolon, comma, newline."""
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str) -> str:
    """Fold at 75 octets, never inside a UTF-8 character (RFC 5545 3.1).

    Course names with accents or emoji are common enough that folding by
    characters rather than bytes would produce lines clients reject.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts: list[str] = []
    limit = 75
    while raw:
        cut = min(limit, len(raw))
        # Back off to a character boundary (continuation bytes are 10xxxxxx).
        while cut < len(raw) and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
        limit = 74  # continuation lines carry a leading space
    return "\r\n ".join(parts)


def _uid(*parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(p or "") for p in parts).encode("utf-8")).hexdigest()[:24]
    return f"ip-{digest}@{UID_DOMAIN}"


def _zone(tz_name: str | None) -> ZoneInfo | None:
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _fmt_dt(local: datetime, zone: ZoneInfo | None) -> str:
    if zone is None:
        return local.strftime("%Y%m%dT%H%M%S")
    utc = local.replace(tzinfo=zone, fold=0).astimezone(ZoneInfo("UTC"))
    return utc.strftime("%Y%m%dT%H%M%SZ")


def _parse_12h(raw: str) -> time | None:
    cleaned = str(raw or "").strip().upper().replace(".", "")
    for fmt in ("%I:%M %p", "%I %p"):
        try:
            return datetime.strptime(cleaned, fmt).time()
        except ValueError:
            continue
    return None


def _block_times(day: date, block: Mapping[str, Any]) -> tuple[datetime, datetime] | None:
    """Clock times for a block, re-anchored on its day.

    Same rules as the one-shot export: only the clock part of start_iso /
    end_iso is trusted (humanize_schedule stamps every day with the date the
    plan was generated), with time_slot as the fallback.
    """
    start_t = end_t = None
    for key in ("start_iso", "end_iso"):
        value = block.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value)).time()
        except ValueError:
            continue
        if key == "start_iso":
            start_t = parsed
        else:
            end_t = parsed
    if start_t is None and block.get("time_slot"):
        parts = str(block["time_slot"]).split("-")
        start_t = _parse_12h(parts[0]) if parts else None
        if len(parts) > 1:
            end_t = _parse_12h(parts[1])
    if start_t is None:
        return None
    start = datetime.combine(day, start_t)
    if end_t is not None:
        end = datetime.combine(day, end_t)
        if end <= start:
            end += timedelta(days=1)
    else:
        try:
            minutes = int(block.get("duration_minutes") or 30)
        except (TypeError, ValueError):
            minutes = 30
        end = start + timedelta(minutes=max(5, minutes))
    return start, end


def _label(title: Any, course: Any) -> str:
    title = str(title or "").strip() or "Study block"
    course = str(course or "").strip()
    return f"{title} ({course})" if course else title


def _plan_events(schedule_data: Mapping[str, Any], zone, private: bool, link: str) -> list[list[str]]:
    events = []
    for day in schedule_data.get("schedule") or []:
        if not isinstance(day, Mapping):
            continue
        try:
            day_date = date.fromisoformat(str(day.get("date") or "")[:10])
        except ValueError:
            continue
        for block in day.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("is_break") or block.get("unplaced"):
                continue
            times = _block_times(day_date, block)
            if times is None:
                continue
            start, end = times
            summary = "Study block" if private else _label(block.get("assignment"), block.get("course"))
            ident = block.get("block_id") or block.get("assignment") or ""
            desc = "Planned with IntelliPlan." + (f" Open your plan: {link}" if link else "")
            status = "CONFIRMED"
            if block.get("done"):
                # A finished block stays visible -- it is the record of the
                # work -- but reads as done rather than as a pending task.
                summary = f"✓ {summary}"
            events.append([
                "BEGIN:VEVENT",
                f"UID:{_uid('block', day_date.isoformat(), ident, start.time().isoformat())}",
                f"DTSTART:{_fmt_dt(start, zone)}",
                f"DTEND:{_fmt_dt(end, zone)}",
                f"SUMMARY:{ics_escape(summary)}",
                f"DESCRIPTION:{ics_escape(desc)}",
                f"STATUS:{status}",
                "TRANSP:OPAQUE",
                "CATEGORIES:Study",
                "END:VEVENT",
            ])
    return events


def _due_events(due_items: Iterable[Mapping[str, Any]], today: date, private: bool) -> list[list[str]]:
    seen: set[tuple[str, str, str]] = set()
    events = []
    first = today - timedelta(days=DUE_LOOKBACK_DAYS)
    last = today + timedelta(days=DUE_LOOKAHEAD_DAYS)
    for item in due_items or []:
        if not isinstance(item, Mapping):
            continue
        try:
            due = date.fromisoformat(str(item.get("due_date") or "")[:10])
        except ValueError:
            continue
        if not first <= due <= last:
            continue
        title = str(item.get("title") or "").strip()
        course = str(item.get("course") or "").strip()
        if not title:
            continue
        key = (title.lower(), course.lower(), due.isoformat())
        if key in seen:
            continue
        seen.add(key)
        summary = "Assignment due" if private else f"Due: {_label(title, course)}"
        # All-day and transparent: a deadline is a marker, not a meeting,
        # and must not show the student as busy all day to anyone who can
        # see their free/busy.
        events.append([
            "BEGIN:VEVENT",
            f"UID:{_uid('due', *key)}",
            f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(due + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(summary)}",
            "TRANSP:TRANSPARENT",
            "CATEGORIES:Deadline",
            "END:VEVENT",
        ])
    return events


def plan_due_items(schedule_data: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Due dates the plan itself carries (enrich_schedule_data and Grade
    Pulse both stamp ``due_date`` on blocks)."""
    out = []
    for day in (schedule_data or {}).get("schedule") or []:
        for block in (day.get("blocks") if isinstance(day, Mapping) else None) or []:
            if not isinstance(block, Mapping) or block.get("is_break") or not block.get("due_date"):
                continue
            out.append({
                "title": str(block.get("parent_title") or block.get("assignment") or ""),
                "course": str(block.get("course") or ""),
                "due_date": str(block.get("due_date")),
            })
    return out


def build_plan_calendar(
    schedule_data: Mapping[str, Any] | None,
    due_items: Sequence[Mapping[str, Any]] = (),
    *,
    tz_name: str | None = None,
    detail: str = "full",
    now: datetime,
    link: str = "",
) -> str:
    """The whole VCALENDAR. ``now`` is naive UTC (time_utils convention)."""
    zone = _zone(tz_name)
    private = detail == "private"
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    today = (now.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone).date()
             if zone else now.date())

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CAL_NAME)}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}",
        f"X-PUBLISHED-TTL:{REFRESH}",
    ]
    if zone is not None:
        lines.append(f"X-WR-TIMEZONE:{tz_name}")
    events = _plan_events(schedule_data or {}, zone, private, link)
    events += _due_events(list(due_items or ()) + plan_due_items(schedule_data), today, private)
    for event in events:
        # DTSTAMP goes right after UID; it is required on every VEVENT.
        lines.extend(event[:2] + [f"DTSTAMP:{stamp}"] + event[2:])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def content_etag(ics_text: str) -> str:
    """An ETag that ignores DTSTAMP, so an unchanged plan is a 304.

    DTSTAMP is "when this copy was generated" and changes every request;
    hashing it in would make every poll look like a change.
    """
    body = "\n".join(l for l in ics_text.splitlines() if not l.startswith("DTSTAMP:"))
    return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'
