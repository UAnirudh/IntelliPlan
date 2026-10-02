"""Fill a student's timetable from systems that already know it.

MyStudyLife makes students type their timetable in by hand, then charges
for the photo scan that would spare them. The school's own systems already
hold most of it, so this reads it from there:

* **StudentVUE** — ``StudentClassList`` lists every class (period, room,
  teacher) and carries *today's* bell times. Over SOAP first, then the JSON
  API districts are moving to. On the JSON path the per-day method
  (``GetStudentClasesForGivenDay``) is sampled across the coming school
  days, which fills in times for classes that do not meet today and tells
  us which days each class meets on.
* **Schoology** — sections carry ``meeting_days``, ``start_time``,
  ``end_time`` and ``location`` when the school fills them in.
* **A photo** of the printed schedule, through the existing vision model.

Every mapper here is pure and tolerant: a field missing from one district's
payload costs that field, never the import. The fetchers are thin wrappers
that hand raw payloads to the mappers, so tests feed the mappers recorded
shapes with no network at all.

Output rows share one shape::

    {"course", "period", "room", "teacher", "start": "HH:MM" | "",
     "end", "weekdays": ["Mon", ...], "rotation_days": [1, ...],
     "source", "external_id", "needs_days": bool}
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from intelliplan.domain.timetable import (
    WEEKDAY_ABBR,
    fmt_clock,
    parse_clock,
    parse_int_list,
    parse_weekdays,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHOTO_PROMPT",
    "build_studentvue_timetable",
    "fetch_schoology_timetable",
    "fetch_studentvue_timetable",
    "map_schoology_sections",
    "parse_photo_timetable",
    "parse_rotation_label",
    "parse_studentvue_class_list",
    "studentvue_json_records",
]

MAX_CLASSES = 30

_COURSE_KEYS = ("coursetitle", "classname", "course", "coursename", "title", "name")
_PERIOD_KEYS = ("period", "periodnumber", "periodname")
_ROOM_KEYS = ("roomname", "room", "location")
_TEACHER_KEYS = ("teacher", "teachername", "staff", "staffname")
_START_KEYS = ("starttime", "start", "start_time", "classstarttime")
_END_KEYS = ("endtime", "end", "end_time", "classendtime")
_ID_KEYS = ("sectiongu", "sectionid", "section_id", "id")


def _lookup(record: Mapping[str, Any], keys: Sequence[str]) -> str:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        value = lowered.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value).strip()
    return ""


def _clock_text(value: Any) -> str:
    minutes = parse_clock(value)
    return fmt_clock(minutes) if minutes is not None else ""


def _row(record: Mapping[str, Any], source: str) -> dict[str, Any] | None:
    course = _lookup(record, _COURSE_KEYS)[:256]
    if not course:
        return None
    start, end = _clock_text(_lookup(record, _START_KEYS)), _clock_text(_lookup(record, _END_KEYS))
    if start and end and parse_clock(end) <= parse_clock(start):
        start = end = ""
    hide = _lookup(record, ("hideclassstartendtime",)).lower() == "true"
    if hide:
        start = end = ""
    return {
        "course": course,
        "period": _lookup(record, _PERIOD_KEYS)[:32],
        "room": _lookup(record, _ROOM_KEYS)[:64],
        "teacher": _lookup(record, _TEACHER_KEYS)[:128],
        "start": start,
        "end": end,
        "weekdays": [],
        "rotation_days": [],
        "source": source,
        "external_id": _lookup(record, _ID_KEYS)[:64],
        "needs_days": False,
    }


def _pc_key(row: Mapping[str, Any]) -> str:
    return f"{str(row.get('period') or '').strip().lower()}|{str(row.get('course') or '').strip().lower()}"


def _key(row: Mapping[str, Any]) -> str:
    if row.get("external_id"):
        return "id:" + str(row["external_id"]).lower()
    return _pc_key(row)


# ── Rotation labels ("A Day", "Day 3", "Week 2") ──────────────────────

_AB_RE = re.compile(r"(?:\b([AB])\s*[-_ ]?\s*day\b|\bday\s*[-_ ]?\s*([AB])\b)", re.I)
_CYCLE_RE = re.compile(r"\bday\s*[-_ #]?\s*(\d{1,2})\b|\b(\d{1,2})\s*[-_ ]?\s*day\b", re.I)
_WEEK_RE = re.compile(r"\bweek\s*[-_ #]?\s*(\d)\b", re.I)


def parse_rotation_label(text: Any) -> tuple[str, int] | None:
    """``"A Day"`` → ``("ab", 1)``, ``"Day 4 Bell"`` → ``("cycle", 4)``."""
    text = str(text or "")
    if not text.strip():
        return None
    m = _AB_RE.search(text)
    if m:
        letter = (m.group(1) or m.group(2)).upper()
        return ("ab", 1 if letter == "A" else 2)
    m = _WEEK_RE.search(text)
    if m:
        n = int(m.group(1))
        return ("week", n) if 1 <= n <= 4 else None
    m = _CYCLE_RE.search(text)
    if m:
        n = int(m.group(1) or m.group(2))
        return ("cycle", n) if 1 <= n <= 10 else None
    return None


# ── StudentVUE ───────────────────────────────────────────────────────

_SOAP_RESULT_RE = re.compile(
    r"<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>", re.DOTALL
)
_TAG_RE = re.compile(r"<(ClassListing|ClassInfo|SchoolInfo|TodayScheduleInfoData)\b([^>]*?)/?>", re.S)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _studentvue_inner(text: str) -> str:
    match = _SOAP_RESULT_RE.search(text or "")
    if match:
        return html_module.unescape(match.group(1))
    return text or ""


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_studentvue_class_list(text: str) -> dict[str, Any]:
    """Parse a SOAP ``StudentClassList`` reply (envelope or inner XML).

    Returns ``{"listing": [rows], "days": [{"date", "bell", "classes"}]}``
    — the raw material :func:`build_studentvue_timetable` assembles. Falls
    back to an attribute regex when a district sends XML ElementTree will
    not parse, which does happen.
    """
    inner = _studentvue_inner(text)
    listing: list[dict] = []
    today: dict[str, Any] = {"date": None, "bell": "", "classes": []}
    try:
        root = ET.fromstring(inner)
        nodes = [(_strip_ns(el.tag), dict(el.attrib)) for el in root.iter()]
    except ET.ParseError:
        nodes = [
            (m.group(1), {k: html_module.unescape(v) for k, v in _ATTR_RE.findall(m.group(2))})
            for m in _TAG_RE.finditer(inner)
        ]
    for tag, attrs in nodes:
        if tag == "ClassListing":
            row = _row(attrs, "studentvue")
            if row:
                listing.append(row)
        elif tag == "TodayScheduleInfoData":
            today["date"] = _mdy(attrs.get("SchoolDate"))
        elif tag == "SchoolInfo":
            today["bell"] = today["bell"] or attrs.get("BellSchedName", "")
        elif tag == "ClassInfo":
            row = _row(attrs, "studentvue")
            if row:
                today["classes"].append(row)
    return {"listing": listing, "days": [today] if today["classes"] else []}


def _mdy(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt, width in (("%m/%d/%Y", 10), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:width], fmt).date()
        except ValueError:
            continue
    return None


def _walk(node: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def studentvue_json_records(data: Any, source: str = "studentvue") -> dict[str, Any]:
    """Pull class rows out of a JSON-API payload of unknown nesting.

    The JSON API's keys are the SOAP attribute names in camelCase, but the
    nesting has not been confirmed against a live district for these two
    methods, so this walks the whole payload and takes every object that
    looks like a class (a course name plus a period or a time). The bell
    schedule name and school date are picked up wherever they appear.
    """
    rows: list[dict] = []
    bell, school_date = "", None
    for record in _walk(data):
        lowered = {str(k).lower(): v for k, v in record.items()}
        if not bell and isinstance(lowered.get("bellschedname"), str):
            bell = lowered["bellschedname"]
        if school_date is None and lowered.get("schooldate"):
            school_date = _mdy(lowered.get("schooldate"))
        if not _lookup(record, _COURSE_KEYS):
            continue
        if not (_lookup(record, _PERIOD_KEYS) or _lookup(record, _START_KEYS)):
            continue
        row = _row(record, source)
        if row:
            rows.append(row)
    return {"rows": rows, "bell": bell, "date": school_date}


def build_studentvue_timetable(
    listing: Sequence[Mapping[str, Any]],
    days: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble classes, times and meeting days from a listing + day samples.

    ``days`` is ``[{"date": date | None, "bell": "A Day", "classes": [rows]}]``
    — one entry per school day we were able to see. From those:

    * times come from whichever sampled day the class met on;
    * when the bell schedule names a rotation day, each class meets on the
      rotation days it was seen on — and on an A/B schedule a class missing
      from the only sampled day is a class of the *other* day;
    * when several weekdays were sampled without rotation labels, a class's
      weekdays are the ones it was seen on.

    Returns ``{"classes", "rotation_hint", "bell"}``.
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in listing:
        merged.setdefault(_key(row), dict(row))
    for sample in days:
        for row in sample.get("classes") or []:
            target = merged.get(_key(row)) or next(
                (v for v in merged.values() if _pc_key(v) == _pc_key(row)), None
            )
            if target is None:
                merged[_key(row)] = dict(row)
                continue
            for field in ("start", "end", "room", "teacher"):
                if not target.get(field) and row.get(field):
                    target[field] = row[field]

    def met(row: Mapping[str, Any], sample: Mapping[str, Any]) -> bool:
        return any(
            _key(r) == _key(row) or _pc_key(r) == _pc_key(row)
            for r in sample.get("classes") or []
        )

    labelled = []
    for sample in days:
        parsed = parse_rotation_label(sample.get("bell"))
        if parsed and sample.get("classes"):
            labelled.append((parsed, sample))
    rotation_hint = None
    classes = list(merged.values())[:MAX_CLASSES]

    if labelled:
        kind = max({p[0] for p, _ in labelled}, key=lambda k: sum(1 for p, _ in labelled if p[0] == k))
        samples = [(p[1], s) for p, s in labelled if p[0] == kind]
        observed = {n for n, _ in samples}
        length = 2 if kind == "ab" else max(max(observed), 2)
        anchor_n, anchor_sample = next(((n, s) for n, s in samples if s.get("date")), samples[0])
        rotation_hint = {
            "kind": kind, "length": length, "today_is": anchor_n,
            "date": anchor_sample["date"].isoformat() if anchor_sample.get("date") else None,
        }
        # Every listed class met on every sampled day: the label is only the
        # bell schedule's name, not evidence of which classes rotate.
        everyone_every_day = all(met(c, s) for c in classes for _, s in samples)
        if not everyone_every_day:
            for c in classes:
                seen = sorted({n for n, s in samples if met(c, s)})
                if seen and set(seen) != set(range(1, length + 1)):
                    c["rotation_days"] = seen
                elif not seen:
                    others = sorted(set(range(1, length + 1)) - observed)
                    if kind == "ab" and others:
                        c["rotation_days"] = others
                    else:
                        c["needs_days"] = True
    else:
        school_samples = [s for s in days if s.get("date") and s.get("classes")]
        weekdays_seen = {s["date"].weekday() for s in school_samples}
        if len(weekdays_seen) >= 2:
            for c in classes:
                seen = sorted({s["date"].weekday() for s in school_samples if met(c, s)})
                if seen and set(seen) != weekdays_seen:
                    c["weekdays"] = [WEEKDAY_ABBR[d] for d in seen]

    bell: dict[str, list[str]] = {}
    for c in classes:
        if c.get("period") and c.get("start") and c.get("end"):
            bell.setdefault(str(c["period"]), [c["start"], c["end"]])
    return {"classes": classes, "rotation_hint": rotation_hint, "bell": bell}


def _next_school_days(start: date, count: int) -> list[date]:
    out, cursor = [], start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def fetch_studentvue_timetable(
    district_url: str,
    username: str,
    password: str,
    *,
    today: date | None = None,
    sample_days: int = 5,
    helper: Any = None,
) -> dict[str, Any]:
    """Timetable from StudentVUE: SOAP first, JSON API as the fallback.

    ``helper`` is the ``studentvue_helper`` module, injectable for tests.
    Raises only when neither API produced anything; the caller turns that
    into "add your classes by hand" rather than an error page.
    """
    if helper is None:
        import studentvue_helper as helper  # noqa: PLW0127 — lazy: pulls in requests

    today = today or date.today()
    soap_error: Exception | None = None
    try:
        raw = helper.make_request(
            district_url, username, password, "StudentClassList",
            "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;",
        )
        inner = _studentvue_inner(raw)
        if "RT_ERROR" not in inner:
            parsed = parse_studentvue_class_list(inner)
            if parsed["listing"] or parsed["days"]:
                return {**build_studentvue_timetable(parsed["listing"], parsed["days"]), "via": "soap"}
    except Exception as exc:  # network, HTTP, or an unparseable reply
        soap_error = exc
        logger.info("StudentVUE SOAP class list failed: %s", exc)

    json_call = getattr(helper, "json_call", None)
    json_login = getattr(helper, "json_login", None)
    if json_call is None or json_login is None:
        if soap_error:
            raise soap_error
        return {"classes": [], "rotation_hint": None, "bell": {}, "via": "none"}
    token = json_login(district_url, username, password)
    if not token:
        return {"classes": [], "rotation_hint": None, "bell": {}, "via": "json"}
    listing_payload = json_call(district_url, token, "StudentClassList", {
        "childIntID": 0, "languageCode": "en",
    })
    listing_parsed = studentvue_json_records(listing_payload)
    samples: list[dict[str, Any]] = []
    if listing_parsed["rows"] and listing_parsed["date"]:
        # The list itself may carry today's schedule, like SOAP does.
        timed = [r for r in listing_parsed["rows"] if r.get("start")]
        if timed:
            samples.append({"date": listing_parsed["date"], "bell": listing_parsed["bell"], "classes": timed})
    for day in _next_school_days(today, max(0, min(sample_days, 10))):
        if any(s.get("date") == day for s in samples):
            continue
        try:
            payload = json_call(district_url, token, "GetStudentClasesForGivenDay", {
                "childIntID": 0, "date": day.strftime("%m/%d/%Y"), "languageCode": "en",
            })
        except Exception as exc:
            logger.info("StudentVUE day sample %s failed: %s", day, exc)
            break
        parsed = studentvue_json_records(payload)
        if parsed["rows"]:
            samples.append({"date": parsed["date"] or day, "bell": parsed["bell"], "classes": parsed["rows"]})
    return {**build_studentvue_timetable(listing_parsed["rows"], samples), "via": "json"}


# ── Schoology ────────────────────────────────────────────────────────

_PERIOD_IN_TITLE = re.compile(r"(?:\bper(?:iod)?\.?\s*(\d{1,2}[A-Z]?)\b|\b(\d{1,2})(?:st|nd|rd|th)\s+period\b|\bP\s*(\d{1,2})\b)", re.I)


def _schoology_weekdays(value: Any) -> list[str]:
    """Schoology sends ``meeting_days`` as day numbers with Sunday = 0
    (unconfirmed against a live school) or as names. Monday-based out."""
    if not value:
        return []
    items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value))
    numeric = [str(v).strip() for v in items if str(v).strip().isdigit()]
    if numeric and len(numeric) == len([v for v in items if str(v).strip()]):
        days = sorted({(int(v) - 1) % 7 for v in numeric if 0 <= int(v) <= 6})
        return [WEEKDAY_ABBR[d] for d in days]
    return [WEEKDAY_ABBR[d] for d in parse_weekdays(items)]


def map_schoology_sections(sections: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Schoology ``/sections`` objects → timetable rows."""
    out: list[dict[str, Any]] = []
    for section in sections or []:
        if not isinstance(section, Mapping):
            continue
        course = str(section.get("course_title") or section.get("section_title") or "").strip()[:256]
        if not course:
            continue
        title = str(section.get("section_title") or "")
        period = ""
        m = _PERIOD_IN_TITLE.search(title)
        if m:
            period = next(g for g in m.groups() if g)
        elif section.get("class_periods"):
            cp = section["class_periods"]
            period = str(cp[0] if isinstance(cp, list) and cp else cp)[:32]
        start, end = _clock_text(section.get("start_time")), _clock_text(section.get("end_time"))
        if start and end and parse_clock(end) <= parse_clock(start):
            start = end = ""
        out.append({
            "course": course,
            "period": period,
            "room": str(section.get("location") or "").strip()[:64],
            "teacher": "",
            "start": start,
            "end": end,
            "weekdays": _schoology_weekdays(section.get("meeting_days")),
            "rotation_days": [],
            "source": "schoology",
            "external_id": str(section.get("id") or "")[:64],
            "needs_days": False,
        })
    return out[:MAX_CLASSES]


