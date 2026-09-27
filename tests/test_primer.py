"""Foundations is a bounded assessed path, scoped to the signed-in account."""

import pytest
from datetime import datetime, timedelta
from flask import Flask
from flask_login import LoginManager, UserMixin
from flask_sqlalchemy import SQLAlchemy

from primer.api import primer_bp
from primer.catalog import ITEM_BY_ID, ITEMS, ITEMS_BY_SKILL, LEGACY_SKILLS, SKILLS, get_item, grade_item, normalize_answer
from primer.generated import GENERATED_SKILLS, generated_item_count, item_for_skill
from primer.advanced import ADVANCED_SKILLS, advanced_item_count, advanced_item_for_skill
from primer import store
from primer.story import ADVANCED_STORIES, SCHOLAR_STORIES, BEAT_CUES, CHAPTER_COUNT, STORIES, view as story_view


class User(UserMixin):
    def __init__(self, user_id):
        self.id = user_id


@pytest.fixture
def app():
    application = Flask(__name__)
    application.config.update(SECRET_KEY='primer-test-secret', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', TESTING=True)
    SQLAlchemy(application)
    login = LoginManager(application)
    login.user_loader(lambda user_id: User(user_id))
    application.register_blueprint(primer_bp)
    yield application


def signed_in(app, user_id='1'):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = user_id
        session['_fresh'] = True
    return client


def create(client, nickname='Sky'):
    response = client.post('/api/primer/learners', json={'nickname': nickname, 'world': 'space'})
    assert response.status_code == 201
    return response.get_json()['learner']['id']


def activity(client, learner_id):
    response = client.get(f'/api/primer/learners/{learner_id}/activity')
    assert response.status_code == 200
    body = response.get_json()
    if 'item' in body:
        assert 'answer' not in body['item']
    return body


def submit(client, learner_id, challenge, answer_text):
    return client.post(f'/api/primer/learners/{learner_id}/answer', json={
        'token': challenge['token'], 'answer': answer_text,
    })


def test_auth_ownership_and_deletion(app):
    anonymous = app.test_client()
    assert anonymous.get('/api/primer/learners').status_code == 401
    assert anonymous.post('/api/primer/learners', json={'nickname': 'X', 'world': 'forest'}).status_code == 401
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    learner_id = create(owner)
    assert other.get(f'/api/primer/learners/{learner_id}/progress').status_code == 404
    assert other.get(f'/api/primer/learners/{learner_id}/activity').status_code == 404
    assert other.delete(f'/api/primer/learners/{learner_id}').status_code == 404
    assert owner.delete(f'/api/primer/learners/{learner_id}').status_code == 200
    assert owner.get(f'/api/primer/learners/{learner_id}/progress').status_code == 404


def test_parent_companion_counts_real_practice_and_local_days(app, monkeypatch):
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    learner_id = create(owner)
    path = f'/api/primer/learners/{learner_id}/parent'
    assert app.test_client().get(path).status_code == 401
    assert other.get(path).status_code == 404
    assert other.put(path + '/goal', json={'weekly_goal': 5}).status_code == 404
    assert other.post(path + '/check-in', json={'domain': 'Reading', 'tz_offset_minutes': 0}).status_code == 404
    assert other.post(path + '/nudge', json={'template_id': 'explore'}).status_code == 404
    assert other.get(f'/api/primer/learners/{learner_id}/nudge').status_code == 404
    assert owner.get(path).get_json()['suggested_nudge'] == 'explore'
    fixed = datetime(2026, 9, 26, 23, 30)
    monkeypatch.setattr(store, 'utcnow', lambda: fixed)
    with app.app_context():
        store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'local-day-1', True)
    utc_view = owner.get(path + '?tz_offset_minutes=0').get_json()
    next_day_view = owner.get(path + '?tz_offset_minutes=120').get_json()
    assert utc_view['practice_days'] == next_day_view['practice_days'] == 1
    assert utc_view['days'][-1]['answers'] == 1
    assert next_day_view['days'][-1]['date'] == '2026-09-27'
    assert next_day_view['days'][-1]['answers'] == 1
    assert utc_view['total_answers'] == 1
    assert utc_view['domain_answers'] == {'Reading': 1, 'Writing': 0, 'Arithmetic': 0}
    assert owner.put(path + '/goal', json={'weekly_goal': True}).status_code == 400
    assert owner.put(path + '/goal', json={'weekly_goal': 8}).status_code == 400
    assert owner.put(path + '/goal', json={'weekly_goal': 4}).status_code == 200
    assert owner.get(path).get_json()['weekly_goal'] == 4
    assert owner.get(path + '?tz_offset_minutes=900').status_code == 400
    assert owner.post(path + '/check-in', json={'domain': 'Reading', 'tz_offset_minutes': 900}).status_code == 400
    assert owner.post(path + '/check-in', json={'domain': 'Writing', 'tz_offset_minutes': 0}).status_code == 201
    assert owner.post(path + '/check-in', json={'domain': 'Writing', 'tz_offset_minutes': 0}).status_code == 409
    together = owner.get(path).get_json()
    assert together['days'][-1]['offline_domain'] == 'Writing'
    assert together['practice_days'] == 1  # two kinds of evidence on one day
    assert other.get(path).status_code == 404
    monkeypatch.setattr(store, 'utcnow', lambda: fixed + timedelta(days=4))
    assert owner.get(path).get_json()['suggested_nudge'] == 'small_step'


