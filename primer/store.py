"""Account-scoped learner evidence and journey state. Raw answers are never persisted."""

import json
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Table, UniqueConstraint, delete, select
from sqlalchemy.exc import IntegrityError

from db_boot import schema_lock
from primer.catalog import ITEMS_BY_SKILL, SKILLS, SKILL_BY_ID
from primer.generated import generated_item_count, item_for_skill
from primer.story import CHAPTER_COUNT
from time_utils import utcnow


HOME_PRACTICE = {
    'read_sounds': 'Say a familiar word together. Ask which sound comes first, then find another word with that sound.',
    'read_sentences': 'Read one short sentence together. Ask who or what it is about and point to the words that tell you.',
    'read_passages': 'Read a short paragraph together. Ask what happened first and which sentence gives the clue.',
    'write_order': 'Give three spoken words and invite the learner to put them into a complete sentence.',
    'write_punctuation': 'Write a short statement and a question. Compare their first letters and ending marks.',
    'write_clarity': 'Describe one thing you can see. Help the learner write who or what it is and what it does.',
    'math_count': 'Count five small objects, moving each one aside so every object is counted once.',
    'math_add': 'Make two small groups of objects. Put them together and count the total.',
    'math_story': 'Tell a short adding story with objects. Ask what the numbers mean before finding the total.',
}
GRADE_HOME_PRACTICE = (
    {
        'Reading': 'Read a short account aloud. Ask which words name what happened and what happened next.',
        'Writing': 'Say one complete sentence together, then check its first letter and ending mark.',
        'Arithmetic': 'Use small objects to show each number before working out the answer.',
    },
    {
        'Reading': 'Read a paragraph together and point to the detail that supports each answer.',
        'Writing': 'Write a sentence, read it aloud, and revise its punctuation or word choice.',
        'Arithmetic': 'Sketch or estimate the quantities first, then explain each step of the calculation.',
    },
    {
        'Reading': 'Discuss a claim in a short text. Ask which exact detail supports it and what might be missing.',
        'Writing': 'Write a claim and one supporting reason, then revise the connection between them.',
        'Arithmetic': 'Represent the relationship with a table, expression, or graph before solving.',
    },
)


_META = MetaData()
LEARNER = Table(
    'primer_learner', _META,
    Column('id', Integer, primary_key=True),
    Column('owner_id', Integer, nullable=False, index=True),
    Column('nickname', String(40), nullable=False),
    Column('world', String(16), nullable=False),
    Column('created_at', DateTime, nullable=False),
)
PROFILE = Table(
    'primer_learning_profile', _META,
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), primary_key=True),
    Column('grade', Integer, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)