def fetch_schoology_timetable(key: str, secret: str, *, helper: Any = None) -> dict[str, Any]:
    if helper is None:
        import schoology_helper as helper  # noqa: PLW0127
    data = helper.make_schoology_request(key, secret, "/sections") or {}
    sections = data.get("section", []) if isinstance(data, Mapping) else []
    classes = map_schoology_sections(sections)
    bell: dict[str, list[str]] = {}
    for c in classes:
        if c["period"] and c["start"] and c["end"]:
            bell.setdefault(c["period"], [c["start"], c["end"]])
    return {"classes": classes, "rotation_hint": None, "bell": bell, "via": "schoology"}


# ── Photo of a printed schedule ──────────────────────────────────────

PHOTO_PROMPT = (
    "This is a photo of a student's class schedule. Return ONLY JSON, no prose, "
    "in this shape: {\"rotation\": {\"kind\": \"none\"|\"ab\"|\"cycle\"|\"week\", "
    "\"length\": number}, \"classes\": [{\"course\": str, \"period\": str, \"room\": str, "
    "\"teacher\": str, \"start\": \"HH:MM\" 24-hour, \"end\": \"HH:MM\" 24-hour, "
    "\"days\": [weekday names like \"Mon\"], \"rotation_days\": [\"A\"|\"B\"|day numbers]}]}. "
    "Use empty strings or empty lists for anything not shown. Do not invent classes."
)

