"""Persistent student model for the adaptive Plani tutor.

Ported from the adaptive-ai-tutor Prisma schema + ``student-model.ts``. Six
tables carry the model:

``adaptive_student_profile``   onboarding answers (grade, subjects, goals, style)
``adaptive_subject_mastery``   per-topic mastery with a weighted moving average
``adaptive_mistake_pattern``   recurring misconceptions with frequency counts
``adaptive_learner_memory``    the durable LLM-built learner model
``adaptive_memory_import``     learner context imported from other AI providers
``adaptive_session_summary``   per-conversation session summaries

Tables are created lazily on first use, matching the existing
``chatbot_api._ensure_tutor_memory_table`` pattern, so no migration step is
required for existing deployments.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from flask import current_app, session
from flask_login import current_user
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
)

from time_utils import utcnow

_META = MetaData()
_READY = False

#: Mastery blends 70% of the running score with 30% of the newest evidence.
_MASTERY_HISTORY_WEIGHT = 0.7
#: Confidence climbs 8 points per attempt from a 20-point floor.
_CONFIDENCE_STEP = 8
_CONFIDENCE_FLOOR = 20

PROFILE = Table(
    'adaptive_student_profile', _META,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, nullable=True, index=True),
    Column('guest_session_id', String(64), nullable=True, index=True),
    Column('grade_level', String(64), nullable=True),
    Column('subjects_json', Text, nullable=False, default='[]'),
    Column('short_term_goals', Text, nullable=True),
    Column('long_term_goals', Text, nullable=True),
    Column('explanation_style', String(32), nullable=False, default='balanced'),
    Column('explanation_length', String(32), nullable=False, default='medium'),
    Column('difficulty_level', String(32), nullable=False, default='medium'),
    Column('interests_json', Text, nullable=False, default='[]'),
    Column('onboarding_completed', Boolean, nullable=False, default=False),
    Column('learning_modality', String(32), nullable=False, default='auto'),
    Column('created_at', DateTime, nullable=False, default=utcnow),
    Column('updated_at', DateTime, nullable=False, default=utcnow),
)

MASTERY = Table(
    'adaptive_subject_mastery', _META,
    Column('id', Integer, primary_key=True),
    Column('profile_id', Integer, nullable=False, index=True),
    Column('subject', String(120), nullable=False),
    Column('topic', String(200), nullable=False),
    Column('mastery_score', Float, nullable=False, default=0.0),
    Column('confidence_level', Float, nullable=False, default=0.0),
    Column('total_attempts', Integer, nullable=False, default=0),
    Column('correct_attempts', Integer, nullable=False, default=0),
    Column('last_practiced', DateTime, nullable=False, default=utcnow),
    Column('created_at', DateTime, nullable=False, default=utcnow),
    Column('updated_at', DateTime, nullable=False, default=utcnow),
    UniqueConstraint('profile_id', 'subject', 'topic', name='uq_adaptive_mastery_topic'),
)

MISTAKE = Table(
    'adaptive_mistake_pattern', _META,
    Column('id', Integer, primary_key=True),
    Column('profile_id', Integer, nullable=False, index=True),
    Column('subject', String(120), nullable=False),
    Column('topic', String(200), nullable=False),
    Column('mistake_type', String(160), nullable=False),
    Column('description', Text, nullable=False),
    Column('frequency', Integer, nullable=False, default=1),
    Column('resolved', Boolean, nullable=False, default=False),
    Column('last_seen', DateTime, nullable=False, default=utcnow),
    Column('created_at', DateTime, nullable=False, default=utcnow),
)

LEARNER_MEMORY = Table(
    'adaptive_learner_memory', _META,
    Column('id', Integer, primary_key=True),
    Column('profile_id', Integer, nullable=False, unique=True, index=True),
    Column('learner_type', String(160), nullable=True),
    Column('confidence', Float, nullable=False, default=0.0),
    Column('summary', Text, nullable=True),
    Column('strengths_json', Text, nullable=False, default='[]'),
    Column('friction_points_json', Text, nullable=False, default='[]'),
    Column('preferred_patterns_json', Text, nullable=False, default='[]'),
    Column('recommended_strategies_json', Text, nullable=False, default='[]'),
    Column('evidence_count', Integer, nullable=False, default=0),
    Column('source_count', Integer, nullable=False, default=0),
    Column('raw_signals_json', Text, nullable=False, default='{}'),
    Column('detected_modality', String(32), nullable=True),
    Column('modality_scores_json', Text, nullable=True),
    Column('last_analyzed_at', DateTime, nullable=False, default=utcnow),
)

MEMORY_IMPORT = Table(
    'adaptive_memory_import', _META,
    Column('id', Integer, primary_key=True),
    Column('profile_id', Integer, nullable=False, index=True),
    Column('provider', String(64), nullable=False),
    Column('source_label', String(160), nullable=True),
    Column('raw_text', Text, nullable=False),
    Column('extracted_summary', Text, nullable=True),
    Column('learner_signals_json', Text, nullable=True),
    Column('created_at', DateTime, nullable=False, default=utcnow),
)

SESSION_SUMMARY = Table(
    'adaptive_session_summary', _META,
    Column('id', Integer, primary_key=True),
    Column('profile_id', Integer, nullable=False, index=True),
    Column('conversation_id', Integer, nullable=True, index=True),
    Column('summary_text', Text, nullable=True),
    Column('topics_covered_json', Text, nullable=False, default='[]'),
    Column('understood_json', Text, nullable=False, default='[]'),
    Column('struggled_json', Text, nullable=False, default='[]'),
    Column('review_next_json', Text, nullable=False, default='[]'),
    Column('started_at', DateTime, nullable=False, default=utcnow),
    Column('ended_at', DateTime, nullable=True),
)

_ALL_TABLES = [PROFILE, MASTERY, MISTAKE, LEARNER_MEMORY, MEMORY_IMPORT, SESSION_SUMMARY]


def _db():
    return current_app.extensions['sqlalchemy']


def ensure_tables() -> None:
    global _READY
    if _READY:
        return
    _META.create_all(bind=_db().engine, tables=_ALL_TABLES)
    _READY = True


def _json_load(raw: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(raw or '')
    except (TypeError, json.JSONDecodeError):
        return fallback
    return fallback if parsed is None else parsed


def current_owner() -> tuple[int | None, str | None]:
    """Resolve the profile owner: a logged-in user id, else a guest session id.

    Mirrors ``chatbot_api._get_tutor_owner`` so a guest's adaptive profile and
    their tutor conversation history stay attached to the same identity.
    """
    try:
        if current_user.is_authenticated:
            return int(current_user.id), None
    except Exception:
        pass
    if 'tutor_guest_id' not in session:
        session['tutor_guest_id'] = str(uuid.uuid4())
        session.permanent = True
        session.modified = True
    return None, session['tutor_guest_id']


def _owner_where(table):
    user_id, guest_id = current_owner()
    if user_id is not None:
        return table.c.user_id == user_id, user_id, None
    return table.c.guest_session_id == guest_id, None, guest_id


# -- Profile ---------------------------------------------------------

DEFAULT_PROFILE: dict[str, Any] = {
    'grade_level': None,
    'subjects': [],
    'short_term_goals': None,
    'long_term_goals': None,
    'explanation_style': 'balanced',
    'explanation_length': 'medium',
    'difficulty_level': 'medium',
    'interests': [],
    'onboarding_completed': False,
    'learning_modality': 'auto',
}


def _profile_to_dict(row: dict) -> dict[str, Any]:
    return {
        'id': row['id'],
        'grade_level': row['grade_level'],
        'subjects': _json_load(row['subjects_json'], []),
        'short_term_goals': row['short_term_goals'],
        'long_term_goals': row['long_term_goals'],
        'explanation_style': row['explanation_style'],
        'explanation_length': row['explanation_length'],
        'difficulty_level': row['difficulty_level'],
        'interests': _json_load(row['interests_json'], []),
        'onboarding_completed': bool(row['onboarding_completed']),
        'learning_modality': row['learning_modality'],
    }


def get_or_create_profile() -> dict[str, Any]:
    ensure_tables()
    db = _db()
    where, user_id, guest_id = _owner_where(PROFILE)

    row = db.session.execute(select(PROFILE).where(where)).mappings().first()
    if row:
        return _profile_to_dict(dict(row))

    now = utcnow()
    db.session.execute(PROFILE.insert().values(
        user_id=user_id,
        guest_session_id=guest_id,
        subjects_json='[]',
        interests_json='[]',
        onboarding_completed=False,
        created_at=now,
        updated_at=now,
    ))
    db.session.commit()
    row = db.session.execute(select(PROFILE).where(where)).mappings().first()
    return _profile_to_dict(dict(row))


_STYLE_CHOICES = {'concise', 'balanced', 'detailed'}
_LENGTH_CHOICES = {'short', 'medium', 'long'}
_DIFFICULTY_CHOICES = {'easy', 'medium', 'hard', 'adaptive'}
_MODALITY_CHOICES = {'auto', 'auditory', 'visual', 'reading', 'blended'}


def _clean_list(value: Any, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item).strip()[:120]
        if text:
            out.append(text)
    return out[:limit]


def _clean_choice(value: Any, choices: set[str], fallback: str) -> str:
    text = str(value or '').strip().lower()
    return text if text in choices else fallback


def update_profile(profile_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply an onboarding/settings patch. Unknown keys are ignored."""
    ensure_tables()
    db = _db()
    values: dict[str, Any] = {'updated_at': utcnow()}

    if 'grade_level' in payload:
        grade = str(payload.get('grade_level') or '').strip()[:64]
        values['grade_level'] = grade or None
    if 'subjects' in payload:
        values['subjects_json'] = json.dumps(_clean_list(payload.get('subjects')))
    if 'interests' in payload:
        values['interests_json'] = json.dumps(_clean_list(payload.get('interests')))
    if 'short_term_goals' in payload:
        goals = str(payload.get('short_term_goals') or '').strip()[:1200]
        values['short_term_goals'] = goals or None
    if 'long_term_goals' in payload:
        goals = str(payload.get('long_term_goals') or '').strip()[:1200]
        values['long_term_goals'] = goals or None
    if 'explanation_style' in payload:
        values['explanation_style'] = _clean_choice(
            payload.get('explanation_style'), _STYLE_CHOICES, 'balanced')
    if 'explanation_length' in payload:
        values['explanation_length'] = _clean_choice(
            payload.get('explanation_length'), _LENGTH_CHOICES, 'medium')
    if 'difficulty_level' in payload:
        values['difficulty_level'] = _clean_choice(
            payload.get('difficulty_level'), _DIFFICULTY_CHOICES, 'medium')
    if 'learning_modality' in payload:
        values['learning_modality'] = _clean_choice(
            payload.get('learning_modality'), _MODALITY_CHOICES, 'auto')
    if 'onboarding_completed' in payload:
        values['onboarding_completed'] = bool(payload.get('onboarding_completed'))

    db.session.execute(PROFILE.update().where(PROFILE.c.id == profile_id).values(**values))
    db.session.commit()

    row = db.session.execute(select(PROFILE).where(PROFILE.c.id == profile_id)).mappings().first()
    return _profile_to_dict(dict(row))


