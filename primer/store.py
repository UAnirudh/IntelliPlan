"""Account-scoped learner evidence. Raw answers are never persisted."""

from datetime import timedelta

from flask import current_app
from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Table, UniqueConstraint, delete, select
from sqlalchemy.exc import IntegrityError

from db_boot import schema_lock
from primer.catalog import ITEMS_BY_SKILL, SKILLS, SKILL_BY_ID
from time_utils import utcnow


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
    db.session.execute(delete(ATTEMPT).where(ATTEMPT.c.learner_id == learner_id))
    db.session.execute(delete(STATE).where(STATE.c.learner_id == learner_id))
    db.session.execute(delete(LEARNER).where(LEARNER.c.id == learner_id, LEARNER.c.owner_id == owner_id))
    db.session.commit()
    return True


def states_for(learner_id: int) -> dict[str, dict]:
    ensure_tables()
    rows = _db().session.execute(select(STATE).where(STATE.c.learner_id == learner_id)).mappings().all()
    return {row['skill_id']: dict(row) for row in rows}


def _unlocked(skill_id: str, states: dict[str, dict]) -> bool:
    prerequisite = SKILL_BY_ID[skill_id].prerequisite
    return prerequisite is None or states.get(prerequisite, {}).get('correct', 0) >= 2


def choose_activity(learner_id: int) -> tuple[str, object]:
    """Prioritize new/weak due skills, rotating domains and items."""
    states = states_for(learner_id)
    now = utcnow()
    last = _db().session.execute(select(ATTEMPT.c.skill_id).where(ATTEMPT.c.learner_id == learner_id).order_by(ATTEMPT.c.id.desc()).limit(1)).scalar()
    last_domain = SKILL_BY_ID[last].domain if last in SKILL_BY_ID else None
    candidates = [skill for skill in SKILLS if _unlocked(skill.id, states)]

    def rank(skill):
        state = states.get(skill.id)
        due = not state or not state['due_at'] or state['due_at'] <= now
        # Return to an error after another domain has had a turn. New skills
        # follow, then strong skills waiting for their spaced review date.
        error = bool(state and state['streak'] == 0)
        return (0 if due else 1, 0 if skill.domain != last_domain else 1,
                0 if error else 1, 0 if not state else 1,
                state['estimate'] if state else 0.5, SKILLS.index(skill))

    skill = min(candidates, key=rank)
    attempts = states.get(skill.id, {}).get('attempts', 0)
    items = ITEMS_BY_SKILL[skill.id]
    return skill.id, items[attempts % len(items)]


def record_attempt(learner_id: int, skill_id: str, item_id: str, nonce: str, correct: bool) -> dict:
    """Insert the evidence and update a skill state in one transaction."""
    ensure_tables()
    db = _db()
    now = utcnow()
    try:
        db.session.execute(ATTEMPT.insert().values(
            learner_id=learner_id, skill_id=skill_id, item_id=item_id,
            nonce=nonce, correct=int(correct), created_at=now,
        ))
        row = db.session.execute(select(STATE).where(STATE.c.learner_id == learner_id, STATE.c.skill_id == skill_id).with_for_update()).mappings().first()
        attempts = (row['attempts'] if row else 0) + 1
        successes = (row['correct'] if row else 0) + int(correct)
        streak = ((row['streak'] if row else 0) + 1) if correct else 0
        delay = 0 if not correct else (1 if streak == 1 else 3 if streak == 2 else 7)
        values = dict(attempts=attempts, correct=successes, streak=streak,
                      estimate=round((successes + 1) / (attempts + 2), 3),
                      due_at=now + timedelta(days=delay), updated_at=now)
        if row:
            db.session.execute(STATE.update().where(STATE.c.id == row['id']).values(**values))
        else:
            db.session.execute(STATE.insert().values(learner_id=learner_id, skill_id=skill_id, **values))
        db.session.commit()
        return values
    except IntegrityError:
        db.session.rollback()
        raise


def progress(learner_id: int) -> dict:
    states = states_for(learner_id)
    skills = []
    for skill in SKILLS:
        state = states.get(skill.id, {})
        attempts = state.get('attempts', 0)
        successes = state.get('correct', 0)
        estimate = state.get('estimate', 0.5)
        if attempts >= 4 and estimate >= 0.75 and state.get('streak', 0) >= 2:
            label = 'Strong'
        elif successes >= 2:
            label = 'Growing'
        else:
            label = 'Practicing'
        skills.append(dict(id=skill.id, domain=skill.domain, title=skill.title,
                           unlocked=_unlocked(skill.id, states), attempts=attempts,
                           correct=successes, estimate=estimate, label=label,
                           due_at=state['due_at'].isoformat() + 'Z' if state.get('due_at') else None))
    return {'skills': skills, 'total_attempts': sum(row['attempts'] for row in states.values())}