def test_parent_nudge_is_reviewed_limited_and_visible_to_learner(app, monkeypatch):
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    learner_id = create(owner)
    path = f'/api/primer/learners/{learner_id}'
    assert owner.post(path + '/parent/nudge', json={'template_id': '<script>'}).status_code == 400
    first = owner.post(path + '/parent/nudge', json={'template_id': 'explore'})
    assert first.status_code == 201
    note = first.get_json()['nudge']
    assert '_created_at' not in note
    assert owner.post(path + '/parent/nudge', json={'template_id': 'choose'}).status_code == 429
    assert other.post(path + f"/nudge/{note['id']}/acknowledge").status_code == 404
    challenge = activity(owner, learner_id)
    assert challenge['family_note']['message'] == store.NUDGE_TEMPLATES['explore']
    assert challenge['family_note']['id'] == note['id']
    assert owner.get(path + '/nudge').get_json()['nudge']['id'] == note['id']
    assert owner.get(path + '/parent').get_json()['can_send_nudge'] is False
    assert owner.post(path + f"/nudge/{note['id']}/acknowledge").status_code == 200
    assert owner.post(path + f"/nudge/{note['id']}/acknowledge").status_code == 404
    assert 'family_note' not in activity(owner, learner_id)
    assert owner.get(path + '/nudge').get_json()['nudge'] is None
    overview = owner.get(path + '/parent').get_json()
    assert overview['latest_nudge']['acknowledged_at']
    assert overview['latest_nudge']['practice_after'] is False
    assert overview['can_send_nudge'] is False
    assert owner.post(path + '/parent/nudge', json={'template_id': 'choose'}).status_code == 429
    with app.app_context():
        first_time = store.latest_nudge(learner_id)['_created_at']
    monkeypatch.setattr(store, 'utcnow', lambda: first_time + timedelta(days=2))
    second = owner.post(path + '/parent/nudge', json={'template_id': 'choose'})
    assert second.status_code == 201
    second_id = second.get_json()['nudge']['id']
    assert owner.delete(path + '/parent/nudge', json={'id': second_id}).status_code == 200
    assert 'family_note' not in activity(owner, learner_id)
    assert owner.post(path + '/parent/nudge', json={'template_id': 'small_step'}).status_code == 429
    monkeypatch.setattr(store, 'utcnow', lambda: first_time + timedelta(days=10))
    assert owner.post(path + '/parent/nudge', json={'template_id': 'small_step'}).status_code == 201
    final = owner.get(path + '/parent').get_json()
    assert final['latest_nudge']['template_id'] == 'small_step'
    assert final['can_send_nudge'] is False
    assert owner.delete(path).status_code == 200
    with app.app_context():
        for table in (store.NUDGE, store.OFFLINE_CHECKIN, store.PARENT_SETTING):
            assert not store._db().session.execute(store.select(table).where(table.c.learner_id == learner_id)).first()


