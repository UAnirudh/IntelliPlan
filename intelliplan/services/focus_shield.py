"""Focus Shield: which study block is happening right now, and what to block.

The browser extension blocks distracting sites *only* while the student is
meant to be studying -- a planned block from their saved schedule, or an
Active session they started. Everything here is pure: the plan and the
clock go in, a description of the current and next block comes out. The
glue module (``focus_shield_glue.py``) does the database reads and the
timezone lookup; this module never touches either.

Times
-----
A plan block carries its day (``YYYY-MM-DD``) and a wall-clock slot
("4:30 PM - 5:15 PM"), so it is in the student's local time. The caller
passes ``local_now`` in that same wall clock, plus a ``to_utc`` callable so
the response can also carry absolute instants. The extension compares those
absolute instants against its own clock, which keeps it right offline and
across a DST change mid-block.

``start_iso`` / ``end_iso`` are read for their *clock* only and re-anchored
onto the day's own date, for the reason ``_schedule_to_ics`` gives:
humanize_schedule() stamps every day's ISO times with today's date.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable, Mapping

#: Sites blocked when the student has not chosen their own list. Domains,
#: not URLs: a rule for ``youtube.com`` covers ``www.`` and ``m.`` too.
DEFAULT_BLOCKLIST: tuple[str, ...] = (
    "youtube.com",
    "tiktok.com",
    "instagram.com",
    "reddit.com",
    "x.com",
    "twitter.com",
    "netflix.com",
    "discord.com",
    "roblox.com",
)

#: A list longer than this is a paste accident, and every entry becomes a
#: dynamic rule in the extension, whose quota is finite.
MAX_BLOCKLIST = 60
#: Length of one "take a 5-minute break" pass.
BREAK_MINUTES = 5
#: Default number of breaks per block, and the ceiling a student can set.
DEFAULT_BREAKS_PER_BLOCK = 2
MAX_BREAKS_PER_BLOCK = 5
#: How far ahead the response lists blocks, so the extension can keep
#: enforcing from its cache when the network drops.
LOOKAHEAD_HOURS = 18

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


# ── Blocklist ───────────────────────────────────────────────────────────


def normalize_domain(raw: Any) -> str | None:
    """``https://www.YouTube.com/watch?v=1`` -> ``youtube.com``; junk -> None.

    Students paste whatever is in the address bar, so a URL is accepted and
    reduced to its host. A leading ``www.`` or ``m.`` is dropped because the
    extension's rule already matches every subdomain.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return None
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.split("@")[-1].split(":", 1)[0].strip(".")
    for prefix in ("www.", "m.", "mobile."):
        if text.startswith(prefix) and text.count(".") > 1:
            text = text[len(prefix):]
    if not _DOMAIN_RE.match(text):
        return None
    # Never let a student lock themselves out of the app that unlocks them.
    if text == "intelliplan.tech" or text.endswith(".intelliplan.tech"):
        return None
    return text


