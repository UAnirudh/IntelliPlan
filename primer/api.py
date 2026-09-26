"""Authenticated Foundations API."""

import secrets

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from primer import store
from primer.catalog import ITEM_BY_ID, SKILL_BY_ID, WORLDS, grade_item, public_item
from primer.story import BEATS, valid_choice, view as story_view


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
    journey = store.journey_for(learner_id)
    return jsonify({'learner': _public_learner(learner), **store.progress(learner_id),
                    'story': story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'],
                                        bool(journey['repair_skill_id']))})


@primer_bp.route('/api/primer/learners/<int:learner_id>/activity', methods=['GET'])
def activity(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    journey = store.journey_for(learner_id)
    story = story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'],
                       bool(journey['repair_skill_id']))
    response = {
        'learner': _public_learner(learner),
        'world_name': WORLDS[learner['world']]['name'],
        'story': story,
    }
    if story['complete']:
        return jsonify(response)
    if story['awaiting_choice']:
        response['token'] = _signer().dumps({'kind': 'choice', 'learner_id': learner_id,
                                            'version': journey['version'], 'chapter': journey['chapter']})
        return jsonify(response)
    skill_id, item = store.choose_story_activity(learner_id, BEATS[journey['beat']][0],
                                                 journey['repair_skill_id'])
    response.update({
        'skill': {'id': skill_id, 'domain': SKILL_BY_ID[skill_id].domain, 'title': SKILL_BY_ID[skill_id].title},
        'item': public_item(item, learner['world']),
        'token': _signer().dumps({'kind': 'activity', 'learner_id': learner_id, 'item_id': item.id,
                                  'version': journey['version'], 'nonce': secrets.token_urlsafe(18)}),
    })
    return jsonify(response)


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
    if not isinstance(challenge, dict) or challenge.get('kind') != 'activity' or challenge.get('learner_id') != learner_id:
        return jsonify({'error': 'Invalid activity.'}), 400
    item = ITEM_BY_ID.get(challenge.get('item_id'))
    nonce = challenge.get('nonce')
    version = challenge.get('version')
    if not item or not isinstance(nonce, str) or len(nonce) > 40 or type(version) is not int:
        return jsonify({'error': 'Invalid activity.'}), 400
    correct, feedback = grade_item(item, response)
    try:
        state = store.record_attempt(learner['id'], item.skill_id, item.id, nonce, correct,
                                     expected_journey_version=version)
    except store.StaleJourney:
        return jsonify({'error': 'This chapter has moved on. Load the current step.'}), 409
    except IntegrityError:
        return jsonify({'error': 'This activity was already answered. Start the next one.'}), 409
    return jsonify({
        'correct': correct,
        'feedback': feedback,
        'retry': bool(store.journey_for(learner_id)['repair_skill_id']),
        'skill': {'id': item.skill_id, 'title': SKILL_BY_ID[item.skill_id].title},
        'evidence': {'attempts': state['attempts'], 'correct': state['correct'],
                     'estimate': state['estimate'], 'clue_used': state['hint_used']},
    })


@primer_bp.route('/api/primer/learners/<int:learner_id>/hint', methods=['POST'])
def hint(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    token = _payload().get('token')
    if not isinstance(token, str):
        return jsonify({'error': 'Invalid activity.'}), 400
    try:
        challenge = _signer().loads(token, max_age=3600)
    except SignatureExpired:
        return jsonify({'error': 'This activity expired. Load the current step.'}), 410
    except BadSignature:
        return jsonify({'error': 'Invalid activity.'}), 400
    if (not isinstance(challenge, dict) or challenge.get('kind') != 'activity'
            or challenge.get('learner_id') != learner_id or type(challenge.get('version')) is not int
            or not isinstance(challenge.get('nonce'), str) or len(challenge['nonce']) > 40):
        return jsonify({'error': 'Invalid activity.'}), 400
    item = ITEM_BY_ID.get(challenge.get('item_id'))
    if not item:
        return jsonify({'error': 'Invalid activity.'}), 400
    try:
        store.reveal_hint(learner['id'], challenge['nonce'], challenge['version'])
    except store.StaleJourney:
        return jsonify({'error': 'This chapter has moved on. Load the current step.'}), 409
    return jsonify({'hint': item.hint})


@primer_bp.route('/api/primer/learners/<int:learner_id>/choice', methods=['POST'])
def choose_path(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    payload = _payload()
    token, choice_id = payload.get('token'), payload.get('choice')
    if not isinstance(token, str) or not isinstance(choice_id, str):
        return jsonify({'error': 'Choose a path for this chapter.'}), 400
    try:
        challenge = _signer().loads(token, max_age=3600)
    except SignatureExpired:
        return jsonify({'error': 'This chapter expired. Load the current step.'}), 410
    except BadSignature:
        return jsonify({'error': 'Invalid chapter choice.'}), 400
    if (not isinstance(challenge, dict) or challenge.get('kind') != 'choice'
            or challenge.get('learner_id') != learner_id or type(challenge.get('version')) is not int
            or type(challenge.get('chapter')) is not int
            or not valid_choice(learner['world'], challenge['chapter'], choice_id)):
        return jsonify({'error': 'Invalid chapter choice.'}), 400
    try:
        journey = store.choose_story_path(learner_id, challenge['version'], choice_id)
    except store.StaleJourney:
        return jsonify({'error': 'This chapter has moved on. Load the current step.'}), 409
    return jsonify({'story': story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'])})


@primer_bp.route('/api/primer/learners/<int:learner_id>/journey/restart', methods=['POST'])
def restart_path(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    try:
        journey = store.restart_journey(learner_id)
    except store.StaleJourney:
        return jsonify({'error': 'Finish the current journey first.'}), 409
    return jsonify({'story': story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'])})