def test_server_grades_and_replay_is_rejected(app):
    client = signed_in(app)
    learner_id = create(client)
    challenge = activity(client, learner_id)
    item = get_item(challenge['item']['id'])
    wrong = next(option for option in item.options if normalize_answer(option) != normalize_answer(item.answer))
    result = submit(client, learner_id, challenge, wrong)
    assert result.status_code == 200
    assert result.get_json()['correct'] is False
    assert result.get_json()['retry'] is True
    assert result.get_json()['evidence']['estimate'] == pytest.approx(1 / 3, abs=.001)
    assert submit(client, learner_id, challenge, item.answer).status_code == 409
    progress = client.get(f'/api/primer/learners/{learner_id}/progress').get_json()
    assert progress['total_attempts'] == 1
    assert progress['skills'][0]['label'] == 'Practicing'
    assert 'answer' not in store.ATTEMPT.c.keys()
    assert client.get(f'/api/primer/learners/{learner_id}/progress').headers['Cache-Control'] == 'private, no-store'
    next_activity = activity(client, learner_id)
    assert next_activity['skill']['domain'] == 'Reading'
    assert next_activity['story']['repair'] is True
    assert next_activity['item']['id'] != challenge['item']['id']
    assert submit(client, learner_id, next_activity, get_item(next_activity['item']['id']).answer).status_code == 200
    next_activity = activity(client, learner_id)
    assert next_activity['skill']['domain'] == 'Writing'
    assert submit(client, learner_id, next_activity, get_item(next_activity['item']['id']).answer).status_code == 200
    assert activity(client, learner_id)['skill']['domain'] == 'Arithmetic'


def test_two_misses_offer_one_repair_then_move_on(app):
    client = signed_in(app)
    learner_id = create(client)
    first = activity(client, learner_id)
    assert submit(client, learner_id, first, 'wrong').get_json()['retry'] is True
    repair = activity(client, learner_id)
    assert repair['story']['beat'] == 1
    assert repair['skill']['id'] == first['skill']['id']
    assert submit(client, learner_id, repair, 'wrong').get_json()['retry'] is False
    assert activity(client, learner_id)['skill']['domain'] == 'Writing'


def test_clue_use_is_recorded_and_does_not_unlock_a_skill(app):
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    learner_id = create(owner)
    challenge = activity(owner, learner_id)
    path = f'/api/primer/learners/{learner_id}/hint'
    assert other.post(path, json={'token': challenge['token']}).status_code == 404
    clue = owner.post(path, json={'token': challenge['token']})
    assert clue.status_code == 200
    assert clue.get_json()['hint'] == get_item(challenge['item']['id']).hint
    assert 'clue' not in challenge['item']
    answer = submit(owner, learner_id, challenge, get_item(challenge['item']['id']).answer)
    assert answer.get_json()['evidence']['clue_used'] is True
    assert owner.post(path, json={'token': challenge['token']}).status_code == 409
    progress = owner.get(f'/api/primer/learners/{learner_id}/progress').get_json()
    first = next(row for row in progress['skills'] if row['id'] == 'read_sounds')
    second = next(row for row in progress['skills'] if row['id'] == 'read_sentences')
    assert first['correct'] == 1 and first['independent_correct'] == 0
    assert second['unlocked'] is False


def test_two_independent_answers_unlock_and_supported_answers_do_not(app):
    with app.app_context():
        learner = store.create_learner(1, 'Sky', 'forest')
        learner_id = learner['id']
        store.reveal_hint(learner_id, 'clued-1', 0)
        store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'clued-1', True)
        store.record_attempt(learner_id, 'read_sounds', 'sound_s', 'independent-1', True)
        assert store.progress(learner_id)['skills'][1]['unlocked'] is False
        store.record_attempt(learner_id, 'read_sounds', 'sound_b', 'independent-2', True)
        progress = store.progress(learner_id)
        assert progress['skills'][0]['independent_correct'] == 2
        assert progress['skills'][1]['unlocked'] is True


