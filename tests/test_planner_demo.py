"""The public demonstration cannot accept or save learner coursework."""
from pathlib import Path

from flask import Flask
import pytest

from intelliplan.api.planner_demo import bp


@pytest.fixture
def client():
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / 'Main_Project/templates'),
                static_folder=str(root / 'static'))
    app.config.update(TESTING=True, SECRET_KEY='synthetic-demo-test-only')
    app.register_blueprint(bp)
    return app.test_client()


def test_public_demo_is_standalone_fabricated_and_does_not_issue_session(client):
    response = client.get('/demo/planner')
    assert response.status_code == 200
    assert b'Fabricated coursework only' in response.data
    assert b'Reset demo' in response.data
    assert b'/login/account' in response.data
    assert b'/legal' in response.data
    assert b'<input' not in response.data and b'<textarea' not in response.data
    assert 'Set-Cookie' not in response.headers


def test_demo_refuses_posted_coursework_and_blocks_network_connections(client):
    assert client.post('/demo/planner', json={'title': 'private coursework'}).status_code == 405
    response = client.get('/demo/planner')
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Robots-Tag'] == 'noindex, nofollow'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert "connect-src 'none'" in response.headers['Content-Security-Policy']
    assert "form-action 'none'" in response.headers['Content-Security-Policy']


def test_demo_script_has_no_network_or_browser_storage(client):
    response = client.get('/static/js/planner-demo.js')
    assert response.status_code == 200
    source = response.data.decode()
    for forbidden in ('fetch(', 'XMLHttpRequest', 'WebSocket', 'sendBeacon',
                      'localStorage', 'sessionStorage', 'document.cookie'):
        assert forbidden not in source


@pytest.mark.parametrize('enforce', ['0', '1'])
def test_full_application_preserves_demo_policy_and_anonymous_access(monkeypatch, enforce):
    import App
    monkeypatch.setenv('CSP_ENFORCE', enforce)
    response = App.app.test_client().get('/demo/planner')
    assert response.status_code == 200
    assert "connect-src 'none'" in response.headers['Content-Security-Policy']
    assert "form-action 'none'" in response.headers['Content-Security-Policy']
    assert b'Fabricated coursework only' in response.data
    assert 'Set-Cookie' not in response.headers


def test_homepage_links_to_fabricated_demo():
    import App
    response = App.app.test_client().get('/')
    assert response.status_code == 200
    assert b'href="/demo/planner"' in response.data
