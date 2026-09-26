"""Account-scoped learner evidence and journey state. Raw answers are never persisted."""

import json
from datetime import timedelta

from flask import current_app
from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Table, UniqueConstraint, delete, select
from sqlalchemy.exc import IntegrityError

from db_boot import schema_lock
from primer.catalog import ITEMS_BY_SKILL, SKILLS, SKILL_BY_ID
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


_META = MetaData()
LEARNER = Table(
    'primer_learner', _META,
    Column('id', Integer, primary_key=True),
    Column('owner_id', Integer, nullable=False, index=True),
    Column('nickname', String(40), nullable=False),
    Column('world', String(16), nullable=False),
    Column('created_at', DateTime, nullable=False),
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
    rows = _db().session.execute(select(LEARNER).where(LEARNER.c.owner_id == owner_id).order_by(LEARNER.c.id)).mappings().all()
    return [dict(row) for row in rows]


def create_learner(owner_id: int, nickname: str, world: str) -> dict:
    ensure_tables()
    db = _db()
    result = db.session.execute(LEARNER.insert().values(owner_id=owner_id, nickname=nickname, world=world, created_at=utcnow()))
    db.session.execute(JOURNEY.insert().values(learner_id=result.inserted_primary_key[0], chapter=0,
                                               beat=0, path='[]', version=0, updated_at=utcnow()))
    db.session.commit()
    return get_learner(owner_id, result.inserted_primary_key[0])


def get_learner(owner_id: int, learner_id: int) -> dict | None:
    ensure_tables()
    row = _db().session.execute(select(LEARNER).where(LEARNER.c.owner_id == owner_id, LEARNER.c.id == learner_id)).mappings().first()
    return dict(row) if row else None


def delete_learner(owner_id: int, learner_id: int) -> bool:
    if not get_learner(owner_id, learner_id):
        return False
    db = _db()
    db.session.execute(delete(HINT).where(HINT.c.learner_id == learner_id))
    db.session.execute(delete(ATTEMPT).where(ATTEMPT.c.learner_id == learner_id))
    db.session.execute(delete(STATE).where(STATE.c.learner_id == learner_id))
    db.session.execute(delete(JOURNEY).where(JOURNEY.c.learner_id == learner_id))
    db.session.execute(delete(LEARNER).where(LEARNER.c.id == learner_id, LEARNER.c.owner_id == owner_id))
    db.session.commit()
    return True


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


def choose_story_activity(learner_id: int, domain: str, repair_skill_id: str | None = None) -> tuple[str, object]:
    """Pick a due skill in the chapter's domain, preferring weak evidence."""
    states = states_for(learner_id)
    now = utcnow()
    candidates = [skill for skill in SKILLS if skill.domain == domain and _unlocked(skill.id, states)]

    def rank(skill):
        state = states.get(skill.id)
        due = not state or not state['due_at'] or state['due_at'] <= now
        return (0 if due else 1, 0 if state and state['streak'] == 0 else 1,
                0 if not state else 1, state['estimate'] if state else 0.5, SKILLS.index(skill))

    skill = SKILL_BY_ID[repair_skill_id] if repair_skill_id else min(candidates, key=rank)
    attempts = states.get(skill.id, {}).get('attempts', 0)
    items = ITEMS_BY_SKILL[skill.id]
    return skill.id, items[attempts % len(items)]


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


def progress(learner_id: int) -> dict:
    states = states_for(learner_id)
    now = utcnow()
    skills = []
    for skill in SKILLS:
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
        skills.append(dict(id=skill.id, domain=skill.domain, title=skill.title,
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
    return {'skills': skills, 'total_attempts': sum(row['attempts'] for row in states.values()),
            'focus': {'skill': focus['title'], 'domain': focus['domain'], 'reason': reason,
                      'try_together': HOME_PRACTICE[focus['id']]}}
