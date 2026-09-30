"""Flask routes for the adaptive tutor student model.

Mounted under ``/api/tutor/adaptive``. The chat turn itself still runs through
``chatbot_api``'s ``/api/tutor``; these endpoints cover onboarding, the
progress dashboard, memory imports, modality control, and session close-out.
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired

from adaptive_tutor import checks, education, engine, modality as modality_lib, store

logger = logging.getLogger(__name__)

adaptive_tutor_bp = Blueprint('adaptive_tutor', __name__)

_MAX_IMPORT_CHARS = 200_000


@adaptive_tutor_bp.route('/api/tutor/adaptive/education-plan', methods=['GET', 'POST', 'DELETE'])
def education_plan():
    from chatbot_api import _schoolwork_access_error
    access_error = _check_access() if request.method == 'DELETE' else _schoolwork_access_error()
    if access_error:
        return access_error
    owner_id = int(current_user.id)
    try:
        if request.method == 'DELETE':
            education.delete(owner_id)
            result = {'plan': None}
        elif request.method == 'GET':
            result = {'plan': education.load(owner_id)}
        else:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({'error': 'Enter your education goal.'}), 400
            goal = education.validate_goal(payload.get('goal'))
            revision = payload.get('revision', 0)
            if type(revision) is not int or revision < 0:
                return jsonify({'error': 'Reload your plan before rebuilding.'}), 400
            existing = education.load(owner_id)
            if revision != (existing['revision'] if existing else 0):
                return jsonify({'error': 'Your plan changed in another tab. Reload it first.'}), 409
            snapshot = education.collect_snapshot(owner_id)
            prompts = education.plan_messages(goal, snapshot)
            import ai_firewall
            from ai_firewall import AIBlocked
            try:
                decision = ai_firewall.guard(current_user, prompts=[prompts[-1]['content']],
                                             want_output_tokens=2200, feature='tutor')
                raw = _chat()(model='llama-3.3-70b-versatile', messages=prompts,
                              temperature=0.2, max_tokens=min(2200, decision.max_output_tokens),
                              response_format={'type': 'json_object'}, plan=decision.plan)
                ai_firewall.record_tokens(decision, sum(len(p['content']) for p in prompts), len(raw))
            except AIBlocked as exc:
                return jsonify({'error': exc.message}), exc.status
            steps = education.parse_steps(raw, goal, snapshot)
            education.save(owner_id, goal, snapshot, steps, revision)
            result = {'plan': education.load(owner_id)}
        response = jsonify(result)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        logger.exception('adaptive tutor: education plan failed')
        return jsonify({'error': 'Your plan could not be loaded or updated. Your saved plan has been kept.'}), 503


@adaptive_tutor_bp.route('/api/tutor/adaptive/education-plan/steps/<step_id>', methods=['PATCH'])
def education_progress(step_id):
    from chatbot_api import _schoolwork_access_error
    access_error = _schoolwork_access_error()
    if access_error:
        return access_error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
        return jsonify({'error': 'Reload your plan before updating progress.'}), 400
    try:
        education.set_progress(int(current_user.id), step_id,
                               payload.get('status'), payload['revision'])
        response = jsonify({'plan': education.load(int(current_user.id))})
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except LookupError as exc:
        return jsonify({'error': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 409
    except Exception:
        logger.exception('adaptive tutor: education progress failed')
        return jsonify({'error': 'Progress could not be saved. Please reload your plan.'}), 503


def _check_access():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Sign in to save your practice checks.'}), 401
    birth_year = getattr(current_user, 'birth_year', None)
    if birth_year is None:
        return jsonify({'error': 'Complete the age check before saving practice.',
                        'age_check_url': '/account/age?next=/tutor'}), 403
    if datetime.utcnow().year - int(birth_year) < 13 and not getattr(current_user, 'parent_consent_granted', False):
        return jsonify({'error': 'A parent must approve this account before practice can be saved.'}), 403
    return None


def _challenge_or_error(payload):
    token = payload.get('token')
    if not isinstance(token, str) or len(token) > 600:
        return None, (jsonify({'error': 'Choose a valid practice check.'}), 400)
    try:
        challenge = checks.signer().loads(token, max_age=1800)
    except SignatureExpired:
        return None, (jsonify({'error': 'This check expired. Start another one.'}), 410)
    except BadSignature:
        return None, (jsonify({'error': 'Choose a valid practice check.'}), 400)
    if (not isinstance(challenge, dict)
            or challenge.get('owner_id') != int(current_user.id)
            or challenge.get('grade') != checks.selected_grade(int(current_user.id))
            or not isinstance(challenge.get('nonce'), str)
            or not isinstance(challenge.get('item_id'), str)
            or not isinstance(challenge.get('skill_id'), str)
            or type(challenge.get('sequence')) is not int
            or challenge['sequence'] < 0):
        return None, (jsonify({'error': 'This check belongs to another profile or grade. Start another one.'}), 409)
    return challenge, None


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
    """Close a conversation with a recap and possible friction points."""
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


@adaptive_tutor_bp.route('/api/tutor/adaptive/check', methods=['POST'])
def new_check():
    access_error = _check_access()
    if access_error:
        return access_error
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    area = payload.get('area')
    if area not in checks.DOMAINS:
        return jsonify({'error': 'Choose math, reading, or writing.'}), 400
    skill_id = payload.get('skill_id')
    if skill_id is not None and (not isinstance(skill_id, str) or len(skill_id) > 32):
        return jsonify({'error': 'Choose a valid skill.'}), 400
    grade = checks.selected_grade(int(current_user.id))
    if grade is None:
        return jsonify({'error': 'Set a grade from kindergarten through grade 12, or college, in your Learning profile.'}), 409
    try:
        result = checks.choose(int(current_user.id), grade, area, skill_id)
        response = jsonify(result)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        logger.exception('adaptive tutor: check selection failed')
        return jsonify({'error': 'A practice check is unavailable right now.'}), 503


@adaptive_tutor_bp.route('/api/tutor/adaptive/check/path', methods=['GET'])
def check_path():
    access_error = _check_access()
    if access_error:
        return access_error
    grade = checks.selected_grade(int(current_user.id))
    if grade is None:
        return jsonify({'error': 'Set a grade in your Learning profile to see your practice path.'}), 409
    try:
        response = jsonify({'grade': grade, 'path': checks.practice_path(int(current_user.id), grade)})
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except Exception:
        logger.exception('adaptive tutor: practice path failed')
        return jsonify({'error': 'Your practice path is unavailable right now.'}), 503


@adaptive_tutor_bp.route('/api/tutor/adaptive/check/hint', methods=['POST'])
def check_hint():
    access_error = _check_access()
    if access_error:
        return access_error
    payload = request.get_json(silent=True)
    challenge, error = _challenge_or_error(payload if isinstance(payload, dict) else {})
    if error:
        return error
    try:
        hint = checks.reveal_hint(int(current_user.id), challenge)
        response = jsonify({'hint': hint})
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 409
    except Exception:
        logger.exception('adaptive tutor: check hint failed')
        return jsonify({'error': 'The hint is unavailable right now.'}), 503


@adaptive_tutor_bp.route('/api/tutor/adaptive/check/answer', methods=['POST'])
def check_answer():
    access_error = _check_access()
    if access_error:
        return access_error
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    response_text = payload.get('answer')
    if not isinstance(response_text, str) or not response_text.strip() or len(response_text) > 200:
        return jsonify({'error': 'Enter an answer of 1 to 200 characters.'}), 400
    challenge, error = _challenge_or_error(payload)
    if error:
        return error
    try:
        result = checks.answer(int(current_user.id), challenge, response_text)
        response = jsonify(result)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 409
    except Exception:
        logger.exception('adaptive tutor: check answer failed')
        return jsonify({'error': 'Your answer could not be checked right now.'}), 503
