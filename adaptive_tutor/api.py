"""Flask routes for the adaptive tutor student model.

Mounted under ``/api/tutor/adaptive``. The chat turn itself still runs through
``chatbot_api``'s ``/api/tutor``; these endpoints cover onboarding, the
progress dashboard, memory imports, modality control, and session close-out.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from adaptive_tutor import engine, modality as modality_lib, store

logger = logging.getLogger(__name__)

adaptive_tutor_bp = Blueprint('adaptive_tutor', __name__)

_MAX_IMPORT_CHARS = 200_000


def _chat():
    """Lazy import: chatbot_api imports this package's engine indirectly."""
    from chatbot_api import _llm_chat
    return _llm_chat


@adaptive_tutor_bp.route('/api/tutor/adaptive/profile', methods=['GET'])
def get_profile():
    try:
        profile = store.get_or_create_profile()
        context = store.get_student_context()
        return jsonify({
            'profile': profile,
            'modality': {
                'mode': profile.get('learning_modality') or 'auto',
                'weights': engine.resolve_weights(context),
                'labels': modality_lib.MODALITY_LABELS,
            },
        })
    except Exception as exc:
        logger.exception('adaptive tutor: profile load failed')
        return jsonify({'error': 'profile load failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/profile', methods=['POST'])
def save_profile():
    """Save onboarding answers. Partial patches are allowed."""
    try:
        payload = request.get_json(silent=True) or {}
        profile = store.get_or_create_profile()
        updated = store.update_profile(profile['id'], payload)
        return jsonify({'profile': updated})
    except Exception as exc:
        logger.exception('adaptive tutor: profile save failed')
        return jsonify({'error': 'profile save failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/modality', methods=['POST'])
def set_modality():
    try:
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get('mode') or 'auto').strip().lower()
        if mode not in modality_lib.MODALITY_MODES:
            return jsonify({'error': 'invalid mode'}), 400

        profile = store.get_or_create_profile()
        updated = store.update_profile(profile['id'], {'learning_modality': mode})
        context = store.get_student_context()
        weights = engine.resolve_weights(context)

        return jsonify({
            'mode': updated.get('learning_modality'),
            'weights': weights,
            'active': modality_lib.get_active_modalities(mode, weights),
        })
    except Exception as exc:
        logger.exception('adaptive tutor: modality update failed')
        return jsonify({'error': 'modality update failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/dashboard', methods=['GET'])
def dashboard():
    try:
        return jsonify(engine.build_dashboard())
    except Exception as exc:
        logger.exception('adaptive tutor: dashboard failed')
        return jsonify({'error': 'dashboard failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/summarize', methods=['POST'])
def summarize():
    """Close a conversation: summary, mistake extraction, mastery movement."""
    try:
        payload = request.get_json(silent=True) or {}
        conversation_id = payload.get('conversation_id')
        messages = payload.get('messages')

        if not messages and conversation_id:
            from chatbot_api import _get_conversation, _safe_json
            row = _get_conversation(int(conversation_id))
            messages = _safe_json((row or {}).get('messages_json'), [])

        messages = [
            m for m in (messages or [])
            if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and m.get('content')
        ]
        if len(messages) < 2:
            return jsonify({'error': 'not enough conversation to summarize'}), 400

        result = engine.summarize_conversation(
            _chat(),
            int(conversation_id) if conversation_id else None,
            messages,
        )
        return jsonify(result)
    except Exception as exc:
        logger.exception('adaptive tutor: summarize failed')
        return jsonify({'error': 'summarize failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/memory-imports', methods=['GET'])
def list_imports():
    try:
        profile = store.get_or_create_profile()
        rows = store.list_memory_imports(profile['id'])
        return jsonify({
            'imports': [
                {
                    'id': row['id'],
                    'provider': row['provider'],
                    'source_label': row['source_label'],
                    'extracted_summary': row['extracted_summary'],
                    'preview': str(row['raw_text'] or '')[:280],
                    'created_at': row['created_at'].isoformat() if row.get('created_at') else None,
                }
                for row in rows
            ]
        })
    except Exception as exc:
        logger.exception('adaptive tutor: import list failed')
        return jsonify({'error': 'import list failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/memory-imports', methods=['POST'])
def create_import():
    """Import learner context exported from another AI provider."""
    try:
        payload = request.get_json(silent=True) or {}
        raw_text = str(payload.get('raw_text') or '').strip()
        if not raw_text:
            return jsonify({'error': 'raw_text is required'}), 400
        if len(raw_text) > _MAX_IMPORT_CHARS:
            return jsonify({'error': f'raw_text exceeds {_MAX_IMPORT_CHARS} characters'}), 413

        provider = str(payload.get('provider') or 'unknown').strip()[:64]
        source_label = str(payload.get('source_label') or '').strip()[:160] or None

        profile = store.get_or_create_profile()
        import_id = store.save_memory_import(
            profile['id'], provider, raw_text, source_label=source_label
        )

        # Rebuild the durable learner model immediately so the import is felt
        # on the very next message rather than several turns later.
        context = store.get_student_context()
        analysis_result = None
        try:
            from adaptive_tutor import analysis as analysis_lib
            analysis_result = analysis_lib.analyze_learner_memory(
                _chat(),
                existing_summary=(context.get('learner_memory') or {}).get('summary'),
                profile=context['profile'],
                imported_memories=[
                    {
                        'provider': item.get('provider'),
                        'source_label': item.get('source_label'),
                        'text': item.get('extracted_summary') or item.get('raw_text'),
                    }
                    for item in context.get('memory_imports') or []
                ],
            )
            blended = None
            detected = None
            if analysis_result.get('modalityScores'):
                blended = modality_lib.blend_weights(
                    (context.get('learner_memory') or {}).get('modality_scores'),
                    analysis_result['modalityScores'],
                )
                detected = modality_lib.get_dominant_modality(blended)
            store.upsert_learner_memory(
                profile['id'], analysis_result,
                source_count=len(context.get('memory_imports') or []),
                blended_scores=blended, detected_modality=detected,
            )
        except Exception as exc:
            logger.warning('adaptive tutor: post-import analysis failed: %s', exc)

        return jsonify({
            'id': import_id,
            'provider': provider,
            'learner_memory': store.get_learner_memory(profile['id']),
        })
    except Exception as exc:
        logger.exception('adaptive tutor: import failed')
        return jsonify({'error': 'import failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/memory-imports/<int:import_id>', methods=['DELETE'])
def delete_import(import_id: int):
    try:
        profile = store.get_or_create_profile()
        ok = store.delete_memory_import(profile['id'], import_id)
        return jsonify({'ok': ok}), (200 if ok else 404)
    except Exception as exc:
        logger.exception('adaptive tutor: import delete failed')
        return jsonify({'error': 'import delete failed', 'detail': str(exc)}), 500


@adaptive_tutor_bp.route('/api/tutor/adaptive/mistakes/<int:mistake_id>/resolve', methods=['POST'])
def resolve_mistake(mistake_id: int):
    try:
        profile = store.get_or_create_profile()
        ok = store.resolve_mistake(profile['id'], mistake_id)
        return jsonify({'ok': ok}), (200 if ok else 404)
    except Exception as exc:
        logger.exception('adaptive tutor: mistake resolve failed')
        return jsonify({'error': 'resolve failed', 'detail': str(exc)}), 500
