"""Student-local test prep discovery. No calendar writes or model calls."""

import re
from collections.abc import Mapping
from datetime import date, datetime, timezone

from intelliplan.repositories.assignments import parse_due_date, stable_id
from time_utils import to_local

PRACTICE_MINUTES = 20
_ASSESSMENT = re.compile(r"\b(?:exams?|tests?|quiz(?:zes)?|midterms?|finals?)\b", re.I)
_NOT_ASSESSMENT = re.compile(
    r"^\s*(?:study|review|prepare|prep)\s*:|"
    r"\b(?:practice|mock|study for|review for|prepare for)\s+(?:the\s+)?"
    r"(?:\w+\s+){0,4}(?:exam|test|quiz|midterm|final)\b|"
    r"\b(?:exam|test|quiz|midterm|final)\s+(?:review|prep|practice|study|corrections|"
    r"results|scores?|schedule)\b|"
    r"\b(?:final\s+(?:draft|project|essay|presentation|paper)|blood test|"
    r"eye exam|driving test|drug test|test drive|software test)\b",
    re.I,
)


def _text(value, limit=180):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def _assessment(title, kind=''):
    return bool(title and not _NOT_ASSESSMENT.search(title)
                and (kind in ('exam', 'test', 'quiz') or _ASSESSMENT.search(title)))


def _calendar_date(raw, tz_name, offset):
    if not isinstance(raw, str):
        return None
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw)
        at = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if at.tzinfo:
            at = to_local(at.astimezone(timezone.utc).replace(tzinfo=None), tz_name, offset)
        return at.date()
    except (TypeError, ValueError, OverflowError):
        return None


def prep_candidates(today, *, tasks=(), plan=None, events=(), tz_name='', offset=0):
    """Combine upcoming assessments; show reminders only two or three days ahead.

    Late-added tests are still available from Tutor until their date passes.
    Titles and courses are the only calendar/task text surfaced; no descriptions,
    attendees, or provider identifiers are included.
    """
    result = []
    by_key = {}
    tasks = tuple(tasks)
    completed = {
        (_text(task.title).casefold(), parse_due_date(task.due_date))
        for task in tasks if task.done
    }

    def add(title, course, due, source, kind=''):
        title, course = _text(title), _text(course, 100)
        course = '' if course.casefold() in ('personal', 'class') else course
        if not due or not _assessment(title, kind):
            return
        days = (due - today).days
        if not 0 <= days <= 3:
            return
        key = (title.casefold(), due)
        if source == 'plan' and key in completed:
            return
        courses = by_key.setdefault(key, set())
        course_key = course.casefold()
        if course_key in courses or '' in courses or (not course_key and courses):
            return
        courses.add(course_key)
        result.append({
            'id': stable_id('prep', title.casefold(), course_key, due),
            'title': title,
            'course': course,
            'date': due.isoformat(),
            'days_away': days,
            'minutes': PRACTICE_MINUTES,
            'suggest_today': days in (2, 3),
        })

    for task in sorted(tasks, key=lambda row: (not _text(row.course)
                                               or _text(row.course).casefold() in ('personal', 'class'))):
        if not task.done:
            add(task.title, task.course, parse_due_date(task.due_date), 'task')

    plan = plan or {}
    for schedule_day in plan.get('schedule') or ():
        if not isinstance(schedule_day, Mapping):
            continue
        for block in schedule_day.get('blocks') or ():
            if isinstance(block, Mapping) and block.get('kind') in ('exam', 'test', 'quiz'):
                add(block.get('parent_title') or block.get('assignment'), block.get('course'),
                    parse_due_date(block.get('due_date')), 'plan', block.get('kind'))
    for deferred in plan.get('deferred') or ():
        if isinstance(deferred, Mapping) and deferred.get('kind') in ('exam', 'test', 'quiz'):
            add(deferred.get('title'), deferred.get('course'), parse_due_date(deferred.get('due_date')),
                'plan', deferred.get('kind'))

    for event in events:
        if not isinstance(event, Mapping) or event.get('status') == 'cancelled':
            continue
        add(event.get('title'), '', _calendar_date(event.get('start'), tz_name, offset), 'calendar')
    return sorted(result, key=lambda item: (item['date'], item['title'].casefold(), item['course']))[:20]
