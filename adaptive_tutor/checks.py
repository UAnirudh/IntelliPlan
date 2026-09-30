"""Account-owned, server-scored tutor checks using the Foundations item bank.

Only item IDs and outcomes are retained. AI recaps and raw answers never enter
this evidence path, and historic inferred mastery rows remain excluded.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any

from flask import current_app
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import (Boolean, Column, DateTime, Integer, MetaData, String,
                        Table, UniqueConstraint, and_, case, func, select)
from sqlalchemy.exc import IntegrityError

from db_boot import schema_lock
from primer.advanced import advanced_item_count, advanced_item_for_skill
from primer.catalog import SKILL_BY_ID, get_item, grade_item, public_item
from primer.generated import generated_item_count, item_for_skill
from time_utils import utcnow


_META = MetaData()
ATTEMPT = Table(
    'tutor_scored_check', _META,
    Column('id', Integer, primary_key=True),
    Column('owner_id', Integer, nullable=False, index=True),
    Column('nonce', String(40), nullable=False, unique=True),
    Column('grade', Integer, nullable=False),
    Column('skill_id', String(32), nullable=False),
    Column('sequence', Integer, nullable=False),
    Column('item_id', String(32), nullable=False),
    Column('correct', Boolean, nullable=False),
    Column('assisted', Boolean, nullable=False, default=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('owner_id', 'skill_id', 'sequence', name='uq_tutor_check_sequence'),
)
HINT = Table(
    'tutor_check_hint', _META,
    Column('id', Integer, primary_key=True),
    Column('owner_id', Integer, nullable=False, index=True),
    Column('nonce', String(40), nullable=False, unique=True),
    Column('skill_id', String(32), nullable=False),
    Column('sequence', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('owner_id', 'skill_id', 'sequence', name='uq_tutor_hint_sequence'),
)
DOMAINS = {'math': 'Arithmetic', 'reading': 'Reading', 'writing': 'Writing'}


def _db():
    return current_app.extensions['sqlalchemy']


def ensure_tables():
    if not current_app.extensions.get('tutor_checks_ready'):
        with schema_lock(_db().engine):
            _META.create_all(_db().engine)
        current_app.extensions['tutor_checks_ready'] = True


def parse_grade(value: Any) -> int | None:
    """Map an explicit profile grade to the bank's K-12 / college levels."""
    text = str(value or '').strip().lower()
    if text in ('k', 'kg', 'kindergarten'):
        return 0
    if any(word in text for word in ('college', 'university', 'undergraduate')):
        return 13
    match = re.fullmatch(r'(?:grade\s*)?(1[0-2]|[1-9])(?:st|nd|rd|th)?(?:\s*grade)?', text)
    return int(match.group(1)) if match else None


def selected_grade(owner_id: int) -> int | None:
    from adaptive_tutor.store import get_or_create_profile
    profile_grade = get_or_create_profile().get('grade_level')
    if profile_grade:
        return parse_grade(profile_grade)
    from App import UserIdentity
    identity = UserIdentity.query.filter_by(user_id=owner_id).first()
    return parse_grade(identity.grade_level if identity else None)


def signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='tutor-scored-check-v1')


def _skill_ids(grade: int, domain: str) -> list[str]:
    return [skill.id for skill in SKILL_BY_ID.values()
            if skill.grade == grade and skill.domain == domain and skill.id.startswith('g')]


def _counts(owner_id: int) -> dict[str, dict[str, int]]:
    ensure_tables()
    rows = _db().session.execute(
        select(ATTEMPT.c.skill_id,
               func.count().label('attempts'),
               func.sum(case((ATTEMPT.c.correct.is_(True), 1), else_=0)).label('correct'),
               func.sum(case((and_(ATTEMPT.c.correct.is_(True),
                                   ATTEMPT.c.assisted.is_(False)), 1), else_=0)).label('independent_correct'))
        .where(ATTEMPT.c.owner_id == owner_id)
        .group_by(ATTEMPT.c.skill_id)
    ).mappings().all()
    return {row['skill_id']: {key: int(row[key] or 0) for key in
            ('attempts', 'correct', 'independent_correct')} for row in rows}


