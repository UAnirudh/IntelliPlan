"""Per-turn orchestration for the adaptive Plani tutor.

This is the layer ``chatbot_api`` calls. It owns three moments:

``prepare_turn``          before the LLM call - assemble the student model into a
                          system message and decide which output channels are live
``record_turn``           after the LLM call - refresh the durable learner memory
``summarize_conversation`` when a conversation closes - write a session recap
                          and possible friction points without scoring mastery

Every entry point swallows its own failures. The tutor must answer even when
the analysis passes are down.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from flask_login import current_user

from adaptive_tutor import analysis, checks, education, modality as modality_lib, store
from adaptive_tutor.prompt import build_adaptive_prompt

logger = logging.getLogger(__name__)

_SUBJECT_TAG = re.compile(r'^\[Subject:\s*([^\]]+)\]', re.I)
#: The learner memory pass is expensive; only re-run it every N tutor turns.
_MEMORY_REFRESH_EVERY = 3


def _conversation_context() -> dict[str, Any]:
    """Use server-scored checks in teaching; exclude legacy inferred scores.

    Older sessions created scores from a model-written recap. Those rows have
    no independent answer evidence or provenance, so they cannot establish a
    skill level. Preserve them in storage for an eventual migration, but do
    not present or act on them as scored practice.
    """
    context = store.get_student_context()
    practice = []
    try:
        if current_user.is_authenticated:
            owner_id = int(current_user.id)
            grade = checks.selected_grade(owner_id)
            practice = checks.evidence(owner_id, grade) if grade is not None else []
    except Exception as exc:
        logger.warning('adaptive tutor: scored checks unavailable: %s', exc)
    education_context = None
    try:
        education_context = education.tutor_context()
    except Exception as exc:
        logger.warning('adaptive tutor: education plan unavailable: %s', exc)
    return {**context, 'mastery': practice, 'education_plan': education_context}


def split_subject(text: str) -> tuple[str, str]:
    """Split IntelliPlan's ``[Subject: X]`` prefix off a user message."""
    match = _SUBJECT_TAG.match(str(text or ''))
    if not match:
        return 'General', str(text or '').strip()
    subject = match.group(1).strip() or 'General'
    return subject, _SUBJECT_TAG.sub('', str(text), count=1).strip()


def resolve_weights(context: dict[str, Any]) -> modality_lib.ModalityWeights:
    memory = context.get('learner_memory') or {}
    stored = memory.get('modality_scores')
    if not stored:
        return dict(modality_lib.DEFAULT_WEIGHTS)  # type: ignore[return-value]
    return modality_lib.normalize_weights(stored)


def prepare_turn(mode_override: str | None = None, student_message: str = '') -> dict[str, Any]:
    """Load the student model and build this turn's adaptive system message."""
    context = _conversation_context()
    profile = context['profile']

    mode = (mode_override or profile.get('learning_modality') or 'auto').strip().lower()
    if mode not in modality_lib.MODALITY_MODES:
        mode = 'auto'

    weights = resolve_weights(context)
    active = modality_lib.get_active_modalities(mode, weights)

    subject, body = split_subject(student_message)

    prompt = build_adaptive_prompt(
        context,
        use_voice=active['use_voice'],
        use_artifacts=active['use_artifacts'],
        focus_subject=subject,
        focus_text=body,
    )

    return {
        'context': context,
        'profile': profile,
        'mode': mode,
        'weights': weights,
        'active': active,
        'prompt': prompt,
    }


def _should_refresh_memory(context: dict[str, Any], turn_count: int) -> bool:
    if not context.get('learner_memory'):
        return True
    return turn_count % _MEMORY_REFRESH_EVERY == 0


def record_turn(chat: Callable[..., str], turn: dict[str, Any],
                history: list[dict[str, Any]], user_message: str, reply: str) -> None:
    """Refresh the durable learner memory after a tutor exchange."""
    try:
        context = turn['context']
        profile = turn['profile']
        turn_count = len(history) // 2 + 1

        if not _should_refresh_memory(context, turn_count):
            return

        transcript = list(history[-8:]) + [
            {'role': 'user', 'content': user_message},
            {'role': 'assistant', 'content': reply},
        ]

        result = analysis.analyze_learner_memory(
            chat,
            existing_summary=(context.get('learner_memory') or {}).get('summary'),
            profile={
                'gradeLevel': profile.get('grade_level'),
                'subjects': profile.get('subjects'),
                'shortTermGoals': profile.get('short_term_goals'),
                'longTermGoals': profile.get('long_term_goals'),
                'explanationStyle': profile.get('explanation_style'),
                'explanationLength': profile.get('explanation_length'),
                'difficultyLevel': profile.get('difficulty_level'),
                'interests': profile.get('interests'),
            },
            imported_memories=[
                {
                    'provider': item.get('provider'),
                    'source_label': item.get('source_label'),
                    'text': item.get('extracted_summary') or item.get('raw_text'),
                }
                for item in context.get('memory_imports') or []
            ],
            recent_transcript=transcript,
        )

        blended = None
        detected = None
        if result.get('modalityScores'):
            previous = (context.get('learner_memory') or {}).get('modality_scores')
            blended = modality_lib.blend_weights(previous, result['modalityScores'])
            detected = modality_lib.get_dominant_modality(blended)

        store.upsert_learner_memory(
            profile['id'],
            result,
            source_count=len(context.get('memory_imports') or []),
            blended_scores=blended,
            detected_modality=detected,
        )
    except Exception as exc:
        logger.warning('adaptive tutor: learner memory update failed: %s', exc)


