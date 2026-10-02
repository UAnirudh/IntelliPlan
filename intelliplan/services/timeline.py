"""The Today timeline: one day, drawn to scale, from real data.

Structured and Tiimo made the visual day popular, and in both the student
types every item in by hand. Here the day is already known: classes come
from the timetable, study blocks from the saved plan, and busy time from
the calendars the student connected. This module merges them into a single
list of positioned items, and answers "what happens if I move this block
to 4:30?" before anything is saved.

Moves are applied *literally* — the block the student dragged goes where
they put it and nothing else on the day shifts. That is the same contract
as :mod:`intelliplan.intelligence.overrides`: a drag that quietly
rearranges four other things is a plan the student did not ask for.

Purity
------
No Flask, no ORM, no clock reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from intelliplan.domain.timetable import (
    Meeting,
    Rotation,
    fmt_clock,
    meetings_on,
    next_meeting,
    parse_clock,
    rotation_label,
    skip_reason,
)

__all__ = [
    "DEFAULT_DAY_START",
    "DEFAULT_DAY_END",
    "MoveCheck",
    "apply_move",
    "block_key",
    "build_timeline",
    "check_move",
    "day_blocks",
]

#: The visible day when nothing is scheduled outside it.
DEFAULT_DAY_START = 7 * 60
DEFAULT_DAY_END = 22 * 60
#: Earliest / latest a study block may be dragged to.
MOVE_FLOOR = 5 * 60
MOVE_CEILING = 24 * 60


def _fmt12(minutes: int) -> str:
    h, m = divmod(int(minutes) % (24 * 60), 60)
    suffix = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12}:{m:02d} {suffix}"


def _end_clock(minutes: int) -> str:
    """``fmt_clock`` that writes midnight-at-the-end as 24:00, not 00:00."""
    return "24:00" if minutes >= 24 * 60 else fmt_clock(minutes)


def block_key(block: Mapping[str, Any], index: int) -> str:
    """Stable id for a plan block: ``block_id``, then ``id``, then position."""
    return str(block.get("block_id") or block.get("id") or f"idx-{index}")


def _block_minutes(block: Mapping[str, Any]) -> tuple[int, int] | None:
    """Clock range of a plan block. ISO times carry only a trustworthy clock:
    some writers stamp every day's ISO with *today's* date."""
    start = end = None
    for key in ("start_iso", "end_iso"):
        raw = block.get(key)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        minute = parsed.hour * 60 + parsed.minute
        if key == "start_iso":
            start = minute
        else:
            end = minute
    if start is None and block.get("time_slot"):
        parts = str(block["time_slot"]).replace("–", "-").split("-")
        start = parse_clock(parts[0]) if parts else None
        if len(parts) > 1:
            end = parse_clock(parts[1])
    if start is None:
        return None
    if end is None or end <= start:
        try:
            end = start + int(block.get("duration_minutes") or 30)
        except (TypeError, ValueError):
            end = start + 30
    return start, min(end, 24 * 60)


def day_blocks(schedule_data: Mapping[str, Any] | None, day: date) -> list[dict[str, Any]]:
    """The saved plan's blocks for ``day``, or ``[]``."""
    for entry in (schedule_data or {}).get("schedule") or []:
        if isinstance(entry, Mapping) and str(entry.get("date") or "")[:10] == day.isoformat():
            return [b for b in entry.get("blocks") or [] if isinstance(b, Mapping)]
    return []


def _class_item(m: Meeting) -> dict[str, Any]:
    subtitle = " · ".join(
        bit for bit in (
            f"Period {m.period}" if m.period and not str(m.period).lower().startswith("p") else m.period,
            f"Room {m.room}" if m.room and not str(m.room).lower().startswith("room") else m.room,
            m.teacher,
        ) if bit
    )
    return {
        "id": f"class-{m.id}",
        "kind": "class",
        "title": m.course,
        "subtitle": subtitle,
        "course": m.course,
        "room": m.room,
        "period": m.period,
        "teacher": m.teacher,
        "start": fmt_clock(m.start_minute),
        "end": fmt_clock(m.end_minute),
        "start_minute": m.start_minute,
        "end_minute": m.end_minute,
        "label": f"{_fmt12(m.start_minute)} – {_fmt12(m.end_minute)}",
        "movable": False,
        "color": m.color,
    }


