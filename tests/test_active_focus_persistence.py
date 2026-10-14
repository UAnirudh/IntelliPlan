"""Real study persistence keeps cumulative observations across restarts."""
from uuid import uuid4

import pytest

import App
import active_glue
from intelliplan.learning import policy


@pytest.fixture
def study():
    previous = App.limiter.enabled
    App.limiter.enabled = False
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'focus-{uuid4().hex}@example.test', password_hash='x', birth_year=2000)
        App.db.session.add(user)
        App.db.session.commit()
        uid = user.id
        App._get_or_create_identity(uid).completed = True
        App.db.session.commit()
    client = App.app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    response = client.post('/api/active/start', json={'title': 'Practice', 'planned_minutes': 25})
    assert response.status_code == 201
    sid = response.get_json()['session']['id']
    yield client, sid
    with App.app.app_context():
        models = policy.models()
        org_ids = [m.organization_id for m in models['Membership'].query.filter_by(user_id=uid)]
        models['Membership'].query.filter_by(user_id=uid).delete()
        if org_ids:
            models['Organization'].query.filter(models['Organization'].id.in_(org_ids)).delete(synchronize_session=False)
        App.ActiveFocusSample.query.filter_by(session_id=sid).delete()
        App.ActiveSession.query.filter_by(id=sid).delete()
        App.UserIdentity.query.filter_by(user_id=uid).delete()
        App.User.query.filter_by(id=uid).delete()
        App.db.session.commit()
    App.limiter.enabled = previous


def summary(samples, present, away=0):
    return {'samples': samples, 'present': present, 'away': away, 'absent': 0,
            'distraction_events': 1, 'longest_streak_seconds': 20}


def test_late_opt_in_pause_resume_and_delayed_packets_preserve_saved_counts(study):
    client, sid = study
    path = f'/api/active/{sid}/heartbeat'
    assert client.post(path, json={'focus': {'summary': summary(30, 25, 5)}}).status_code == 200
    with App.app.app_context():
        assert App.db.session.get(App.ActiveSession, sid).focus_samples == 0
    first = client.post(path, json={'enable_focus': True, 'focus': {
        'offset_seconds': 60, 'summary': summary(30, 25, 5)}})
    assert first.get_json()['session']['focus']['samples'] == 30
    client.post(path, json={'state': 'paused', 'focus': {'summary': summary(30, 25, 5)}})
    resumed = client.post(path, json={'state': 'running', 'focus': {
        'offset_seconds': 180, 'summary': summary(35, 30, 5)}})
    assert resumed.get_json()['session']['focus']['samples'] == 35
    assert resumed.get_json()['session']['focus']['present'] == 30
    delayed = client.post(path, json={'focus': {'summary': summary(5, 5)}})
    assert delayed.get_json()['session']['focus']['samples'] == 35
    restored = client.get('/api/active/current').get_json()['session']['focus']
    assert (restored['samples'], restored['present'], restored['away']) == (35, 30, 5)


def test_database_update_uses_current_counts_instead_of_stale_loaded_row(study):
    _, sid = study
    with App.app.app_context():
        row = App.db.session.get(App.ActiveSession, sid)
        # Simulate another worker saving a later summary while this worker's
        # already-loaded ORM instance still has the original zero counters.
        App.db.session.execute(App.db.update(App.ActiveSession).where(App.ActiveSession.id == sid).values(
            focus_samples=40, focus_present_samples=30, focus_away_samples=10,
        ).execution_options(synchronize_session=False))
        active_glue._get_repository().apply_focus_summary(
            row, samples=5, present=5, away=0, absent=0, distraction_events=0,
            longest_streak_seconds=0,
        )
        assert (row.focus_samples, row.focus_present_samples, row.focus_away_samples) == (40, 30, 10)


def deny_focus(study):
    _, sid = study
    with App.app.app_context():
        uid = App.db.session.get(App.ActiveSession, sid).user_id
        models = policy.models()
        org = models['Organization'](name='Camera policy fixture', kind='school',
                                     policy={'study': True, 'focus': False})
        App.db.session.add(org)
        App.db.session.flush()
        App.db.session.add(models['Membership'](user_id=uid, organization_id=org.id, role='student'))
        App.db.session.commit()
    policy.invalidate_cache()


def test_school_focus_denial_disables_camera_but_keeps_timer_usable(study):
    client, sid = study
    deny_focus(study)
    assert client.get('/api/active/focus-permission').get_json() == {'allowed': False}
    for view in ('desktop', 'phone'):
        html = client.get(f'/active?view={view}').get_data(as_text=True)
        checkbox = html[html.index('id="ipaFocusToggle"'):].split('>', 1)[0]
        assert 'disabled' in checkbox
        assert 'id="ipaCameraSource"' not in html
    response = client.post(f'/api/active/{sid}/heartbeat', json={
        'enable_focus': True, 'state': 'paused', 'focus': {'summary': summary(30, 25, 5)}})
    assert response.status_code == 200
    assert response.get_json()['focus_allowed'] is False
    assert response.get_json()['session']['state'] == 'paused'
    assert response.get_json()['session']['focus']['samples'] == 0


def test_revocation_stops_new_counts_and_does_not_erase_earlier_counts(study):
    client, sid = study
    path = f'/api/active/{sid}/heartbeat'
    client.post(path, json={'enable_focus': True, 'focus': {'summary': summary(30, 25, 5)}})
    deny_focus(study)
    response = client.post(path, json={'focus': {'summary': summary(99, 90, 9)}})
    assert response.get_json()['focus_allowed'] is False
    assert response.get_json()['session']['focus']['samples'] == 30


def test_revocation_during_progress_save_keeps_timer_but_discards_new_counts(study, monkeypatch):
    client, sid = study
    path = f'/api/active/{sid}/heartbeat'
    client.post(path, json={'enable_focus': True, 'focus': {'summary': summary(30, 25, 5)}})
    original = active_glue.ActiveSessionRepository.update_progress
    def revoke_after_progress(repo, row, **kwargs):
        result = original(repo, row, **kwargs)
        deny_focus(study)
        return result
    monkeypatch.setattr(active_glue.ActiveSessionRepository, 'update_progress', revoke_after_progress)
    response = client.post(path, json={'state': 'paused', 'focus': {'summary': summary(99, 90, 9)}})
    assert response.status_code == 200
    assert response.get_json()['session']['state'] == 'paused'
    assert response.get_json()['focus_allowed'] is False
    assert response.get_json()['session']['focus']['samples'] == 30
    response = client.post(f'/api/active/{sid}/finish', json={
        'completed': True, 'focus': {'summary': summary(99, 90, 9)}})
    assert response.status_code == 200
    assert response.get_json()['session']['focus']['samples'] == 30