def summarize_conversation(chat: Callable[..., str], conversation_id: int | None,
                           messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Close out a conversation without treating AI impressions as scores."""
    profile = store.get_or_create_profile()
    profile_id = profile['id']

    subject = 'General'
    for message in reversed(messages or []):
        if message.get('role') == 'user':
            subject, _ = split_subject(message.get('content', ''))
            break

    summary = analysis.generate_session_summary(chat, messages)
    store.save_session_summary(profile_id, conversation_id, summary)

    mistakes = analysis.extract_mistakes(chat, messages)
    for mistake in mistakes:
        try:
            store.record_mistake(profile_id, mistake)
        except Exception as exc:
            logger.warning('adaptive tutor: mistake record failed: %s', exc)

    return {'summary': summary, 'mistakes': mistakes, 'subject': subject}


def build_dashboard() -> dict[str, Any]:
    """Everything the progress dashboard renders."""
    context = _conversation_context()
    profile = context['profile']
    mastery = context['mastery']
    mistakes = context['mistakes']
    sessions = context['recent_sessions']
    memory = context['learner_memory']

    recent_topics = []
    seen_topics = set()
    for session in sessions[:5]:
        for topic in session.get('topics_covered') or []:
            normalized = str(topic).strip()[:120]
            if normalized and normalized.casefold() not in seen_topics:
                seen_topics.add(normalized.casefold())
                recent_topics.append(normalized)

    weights = resolve_weights(context)
    scores = [float(m.get('mastery_score') or 0) for m in mastery]
    average = round(sum(scores) / len(scores), 1) if scores else 0.0

    weakest = sorted(mastery, key=lambda m: float(m.get('mastery_score') or 0))[:5]
    recommendations = [f"Try another independent check on {m.get('topic')}"
                       for m in weakest]
    for row in sessions[:1]:
        for topic in (row.get('review_next') or [])[:3]:
            recommendations.append(f'Follow up on {topic} from your last session')

    return {
        'profile': profile,
        'mastery': [
            {
                'subject': m.get('subject'),
                'topic': m.get('topic'),
                'mastery_score': round(float(m.get('mastery_score') or 0), 1),
                'confidence_level': round(float(m.get('confidence_level') or 0), 1),
                'total_attempts': m.get('total_attempts'),
                'correct_attempts': m.get('correct_attempts'),
                'independent_correct': m.get('independent_correct'),
                'grade': m.get('grade'),
                'source': 'scored_check',
            }
            for m in mastery
        ],
        'mistakes': [
            {
                'id': m.get('id'),
                'subject': m.get('subject'),
                'topic': m.get('topic'),
                'mistake_type': m.get('mistake_type'),
                'description': m.get('description'),
                'frequency': m.get('frequency'),
            }
            for m in mistakes
        ],
        'sessions': [
            {
                'id': s.get('id'),
                'conversation_id': s.get('conversation_id'),
                'summary_text': s.get('summary_text'),
                'topics_covered': s.get('topics_covered'),
                'understood': s.get('understood'),
                'struggled': s.get('struggled'),
                'review_next': s.get('review_next'),
                'ended_at': s['ended_at'].isoformat() if s.get('ended_at') else None,
            }
            for s in sessions
        ],
        'learner_memory': (
            {
                'learner_type': memory.get('learner_type'),
                'confidence': memory.get('confidence'),
                'summary': memory.get('summary'),
                'strengths': memory.get('strengths'),
                'friction_points': memory.get('friction_points'),
                'preferred_patterns': memory.get('preferred_patterns'),
                'recommended_strategies': memory.get('recommended_strategies'),
                'evidence_count': memory.get('evidence_count'),
                'detected_modality': memory.get('detected_modality'),
            }
            if memory else None
        ),
        'modality': {
            'mode': profile.get('learning_modality') or 'auto',
            'weights': weights,
            'detected': modality_lib.get_dominant_modality(weights),
            'labels': modality_lib.MODALITY_LABELS,
        },
        'stats': {
            'average_mastery': average,
            'topics_tracked': len(mastery),
            'active_mistakes': len(mistakes),
            'sessions_summarized': len(sessions),
        },
        'recommendations': recommendations[:6],
        'recent_topics': recent_topics[:8],
    }