# -- Mastery ---------------------------------------------------------

def update_mastery(profile_id: int, subject: str, topic: str, correct: bool) -> None:
    """Weighted moving average: recent performance outweighs history."""
    ensure_tables()
    db = _db()
    subject = (subject or 'General').strip()[:120]
    topic = (topic or subject).strip()[:200]

    row = db.session.execute(
        select(MASTERY)
        .where(MASTERY.c.profile_id == profile_id)
        .where(MASTERY.c.subject == subject)
        .where(MASTERY.c.topic == topic)
    ).mappings().first()

    now = utcnow()
    if not row:
        db.session.execute(MASTERY.insert().values(
            profile_id=profile_id, subject=subject, topic=topic,
            total_attempts=1, correct_attempts=1 if correct else 0,
            mastery_score=60.0 if correct else 20.0,
            confidence_level=float(_CONFIDENCE_FLOOR),
            last_practiced=now, created_at=now, updated_at=now,
        ))
        db.session.commit()
        return

    attempts = int(row['total_attempts']) + 1
    correct_attempts = int(row['correct_attempts']) + (1 if correct else 0)
    raw_score = (correct_attempts / attempts) * 100
    blended = (float(row['mastery_score']) * _MASTERY_HISTORY_WEIGHT
               + raw_score * (1 - _MASTERY_HISTORY_WEIGHT))

    db.session.execute(MASTERY.update().where(MASTERY.c.id == row['id']).values(
        total_attempts=attempts,
        correct_attempts=correct_attempts,
        mastery_score=round(blended, 2),
        confidence_level=float(min(100, _CONFIDENCE_FLOOR + attempts * _CONFIDENCE_STEP)),
        last_practiced=now,
        updated_at=now,
    ))
    db.session.commit()