def test_revealed_clues_never_create_strong_evidence_by_themselves(app):
    with app.app_context():
        learner_id = store.create_learner(1, 'Sky', 'ocean')['id']
        for index, item_id in enumerate(('sound_m', 'sound_s', 'sound_b', 'sound_f')):
            nonce = f'clue-{index}'
            store.reveal_hint(learner_id, nonce, 0)
            attempt = store.record_attempt(learner_id, 'read_sounds', item_id, nonce, True)
            assert (attempt['due_at'] - attempt['updated_at']).days == 1
        progress = store.progress(learner_id)
        assert progress['skills'][0]['correct'] == 4
        assert progress['skills'][0]['independent_correct'] == 0
        assert progress['skills'][0]['label'] == 'Practicing'
        assert progress['skills'][1]['unlocked'] is False


def test_challenges_are_scoped_and_tamper_protected(app):
    client = signed_in(app)
    first, second = create(client, 'One'), create(client, 'Two')
    challenge = activity(client, first)
    answer = get_item(challenge['item']['id']).answer
    assert submit(client, second, challenge, answer).status_code == 400
    challenge['token'] += 'tampered'
    assert submit(client, first, challenge, answer).status_code == 400
    assert client.get(f'/api/primer/learners/{first}/progress').get_json()['total_attempts'] == 0


def test_all_domains_unlock_and_progress_with_evidence(app):
    client = signed_in(app)
    learner_id = create(client)
    seen = set()
    for _ in range(24):
        challenge = activity(client, learner_id)
        while 'item' not in challenge:
            if challenge['story']['complete']:
                assert client.post(f'/api/primer/learners/{learner_id}/journey/restart').status_code == 200
            else:
                chosen = challenge['story']['choices'][0]['id']
                assert client.post(f'/api/primer/learners/{learner_id}/choice', json={
                    'token': challenge['token'], 'choice': chosen,
                }).status_code == 200
            challenge = activity(client, learner_id)
        seen.add(challenge['skill']['domain'])
        answer = get_item(challenge['item']['id']).answer
        result = submit(client, learner_id, challenge, answer)
        assert result.status_code == 200
        assert result.get_json()['correct'] is True
    assert seen == {'Reading', 'Writing', 'Arithmetic'}
    progress = client.get(f'/api/primer/learners/{learner_id}/progress').get_json()
    assert progress['total_attempts'] == 24
    assert all(row['unlocked'] for row in progress['skills'] if row['id'] in ITEMS_BY_SKILL)
    assert any(row['label'] in {'Growing', 'Strong'} for row in progress['skills'])


def test_story_choice_changes_next_scene_and_survives_resume(app):
    client = signed_in(app)
    learner_id = create(client)
    for domain in ('Reading', 'Writing', 'Arithmetic'):
        challenge = activity(client, learner_id)
        assert challenge['skill']['domain'] == domain
        assert submit(client, learner_id, challenge, get_item(challenge['item']['id']).answer).status_code == 200
    choice_screen = activity(client, learner_id)
    assert choice_screen['story']['awaiting_choice'] is True
    assert client.get(f'/api/primer/learners/{learner_id}/parent').get_json()['suggested_nudge'] == 'choose'
    assert 'item' not in choice_screen
    assert 'answer' not in str(choice_screen)
    choice = choice_screen['story']['choices'][1]['id']
    response = client.post(f'/api/primer/learners/{learner_id}/choice', json={
        'token': choice_screen['token'], 'choice': choice,
    })
    assert response.status_code == 200
    assert client.post(f'/api/primer/learners/{learner_id}/choice', json={
        'token': choice_screen['token'], 'choice': choice,
    }).status_code == 409
    resumed = activity(client, learner_id)
    assert resumed['story']['chapter'] == 2
    assert resumed['story']['previous_choice'] == STORIES['space'][0].choices[1].consequence
    assert resumed['story']['history'] == [{
        'chapter': STORIES['space'][0].title,
        'choice': STORIES['space'][0].choices[1].label,
        'consequence': STORIES['space'][0].choices[1].consequence,
    }]
    assert client.get(f'/api/primer/learners/{learner_id}/progress').get_json()['story']['previous_choice'] == resumed['story']['previous_choice']