STATE = Table(
    'primer_skill_state', _META,
    Column('id', Integer, primary_key=True),
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), nullable=False),
    Column('skill_id', String(32), nullable=False),
    Column('attempts', Integer, nullable=False, default=0),
    Column('correct', Integer, nullable=False, default=0),
    Column('streak', Integer, nullable=False, default=0),
    Column('estimate', Float, nullable=False, default=0.5),
    Column('due_at', DateTime, nullable=True),
    Column('updated_at', DateTime, nullable=False),
    UniqueConstraint('learner_id', 'skill_id', name='uq_primer_learner_skill'),
)
ATTEMPT = Table(
    'primer_attempt', _META,
    Column('id', Integer, primary_key=True),
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), nullable=False),
    Column('skill_id', String(32), nullable=False),
    Column('item_id', String(32), nullable=False),
    Column('nonce', String(40), nullable=False, unique=True),
    Column('correct', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
    Index('ix_primer_attempt_learner_time', 'learner_id', 'created_at'),
)
HINT = Table(
    'primer_hint_used', _META,
    Column('id', Integer, primary_key=True),
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), nullable=False),
    Column('nonce', String(40), nullable=False, unique=True),
    Column('created_at', DateTime, nullable=False),
)
JOURNEY = Table(
    'primer_journey', _META,
    Column('id', Integer, primary_key=True),
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), nullable=False, unique=True),
    Column('chapter', Integer, nullable=False, default=0),
    Column('beat', Integer, nullable=False, default=0),
    Column('repair_skill_id', String(32), nullable=True),
    Column('path', String(120), nullable=False, default='[]'),
    Column('version', Integer, nullable=False, default=0),
    Column('updated_at', DateTime, nullable=False),
)
PARENT_SETTING = Table(
    'primer_parent_setting', _META,
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), primary_key=True),
    Column('weekly_goal', Integer, nullable=False, default=3),
)
OFFLINE_CHECKIN = Table(
    'primer_offline_checkin', _META,
    Column('id', Integer, primary_key=True),
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), nullable=False),
    Column('practice_date', Date, nullable=False),
    Column('domain', String(16), nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('learner_id', 'practice_date', name='uq_primer_offline_day'),
)
NUDGE = Table(
    'primer_nudge', _META,
    Column('id', Integer, primary_key=True),
    Column('learner_id', Integer, ForeignKey('primer_learner.id'), nullable=False),
    Column('template_id', String(32), nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('acknowledged_at', DateTime, nullable=True),
    Column('withdrawn_at', DateTime, nullable=True),
    Index('ix_primer_nudge_learner_time', 'learner_id', 'created_at'),
)

NUDGE_TEMPLATES = {
    'explore': 'I would love to explore the next part of your story together when you are ready.',
    'choose': 'Your next story choice is waiting. Want to see where it leads together?',
    'small_step': 'One small step is enough today. I am proud of you for trying.',
}
NUDGE_LIFETIME = timedelta(days=7)


class StaleJourney(Exception):
    """The learner moved on after a challenge was issued."""


def _db():
    return current_app.extensions['sqlalchemy']


def ensure_tables():
    if not current_app.extensions.get('primer_tables_ready'):
        # App.py protects boot schema work with the same advisory lock.
        # Lazy feature tables need it too when several workers first arrive.
        with schema_lock(_db().engine):
            _META.create_all(_db().engine)
        current_app.extensions['primer_tables_ready'] = True


def list_learners(owner_id: int) -> list[dict]:
    ensure_tables()
    rows = _db().session.execute(select(LEARNER, PROFILE.c.grade).select_from(
        LEARNER.outerjoin(PROFILE, LEARNER.c.id == PROFILE.c.learner_id)
    ).where(LEARNER.c.owner_id == owner_id).order_by(LEARNER.c.id)).mappings().all()
    return [{**dict(row), 'grade': row['grade'] if row['grade'] is not None else 0,
             'grade_set': row['grade'] is not None} for row in rows]


def create_learner(owner_id: int, nickname: str, world: str, grade: int = 0) -> dict:
    ensure_tables()
    db = _db()
    result = db.session.execute(LEARNER.insert().values(owner_id=owner_id, nickname=nickname, world=world, created_at=utcnow()))
    db.session.execute(PROFILE.insert().values(learner_id=result.inserted_primary_key[0], grade=grade, updated_at=utcnow()))
    db.session.execute(JOURNEY.insert().values(learner_id=result.inserted_primary_key[0], chapter=0,
                                               beat=0, path='[]', version=0, updated_at=utcnow()))
    db.session.commit()
    return get_learner(owner_id, result.inserted_primary_key[0])


def get_learner(owner_id: int, learner_id: int) -> dict | None:
    ensure_tables()
    row = _db().session.execute(select(LEARNER, PROFILE.c.grade).select_from(
        LEARNER.outerjoin(PROFILE, LEARNER.c.id == PROFILE.c.learner_id)
    ).where(LEARNER.c.owner_id == owner_id, LEARNER.c.id == learner_id)).mappings().first()
    return ({**dict(row), 'grade': row['grade'] if row['grade'] is not None else 0,
             'grade_set': row['grade'] is not None} if row else None)


def set_grade(owner_id: int, learner_id: int, grade: int) -> dict | None:
    """Change placement and invalidate outstanding story challenges atomically."""
    learner = get_learner(owner_id, learner_id)
    if not learner:
        return None
    db = _db()
    db.session.execute(select(LEARNER.c.id).where(LEARNER.c.id == learner_id).with_for_update()).scalar()
    updated = db.session.execute(PROFILE.update().where(PROFILE.c.learner_id == learner_id)
                                 .values(grade=grade, updated_at=utcnow()))
    if updated.rowcount == 0:
        db.session.execute(PROFILE.insert().values(learner_id=learner_id, grade=grade, updated_at=utcnow()))
    db.session.execute(JOURNEY.update().where(JOURNEY.c.learner_id == learner_id)
                       .values(version=JOURNEY.c.version + 1, repair_skill_id=None, updated_at=utcnow()))
    db.session.commit()
    return get_learner(owner_id, learner_id)


def delete_learner(owner_id: int, learner_id: int) -> bool:
    if not get_learner(owner_id, learner_id):
        return False
    db = _db()
    db.session.execute(delete(HINT).where(HINT.c.learner_id == learner_id))
    db.session.execute(delete(ATTEMPT).where(ATTEMPT.c.learner_id == learner_id))
    db.session.execute(delete(STATE).where(STATE.c.learner_id == learner_id))
    db.session.execute(delete(JOURNEY).where(JOURNEY.c.learner_id == learner_id))
    db.session.execute(delete(NUDGE).where(NUDGE.c.learner_id == learner_id))
    db.session.execute(delete(OFFLINE_CHECKIN).where(OFFLINE_CHECKIN.c.learner_id == learner_id))
    db.session.execute(delete(PARENT_SETTING).where(PARENT_SETTING.c.learner_id == learner_id))
    db.session.execute(delete(PROFILE).where(PROFILE.c.learner_id == learner_id))
    db.session.execute(delete(LEARNER).where(LEARNER.c.id == learner_id, LEARNER.c.owner_id == owner_id))
    db.session.commit()
    return True


def parent_overview(learner_id: int, tz_offset_minutes: int = 0, grade: int = 0) -> dict:
    """Summarize observed and adult-reported practice in the viewer's local days."""
    ensure_tables()
    db = _db()
    now = utcnow()
    shift = timedelta(minutes=tz_offset_minutes)
    today = (now + shift).date()
    first_day = today - timedelta(days=6)
    start_utc = datetime.combine(first_day, datetime.min.time()) - shift
    attempts = db.session.execute(select(ATTEMPT.c.created_at, ATTEMPT.c.skill_id, ATTEMPT.c.correct)
        .where(ATTEMPT.c.learner_id == learner_id, ATTEMPT.c.created_at >= start_utc)
        .order_by(ATTEMPT.c.created_at)).all()
    last_attempt = db.session.execute(select(ATTEMPT.c.created_at)
        .where(ATTEMPT.c.learner_id == learner_id).order_by(ATTEMPT.c.created_at.desc()).limit(1)).scalar()
    checkins = db.session.execute(select(OFFLINE_CHECKIN.c.practice_date, OFFLINE_CHECKIN.c.domain)
        .where(OFFLINE_CHECKIN.c.learner_id == learner_id, OFFLINE_CHECKIN.c.practice_date >= first_day)).all()
    settings = db.session.execute(select(PARENT_SETTING.c.weekly_goal)
        .where(PARENT_SETTING.c.learner_id == learner_id)).scalar()
    goal = settings if settings is not None else 3
    days = {first_day + timedelta(days=i): {'date': (first_day + timedelta(days=i)).isoformat(),
             'answers': 0, 'offline_domain': None} for i in range(7)}
    domain_counts = {domain: 0 for domain in ('Reading', 'Writing', 'Arithmetic')}
    for at, skill_id, _ in attempts:
        day = (at + shift).date()
        if day in days:
            days[day]['answers'] += 1
            domain_counts[SKILL_BY_ID[skill_id].domain] += 1
    for day, domain in checkins:
        if day in days:
            days[day]['offline_domain'] = domain
    practiced = sum(bool(row['answers'] or row['offline_domain']) for row in days.values())
    latest = latest_nudge(learner_id)
    can_send_note = not latest or (
        (latest['acknowledged_at'] is not None or latest['withdrawn_at'] is not None or latest['expired'])
        and now - latest['_created_at'] >= timedelta(hours=24))
    next_note_at = (latest['_created_at'] + timedelta(hours=24)).isoformat() + 'Z' if latest and not can_send_note else None
    if latest:
        practiced_after = db.session.execute(select(ATTEMPT.c.id).where(
            ATTEMPT.c.learner_id == learner_id, ATTEMPT.c.created_at > latest['_created_at']).limit(1)).scalar()
        latest['practice_after'] = bool(practiced_after)
        latest.pop('_created_at')
    evidence = progress(learner_id, grade)
    journey = journey_for(learner_id)
    if journey['beat'] == 3 and journey['chapter'] < CHAPTER_COUNT:
        suggested_nudge = 'choose'
        nudge_reason = 'The next story choice is ready.'
    elif not evidence['total_attempts']:
        suggested_nudge = 'explore'
        nudge_reason = 'A first activity is ready to explore together.'
    elif last_attempt and now - last_attempt >= timedelta(days=3):
        suggested_nudge = 'small_step'
        nudge_reason = 'It has been a few days since an answered activity.'
    else:
        suggested_nudge = 'explore'
        nudge_reason = 'A short next chapter is ready.'
    return {
        'days': list(days.values()), 'practice_days': practiced, 'weekly_goal': goal,
        'last_answered_at': last_attempt.isoformat() + 'Z' if last_attempt else None,
        'domain_answers': domain_counts, 'focus': evidence['focus'],
        'total_answers': evidence['total_attempts'], 'latest_nudge': latest,
        'can_send_nudge': can_send_note, 'next_nudge_at': next_note_at,
        'suggested_nudge': suggested_nudge, 'suggested_nudge_reason': nudge_reason,
        'chapter': min(journey['chapter'] + 1, CHAPTER_COUNT),
        'chapter_count': CHAPTER_COUNT, 'journey_complete': journey['chapter'] >= CHAPTER_COUNT,
    }


def set_weekly_goal(learner_id: int, goal: int) -> None:
    ensure_tables()
    db = _db()
    db.session.execute(select(LEARNER.c.id).where(LEARNER.c.id == learner_id).with_for_update()).scalar()
    updated = db.session.execute(PARENT_SETTING.update().where(
        PARENT_SETTING.c.learner_id == learner_id).values(weekly_goal=goal))
    if updated.rowcount == 0:
        db.session.execute(PARENT_SETTING.insert().values(learner_id=learner_id, weekly_goal=goal))
    db.session.commit()


def record_offline_checkin(learner_id: int, domain: str, tz_offset_minutes: int = 0) -> bool:
    """One adult-reported check-in per local day; no child text is collected."""
    ensure_tables()
    now = utcnow()
    today = (now + timedelta(minutes=tz_offset_minutes)).date()
    db = _db()
    try:
        db.session.execute(OFFLINE_CHECKIN.insert().values(
            learner_id=learner_id, practice_date=today, domain=domain, created_at=now))
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def latest_nudge(learner_id: int) -> dict | None:
    ensure_tables()
    row = _db().session.execute(select(NUDGE).where(NUDGE.c.learner_id == learner_id)
        .order_by(NUDGE.c.created_at.desc(), NUDGE.c.id.desc()).limit(1)).mappings().first()
    if not row:
        return None
    return {'id': row['id'], 'template_id': row['template_id'],
            'message': NUDGE_TEMPLATES[row['template_id']],
            'created_at': row['created_at'].isoformat() + 'Z',
            'acknowledged_at': row['acknowledged_at'].isoformat() + 'Z' if row['acknowledged_at'] else None,
            'withdrawn_at': row['withdrawn_at'].isoformat() + 'Z' if row['withdrawn_at'] else None,
            'expired': utcnow() - row['created_at'] >= NUDGE_LIFETIME,
            '_created_at': row['created_at']}


def create_nudge(learner_id: int, template_id: str) -> dict | None:
    """At most one note per 24 hours and one unacknowledged note at a time."""
    ensure_tables()
    db = _db()
    # Serialize note creation for this learner in Postgres. Without the row
    # lock, two tabs could both observe no note and insert simultaneously.
    db.session.execute(select(LEARNER.c.id).where(LEARNER.c.id == learner_id).with_for_update()).scalar()
    latest = latest_nudge(learner_id)
    if latest and ((latest['acknowledged_at'] is None and latest['withdrawn_at'] is None and not latest['expired']) or
                   utcnow() - latest['_created_at'] < timedelta(hours=24)):
        db.session.rollback()
        return None
    db.session.execute(NUDGE.insert().values(learner_id=learner_id, template_id=template_id,
                                             created_at=utcnow()))
    db.session.commit()
    return latest_nudge(learner_id)


def acknowledge_nudge(learner_id: int, nudge_id: int) -> bool:
    ensure_tables()
    db = _db()
    updated = db.session.execute(NUDGE.update().where(
        NUDGE.c.learner_id == learner_id, NUDGE.c.id == nudge_id,
        NUDGE.c.acknowledged_at.is_(None), NUDGE.c.withdrawn_at.is_(None),
        NUDGE.c.created_at > utcnow() - NUDGE_LIFETIME).values(acknowledged_at=utcnow()))
    db.session.commit()
    return updated.rowcount == 1


def withdraw_nudge(learner_id: int, nudge_id: int) -> bool:
    latest = latest_nudge(learner_id)
    if not latest or latest['id'] != nudge_id or latest['acknowledged_at'] or latest['withdrawn_at'] or latest['expired']:
        return False
    db = _db()
    removed = db.session.execute(NUDGE.update().where(
        NUDGE.c.learner_id == learner_id, NUDGE.c.id == nudge_id,
        NUDGE.c.acknowledged_at.is_(None), NUDGE.c.withdrawn_at.is_(None),
        NUDGE.c.created_at > utcnow() - NUDGE_LIFETIME).values(withdrawn_at=utcnow()))
    db.session.commit()
    return removed.rowcount == 1


def journey_for(learner_id: int) -> dict:
    """Lazily initialize the journey for learners created before this release."""
    ensure_tables()
    db = _db()
    row = db.session.execute(select(JOURNEY).where(JOURNEY.c.learner_id == learner_id)).mappings().first()
    if not row:
        try:
            db.session.execute(JOURNEY.insert().values(learner_id=learner_id, chapter=0,
                                                       beat=0, path='[]', version=0, updated_at=utcnow()))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        row = db.session.execute(select(JOURNEY).where(JOURNEY.c.learner_id == learner_id)).mappings().one()
    result = dict(row)
    result['path'] = json.loads(result['path'])
    return result


def reveal_hint(learner_id: int, nonce: str, expected_version: int) -> None:
    """Record clue use before revealing it; repeated reveals are harmless."""
    journey = journey_for(learner_id)
    if (journey['version'] != expected_version or journey['beat'] >= 3
            or journey['chapter'] >= CHAPTER_COUNT):
        raise StaleJourney()
    db = _db()
    if db.session.execute(select(HINT.c.id).where(HINT.c.nonce == nonce,
                                                    HINT.c.learner_id == learner_id)).scalar():
        return
    try:
        db.session.execute(HINT.insert().values(learner_id=learner_id, nonce=nonce, created_at=utcnow()))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()


def _grade_skills(grade: int, domain: str | None = None):
    return [skill for skill in SKILLS if skill.grade == grade and (domain is None or skill.domain == domain)]


def _needs_scaffold(grade: int, domain: str, states: dict[str, dict]) -> bool:
    if grade == 0:
        return False
    current = _grade_skills(grade, domain)
    misses = sum(states.get(skill.id, {}).get('attempts', 0) -
                 states.get(skill.id, {}).get('correct', 0) for skill in current)
    independent = sum(states.get(skill.id, {}).get('independent_correct', 0) for skill in current)
    prior_attempts = sum(states.get(skill.id, {}).get('attempts', 0)
                         for skill in _grade_skills(grade - 1, domain))
    return misses >= 2 and independent == 0 and prior_attempts < misses // 2


def choose_story_activity(learner_id: int, domain: str, repair_skill_id: str | None = None,
                          grade: int = 0) -> tuple[str, object]:
    """Pick a due skill in the chapter's domain, preferring weak evidence."""
    states = states_for(learner_id)
    now = utcnow()
    band = grade - 1 if _needs_scaffold(grade, domain, states) else grade
    candidates = [skill for skill in _grade_skills(band, domain) if _unlocked(skill.id, states)]

    def rank(skill):
        state = states.get(skill.id)
        due = not state or not state['due_at'] or state['due_at'] <= now
        foundation = (skill.id in ITEMS_BY_SKILL and
                      states.get(skill.id, {}).get('independent_correct', 0) < 2)
        return (0 if grade == 0 and foundation else 1,
                0 if due else 1, 0 if state and state['streak'] == 0 else 1,
                0 if not state else 1, state['estimate'] if state else 0.5, SKILLS.index(skill))

    skill = SKILL_BY_ID[repair_skill_id] if repair_skill_id else min(candidates, key=rank)
    attempts = states.get(skill.id, {}).get('attempts', 0)
    if skill.id in ITEMS_BY_SKILL:
        items = ITEMS_BY_SKILL[skill.id]
        return skill.id, items[attempts % len(items)]
    count = generated_item_count(skill.id)
    index = (attempts * 761 + learner_id * 131) % count
    return skill.id, item_for_skill(skill.id, index)


def choose_story_path(learner_id: int, expected_version: int, choice_id: str) -> dict:
    """Advance only the current choice screen, once, without child free text."""
    db = _db()
    journey = journey_for(learner_id)
    if (journey['version'] != expected_version or journey['beat'] != 3
            or journey['repair_skill_id'] or journey['chapter'] >= CHAPTER_COUNT):
        raise StaleJourney()
    path = journey['path'] + [choice_id]
    updated = db.session.execute(JOURNEY.update().where(
        JOURNEY.c.learner_id == learner_id, JOURNEY.c.version == expected_version,
        JOURNEY.c.chapter == journey['chapter'], JOURNEY.c.beat == 3,
    ).values(chapter=journey['chapter'] + 1, beat=0, path=json.dumps(path),
             version=expected_version + 1, updated_at=utcnow()))
    if updated.rowcount != 1:
        db.session.rollback()
        raise StaleJourney()
    db.session.commit()
    return journey_for(learner_id)


def restart_journey(learner_id: int) -> dict:
    journey = journey_for(learner_id)
    if journey['chapter'] < CHAPTER_COUNT:
        raise StaleJourney()
    db = _db()
    updated = db.session.execute(JOURNEY.update().where(
        JOURNEY.c.learner_id == learner_id,
        JOURNEY.c.version == journey['version'],
        JOURNEY.c.chapter == CHAPTER_COUNT,
    ).values(
        chapter=0, beat=0, repair_skill_id=None, path='[]', version=journey['version'] + 1, updated_at=utcnow()))
    if updated.rowcount != 1:
        db.session.rollback()
        raise StaleJourney()
    db.session.commit()
    return journey_for(learner_id)


def states_for(learner_id: int) -> dict[str, dict]:
    ensure_tables()
    rows = _db().session.execute(select(STATE).where(STATE.c.learner_id == learner_id)).mappings().all()
    result = {row['skill_id']: dict(row) for row in rows}
    evidence = _db().session.execute(
        select(ATTEMPT.c.skill_id, ATTEMPT.c.correct, HINT.c.id).select_from(
            ATTEMPT.outerjoin(HINT, ATTEMPT.c.nonce == HINT.c.nonce)
        ).where(ATTEMPT.c.learner_id == learner_id).order_by(ATTEMPT.c.id.desc())
    ).all()
    streak_open = set()
    for skill_id, correct, hint_id in evidence:
        state = result[skill_id]
        if 'independent_correct' not in state:
            state['independent_correct'] = 0
            state['independent_streak'] = 0
            streak_open.add(skill_id)
        if correct and hint_id is None:
            state['independent_correct'] += 1
            if skill_id in streak_open:
                state['independent_streak'] += 1
        else:
            streak_open.discard(skill_id)
    return result


def _unlocked(skill_id: str, states: dict[str, dict]) -> bool:
    prerequisite = SKILL_BY_ID[skill_id].prerequisite
    return prerequisite is None or states.get(prerequisite, {}).get('independent_correct', 0) >= 2


def record_attempt(learner_id: int, skill_id: str, item_id: str, nonce: str, correct: bool,
                   expected_journey_version: int | None = None) -> dict:
    """Insert the evidence and update a skill state in one transaction."""
    ensure_tables()
    db = _db()
    now = utcnow()
    try:
        hint_used = bool(db.session.execute(select(HINT.c.id).where(
            HINT.c.nonce == nonce, HINT.c.learner_id == learner_id)).scalar())
        if expected_journey_version is not None:
            journey = journey_for(learner_id)
            if (journey['version'] != expected_journey_version
                    or (journey['repair_skill_id'] and journey['repair_skill_id'] != skill_id)):
                db.session.rollback()
                raise StaleJourney()
            if not correct and not journey['repair_skill_id']:
                journey_values = dict(repair_skill_id=skill_id)
            else:
                journey_values = dict(beat=JOURNEY.c.beat + 1, repair_skill_id=None)
            updated = db.session.execute(JOURNEY.update().where(
                JOURNEY.c.learner_id == learner_id,
                JOURNEY.c.version == expected_journey_version,
                JOURNEY.c.beat < 3,
                JOURNEY.c.chapter < CHAPTER_COUNT,
            ).values(version=JOURNEY.c.version + 1, updated_at=now, **journey_values))
            if updated.rowcount != 1:
                db.session.rollback()
                raise StaleJourney()
        db.session.execute(ATTEMPT.insert().values(
            learner_id=learner_id, skill_id=skill_id, item_id=item_id,
            nonce=nonce, correct=int(correct), created_at=now,
        ))
        row = db.session.execute(select(STATE).where(STATE.c.learner_id == learner_id, STATE.c.skill_id == skill_id).with_for_update()).mappings().first()
        attempts = (row['attempts'] if row else 0) + 1
        successes = (row['correct'] if row else 0) + int(correct)
        streak = ((row['streak'] if row else 0) + 1) if correct else 0
        delay = 0 if not correct else (1 if hint_used or streak == 1 else 3 if streak == 2 else 7)
        values = dict(attempts=attempts, correct=successes, streak=streak,
                      estimate=round((successes + 1) / (attempts + 2), 3),
                      due_at=now + timedelta(days=delay), updated_at=now)
        if row:
            db.session.execute(STATE.update().where(STATE.c.id == row['id']).values(**values))
        else:
            db.session.execute(STATE.insert().values(learner_id=learner_id, skill_id=skill_id, **values))
        db.session.commit()
        return {**values, 'hint_used': hint_used}
    except IntegrityError:
        db.session.rollback()
        raise


def progress(learner_id: int, grade: int = 0) -> dict:
    states = states_for(learner_id)
    now = utcnow()
    skills = []
    for skill in _grade_skills(grade):
        state = states.get(skill.id, {})
        attempts = state.get('attempts', 0)
        successes = state.get('correct', 0)
        estimate = state.get('estimate', 0.5)
        independent_correct = state.get('independent_correct', 0)
        independent_streak = state.get('independent_streak', 0)
        if (attempts >= 4 and estimate >= 0.75 and independent_correct >= 3
                and independent_streak >= 2):
            label = 'Strong'
        elif independent_correct >= 2:
            label = 'Growing'
        else:
            label = 'Practicing'
        skills.append(dict(id=skill.id, domain=skill.domain, title=skill.title, grade=skill.grade,
                           unlocked=_unlocked(skill.id, states), attempts=attempts,
                           correct=successes, independent_correct=independent_correct,
                           estimate=estimate, label=label,
                           due_at=state['due_at'].isoformat() + 'Z' if state.get('due_at') else None))
    candidates = [row for row in skills if row['unlocked']]
    focus = min(candidates, key=lambda row: (
        0 if not states.get(row['id'], {}).get('due_at') or states[row['id']]['due_at'] <= now else 1,
        0 if states.get(row['id'], {}).get('streak', 0) == 0 and row['attempts'] else 1,
        row['attempts'], row['estimate'], SKILLS.index(SKILL_BY_ID[row['id']]),
    ))
    focus_state = states.get(focus['id'], {})
    if focus['attempts'] == 0:
        reason = 'This is a useful place to begin; there are no recorded answers for it yet.'
    elif focus_state['due_at'] and focus_state['due_at'] > now:
        reason = 'Current skills are waiting for their next review; a conversation can keep this one fresh.'
    elif focus_state['streak'] == 0:
        reason = 'The most recent answer missed this skill, so a gentle revisit may help.'
    else:
        reason = 'This skill is ready for another short review.'
    band = 0 if grade <= 2 else 1 if grade <= 5 else 2
    try_together = HOME_PRACTICE.get(focus['id']) or GRADE_HOME_PRACTICE[band][focus['domain']]
    return {'skills': skills, 'grade': grade,
            'scaffolding': {domain: _needs_scaffold(grade, domain, states)
                            for domain in ('Reading', 'Writing', 'Arithmetic')},
            'total_attempts': sum(row['attempts'] for row in states.values()),
            'focus': {'skill': focus['title'], 'domain': focus['domain'], 'reason': reason,
                      'try_together': try_together}}
