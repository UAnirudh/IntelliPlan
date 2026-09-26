"""Authenticated Foundations API."""

import secrets

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from primer import store
from primer.catalog import ITEM_BY_ID, SKILL_BY_ID, WORLDS, normalize_answer, public_item


primer_bp = Blueprint('primer', __name__)


@primer_bp.after_request
def _private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def _owner():
    return int(current_user.id) if current_user.is_authenticated else None


def _payload():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='primer-challenge-v1')


def _learner_or_error(learner_id):
    owner = _owner()
    if owner is None:
        return None, (jsonify({'error': 'Sign in to use Foundations.'}), 401)
    learner = store.get_learner(owner, learner_id)
    if not learner:
        return None, (jsonify({'error': 'Learner not found.'}), 404)
    return learner, None


def _public_learner(row):
    return {'id': row['id'], 'nickname': row['nickname'], 'world': row['world']}


@primer_bp.route('/api/primer/learners', methods=['GET', 'POST'])
def learners():
    owner = _owner()
    if owner is None:
        return jsonify({'error': 'Sign in to use Foundations.'}), 401
    if request.method == 'GET':
        return jsonify({'learners': [_public_learner(row) for row in store.list_learners(owner)]})
    payload = _payload()
    nickname = str(payload.get('nickname') or '').strip()
    world = str(payload.get('world') or '').strip()
    if not nickname or len(nickname) > 40 or any(ord(char) < 32 for char in nickname):
        return jsonify({'error': 'Use a nickname of 1 to 40 characters.'}), 400
    if world not in WORLDS:
        return jsonify({'error': 'Choose a story world.'}), 400
    if len(store.list_learners(owner)) >= 8:
        return jsonify({'error': 'This account has reached its learner limit.'}), 409
    learner = store.create_learner(owner, nickname, world)
    return jsonify({'learner': _public_learner(learner)}), 201


@primer_bp.route('/api/primer/learners/<int:learner_id>', methods=['DELETE'])
def remove_learner(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    store.delete_learner(_owner(), learner['id'])
    return jsonify({'ok': True})


@primer_bp.route('/api/primer/learners/<int:learner_id>/progress', methods=['GET'])
def learner_progress(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    return jsonify({'learner': _public_learner(learner), **store.progress(learner_id)})


@primer_bp.route('/api/primer/learners/<int:learner_id>/activity', methods=['GET'])
def activity(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    skill_id, item = store.choose_activity(learner_id)
    token = _signer().dumps({'learner_id': learner_id, 'item_id': item.id, 'nonce': secrets.token_urlsafe(18)})
    return jsonify({
        'learner': _public_learner(learner),
        'skill': {'id': skill_id, 'domain': SKILL_BY_ID[skill_id].domain, 'title': SKILL_BY_ID[skill_id].title},
        'world_name': WORLDS[learner['world']]['name'],
        'item': public_item(item, learner['world']),
        'token': token,
    })


@primer_bp.route('/api/primer/learners/<int:learner_id>/answer', methods=['POST'])
def answer(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    payload = _payload()
    token = payload.get('token')
    response = payload.get('answer')
    if not isinstance(token, str) or not isinstance(response, str) or len(response) > 200 or not response.strip():
        return jsonify({'error': 'Enter an answer for the current activity.'}), 400
    try:
        challenge = _signer().loads(token, max_age=3600)
    except SignatureExpired:
        return jsonify({'error': 'This activity expired. Start the next one.'}), 410
    except BadSignature:
        return jsonify({'error': 'Invalid activity.'}), 400
    if not isinstance(challenge, dict) or challenge.get('learner_id') != learner_id:
        return jsonify({'error': 'Invalid activity.'}), 400
    item = ITEM_BY_ID.get(challenge.get('item_id'))
    nonce = challenge.get('nonce')
    if not item or not isinstance(nonce, str) or len(nonce) > 40:
        return jsonify({'error': 'Invalid activity.'}), 400
    correct = normalize_answer(response) == normalize_answer(item.answer)
    try:
        state = store.record_attempt(learner['id'], item.skill_id, item.id, nonce, correct)
    except IntegrityError:
        return jsonify({'error': 'This activity was already answered. Start the next one.'}), 409
    return jsonify({
        'correct': correct,
        'feedback': item.explanation if correct else item.hint,
        'skill': {'id': item.skill_id, 'title': SKILL_BY_ID[item.skill_id].title},
        'evidence': {'attempts': state['attempts'], 'correct': state['correct'], 'estimate': state['estimate']},
    })