def test_parallel_challenges_cannot_skip_a_story_beat(app):
    client = signed_in(app)
    learner_id = create(client)
    first = activity(client, learner_id)
    second = activity(client, learner_id)
    answer = get_item(first['item']['id']).answer
    assert submit(client, learner_id, first, answer).status_code == 200
    assert submit(client, learner_id, second, answer).status_code == 409
    assert activity(client, learner_id)['story']['beat'] == 2
    assert client.get(f'/api/primer/learners/{learner_id}/progress').get_json()['total_attempts'] == 1


def test_story_choice_is_scoped_and_finite(app):
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    learner_id = create(owner)
    for chapter in range(4):
        for _ in range(3):
            challenge = activity(owner, learner_id)
            assert submit(owner, learner_id, challenge, get_item(challenge['item']['id']).answer).status_code == 200
        screen = activity(owner, learner_id)
        assert other.post(f'/api/primer/learners/{learner_id}/choice', json={
            'token': screen['token'], 'choice': screen['story']['choices'][0]['id'],
        }).status_code == 404
        assert owner.post(f'/api/primer/learners/{learner_id}/choice', json={
            'token': screen['token'], 'choice': 'made-up',
        }).status_code == 400
        assert owner.post(f'/api/primer/learners/{learner_id}/choice', json={
            'token': screen['token'], 'choice': screen['story']['choices'][0]['id'],
        }).status_code == 200
        next_story = activity(owner, learner_id)['story']
        if chapter < 3:
            assert next_story['chapter'] == chapter + 2
        else:
            assert next_story['complete'] is True
    assert owner.post(f'/api/primer/learners/{learner_id}/journey/restart').status_code == 200
    assert activity(owner, learner_id)['story']['chapter'] == 1


def test_input_validation_does_not_store_child_answers(app):
    client = signed_in(app)
    assert client.post('/api/primer/learners', json={'nickname': '  ', 'world': 'space'}).status_code == 400
    assert client.post('/api/primer/learners', json={'nickname': 'Sky', 'world': 'unknown'}).status_code == 400
    learner_id = create(client)
    challenge = activity(client, learner_id)
    assert submit(client, learner_id, challenge, 'x' * 201).status_code == 400
    assert submit(client, learner_id, challenge, ' ').status_code == 400
    assert client.get(f'/api/primer/learners/{learner_id}/progress').get_json()['total_attempts'] == 0


def test_success_spaces_review_and_error_brings_it_back(app):
    with app.app_context():
        learner = store.create_learner(1, 'Sky', 'forest')
        learner_id = learner['id']
        first = store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'nonce-1', True)
        second = store.record_attempt(learner_id, 'read_sounds', 'sound_s', 'nonce-2', True)
        third = store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'nonce-3', True)
        assert (first['due_at'] - first['updated_at']).days == 1
        assert (second['due_at'] - second['updated_at']).days == 3
        assert (third['due_at'] - third['updated_at']).days == 7
        assert store.progress(learner_id)['skills'][0]['label'] == 'Growing'
        store.record_attempt(learner_id, 'read_sounds', 'sound_s', 'nonce-4', True)
        assert store.progress(learner_id)['skills'][0]['label'] == 'Strong'
        wrong = store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'nonce-5', False)
        assert wrong['due_at'] == wrong['updated_at']
        assert wrong['streak'] == 0


def test_writing_grading_checks_the_skill_it_teaches():
    punctuation = ITEM_BY_ID['punct_where']
    assert grade_item(punctuation, 'where is my hat?') == (False, 'Begin the sentence with a capital letter.')
    assert grade_item(punctuation, 'Where is my hat.') == (False, 'Check the ending mark. This sentence ends with ?')
    assert grade_item(punctuation, 'Where is my hat?')[0] is True
    assert grade_item(ITEM_BY_ID['order_bird'], 'The bird sings')[0] is False
    assert grade_item(ITEM_BY_ID['sound_m'], ' MOON ')[0] is True


