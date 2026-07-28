"""Ask before planning, when the request is too vague to plan well.

A task called "Study" with no subject and no goal can only ever produce a
block that says "Study" back at the student. No amount of prompt engineering
fixes that — the information genuinely isn't there. So instead of guessing,
the scheduler asks two or three concrete questions first.

Because students re-add the same vague task constantly ("Study", every week),
answers are keyed by a normalized form of the title so they can be saved as a
preset and offered back next time.

Pure module: no Flask, no DB, no network. Callers pass dicts in and get dicts
out, so the whole thing is unit-testable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta
from typing import Any, Iterable, Mapping, Sequence

# Titles that name an activity but not a piece of work. "Study" is a verb, not
# a deliverable — there is nothing here to plan against.
GENERIC_TITLES: frozenset[str] = frozenset({
    "study", "studying", "study time", "homework", "hw", "work", "schoolwork",
    "school work", "read", "reading", "review", "revise", "revision",
    "practice", "project", "assignment", "assignments", "notes", "note taking",
    "prep", "prepare", "prepping", "catch up", "catchup", "stuff", "things",
    "tasks", "task", "work on stuff", "do work", "learn", "learning",
    "revise notes", "general", "misc", "test prep", "exam prep",
})

# Course values that carry no information.
#: Compared against normalize() output, so entries here are already
#: punctuation-stripped — "N/A" arrives as "n a", not "n/a".
GENERIC_COURSES: frozenset[str] = frozenset({
    "", "general", "general academics", "academics", "school", "misc",
    "miscellaneous", "other", "personal", "n a", "na", "none", "unknown",
    "general studies",
})

#: Never interrogate. Three questions is already a lot to answer before
#: getting a plan.
MAX_QUESTIONS = 3

#: Below this many characters a title can't be describing real work.
MIN_MEANINGFUL_TITLE_WORDS = 2

_DURATION_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?)?", re.I)


def normalize(text: Any) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def preset_key(title: Any) -> str:
    """Stable key for a task title, so "Study " and "study" share a preset."""
    return normalize(title)[:80]


def is_generic_title(title: Any) -> bool:
    n = normalize(title)
    if not n:
        return True
    if n in GENERIC_TITLES:
        return True
    # "study math" is fine; a bare verb with nothing after it is not.
    words = n.split()
    if len(words) < MIN_MEANINGFUL_TITLE_WORDS and words[0] in GENERIC_TITLES:
        return True
    return False


def is_generic_course(course: Any) -> bool:
    return normalize(course) in GENERIC_COURSES


@dataclass(frozen=True)
class Question:
    """One thing we need to know before a plan is worth making."""

    id: str
    task: str
    field: str
    question: str
    why: str
    options: tuple[str, ...] = ()
    placeholder: str = ""
    allow_free_text: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "task": self.task, "field": self.field,
            "question": self.question, "why": self.why,
            "options": list(self.options), "placeholder": self.placeholder,
            "allow_free_text": self.allow_free_text,
        }


def _q(key_title: str, display: str, fld: str, question: str, why: str,
       options: Iterable[str] = (), placeholder: str = "") -> Question:
    """Build a question.

    ``key_title`` anchors the id and must be the title the student originally
    typed — once a "deliverable" answer renames the task, keying off the new
    title would mint a fresh id and orphan every answer already given.
    ``display`` is what the student reads.
    """
    return Question(
        id=f"{preset_key(key_title)}::{fld}", task=str(key_title), field=fld,
        question=question, why=why,
        options=tuple(dict.fromkeys(o for o in options if o))[:6],
        placeholder=placeholder,
    )


ANSWERED_FLAG = "_answered_fields"


def _task_issues(task: Mapping[str, Any], known_courses: Sequence[str]) -> list[Question]:
    """Questions this one task raises, most valuable first."""
    title = str(task.get("title") or task.get("assignment") or "").strip()
    # Survives the rename that a "deliverable" answer performs.
    key_title = str(task.get("original_title") or title).strip()
    course = task.get("course") or ""
    out: list[Question] = []
    # Anything already answered is never asked again, whatever the answer was.
    # Without this a reply we can't act on ("no deadline", or a deliverable
    # that is itself vague) would re-raise the same question forever.
    answered = set(task.get(ANSWERED_FLAG) or ())

    if "deliverable" not in answered and is_generic_title(title):
        out.append(_q(
            key_title, title, "deliverable",
            f'What do you actually need to finish for "{title}"?',
            "A block labelled “study” can't tell you what to open or when you're done.",
            placeholder="e.g. finish the ch. 7 problem set, redo the quiz I failed",
        ))
    if "subject" not in answered and is_generic_course(course):
        out.append(_q(
            key_title, title, "subject",
            f'Which class is "{title}" for?',
            "Lets the plan weight it against your other coursework.",
            options=known_courses,
            placeholder="e.g. AP Calculus",
        ))
    try:
        est = int(task.get("estimated_time") or 0)
    except (TypeError, ValueError):
        est = 0
    if "duration" not in answered and est <= 0:
        out.append(_q(
            key_title, title, "duration",
            f'Roughly how long will "{title}" take?',
            "Sizing the block wrong is the fastest way to fall behind the plan.",
            options=("30 min", "45 min", "1 hour", "1.5 hours", "2 hours"),
            placeholder="e.g. 45 min",
        ))
    if "deadline" not in answered and not str(task.get("due_date") or "").strip():
        out.append(_q(
            key_title, title, "deadline",
            f'When does "{title}" need to be done?',
            "Without a deadline it can't be ordered against everything else.",
            options=("Today", "Tomorrow", "This week", "Next week", "No deadline"),
            placeholder="e.g. Friday, or 2026-08-03",
        ))
    return out


def assess_tasks(
    tasks: Sequence[Mapping[str, Any]],
    known_courses: Sequence[str] = (),
    max_questions: int = MAX_QUESTIONS,
) -> list[Question]:
    """Return the questions worth asking before planning ``tasks``.

    Empty means the request is specific enough to plan directly — the common
    case for LMS-synced assignments, which arrive with title, course, and due
    date already filled in.

    Questions are spread across tasks before stacking on one, so a student
    with three vague tasks is asked about all three rather than interrogated
    about the first.
    """
    if not tasks:
        return []
    courses = [c for c in dict.fromkeys(
        str(t.get("course") or "").strip() for t in tasks if t.get("course")
    ) if c and not is_generic_course(c)]
    courses = list(dict.fromkeys([*known_courses, *courses]))

    per_task = [(t, _task_issues(t, courses)) for t in tasks]
    # Only the genuinely underspecified tasks are worth asking about; a task
    # missing nothing contributes nothing.
    per_task = [(t, qs) for t, qs in per_task if qs]
    if not per_task:
        return []

    picked: list[Question] = []
    seen: set[str] = set()
    round_idx = 0
    while len(picked) < max_questions:
        added = False
        for _, qs in per_task:
            if round_idx < len(qs):
                q = qs[round_idx]
                if q.id not in seen:
                    picked.append(q)
                    seen.add(q.id)
                    added = True
                if len(picked) >= max_questions:
                    break
        if not added:
            break
        round_idx += 1
    return picked[:max_questions]


def parse_duration_answer(text: Any, default: int = 60) -> int:
    """Read "45 min", "1.5 hours", "2h", or a bare "90" into minutes."""
    m = _DURATION_RE.search(str(text or ""))
    if not m:
        return default
    try:
        num = float(m.group("num"))
    except (TypeError, ValueError):
        return default
    unit = (m.group("unit") or "").lower()
    minutes = num * 60 if unit.startswith("h") else num
    # A bare number under 10 is far more likely to mean hours than minutes.
    if not unit and num < 10:
        minutes = num * 60
    return max(5, min(600, int(round(minutes))))


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday")


def parse_deadline_answer(text: Any, today: _date | None = None) -> str:
    """Resolve a deadline answer to YYYY-MM-DD, or "" if there isn't one.

    Accepts an explicit date, the relative options we offer, or a weekday
    name. "No deadline" resolves to "" — a legitimate answer, not a failure.
    """
    s = normalize(text)
    if not s:
        return ""
    today = today or _date.today()
    m = re.search(r"(\d{4})[-/ ](\d{1,2})[-/ ](\d{1,2})", str(text))
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return ""
    if "no deadline" in s or s in {"none", "no", "n a", "whenever"}:
        return ""
    if "today" in s or "tonight" in s:
        return today.isoformat()
    if "tomorrow" in s:
        return (today + timedelta(days=1)).isoformat()
    if "next week" in s:
        return (today + timedelta(days=(4 - today.weekday()) % 7 + 7)).isoformat()
    if "this week" in s or "end of week" in s:
        return (today + timedelta(days=(4 - today.weekday()) % 7)).isoformat()
    for i, name in enumerate(_WEEKDAYS):
        if name in s or (len(s) == 3 and name.startswith(s)):
            delta = (i - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
    return ""


def apply_answers(
    tasks: Sequence[Mapping[str, Any]],
    answers: Mapping[str, Any],
    today: _date | None = None,
) -> list[dict[str, Any]]:
    """Fold clarification answers back into the task list.

    Returns new dicts — the caller's input is never mutated. Unanswered
    questions leave their field alone, so a partial answer set still helps.

    Every answered field is recorded under ANSWERED_FLAG so assess_tasks()
    never asks it twice, even when the answer couldn't be turned into usable
    data ("no deadline", or a deliverable that is itself vague).
    """
    if not answers:
        return [dict(t) for t in tasks]
    out: list[dict[str, Any]] = []
    for task in tasks:
        t = dict(task)
        title = str(t.get("original_title") or t.get("title") or t.get("assignment") or "")
        key = preset_key(title)
        answered = set(t.get(ANSWERED_FLAG) or ())

        def answer(fld: str) -> str:
            return str(answers.get(f"{key}::{fld}") or "").strip()

        deliverable = answer("deliverable")
        if deliverable:
            # Keep the original title recognizable, but make the block say
            # what the student actually has to do.
            t["title"] = deliverable[:200]
            t["original_title"] = title
            answered.add("deliverable")
        subject = answer("subject")
        if subject:
            t["course"] = subject[:100]
            answered.add("subject")
        duration = answer("duration")
        if duration:
            t["estimated_time"] = parse_duration_answer(duration, int(t.get("estimated_time") or 60))
            answered.add("duration")
        deadline = answer("deadline")
        if deadline:
            resolved = parse_deadline_answer(deadline, today)
            if resolved:
                t["due_date"] = resolved
            t["due_hint"] = deadline[:60]
            answered.add("deadline")
        if answered:
            t[ANSWERED_FLAG] = sorted(answered)
        out.append(t)
    return out


def split_answer_id(answer_id: Any) -> tuple[str, str]:
    """Split a question id back into ``(task_key, field)``.

    Lets answers be persisted without re-deriving the Question objects that
    produced them, so saving doesn't depend on the assessment returning the
    same set twice.
    """
    parts = str(answer_id or "").split("::", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


def preset_payload_from_answers(
    answers: Mapping[str, Any],
    labels: Mapping[str, str] | None = None,
) -> dict[str, dict]:
    """Group a flat answer map into per-task presets, using ids alone.

    ``labels`` optionally maps task_key -> the title as the student typed it,
    purely so the saved preset reads nicely when offered back.
    """
    grouped: dict[str, dict] = {}
    for aid, val in (answers or {}).items():
        text = str(val or "").strip()
        if not text:
            continue
        key, fld = split_answer_id(aid)
        if not key or not fld:
            continue
        entry = grouped.setdefault(key, {"label": (labels or {}).get(key, key), "answers": {}})
        entry["answers"][fld] = text
    return grouped


def labels_for(tasks: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """task_key -> original title, for readable preset labels."""
    out: dict[str, str] = {}
    for t in tasks:
        title = str(t.get("original_title") or t.get("title") or t.get("assignment") or "").strip()
        if title:
            out.setdefault(preset_key(title), title)
    return out


def preset_payload(questions: Sequence[Question], answers: Mapping[str, Any]) -> dict[str, dict]:
    """Group answers by task key, ready to persist as reusable presets.

    Shape: ``{task_key: {"label": <title>, "answers": {field: value}}}``.
    """
    grouped: dict[str, dict] = {}
    for q in questions:
        val = str(answers.get(q.id) or "").strip()
        if not val:
            continue
        key = preset_key(q.task)
        entry = grouped.setdefault(key, {"label": q.task, "answers": {}})
        entry["answers"][q.field] = val
    return grouped


def answers_from_presets(
    tasks: Sequence[Mapping[str, Any]],
    presets: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Expand saved presets into the flat answer map apply_answers() wants.

    Only presets matching a task actually in this request are used.
    """
    out: dict[str, str] = {}
    if not presets:
        return out
    keys = {preset_key(t.get("title") or t.get("assignment") or "") for t in tasks}
    for key, entry in presets.items():
        if key not in keys:
            continue
        for fld, val in (entry.get("answers") or {}).items():
            if val:
                out[f"{key}::{fld}"] = str(val)
    return out