def list_mastery(profile_id: int, limit: int = 20) -> list[dict[str, Any]]:
    ensure_tables()
    rows = _db().session.execute(
        select(MASTERY)
        .where(MASTERY.c.profile_id == profile_id)
        .order_by(MASTERY.c.mastery_score.desc())
        .limit(limit)
    ).mappings().all()
    return [dict(r) for r in rows]


# -- Mistakes --------------------------------------------------------

def record_mistake(profile_id: int, mistake: dict[str, Any]) -> None:
    ensure_tables()
    db = _db()
    subject = str(mistake.get('subject') or 'General').strip()[:120]
    topic = str(mistake.get('topic') or subject).strip()[:200]
    mistake_type = str(
        mistake.get('mistakeType') or mistake.get('mistake_type') or 'misconception'
    ).strip()[:160]
    description = str(mistake.get('description') or '').strip()[:2000]
    if not description:
        return

    row = db.session.execute(
        select(MISTAKE)
        .where(MISTAKE.c.profile_id == profile_id)
        .where(MISTAKE.c.subject == subject)
        .where(MISTAKE.c.topic == topic)
        .where(MISTAKE.c.mistake_type == mistake_type)
        .where(MISTAKE.c.resolved.is_(False))
    ).mappings().first()

    now = utcnow()
    if row:
        db.session.execute(MISTAKE.update().where(MISTAKE.c.id == row['id']).values(
            frequency=int(row['frequency']) + 1, last_seen=now,
        ))
    else:
        db.session.execute(MISTAKE.insert().values(
            profile_id=profile_id, subject=subject, topic=topic,
            mistake_type=mistake_type, description=description,
            frequency=1, resolved=False, last_seen=now, created_at=now,
        ))
    db.session.commit()