def test_reviewed_content_has_complete_branches_and_answer_keys():
    assert len(ITEMS) == len(ITEM_BY_ID) == 36
    assert all(len(ITEMS_BY_SKILL[skill.id]) == 4 for skill in LEGACY_SKILLS)


def test_generated_k8_pool_has_distinct_reconstructable_questions():
    total = sum(generated_item_count(skill.id) for skill in GENERATED_SKILLS)
    assert total >= 200_000
    assert {skill.grade for skill in GENERATED_SKILLS} == set(range(9))
    assert {skill.domain for skill in GENERATED_SKILLS} == {'Reading', 'Writing', 'Arithmetic'}
    for skill in GENERATED_SKILLS:
        count = generated_item_count(skill.id)
        questions = set()
        for index in range(count):
            item = item_for_skill(skill.id, index)
            assert item.skill_id == skill.id and item.answer and item.hint
            assert len(item.id) <= 32 and get_item(item.id) == item
            assert not item.options or item.answer in item.options
            assert len(item.options) == len(set(item.options))
            questions.add((item.prompt, item.options))
        assert len(questions) == count, skill.id
        for index in (0, count // 2, count - 1):
            item = item_for_skill(skill.id, index)
            assert grade_item(item, item.answer)[0]
            assert all(not grade_item(item, option)[0] for option in item.options if option != item.answer)
    assert get_item('v1g8m2-99999') is None
    assert get_item('v1g9m0-00000') is None
    assert grade_item(item_for_skill('g0w0', 0), 'the fox finds the map.')[0] is False
    assert grade_item(item_for_skill('g5m0', 5000), '13/25')[0] is True


def test_grade_placement_scaffold_and_change_invalidate_activity(app):
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    invalid = owner.post('/api/primer/learners', json={'nickname': 'M', 'world': 'space', 'grade': 14})
    assert invalid.status_code == 400
    created = owner.post('/api/primer/learners', json={'nickname': 'M', 'world': 'space', 'grade': 4})
    assert created.status_code == 201
    learner_id = created.get_json()['learner']['id']
    assert created.get_json()['learner']['grade'] == 4
    first = activity(owner, learner_id)
    assert first['skill']['id'] == 'g4r0'
    assert first['story']['scene'] == ADVANCED_STORIES['space'][0].scene
    assert other.put(f'/api/primer/learners/{learner_id}/grade', json={'grade': 5}).status_code == 404
    assert owner.put(f'/api/primer/learners/{learner_id}/grade', json={'grade': True}).status_code == 400
    changed = owner.put(f'/api/primer/learners/{learner_id}/grade', json={'grade': 5})
    assert changed.status_code == 200
    assert changed.get_json()['learner']['grade'] == 5
    assert submit(owner, learner_id, first, get_item(first['item']['id']).answer).status_code == 409
    assert activity(owner, learner_id)['skill']['id'] == 'g5r0'
    assert owner.get(f'/api/primer/learners/{learner_id}/progress').get_json()['grade'] == 5
    with app.app_context():
        for index in range(2):
            item = item_for_skill('g5r0', index)
            store.record_attempt(learner_id, item.skill_id, item.id, f'scaffold-{index}', False)
        skill_id, item = store.choose_story_activity(learner_id, 'Reading', grade=5)
        assert skill_id == 'g4r0' and item.skill_id == 'g4r0'
        store.record_attempt(learner_id, item.skill_id, item.id, 'scaffold-bridge', True)
        assert store.choose_story_activity(learner_id, 'Reading', grade=5)[0] == 'g5r0'
    assert owner.get(f'/api/primer/learners/{learner_id}/progress').get_json()['scaffolding']['Reading'] is False


def test_high_school_and_college_items_are_stable_and_gradeable():
    assert {skill.grade for skill in ADVANCED_SKILLS} == {9, 10, 11, 12, 13}
    for skill in ADVANCED_SKILLS:
        prompts = set()
        answer_positions = set()
        assert advanced_item_count(skill.id) == 1000
        for index in range(1000):
            item = advanced_item_for_skill(skill.id, index)
            assert item.id and len(item.id) <= 32 and get_item(item.id) == item
            assert item.answer and item.hint and item.explanation
            assert item.answer in item.options if item.options else True
            assert len(item.options) == len(set(item.options))
            if item.options:
                answer_positions.add(item.options.index(item.answer))
            assert grade_item(item, item.answer)[0], item.id
            prompts.add(item.prompt)
        assert len(prompts) == 1000, skill.id
        if answer_positions:
            assert answer_positions == set(range(3)), skill.id
    assert get_item('v2g14m0-0000') is None
    assert get_item('v2g13m0-1000') is None


@pytest.mark.parametrize('grade', [9, 10, 11, 12, 13])
def test_older_learner_grade_placement_and_authorization(app, grade):
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    created = owner.post('/api/primer/learners', json={
        'nickname': 'Alex', 'world': 'space', 'grade': grade})
    assert created.status_code == 201
    learner_id = created.get_json()['learner']['id']
    first = activity(owner, learner_id)
    assert first['skill']['id'] == f'g{grade}r0'
    assert other.get(f'/api/primer/learners/{learner_id}/progress').status_code == 404
    assert owner.get(f'/api/primer/learners/{learner_id}/progress').get_json()['grade'] == grade


def test_story_versions_preserve_choice_ids_and_history():
    for world in STORIES:
        for younger, older, scholar in zip(STORIES[world], ADVANCED_STORIES[world], SCHOLAR_STORIES[world]):
            ids = {choice.id for choice in younger.choices}
            assert ids == {choice.id for choice in older.choices}
            assert ids == {choice.id for choice in scholar.choices}
        choice = STORIES[world][0].choices[0].id
        younger = story_view(world, 1, 0, [choice], grade=1)
        older = story_view(world, 1, 0, [choice], grade=8)
        scholar = story_view(world, 1, 0, [choice], grade=13)
        assert younger['history'][0]['choice'] != older['history'][0]['choice'] or younger['scene'] != older['scene']
        assert younger['history'][0]['consequence']
        assert older['history'][0]['consequence']
        assert scholar['scene'] != older['scene']
        assert scholar['history'][0]['consequence']


def test_existing_learner_without_grade_profile_can_be_placed(app):
    owner = signed_in(app)
    learner_id = create(owner)
    with app.app_context():
        store._db().session.execute(store.delete(store.PROFILE).where(store.PROFILE.c.learner_id == learner_id))
        store._db().session.commit()
    listed = owner.get('/api/primer/learners').get_json()['learners'][0]
    assert listed['grade'] == 0 and listed['grade_set'] is False
    assert activity(owner, learner_id)['skill']['domain'] == 'Reading'
    placed = owner.put(f'/api/primer/learners/{learner_id}/grade', json={'grade': 7})
    assert placed.status_code == 200
    assert placed.get_json()['learner']['grade_set'] is True
    assert activity(owner, learner_id)['skill']['id'] == 'g7r0'
    assert 'claim' in owner.get(f'/api/primer/learners/{learner_id}/progress').get_json()['focus']['try_together']
    for item in ITEMS:
        assert item.hint and item.explanation and item.answer
        if item.options:
            assert item.answer in item.options
            assert len(item.options) == len(set(item.options))
        assert grade_item(item, item.answer)[0] is True
    for world, chapters in STORIES.items():
        assert len(chapters) == len(BEAT_CUES[world]) == CHAPTER_COUNT
        for chapter, cues in zip(chapters, BEAT_CUES[world]):
            assert chapter.title and chapter.scene and chapter.conversation
            assert len(cues) == 3 and all(cues)
            assert len({choice.id for choice in chapter.choices}) == 2
            assert all(choice.label and choice.consequence for choice in chapter.choices)