def _recommendation(owner_id: int, grade: int, domain_key: str,
                    counts: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Select a repair, due review, new skill, or the next scheduled review."""
    domain = DOMAINS[domain_key]
    relevant_skills = _skill_ids(grade, domain) + (_skill_ids(grade - 1, domain) if grade else [])
    latest = _db().session.execute(
        select(ATTEMPT).where(ATTEMPT.c.owner_id == owner_id)
        .where(ATTEMPT.c.grade.in_((grade, max(0, grade - 1))))
        .where(ATTEMPT.c.skill_id.in_(relevant_skills))
        .order_by(ATTEMPT.c.id.desc()).limit(2)
    ).mappings().all()
    candidates = _skill_ids(grade, domain)
    if not candidates:
        raise ValueError('No check is available for this grade and area.')
    if latest and latest[0]['grade'] == grade and not latest[0]['correct']:
        same_wrong_twice = (len(latest) > 1 and not latest[1]['correct']
                            and latest[1]['skill_id'] == latest[0]['skill_id'])
        if same_wrong_twice and grade > 0:
            candidates = _skill_ids(grade - 1, domain)
            mode = 'bridge'
            reason = 'Two checks on this skill were missed. Try a foundation step.'
        else:
            candidates = [latest[0]['skill_id']]
            mode = 'repair'
            reason = 'Try another form of the skill you just missed.'
        skill_id = min(candidates, key=lambda item: (
            counts.get(item, {}).get('attempts', 0), candidates.index(item)))
        return {'skill_id': skill_id, 'mode': mode, 'reason': reason,
                'review_due_at': None}
    if latest and latest[0]['grade'] == grade and latest[0]['assisted']:
        return {'skill_id': latest[0]['skill_id'], 'mode': 'independent',
                'reason': 'Try this skill again without the hint.', 'review_due_at': None}

    now = utcnow()
    due = []
    fresh = []
    future = []
    for index, skill_id in enumerate(candidates):
        attempts = _db().session.execute(
            select(ATTEMPT.c.correct, ATTEMPT.c.assisted, ATTEMPT.c.created_at)
            .where(ATTEMPT.c.owner_id == owner_id, ATTEMPT.c.skill_id == skill_id)
            .order_by(ATTEMPT.c.id.desc()).limit(4)
        ).mappings().all()
        if not attempts:
            fresh.append((index, skill_id))
            continue
        # Repeating a correct answer immediately does not advance the review
        # interval. Only a later independent answer can lengthen the cadence.
        streak = 0
        previous_at = None
        intervals = (0, 1, 3, 7, 14)
        for attempt in reversed(attempts):
            if not attempt['correct'] or attempt['assisted']:
                streak = 0
                previous_at = None
                continue
            if (streak and previous_at and attempt['created_at'] - previous_at
                    >= timedelta(days=intervals[min(streak, 4)])):
                streak = min(streak + 1, 4)
            else:
                streak = 1
            previous_at = attempt['created_at']
        # This is a simple practice cadence, not a measured memory model.
        interval = intervals[streak]
        due_at = attempts[0]['created_at'] + timedelta(days=interval)
        entry = (due_at, index, skill_id)
        (due if due_at <= now else future).append(entry)

    if due:
        review_at, _, skill_id = min(due)
        return {'skill_id': skill_id, 'mode': 'review',
                'reason': 'It is time to try this skill again from memory.',
                'review_due_at': review_at.isoformat()}
    if fresh:
        _, skill_id = min(fresh)
        return {'skill_id': skill_id, 'mode': 'diagnostic',
                'reason': 'Start with a small check on a new grade-level skill.',
                'review_due_at': None}
    review_at, _, skill_id = min(future)
    return {'skill_id': skill_id, 'mode': 'practice',
            'reason': 'All practiced skills are scheduled for later review. You can practice one now.',
            'review_due_at': review_at.isoformat()}


def practice_path(owner_id: int, grade: int) -> list[dict[str, Any]]:
    counts = _counts(owner_id)
    result = []
    for area in DOMAINS:
        recommendation = _recommendation(owner_id, grade, area, counts)
        skill = SKILL_BY_ID[recommendation['skill_id']]
        result.append({'area': area, 'mode': recommendation['mode'],
                       'reason': recommendation['reason'],
                       'review_due_at': recommendation['review_due_at'],
                       'skill': {'id': skill.id, 'title': skill.title, 'grade': skill.grade}})
    return result


def choose(owner_id: int, grade: int, domain_key: str, focus_skill_id: str | None = None) -> dict[str, Any]:
    """Use the same practice policy shown in the student's path preview."""
    counts = _counts(owner_id)
    recommendation = _recommendation(owner_id, grade, domain_key, counts)
    if focus_skill_id is not None:
        allowed_skills = _skill_ids(grade, DOMAINS[domain_key]) + _skill_ids(max(0, grade - 1), DOMAINS[domain_key])
        if focus_skill_id not in allowed_skills:
            raise ValueError('This skill is outside your current grade band or chosen area.')
        recommendation = {'skill_id': focus_skill_id, 'mode': 'diagnostic',
                          'reason': 'Check the skill connected to your learning milestone.',
                          'review_due_at': None}
    skill_id = recommendation['skill_id']
    count = generated_item_count(skill_id) or advanced_item_count(skill_id)
    ordinal = counts.get(skill_id, {}).get('attempts', 0)
    seed = hashlib.sha256(f'{owner_id}:{skill_id}'.encode()).digest()
    index = (int.from_bytes(seed[:4], 'big') + ordinal * 761) % count
    item = item_for_skill(skill_id, index) if generated_item_count(skill_id) else advanced_item_for_skill(skill_id, index)
    nonce = secrets.token_urlsafe(18)
    token = signer().dumps({'owner_id': owner_id, 'grade': grade, 'skill_id': skill_id,
                            'sequence': ordinal, 'item_id': item.id, 'nonce': nonce})
    skill = SKILL_BY_ID[skill_id]
    return {'token': token, 'item': public_item(item, 'forest'),
            'skill': {'id': skill_id, 'title': skill.title, 'domain': skill.domain,
                      'grade': skill.grade}, 'mode': recommendation['mode'],
            'reason': recommendation['reason'],
            'review_due_at': recommendation['review_due_at']}


def hinted(owner_id: int, skill_id: str, sequence: int) -> bool:
    ensure_tables()
    return bool(_db().session.execute(select(HINT.c.id).where(
        HINT.c.owner_id == owner_id, HINT.c.skill_id == skill_id,
        HINT.c.sequence == sequence)).first())


def reveal_hint(owner_id: int, challenge: dict[str, Any]) -> str:
    ensure_tables()
    nonce = challenge['nonce']
    if _db().session.execute(select(ATTEMPT.c.id).where(
            ATTEMPT.c.owner_id == owner_id,
            ATTEMPT.c.skill_id == challenge['skill_id'],
            ATTEMPT.c.sequence == challenge['sequence'])).first():
        raise ValueError('This check has already been answered.')
    item = get_item(challenge['item_id'])
    if not item or item.skill_id != challenge['skill_id']:
        raise ValueError('This check is unavailable.')
    try:
        _db().session.execute(HINT.insert().values(
            owner_id=owner_id, nonce=nonce, skill_id=challenge['skill_id'],
            sequence=challenge['sequence'], created_at=utcnow()))
        _db().session.commit()
    except IntegrityError:
        _db().session.rollback()
        if not hinted(owner_id, challenge['skill_id'], challenge['sequence']):
            raise
    return item.hint


def answer(owner_id: int, challenge: dict[str, Any], response: str) -> dict[str, Any]:
    ensure_tables()
    item = get_item(challenge['item_id'])
    if not item or item.skill_id != challenge['skill_id']:
        raise ValueError('This check is unavailable.')
    skill = SKILL_BY_ID.get(item.skill_id)
    if not skill or skill.grade not in (challenge['grade'], max(0, challenge['grade'] - 1)):
        raise ValueError('This check is unavailable.')
    correct, feedback = grade_item(item, response)
    assisted = hinted(owner_id, challenge['skill_id'], challenge['sequence'])
    try:
        _db().session.execute(ATTEMPT.insert().values(
            owner_id=owner_id, nonce=challenge['nonce'], grade=challenge['grade'],
            skill_id=item.skill_id, sequence=challenge['sequence'],
            item_id=item.id, correct=correct,
            assisted=assisted, created_at=utcnow()))
        _db().session.commit()
    except IntegrityError:
        _db().session.rollback()
        raise ValueError('This check was already answered.') from None
    counts = _counts(owner_id)[item.skill_id]
    return {'correct': correct, 'assisted': assisted, 'feedback': feedback,
            'answer': item.answer, 'skill': skill.title,
            'practice': counts}


def evidence(owner_id: int, grade: int | None = None) -> list[dict[str, Any]]:
    """Small-sample practice signal for the student's own recent grade band."""
    counts = _counts(owner_id)
    rows = []
    for skill_id, values in counts.items():
        skill = SKILL_BY_ID.get(skill_id)
        if not skill or (grade is not None and skill.grade not in (grade, max(0, grade - 1))):
            continue
        attempts = values['attempts']
        signal = round(100 * (values['independent_correct'] + 1) / (attempts + 2), 1)
        rows.append({'subject': 'Math' if skill.domain == 'Arithmetic' else 'English',
                     'topic': skill.title, 'skill_id': skill_id, 'grade': skill.grade,
                     'mastery_score': signal, 'confidence_level': min(100, attempts * 20),
                     'total_attempts': attempts, 'correct_attempts': values['correct'],
                     'independent_correct': values['independent_correct'],
                     'source': 'scored_check'})
    return sorted(rows, key=lambda row: (row['grade'], row['subject'], row['topic']))