def list_mistakes(profile_id: int, limit: int = 15,
                  include_resolved: bool = False) -> list[dict[str, Any]]:
    ensure_tables()
    query = select(MISTAKE).where(MISTAKE.c.profile_id == profile_id)
    if not include_resolved:
        query = query.where(MISTAKE.c.resolved.is_(False))
    rows = _db().session.execute(
        query.order_by(MISTAKE.c.frequency.desc()).limit(limit)
    ).mappings().all()
    return [dict(r) for r in rows]


def resolve_mistake(profile_id: int, mistake_id: int) -> bool:
    ensure_tables()
    db = _db()
    result = db.session.execute(
        MISTAKE.update()
        .where(MISTAKE.c.id == mistake_id)
        .where(MISTAKE.c.profile_id == profile_id)
        .values(resolved=True)
    )
    db.session.commit()
    return bool(result.rowcount)


# -- Learner memory --------------------------------------------------

def get_learner_memory(profile_id: int) -> dict[str, Any] | None:
    ensure_tables()
    row = _db().session.execute(
        select(LEARNER_MEMORY).where(LEARNER_MEMORY.c.profile_id == profile_id)
    ).mappings().first()
    if not row:
        return None

    row = dict(row)
    return {
        'learner_type': row['learner_type'],
        'confidence': float(row['confidence'] or 0),
        'summary': row['summary'],
        'strengths': _json_load(row['strengths_json'], []),
        'friction_points': _json_load(row['friction_points_json'], []),
        'preferred_patterns': _json_load(row['preferred_patterns_json'], []),
        'recommended_strategies': _json_load(row['recommended_strategies_json'], []),
        'evidence_count': int(row['evidence_count'] or 0),
        'source_count': int(row['source_count'] or 0),
        'detected_modality': row['detected_modality'],
        'modality_scores': _json_load(row['modality_scores_json'], None),
        'last_analyzed_at': row['last_analyzed_at'],
    }


def upsert_learner_memory(profile_id: int, analysis: dict[str, Any], source_count: int,
                          blended_scores: dict[str, float] | None,
                          detected_modality: str | None) -> None:
    ensure_tables()
    db = _db()
    now = utcnow()

    values = {
        'learner_type': (str(analysis.get('learnerType') or '')[:160] or None),
        'confidence': float(analysis.get('confidence') or 0),
        'summary': analysis.get('summary'),
        'strengths_json': json.dumps(analysis.get('strengths') or []),
        'friction_points_json': json.dumps(analysis.get('frictionPoints') or []),
        'preferred_patterns_json': json.dumps(analysis.get('preferredPatterns') or []),
        'recommended_strategies_json': json.dumps(analysis.get('recommendedStrategies') or []),
        'source_count': int(source_count),
        'raw_signals_json': json.dumps(analysis.get('learnerSignals') or {}),
        'detected_modality': detected_modality,
        'modality_scores_json': json.dumps(blended_scores) if blended_scores else None,
        'last_analyzed_at': now,
    }

    existing = db.session.execute(
        select(LEARNER_MEMORY.c.id, LEARNER_MEMORY.c.evidence_count)
        .where(LEARNER_MEMORY.c.profile_id == profile_id)
    ).mappings().first()

    if existing:
        values['evidence_count'] = int(existing['evidence_count'] or 0) + 1
        db.session.execute(
            LEARNER_MEMORY.update().where(LEARNER_MEMORY.c.id == existing['id']).values(**values)
        )
    else:
        values['evidence_count'] = 1
        values['profile_id'] = profile_id
        db.session.execute(LEARNER_MEMORY.insert().values(**values))
    db.session.commit()


