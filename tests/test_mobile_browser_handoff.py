"""Mobile handoff browser integration and immutable issuance proof."""
import uuid

import App
import app_link
import auth_api
from intelliplan.persistence.app_links import AppLinkRedemption


def test_full_web_mobile_handoff_signs_in_rotates_session_and_rejects_replay():
    app = App.app
    with app.app_context():
        user = App.account_repository().create(email=f'web-mobile-{uuid.uuid4().hex}@example.test',
            password_hash='synthetic-hash', attributes={'birth_year': 2000, 'parent_consent_granted': True})
        user_id = user.id
        token = auth_api.make_token(user)
    client = app.test_client()
    response = client.post('/api/v1/link/session', json={'provider': 'settings'},
                           headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200
    path = '/link/' + response.get_json()['url'].rsplit('/', 1)[1]
    with client.session_transaction() as cookie:
        cookie['pre_handoff_marker'] = 'must-clear'
        old_sid = cookie.sid
    redeemed = client.get(path)
    assert redeemed.status_code == 302
    assert redeemed.headers['Location'] == '/settings'
    with client.session_transaction() as cookie:
        assert cookie['_user_id'] == str(user_id)
        assert cookie['session_epoch'] == 0
        assert cookie['app_link_return'] is True
        assert 'pre_handoff_marker' not in cookie
        assert cookie.sid != old_sid
    assert app.test_client().get(path).headers['Location'].endswith('/login')


def test_browser_handoff_stamps_immutable_redemption_epoch(monkeypatch):
    from intelliplan.persistence.mongo_accounts import Account
    user = Account({'id': 987654321, 'email': 'synthetic-proof@example.invalid', 'session_epoch': 5})
    class Tickets:
        def redeem(self, code):
            return AppLinkRedemption(user, 2, '/settings')
    monkeypatch.setitem(App.app.extensions, 'app_link_tickets_repository', Tickets())
    with App.app.test_request_context('/link/' + app_link.new_code()):
        response = App.app_link_redeem(app_link.new_code())
        assert response.headers['Location'] == '/settings'
        assert App.session['session_epoch'] == 2
