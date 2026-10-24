"""Interpret individual submission evidence without guessing from an absent grade.

These functions do not contact providers or change assignment deadlines. A class-wide
submission count, a blank grade, and StudentVUE's 'Not Graded' are not evidence that
this student turned work in. Explicit requests to redo work override older evidence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


_ACTION_STATES = {'unsubmitted', 'reopened', 'reassigned', 'redo', 'missing', 'returned_for_revision'}
_WAITING_STATES = {'submitted', 'pending_review', 'turned_in', 'awaiting_grade', 'student_reported'}
_DONE_STATES = {'graded', 'completed', 'excused'}


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        return True
    except ValueError:
        return False


def submission_facts(record: dict) -> dict:
    """Return a small normalized state that can travel with an LMS task dict."""
    if not isinstance(record, dict):
        record = {}
    nested = record.get('submission')
    evidence = nested if isinstance(nested, dict) else record
    state = str(evidence.get('workflow_state') or evidence.get('submission_status') or '').lower()
    # Some feeds normalize submission metadata before the repository sees it.
    normalized = str(record.get('submission_status') or '').lower()
    redo = any(evidence.get(key) is True for key in ('redo_request', 'reopened', 'requires_resubmission'))
    redo = redo or any(record.get(key) is True for key in ('redo_request', 'reopened', 'requires_resubmission'))
    reopened = redo or state in {'reopened', 'reassigned', 'redo', 'returned_for_revision'}
    if reopened:
        status = 'needs_action'
    elif normalized == 'student_reported' and record.get('student_submission_override') is True:
        status = 'student_reported'
    elif state in _ACTION_STATES:
        status = 'needs_action'
    elif evidence.get('excused') is True or state == 'excused':
        status = 'excused'
    elif state in _DONE_STATES or record.get('done') is True:
        status = 'graded' if state == 'graded' else 'completed'
    elif state in _WAITING_STATES or _timestamp(evidence.get('submitted_at')):
        status = 'student_reported' if state == 'student_reported' else 'awaiting_grade'
    elif normalized in _WAITING_STATES | _DONE_STATES:
        status = ('student_reported' if normalized == 'student_reported' else
                  'awaiting_grade' if normalized in _WAITING_STATES else normalized)
    else:
        status = 'unknown'
    waiting = status in {'awaiting_grade', 'student_reported'}
    labels = {
        'awaiting_grade': 'Submitted · waiting for grade',
        'student_reported': 'You marked submitted · waiting for grade',
        'graded': 'Graded', 'completed': 'Completed', 'excused': 'Excused',
        'needs_action': 'Needs your attention', 'unknown': 'Submission not confirmed',
    }
    return {
        'submission_status': status,
        'requires_action': status in {'needs_action', 'unknown'},
        'awaiting_grade': waiting,
        'submission_verified': status in {'awaiting_grade', 'graded', 'completed', 'excused'},
        'status_label': labels[status],
        'submission_reopened': reopened,
    }


def annotate_assignment(record: dict) -> dict:
    """Copy a task and attach normalized submission facts; preserve its due date."""
    return {**record, **submission_facts(record)}


def requires_student_action(record: dict) -> bool:
    """Whether new homework time or an overdue action is appropriate for this row."""
    return submission_facts(record)['requires_action']
