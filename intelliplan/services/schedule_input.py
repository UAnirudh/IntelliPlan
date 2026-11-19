"""Validate the scheduler's JSON envelope before reading student calendars."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import time, timedelta
from typing import Any


def validate_study_rules(body: Any) -> dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError('Study rules must be an object.')
    rules = {}
    for key, low, high, label in (
        ('max_session_minutes', 15, 90, 'Longest study session'),
        ('calendar_buffer_minutes', 0, 30, 'Calendar buffer'),
    ):
        value = body.get(key)
        if value is None:
            continue
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{label} must be a whole number from {low} to {high} minutes.')
        if value:
            rules[key] = value
    start, end = (body.get(key) for key in ('study_start_time', 'study_end_time'))
    clocks = (start, end)
    if any(clock is not None and not isinstance(clock, str) for clock in clocks):
        raise ValueError('Study times must use a valid clock time.')
    start, end = ((clock or '').strip() for clock in clocks)
    if bool(start) != bool(end):
        raise ValueError('Choose both a study start and end time.')
    if start:
        if any(not re.fullmatch(r'(?:[01]?[0-9]|2[0-3]):[0-5]?[0-9]', clock) for clock in (start, end)):
            raise ValueError('Study times must use a valid clock time.')
        start, end = (':'.join(part.zfill(2) for part in clock.split(':')) for clock in (start, end))
        if time.fromisoformat(end) <= time.fromisoformat(start):
            raise ValueError('Your study end time must be after the start time.')
        rules.update(study_start_time=start, study_end_time=end)
    return rules


def apply_study_rules(rules, availability, busy_by_date):
    if 'study_start_time' in rules:
        from scheduler_engine import DAY_ABBR

        availability = {
            day: {'start': rules['study_start_time'], 'end': rules['study_end_time']}
            for day in DAY_ABBR
        }
    buffer = rules.get('calendar_buffer_minutes', 0)
    if buffer:
        padded = defaultdict(list)
        one_day = timedelta(days=1)
        for day, intervals in busy_by_date.items():
            for start, end in intervals:
                padded_start, padded_end = start - buffer, end + buffer
                padded[day].append((max(0, padded_start), min(1440, padded_end)))
                if padded_start < 0:
                    padded[day - one_day].append((1440 + padded_start, 1440))
                if padded_end > 1440:
                    padded[day + one_day].append((0, padded_end - 1440))
        busy_by_date = dict(padded)
    return availability, busy_by_date


def validate_saved_schedule(body: Any) -> dict[str, Any]:
    """Check the stored plan envelope before retiring an existing plan."""
    if not isinstance(body, dict):
        raise ValueError('Send a JSON object containing a schedule.')
    plan = body.get('schedule_data')
    if not isinstance(plan, dict) or not isinstance(plan.get('schedule'), list):
        raise ValueError('Send a schedule containing a list of days.')
    validate_study_rules(plan.get('study_rules'))
    for day in plan['schedule']:
        if not isinstance(day, dict) or not isinstance(day.get('blocks', []), list):
            raise ValueError('Each schedule day must contain a list of blocks.')
        if any(not isinstance(block, dict) for block in day.get('blocks', [])):
            raise ValueError('Schedule blocks must be objects.')
    name = body.get('name')
    if 'name' in body and (not isinstance(name, str) or not name.strip() or len(name) > 256):
        raise ValueError('Schedule name must contain 1 to 256 characters.')
    return body


def validate_schedule_request(body: Any) -> dict[str, Any]:
    """Return a copy with normalized hours; leave provider metadata intact.

    Custom tasks support both original free-text titles and clarified task
    dictionaries. Numeric strings remain compatible with older form clients.
    """
    if not isinstance(body, dict):
        raise ValueError('Send a JSON object containing assignments or custom tasks.')
    assignments = body.get('assignments', [])
    if not isinstance(assignments, list) or any(not isinstance(row, dict) for row in assignments):
        raise ValueError('Assignments must be a list of task objects.')
    custom_tasks = body.get('custom_tasks', [])
    if not isinstance(custom_tasks, list) or any(not isinstance(row, (str, dict)) for row in custom_tasks):
        raise ValueError('Custom tasks must be a list of titles or task objects.')
    hours = body.get('hours_per_day', 2)
    if isinstance(hours, bool) or not isinstance(hours, (int, float, str)):
        raise ValueError('Study hours per day must be a number greater than 0 and at most 24.')
    try:
        hours = float(hours)
    except (ValueError, OverflowError):
        raise ValueError('Study hours per day must be a number greater than 0 and at most 24.') from None
    if not math.isfinite(hours) or not 0 < hours <= 24:
        raise ValueError('Study hours per day must be a number greater than 0 and at most 24.')
    return {**body, 'assignments': assignments, 'custom_tasks': custom_tasks, 'hours_per_day': hours}
