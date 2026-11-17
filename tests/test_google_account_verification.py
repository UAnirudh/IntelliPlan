"""A Google email claim must be verified before account resolution/linking."""
from types import SimpleNamespace

import pytest
from flask import session

import App


@pytest.mark.parametrize('verified', [None, False, 'true'])
def test_unverified_or_missing_google_email_never_reaches_account_linking(monkeypatch, verified):
    info = {'sub': 'synthetic-google-subject', 'email': 'student@example.test', 'name': 'Student'}
    if verified is not None:
        info['email_verified'] = verified
    monkeypatch.setattr(App, 'exchange_code_for_token', lambda *a, **k: {'access_token': 'synthetic-test-token'})
    monkeypatch.setattr(App.requests, 'get', lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: info))
    monkeypatch.setattr(App, 'log_security_event', lambda *a, **k: None)

    def must_not_resolve():
        raise AssertionError('Unverified email reached account repository')

    monkeypatch.setattr(App, 'account_repository', must_not_resolve)
    with App.app.test_request_context('/google/callback?state=synthetic-state&code=synthetic-code'):
        session['oauth_state'] = 'synthetic-state'
        session['oauth_purpose'] = 'login'
        response = App._handle_google_callback()
        assert response.status_code == 302
        assert response.location.endswith('/login')