# -- Memory imports --------------------------------------------------

def save_memory_import(profile_id: int, provider: str, raw_text: str,
                       source_label: str | None = None,
                       extracted_summary: str | None = None,
                       learner_signals: dict[str, Any] | None = None) -> int:
    ensure_tables()
    db = _db()
    result = db.session.execute(MEMORY_IMPORT.insert().values(
        profile_id=profile_id,
        provider=str(provider or 'unknown')[:64],
        source_label=str(source_label)[:160] if source_label else None,
        raw_text=str(raw_text)[:200000],
        extracted_summary=extracted_summary,
        learner_signals_json=json.dumps(learner_signals) if learner_signals else None,
        created_at=utcnow(),
    ))
    db.session.commit()
    return int(result.inserted_primary_key[0])


def list_memory_imports(profile_id: int, limit: int = 10) -> list[dict[str, Any]]:
    ensure_tables()
    rows = _db().session.execute(
        select(MEMORY_IMPORT)
        .where(MEMORY_IMPORT.c.profile_id == profile_id)
        .order_by(MEMORY_IMPORT.c.created_at.desc())
        .limit(limit)
    ).mappings().all()
    return [dict(r) for r in rows]


def delete_memory_import(profile_id: int, import_id: int) -> bool:
    ensure_tables()
    db = _db()
    result = db.session.execute(
        MEMORY_IMPORT.delete()
        .where(MEMORY_IMPORT.c.id == import_id)
        .where(MEMORY_IMPORT.c.profile_id == profile_id)
    )
    db.session.commit()
    return bool(result.rowcount)


# -- Session summaries -----------------------------------------------

def save_session_summary(profile_id: int, conversation_id: int | None,
                         summary: dict[str, Any]) -> None:
    ensure_tables()
    db = _db()
    now = utcnow()
    values = {
        'summary_text': str(summary.get('summaryText') or '')[:4000] or None,
        'topics_covered_json': json.dumps(_clean_list(summary.get('topicsCovered'), 30)),
        'understood_json': json.dumps(_clean_list(summary.get('understood'), 30)),
        'struggled_json': json.dumps(_clean_list(summary.get('struggled'), 30)),
        'review_next_json': json.dumps(_clean_list(summary.get('reviewNext'), 30)),
        'ended_at': now,
    }

    existing = None
    if conversation_id is not None:
        existing = db.session.execute(
            select(SESSION_SUMMARY.c.id)
            .where(SESSION_SUMMARY.c.profile_id == profile_id)
            .where(SESSION_SUMMARY.c.conversation_id == conversation_id)
        ).mappings().first()

    if existing:
        db.session.execute(
            SESSION_SUMMARY.update()
            .where(SESSION_SUMMARY.c.id == existing['id'])
            .values(**values)
        )
    else:
        values['profile_id'] = profile_id
        values['conversation_id'] = conversation_id
        values['started_at'] = now
        db.session.execute(SESSION_SUMMARY.insert().values(**values))
    db.session.commit()


def list_session_summaries(profile_id: int, limit: int = 5) -> list[dict[str, Any]]:
    ensure_tables()
    rows = _db().session.execute(
        select(SESSION_SUMMARY)
        .where(SESSION_SUMMARY.c.profile_id == profile_id)
        .order_by(SESSION_SUMMARY.c.started_at.desc())
        .limit(limit)
    ).mappings().all()

    out = []
    for row in rows:
        row = dict(row)
        out.append({
            'id': row['id'],
            'conversation_id': row['conversation_id'],
            'summary_text': row['summary_text'],
            'topics_covered': _json_load(row['topics_covered_json'], []),
            'understood': _json_load(row['understood_json'], []),
            'struggled': _json_load(row['struggled_json'], []),
            'review_next': _json_load(row['review_next_json'], []),
            'started_at': row['started_at'],
            'ended_at': row['ended_at'],
        })
    return out


# -- Aggregate context -----------------------------------------------

def get_student_context() -> dict[str, Any]:
    """Everything the prompt builder needs, in one call."""
    profile = get_or_create_profile()
    profile_id = profile['id']
    return {
        'profile': profile,
        'mastery': list_mastery(profile_id),
        'mistakes': list_mistakes(profile_id),
        'recent_sessions': list_session_summaries(profile_id),
        'learner_memory': get_learner_memory(profile_id),
        'memory_imports': list_memory_imports(profile_id),
    }