def build_timeline(
    day: date,
    *,
    schedule_data: Mapping[str, Any] | None,
    progress: Mapping[str, Any] | None,
    meetings: Sequence[Meeting],
    rotation: Rotation,
    bell: Mapping[str, Sequence[Any]] | None = None,
    busy: Iterable[tuple[int, int]] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Everything the timeline draws for ``day``, sorted by start time."""
    items: list[dict[str, Any]] = []
    for m in meetings_on(day, meetings, rotation, bell):
        items.append(_class_item(m))

    progress = progress or {}
    for index, block in enumerate(day_blocks(schedule_data, day)):
        span = _block_minutes(block)
        if span is None:
            continue
        key = block_key(block, index)
        is_break = bool(block.get("is_break"))
        state = progress.get(key) if isinstance(progress.get(key), Mapping) else {}
        title = str(block.get("assignment") or ("Break" if is_break else "Study block"))
        items.append({
            "id": key,
            "kind": "break" if is_break else "study",
            "title": title,
            "subtitle": str(block.get("course") or ""),
            "course": str(block.get("course") or ""),
            "start": fmt_clock(span[0]),
            "end": fmt_clock(span[1]),
            "start_minute": span[0],
            "end_minute": span[1],
            "label": f"{_fmt12(span[0])} – {_fmt12(span[1])}",
            "movable": not is_break,
            "done": bool(state.get("done")),
            "conflict": bool(block.get("conflict")),
        })

    for start, end in sorted(_merge(busy)):
        items.append({
            "id": f"busy-{start}-{end}",
            "kind": "busy",
            "title": "Busy",
            "subtitle": "From your calendar",
            "start": fmt_clock(start),
            "end": _end_clock(end),
            "start_minute": start,
            "end_minute": end,
            "label": "All day" if start == 0 and end >= 24 * 60 else f"{_fmt12(start)} – {_fmt12(end)}",
            "movable": False,
        })

    order = {"class": 0, "busy": 1, "study": 2, "break": 3}
    items.sort(key=lambda i: (i["start_minute"], order.get(i["kind"], 9), i["end_minute"]))

    # The visible range: the default day, stretched to whatever is on it, on
    # whole hours. All-day busy time does not stretch it to midnight.
    timed = [i for i in items if not (i["kind"] == "busy" and i["start_minute"] == 0 and i["end_minute"] >= 24 * 60)]
    lo = min([DEFAULT_DAY_START] + [i["start_minute"] for i in timed])
    hi = max([DEFAULT_DAY_END] + [i["end_minute"] for i in timed])
    lo, hi = (lo // 60) * 60, min(24 * 60, -(-hi // 60) * 60)

    payload: dict[str, Any] = {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "school_day": skip_reason(day, rotation) is None,
        "skip_reason": skip_reason(day, rotation),
        "rotation_label": rotation_label(day, rotation),
        "range": {"start": lo, "end": hi},
        "items": items,
        "counts": {
            kind: sum(1 for i in items if i["kind"] == kind)
            for kind in ("class", "study", "busy")
        },
        "next_class": None,
    }
    if now is not None:
        found = next_meeting(now, meetings, rotation, bell)
        if found:
            when, m = found
            payload["next_class"] = {
                **_class_item(m),
                "date": when.isoformat(),
                "rotation_label": rotation_label(when, rotation),
            }
    return payload


def _merge(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted((int(a), int(b)) for a, b in intervals if int(b) > int(a)):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# ── Moving a block ───────────────────────────────────────────────────


@dataclass(frozen=True)
class MoveCheck:
    """What moving one block would do, before it is done."""

    found: bool
    allowed: bool
    start_minute: int = 0
    end_minute: int = 0
    title: str = ""
    conflicts: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    reason: str = ""

    @property
    def summary(self) -> str:
        if not self.found:
            return "That study block isn't in today's plan any more."
        when = f"{_fmt12(self.start_minute)} – {_fmt12(self.end_minute)}"
        if not self.allowed:
            return self.reason or f"Can't move {self.title} to {when}."
        if self.warnings:
            names = ", ".join(sorted({w["title"] for w in self.warnings}))
            return f"{self.title} moves to {when}. It overlaps {names} on your calendar."
        return f"{self.title} moves to {when}. Nothing else changes."

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "summary": self.summary,
            "block": {
                "title": self.title,
                "start": fmt_clock(self.start_minute),
                "end": fmt_clock(self.end_minute),
                "start_minute": self.start_minute,
                "end_minute": self.end_minute,
                "label": f"{_fmt12(self.start_minute)} – {_fmt12(self.end_minute)}",
            },
            "conflicts": list(self.conflicts),
            "warnings": list(self.warnings),
        }


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def check_move(
    day: date,
    block_id: str,
    new_start: int,
    *,
    schedule_data: Mapping[str, Any] | None,
    meetings: Sequence[Meeting],
    rotation: Rotation,
    bell: Mapping[str, Sequence[Any]] | None = None,
    busy: Iterable[tuple[int, int]] = (),
) -> MoveCheck:
    """Price a move. Classes and other study blocks are hard conflicts —
    a student cannot be in two places — while calendar busy time is a
    warning: the calendar may hold an optional event, and the student is
    the one who knows."""
    blocks = day_blocks(schedule_data, day)
    target = None
    for index, block in enumerate(blocks):
        if block_key(block, index) == str(block_id) and not block.get("is_break"):
            target = (index, block)
            break
    if target is None:
        return MoveCheck(found=False, allowed=False)
    index, block = target
    span = _block_minutes(block)
    duration = (span[1] - span[0]) if span else int(block.get("duration_minutes") or 30)
    duration = max(5, duration)
    new_end = new_start + duration
    title = str(block.get("assignment") or "Study block")
    if new_start < MOVE_FLOOR or new_end > MOVE_CEILING:
        return MoveCheck(
            found=True, allowed=False, start_minute=new_start, end_minute=new_end, title=title,
            reason="Study blocks have to sit between 5 AM and midnight.",
        )
    proposed = (new_start, new_end)
    conflicts: list[dict[str, Any]] = []
    for m in meetings_on(day, meetings, rotation, bell):
        if _overlaps(proposed, (m.start_minute, m.end_minute)):
            conflicts.append({"kind": "class", "title": m.course,
                              "start": fmt_clock(m.start_minute), "end": fmt_clock(m.end_minute)})
    for other_index, other in enumerate(blocks):
        if other_index == index or other.get("is_break"):
            continue
        other_span = _block_minutes(other)
        if other_span and _overlaps(proposed, other_span):
            conflicts.append({"kind": "study", "title": str(other.get("assignment") or "Study block"),
                              "start": fmt_clock(other_span[0]), "end": fmt_clock(other_span[1])})
    warnings = [
        {"kind": "busy", "title": "a busy time", "start": fmt_clock(s), "end": _end_clock(e)}
        for s, e in _merge(busy) if _overlaps(proposed, (s, e))
    ]
    reason = ""
    if conflicts:
        first = conflicts[0]
        reason = (
            f"That would put {title} during {first['title']} "
            f"({_fmt12(parse_clock(first['start']))} – {_fmt12(parse_clock(first['end']))})."
        )
    return MoveCheck(
        found=True, allowed=not conflicts, start_minute=new_start, end_minute=new_end,
        title=title, conflicts=tuple(conflicts), warnings=tuple(warnings), reason=reason,
    )


def apply_move(schedule_data: dict[str, Any], day: date, block_id: str, check: MoveCheck) -> dict[str, Any]:
    """Write an allowed move into ``schedule_data`` (in place) and return it.

    The day's blocks are re-sorted by clock so every list view of the plan
    reads top to bottom in the order the day happens.
    """
    if not check.allowed:
        raise ValueError("move is not allowed")
    for entry in schedule_data.get("schedule") or []:
        if str(entry.get("date") or "")[:10] != day.isoformat():
            continue
        blocks = [b for b in entry.get("blocks") or [] if isinstance(b, dict)]
        # Pin every key before re-sorting: a positional id would otherwise
        # point at a different block afterwards.
        keys = [block_key(b, i) for i, b in enumerate(blocks)]
        for block, key in zip(blocks, keys):
            if not block.get("block_id") and not block.get("id"):
                block["block_id"] = key
        for block, key in zip(blocks, keys):
            if key != str(block_id):
                continue
            midnight = datetime.combine(day, datetime.min.time())
            start = midnight + timedelta(minutes=check.start_minute)
            end = midnight + timedelta(minutes=check.end_minute)
            block["start_iso"] = start.isoformat()
            block["end_iso"] = end.isoformat()
            block["time_slot"] = f"{_fmt12(check.start_minute)} - {_fmt12(check.end_minute)}"
            block["duration_minutes"] = check.end_minute - check.start_minute
            block["moved_by_student"] = True
            block.pop("conflict", None)
            break

        def sort_key(b: Mapping[str, Any]) -> int:
            span = _block_minutes(b)
            return span[0] if span else 24 * 60

        entry["blocks"] = sorted(blocks, key=sort_key)
        break
    return schedule_data
