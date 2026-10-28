"""Validate the scheduler's JSON envelope before reading student calendars."""
from __future__ import annotations

import math
from typing import Any


def validate_saved_schedule(body: Any) -> dict[str, Any]:
    """Check the stored plan envelope before retiring an existing plan."""
    if not isinstance(body, dict):
        raise ValueError('Send a JSON object containing a schedule.')
    plan = body.get('schedule_data')
    if not isinstance(plan, dict) or not isinstance(plan.get('schedule'), list):
        raise ValueError('Send a schedule containing a list of days.')
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