_JSON_RE = re.compile(r"(\{.*\}|\[.*\])", re.S)


def parse_photo_timetable(text: str) -> dict[str, Any]:
    """Vision-model output → ``{"classes", "rotation_hint"}``. Never raises."""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\s*|\s*```$", "", raw, flags=re.I)
    data: Any = None
    for candidate in (raw, *(m.group(1) for m in [_JSON_RE.search(raw)] if m)):
        try:
            data = json.loads(candidate)
            break
        except (ValueError, TypeError):
            continue
    if isinstance(data, list):
        data = {"classes": data}
    if not isinstance(data, Mapping):
        return {"classes": [], "rotation_hint": None}
    classes = []
    for item in data.get("classes") or []:
        if not isinstance(item, Mapping):
            continue
        row = _row(item, "photo")
        if not row:
            continue
        row["weekdays"] = [WEEKDAY_ABBR[d] for d in parse_weekdays(item.get("days") or item.get("weekdays"))]
        row["rotation_days"] = list(parse_int_list(item.get("rotation_days")))
        classes.append(row)
    hint = None
    rotation = data.get("rotation")
    if isinstance(rotation, Mapping) and rotation.get("kind") in ("ab", "cycle", "week"):
        try:
            length = int(rotation.get("length") or 2)
        except (TypeError, ValueError):
            length = 2
        hint = {"kind": rotation["kind"], "length": length, "today_is": None, "date": None}
    return {"classes": classes[:MAX_CLASSES], "rotation_hint": hint}