def clean_blocklist(items: Iterable[Any] | None) -> list[str]:
    """Normalise, de-duplicate and cap a user-supplied list. Order is kept."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        domain = normalize_domain(item)
        if domain and domain not in seen:
            seen.add(domain)
            out.append(domain)
        if len(out) >= MAX_BLOCKLIST:
            break
    return out


# ── Plan blocks ─────────────────────────────────────────────────────────


def _parse_clock(text: Any) -> time | None:
    raw = str(text or "").strip().upper().replace(".", "")
    if not raw:
        return None
    for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _iso_clock(value: Any) -> time | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).time()
    except ValueError:
        return None


def block_window(day: date, block: Mapping[str, Any]) -> tuple[datetime, datetime] | None:
    """Local start and end of one plan block, or None if it has no time."""
    start_t = _iso_clock(block.get("start_iso"))
    end_t = _iso_clock(block.get("end_iso"))
    slot = str(block.get("time_slot") or "")
    if start_t is None and slot:
        parts = re.split(r"\s+-\s+|\s*[-–]\s*", slot, maxsplit=1)
        start_t = _parse_clock(parts[0]) if parts else None
        if len(parts) > 1:
            end_t = _parse_clock(parts[1])
    if start_t is None:
        return None
    start = datetime.combine(day, start_t)
    if end_t is not None:
        end = datetime.combine(day, end_t)
        if end <= start:
            end += timedelta(days=1)  # a block that runs past midnight
    else:
        try:
            minutes = int(block.get("duration_minutes") or 30)
        except (TypeError, ValueError):
            minutes = 30
        end = start + timedelta(minutes=max(1, minutes))
    return start, end


def block_key(day: date, block: Mapping[str, Any]) -> str:
    """Stable id for a block: its own id, else its day and slot."""
    for key in ("block_id", "id"):
        if block.get(key):
            return str(block[key])[:64]
    return f"{day.isoformat()}@{str(block.get('time_slot') or '')[:24]}"


def _is_done(block: Mapping[str, Any], key: str, progress: Mapping[str, Any] | None) -> bool:
    if block.get("done"):
        return True
    entry = (progress or {}).get(key)
    return entry is True or (isinstance(entry, Mapping) and bool(entry.get("done")))


def plan_windows(
    plan: Mapping[str, Any] | None,
    progress: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every timed, unfinished, non-break block in the plan, in local time."""
    out: list[dict[str, Any]] = []
    for day in (plan or {}).get("schedule", []) or []:
        try:
            day_date = date.fromisoformat(str(day.get("date") or "")[:10])
        except ValueError:
            continue
        for block in day.get("blocks", []) or []:
            if not isinstance(block, Mapping) or block.get("is_break"):
                continue
            window = block_window(day_date, block)
            if window is None:
                continue
            key = block_key(day_date, block)
            if _is_done(block, key, progress):
                continue
            out.append({
                "key": key,
                "source": "plan",
                # Only what a block page needs to say what to do. The course
                # is the student's own and stays on their own device.
                "title": str(block.get("parent_title") or block.get("assignment") or "Study block")[:200],
                "course": str(block.get("course") or "")[:120],
                "start": window[0],
                "end": window[1],
            })
    out.sort(key=lambda w: w["start"])
    return out


def resolve(
    windows: list[dict[str, Any]],
    local_now: datetime,
    to_utc: Callable[[datetime], datetime],
    *,
    active: Mapping[str, Any] | None = None,
    lookahead_hours: int = LOOKAHEAD_HOURS,
) -> dict[str, Any]:
    """The current block, the next one, and the upcoming list.

    ``active`` is a running Active session already expressed in local time
    (``title``, ``course``, ``start``, ``end``, ``key``). It wins over the
    plan: a student who started a session has told us they are studying now,
    whatever the plan said.
    """
    horizon = local_now + timedelta(hours=lookahead_hours)

    def _out(w: Mapping[str, Any]) -> dict[str, Any]:
        start_utc = to_utc(w["start"])
        end_utc = to_utc(w["end"])
        return {
            "key": w["key"],
            "source": w["source"],
            "title": w["title"],
            "course": w.get("course", ""),
            "starts_at": _z(start_utc),
            "ends_at": _z(end_utc),
            "local_start": w["start"].strftime("%Y-%m-%dT%H:%M"),
            "local_end": w["end"].strftime("%Y-%m-%dT%H:%M"),
            "minutes_left": max(0, int((w["end"] - max(local_now, w["start"])).total_seconds() // 60)),
        }

    current = None
    if active:
        current = _out({**active, "source": "active"})
    upcoming: list[dict[str, Any]] = []
    for w in windows:
        if w["end"] <= local_now or w["start"] > horizon:
            continue
        if current is None and w["start"] <= local_now < w["end"]:
            current = _out(w)
            continue
        if w["start"] > local_now:
            upcoming.append(_out(w))
    return {
        "current": current,
        "next": upcoming[0] if upcoming else None,
        "upcoming": upcoming[:12],
    }


def active_window(row: Any, now_utc: datetime, to_local: Callable[[datetime], datetime]) -> dict[str, Any] | None:
    """A running or paused Active session as a local-time window.

    The end is the planned length plus time spent paused. A session that has
    run past that is still a session -- the student has not pressed finish --
    so it stays "current" with a short rolling end the extension re-checks.
    """
    if row is None or getattr(row, "state", None) not in ("running", "paused"):
        return None
    started = getattr(row, "started_at", None) or now_utc
    planned = int(getattr(row, "planned_minutes", 0) or 25)
    paused = int(getattr(row, "paused_seconds", 0) or 0)
    end_utc = started + timedelta(minutes=planned, seconds=paused)
    if end_utc <= now_utc:
        end_utc = now_utc + timedelta(minutes=BREAK_MINUTES)
    return {
        "key": f"active-{getattr(row, 'id', '')}",
        "title": str(getattr(row, "title", "") or "Study session")[:200],
        "course": str(getattr(row, "course", "") or "")[:120],
        "start": to_local(started),
        "end": to_local(end_utc),
    }


def _z(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat() + "Z"
