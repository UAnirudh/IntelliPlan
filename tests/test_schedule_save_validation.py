"""Malformed saves must not retire the student's usable plan."""
import json
from uuid import uuid4

import pytest

import App


@pytest.fixture
def saved_client():
    with App.app.app_context():
        user = App.User(email=f'schedule-save-{uuid4().hex}@example.test', password_hash='x')
        App.db.session.add(user)
        App.db.session.flush()
        uid = user.id
        plan = App.SavedSchedule(user_id=uid, name='Keep this plan', is_active=True,
                                 schedule_data=json.dumps({'schedule': []}))
        App.db.session.add(plan)
        App.db.session.commit()
        original_id = plan.id
    with App.app.test_client() as client:
        with client.session_transaction() as session:
            session.update(_user_id=str(uid), _fresh=True, session_epoch=0)
        yield client, uid, original_id
    with App.app.app_context():
        App.SavedSchedule.query.filter_by(user_id=uid).delete()
        App.User.query.filter_by(id=uid).delete()
        App.db.session.commit()


@pytest.mark.parametrize('body', [
    [], 'text', {'schedule_data': 'bad'}, {'schedule_data': []},
    {'schedule_data': {'schedule': 'bad'}}, {'schedule_data': {'schedule': [None]}},
    {'schedule_data': {'schedule': [{'blocks': 'bad'}]}},
    {'schedule_data': {'schedule': [{'blocks': [None]}]}},
    {'schedule_data': {'schedule': []}, 'name': []},
])
def test_invalid_save_keeps_original_plan_active(saved_client, body):
    client, uid, original_id = saved_client
    response = client.post('/schedule/save', json=body)
    assert response.status_code == 400 and response.json['status'] == 'error'
    with App.app.app_context():
        rows = App.SavedSchedule.query.filter_by(user_id=uid).all()
        assert len(rows) == 1 and rows[0].id == original_id and rows[0].is_active
    assert client.get('/schedule/saved').json['name'] == 'Keep this plan'


def test_bad_json_keeps_original_plan(saved_client):
    client, uid, original_id = saved_client
    response = client.post('/schedule/save', data='{', content_type='application/json')
    assert response.status_code == 400 and response.is_json
    with App.app.app_context():
        assert App.db.session.get(App.SavedSchedule, original_id).is_active


def test_valid_save_retires_only_owners_previous_plan(saved_client):
    client, uid, original_id = saved_client
    response = client.post('/schedule/save', json={
        'name': 'New plan', 'schedule_data': {'schedule': [{'blocks': []}]}})
    assert response.status_code == 200 and response.json['status'] == 'ok'
    with App.app.app_context():
        assert not App.db.session.get(App.SavedSchedule, original_id).is_active
        active = App.SavedSchedule.query.filter_by(user_id=uid, is_active=True).one()
        assert active.id == response.json['id'] and active.name == 'New plan'
