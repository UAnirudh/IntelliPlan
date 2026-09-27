"""Authenticated Foundations API."""

import secrets

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from primer import store
from primer.catalog import SKILL_BY_ID, WORLDS, get_item, grade_item, public_item
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
    return {'id': row['id'], 'nickname': row['nickname'], 'world': row['world'],
            'grade': row['grade'], 'grade_set': row['grade_set']}


def _offset(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if -720 <= minutes <= 840 else None


def _public_nudge(note):
    return {key: value for key, value in note.items() if not key.startswith('_')} if note else None


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
    grade = payload.get('grade', 0)
    if not nickname or len(nickname) > 40 or any(ord(char) < 32 for char in nickname):
        return jsonify({'error': 'Use a nickname of 1 to 40 characters.'}), 400
    if world not in WORLDS:
        return jsonify({'error': 'Choose a story world.'}), 400
    if type(grade) is not int or not 0 <= grade <= 13:
        return jsonify({'error': 'Choose kindergarten through grade 12 or college foundation.'}), 400
    if len(store.list_learners(owner)) >= 8:
        return jsonify({'error': 'This account has reached its learner limit.'}), 409
    learner = store.create_learner(owner, nickname, world, grade)
    return jsonify({'learner': _public_learner(learner)}), 201


@primer_bp.route('/api/primer/learners/<int:learner_id>', methods=['DELETE'])
def remove_learner(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    store.delete_learner(_owner(), learner['id'])
    return jsonify({'ok': True})


@primer_bp.route('/api/primer/learners/<int:learner_id>/grade', methods=['PUT'])
def update_grade(learner_id):
    _, error = _learner_or_error(learner_id)
    if error:
        return error
    grade = _payload().get('grade')
    if type(grade) is not int or not 0 <= grade <= 13:
        return jsonify({'error': 'Choose kindergarten through grade 12 or college foundation.'}), 400
    learner = store.set_grade(_owner(), learner_id, grade)
    return jsonify({'learner': _public_learner(learner)})


@primer_bp.route('/api/primer/learners/<int:learner_id>/progress', methods=['GET'])
def learner_progress(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    journey = store.journey_for(learner_id)
    return jsonify({'learner': _public_learner(learner), **store.progress(learner_id, learner['grade']),
                    'story': story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'],
                                        bool(journey['repair_skill_id']), learner['grade'])})


@primer_bp.route('/api/primer/learners/<int:learner_id>/parent', methods=['GET'])
def parent_overview(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    offset = _offset(request.args.get('tz_offset_minutes', 0))
    if offset is None:
        return jsonify({'error': 'Invalid timezone offset.'}), 400
    return jsonify({'learner': _public_learner(learner), **store.parent_overview(learner_id, offset, learner['grade'])})


@primer_bp.route('/api/primer/learners/<int:learner_id>/parent/goal', methods=['PUT'])
def parent_goal(learner_id):
    _, error = _learner_or_error(learner_id)
    if error:
        return error
    goal = _payload().get('weekly_goal')
    if type(goal) is not int or not 1 <= goal <= 7:
        return jsonify({'error': 'Choose a goal of 1 to 7 practice days.'}), 400
    store.set_weekly_goal(learner_id, goal)
    return jsonify({'weekly_goal': goal})


@primer_bp.route('/api/primer/learners/<int:learner_id>/parent/check-in', methods=['POST'])
def parent_check_in(learner_id):
    _, error = _learner_or_error(learner_id)
    if error:
        return error
    payload = _payload()
    domain = payload.get('domain')
    offset = _offset(payload.get('tz_offset_minutes'))
    if domain not in ('Reading', 'Writing', 'Arithmetic') or offset is None:
        return jsonify({'error': 'Choose a practice area and valid timezone.'}), 400
    if not store.record_offline_checkin(learner_id, domain, offset):
        return jsonify({'error': 'An offline practice check-in was already recorded today.'}), 409
    return jsonify({'ok': True}), 201


@primer_bp.route('/api/primer/learners/<int:learner_id>/parent/nudge', methods=['POST', 'DELETE'])
def parent_nudge(learner_id):
    _, error = _learner_or_error(learner_id)
    if error:
        return error
    payload = _payload()
    if request.method == 'DELETE':
        note_id = payload.get('id')
        if type(note_id) is not int or not store.withdraw_nudge(learner_id, note_id):
            return jsonify({'error': 'Active note not found.'}), 404
        return jsonify({'ok': True})
    template_id = payload.get('template_id')
    if not isinstance(template_id, str) or template_id not in store.NUDGE_TEMPLATES:
        return jsonify({'error': 'Choose a supportive note.'}), 400
    note = store.create_nudge(learner_id, template_id)
    if not note:
        return jsonify({'error': 'Wait for the current note to be acknowledged and allow a day between notes.'}), 429
    return jsonify({'nudge': _public_nudge(note)}), 201


@primer_bp.route('/api/primer/learners/<int:learner_id>/nudge/<int:nudge_id>/acknowledge', methods=['POST'])
def acknowledge_nudge(learner_id, nudge_id):
    _, error = _learner_or_error(learner_id)
    if error:
        return error
    if not store.acknowledge_nudge(learner_id, nudge_id):
        return jsonify({'error': 'Note not found or already acknowledged.'}), 404
    return jsonify({'ok': True})


@primer_bp.route('/api/primer/learners/<int:learner_id>/nudge', methods=['GET'])
def current_nudge(learner_id):
    _, error = _learner_or_error(learner_id)
    if error:
        return error
    note = store.latest_nudge(learner_id)
    if not note or note['acknowledged_at'] or note['withdrawn_at'] or note['expired']:
        note = None
    return jsonify({'nudge': _public_nudge(note)})


@primer_bp.route('/api/primer/learners/<int:learner_id>/activity', methods=['GET'])
def activity(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    journey = store.journey_for(learner_id)
    story = story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'],
                       bool(journey['repair_skill_id']), learner['grade'])
    response = {
        'learner': _public_learner(learner),
        'world_name': WORLDS[learner['world']]['name'],
        'story': story,
    }
    note = store.latest_nudge(learner_id)
    if note and note['acknowledged_at'] is None and note['withdrawn_at'] is None and not note['expired']:
        response['family_note'] = _public_nudge(note)
    if story['complete']:
        return jsonify(response)
    if story['awaiting_choice']:
        response['token'] = _signer().dumps({'kind': 'choice', 'learner_id': learner_id,
                                            'version': journey['version'], 'chapter': journey['chapter']})
        return jsonify(response)
    skill_id, item = store.choose_story_activity(learner_id, BEATS[journey['beat']][0],
                                                 journey['repair_skill_id'], learner['grade'])
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
    item = get_item(challenge.get('item_id'))
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
    item = get_item(challenge.get('item_id'))
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
    return jsonify({'story': story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'], grade=learner['grade'])})


@primer_bp.route('/api/primer/learners/<int:learner_id>/journey/restart', methods=['POST'])
def restart_path(learner_id):
    learner, error = _learner_or_error(learner_id)
    if error:
        return error
    try:
        journey = store.restart_journey(learner_id)
    except store.StaleJourney:
        return jsonify({'error': 'Finish the current journey first.'}), 409
    return jsonify({'story': story_view(learner['world'], journey['chapter'], journey['beat'], journey['path'], grade=learner['grade'])})
