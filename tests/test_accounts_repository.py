"""Run the real bearer-auth blueprint with either repository and no App import."""
from datetime import datetime, timedelta
import time
import json

import mongomock
import pytest
from flask import Flask, g
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin
from flask_sqlalchemy import SQLAlchemy

import auth_api
import desktop_auth
from intelliplan.persistence.accounts import AccountExists, IdentityConflict, PasswordConflict, SQLAccounts
from intelliplan.persistence.desktop import SQLDesktopTickets
from intelliplan.persistence.extensions import SQLExtensionTokens
from intelliplan.persistence.identity import SQLIdentities
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_desktop import MongoDesktopTickets
from intelliplan.persistence.mongo_extensions import MongoExtensionTokens
from intelliplan.persistence.mongo_identity import MongoIdentities


@pytest.fixture(params=["mongo", "sql"])
def account_app(request, monkeypatch):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="repository-test-only", TESTING=True,
                      SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    passwords = Bcrypt(app)
    if request.param == "mongo":
        client = mongomock.MongoClient()
        store = MongoStore("mongodb://test", "accounts", client_factory=lambda *a, **k: client)
        monkeypatch.setattr(store, 'transaction', lambda work: work(store.database, None))
        store.seed_id("users", 1000)
        repo = MongoAccounts(store)
        repo.ensure_indexes()
        tickets = MongoDesktopTickets(store)
        tickets.ensure_indexes()
        tokens = MongoExtensionTokens(store)
        tokens.ensure_indexes()
        profiles = MongoIdentities(store)
        profiles.ensure_indexes()
    else:
        db = SQLAlchemy(app)

        class User(UserMixin, db.Model):
            __tablename__ = "users"
            id = db.Column(db.Integer, primary_key=True)
            email = db.Column(db.String, nullable=False, unique=True)
            name = db.Column(db.String)
            google_id = db.Column(db.String, unique=True)
            password_hash = db.Column(db.String, default="")
            created_at = db.Column(db.DateTime, default=datetime.utcnow)
            session_epoch = db.Column(db.Integer, default=0)
            failed_login_count = db.Column(db.Integer, default=0)
            login_locked_until = db.Column(db.DateTime)
            birth_year = db.Column(db.Integer)
            role = db.Column(db.String, default='student')
            parent_email = db.Column(db.String)
            parent_consent_granted = db.Column(db.Boolean, default=False)
            parent_consent_token = db.Column(db.String)
            marketing_emails_opt_in = db.Column(db.Boolean, default=False)
            ai_personalization_opt_in = db.Column(db.Boolean, default=False)
            phone = db.Column(db.String)
            sms_reminders_opt_in = db.Column(db.Boolean, default=False)
            push_reminders_opt_in = db.Column(db.Boolean, default=False)
            reminder_lead_minutes = db.Column(db.Integer, default=60)
            sms_carrier = db.Column(db.String, default='tmobile')

        class ResetToken(db.Model):
            __tablename__ = 'password_reset_tokens'
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
            token_hash = db.Column(db.String, unique=True, nullable=False)
            expires_at = db.Column(db.DateTime, nullable=False)
            used_at = db.Column(db.DateTime)
            created_at = db.Column(db.DateTime, nullable=False)
            requested_ip = db.Column(db.String)

        class PolicyAcknowledgement(db.Model):
            __tablename__ = 'policy_acknowledgements'
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
            guest_session_id = db.Column(db.String)
            doc = db.Column(db.String, nullable=False)
            version = db.Column(db.Integer, nullable=False)
            acknowledged_at = db.Column(db.DateTime, default=datetime.utcnow)
            user_agent = db.Column(db.String)

        class SecurityEvent(db.Model):
            __tablename__ = 'security_events'
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
            email = db.Column(db.String)
            event = db.Column(db.String, nullable=False)
            detail = db.Column(db.String)
            ip = db.Column(db.String)
            user_agent = db.Column(db.String)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        class DesktopAuthCode(db.Model):
            __tablename__ = 'desktop_auth_codes'
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
            code_hash = db.Column(db.String, nullable=False, unique=True)
            code_challenge = db.Column(db.String, nullable=False)
            created_at = db.Column(db.DateTime, nullable=False)
            used_at = db.Column(db.DateTime)
            session_epoch = db.Column(db.Integer, default=0)

        class ExtensionToken(db.Model):
            __tablename__ = 'extension_tokens'
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
            token = db.Column(db.String, nullable=False, unique=True)
            created_at = db.Column(db.DateTime, nullable=False)

        class UserIdentity(db.Model):
            __tablename__ = 'user_identities'
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
            grade_level = db.Column(db.String)
            focus_areas = db.Column(db.Text, default='[]')
            goals = db.Column(db.Text, default='')
            completed = db.Column(db.Boolean, default=False)
            availability = db.Column(db.Text, default='{}')
            weekly_commitments = db.Column(db.Text, default='')
            class_schedule = db.Column(db.Text, default='[]')
            assistant_name = db.Column(db.String)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)
            updated_at = db.Column(db.DateTime, default=datetime.utcnow)

        with app.app_context():
            db.create_all()
        repo = SQLAccounts(db, User, reset_model=ResetToken,
                           policy_model=PolicyAcknowledgement, security_model=SecurityEvent)
        tickets = SQLDesktopTickets(db, DesktopAuthCode, User)
        tokens = SQLExtensionTokens(db, ExtensionToken, User)
        profiles = SQLIdentities(db, UserIdentity, User)
    app.extensions["accounts_repository"] = repo
    app.extensions['desktop_tickets_repository'] = tickets
    app.extensions['extension_tokens_repository'] = tokens
    app.extensions['identity_repository'] = profiles
    app.extensions["account_passwords"] = passwords
    manager = LoginManager(app)
    manager.user_loader(repo.get)
    app.register_blueprint(auth_api.auth_bp)

    @app.before_request
    def clear_cached_login():
        # The repository tests retain an app context for direct SQL assertions;
        # real requests have independent contexts and independent login caches.
        g.pop("_login_user", None)

    def no_legacy_models():
        raise AssertionError("Native account routes imported the legacy app")

    monkeypatch.setattr(auth_api, "_models", no_legacy_models)
    with app.app_context():
        yield app, repo, passwords


def test_register_login_me_and_duplicate_identity(account_app):
    app, repo, _ = account_app
    client = app.test_client()
    response = client.post("/api/auth/register", json={
        "email": " Student@Example.test ", "password": "a-strong-password", "name": "Student"})
    assert response.status_code == 201
    data = response.json
    user_id = data["user"]["id"]
    assert repo.get(user_id).email == "student@example.test"
    assert "password_hash" not in data["user"]
    assert client.post("/api/auth/register", json={
        "email": "student@example.test", "password": "a-strong-password"}).status_code == 409
    login = client.post("/api/ext/login", json={
        "email": "STUDENT@example.test", "password": "a-strong-password"})
    assert login.status_code == 200
    me = client.get("/api/ext/me", headers={"Authorization": f"Bearer {login.json['token']}"})
    assert me.status_code == 200
    assert me.json["user"]["id"] == user_id
    assert repo.count() == 1


def test_passwordless_and_unknown_accounts_cannot_get_password_tokens(account_app):
    app, repo, _ = account_app
    repo.create(email="oauth@example.test", name="OAuth", password_hash="")
    for email in ("oauth@example.test", "unknown@example.test"):
        result = app.test_client().post("/api/auth/login", json={"email": email, "password": "guess"})
        assert result.status_code == 401


def test_password_compare_and_swap_revokes_other_cookie_sessions(account_app):
    app, repo, passwords = account_app
    user = repo.create(email="password@example.test", name="Student",
                       password_hash=passwords.generate_password_hash("old-password").decode())
    old_hash = user.password_hash
    changed = repo.change_password(user.id, expected_hash=old_hash, password_hash="new-hash")
    assert changed.session_epoch == 1
    with pytest.raises(PasswordConflict):
        repo.change_password(user.id, expected_hash=old_hash, password_hash="stale-overwrite")
    assert repo.get(user.id).password_hash == "new-hash"
    assert repo.get(user.id).session_epoch == 1


def test_oauth_password_requires_recent_cookie_authentication(account_app):
    app, repo, _ = account_app
    user = repo.create(email="oauth@example.test", name="Student", password_hash="")
    client = app.test_client()
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 401
    with client.session_transaction() as cookie:
        cookie["_user_id"] = str(user.id)
        cookie["_fresh"] = True
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 401
    with client.session_transaction() as cookie:
        cookie["auth_time"] = time.time()
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 200
    with client.session_transaction() as cookie:
        assert cookie["session_epoch"] == 1


def test_duplicate_create_is_a_repository_error(account_app):
    _, repo, _ = account_app
    repo.create(email="same@example.test", name="One", password_hash="hash")
    with pytest.raises(AccountExists):
        repo.create(email="SAME@example.test", name="Two", password_hash="hash")


def test_browser_signup_preserves_consent_and_policy_records(account_app):
    _, repo, _ = account_app
    user = repo.create(email='child@example.test', password_hash='hash', attributes={
        'birth_year': 2018, 'role': 'student', 'parent_email': 'parent@example.test',
        'parent_consent_granted': False, 'parent_consent_token': 'synthetic-consent',
        'marketing_emails_opt_in': False,
    }, acknowledgements=[{'doc': 'privacy', 'version': 2, 'user_agent': 'synthetic agent'}])
    saved = repo.get(user.id)
    assert saved.birth_year == 2018
    assert saved.parent_email == 'parent@example.test'
    assert saved.parent_consent_granted is False
    assert saved.marketing_emails_opt_in is False
    if isinstance(repo, MongoAccounts):
        records = list(repo.store.database.policy_acknowledgements.find({'user_id': user.id}))
        assert [(r['doc'], r['version']) for r in records] == [('privacy', 2)]
    else:
        records = repo.policy_model.query.filter_by(user_id=user.id).all()
        assert [(r.doc, r.version) for r in records] == [('privacy', 2)]


def test_signup_cannot_override_identity_or_policy_ownership(account_app):
    _, repo, _ = account_app
    with pytest.raises(ValueError):
        repo.create(email='blocked@example.test', password_hash='hash', attributes={'id': 42})
    with pytest.raises(ValueError):
        repo.create(email='blocked@example.test', password_hash='hash',
            acknowledgements=[{'doc': 'privacy', 'version': 2, 'user_id': 42}])
    assert repo.by_email('blocked@example.test') is None


def test_policy_versions_are_owner_scoped_and_never_move_backwards(account_app):
    _, repo, _ = account_app
    one = repo.create(email='policy-one@example.test', password_hash='hash')
    two = repo.create(email='policy-two@example.test', password_hash='hash')
    assert not repo.has_policy(user_id=one.id)
    assert repo.acknowledge_policy('privacy', 2, user_id=one.id, user_agent='x' * 300) == 2
    assert repo.acknowledge_policy('privacy', 1, user_id=one.id) == 2
    assert repo.acknowledge_policy('privacy', 2, user_id=one.id) == 2
    assert repo.acknowledge_policy('privacy', 3, guest_session_id='guest-one') == 3
    assert repo.latest_policy('privacy', user_id=one.id).version == 2
    assert repo.latest_policy('privacy', user_id=one.id).user_agent == 'x' * 256
    assert repo.latest_policy('privacy', user_id=two.id) is None
    assert repo.latest_policy('privacy', guest_session_id='guest-one').version == 3
    assert repo.latest_policy('privacy', guest_session_id='guest-two') is None
    assert repo.has_policy(user_id=one.id)
    assert not repo.has_policy(user_id=two.id)


def test_policy_requires_one_owner_and_refuses_missing_accounts(account_app):
    _, repo, _ = account_app
    for owner in ({}, {'user_id': 42, 'guest_session_id': 'guest'}, {'guest_session_id': ''}):
        with pytest.raises(ValueError):
            repo.acknowledge_policy('privacy', 2, **owner)
    with pytest.raises(LookupError):
        repo.acknowledge_policy('privacy', 2, user_id=999999)
    assert repo.latest_policy('privacy', user_id=999999) is None


def test_desktop_handoff_is_single_use_and_pkce_bound(account_app):
    app, repo, _ = account_app
    tickets = app.extensions['desktop_tickets_repository']
    user = repo.create(email='desktop@example.test', password_hash='hash')
    verifier = desktop_auth.new_verifier()
    code_hash = desktop_auth.hash_code(desktop_auth.new_code())
    tickets.issue(user.id, code_hash=code_hash, code_challenge=desktop_auth.challenge_for(verifier))
    redemption = tickets.redeem(code_hash=code_hash, verifier=verifier)
    assert redemption.user.id == user.id
    assert redemption.session_epoch == 0
    assert tickets.redeem(code_hash=code_hash, verifier=verifier) is None
    stolen_hash = desktop_auth.hash_code(desktop_auth.new_code())
    tickets.issue(user.id, code_hash=stolen_hash, code_challenge=desktop_auth.challenge_for(verifier))
    assert tickets.redeem(code_hash=stolen_hash, verifier=desktop_auth.new_verifier()) is None
    assert tickets.redeem(code_hash=stolen_hash, verifier=verifier) is None


def test_desktop_expired_and_malformed_verifiers_cannot_authenticate(account_app):
    app, repo, _ = account_app
    tickets = app.extensions['desktop_tickets_repository']
    user = repo.create(email='desktop-expired@example.test', password_hash='hash')
    verifier = desktop_auth.new_verifier()
    tickets.issue(user.id, code_hash='synthetic-hash', code_challenge=desktop_auth.challenge_for(verifier))
    assert tickets.redeem(code_hash='synthetic-hash', verifier='short') is None
    expired = datetime.utcnow() - timedelta(seconds=desktop_auth.CODE_TTL_SECONDS)
    if isinstance(tickets, MongoDesktopTickets):
        tickets.store.database.desktop_auth_codes.update_one({'code_hash': 'synthetic-hash'},
            {'$set': {'created_at': expired.isoformat()}})
    else:
        tickets.model.query.filter_by(code_hash='synthetic-hash').one().created_at = expired
        tickets.db.session.commit()
    assert tickets.redeem(code_hash='synthetic-hash', verifier=verifier) is None


def test_desktop_ticket_cannot_be_created_for_missing_owner(account_app):
    app, _, _ = account_app
    tickets = app.extensions['desktop_tickets_repository']
    with pytest.raises(LookupError):
        tickets.issue(999999, code_hash='unknown-owner',
                      code_challenge=desktop_auth.challenge_for(desktop_auth.new_verifier()))


def test_desktop_ticket_epoch_cannot_be_upgraded_after_password_reset(account_app):
    app, repo, _ = account_app
    tickets = app.extensions['desktop_tickets_repository']
    user = repo.create(email='desktop-reset@example.test', password_hash='hash')
    verifier = desktop_auth.new_verifier()
    challenge = desktop_auth.challenge_for(verifier)
    tickets.issue(user.id, code_hash='before-reset', code_challenge=challenge, expected_epoch=0)
    repo.change_password(user.id, expected_hash='hash', password_hash='reset')
    assert tickets.redeem(code_hash='before-reset', verifier=verifier) is None
    with pytest.raises(ValueError):
        tickets.issue(user.id, code_hash='stale-browser', code_challenge=challenge, expected_epoch=0)
    tickets.issue(user.id, code_hash='fresh', code_challenge=challenge, expected_epoch=1)
    proof = tickets.redeem(code_hash='fresh', verifier=verifier)
    repo.change_password(user.id, expected_hash='reset', password_hash='later-reset')
    assert proof.session_epoch == 1
    assert repo.get(user.id).session_epoch == 2


def test_extension_tokens_reuse_and_revoke_only_the_selected_credential(account_app):
    app, repo, _ = account_app
    tokens = app.extensions['extension_tokens_repository']
    owner = repo.create(email='extension@example.test', password_hash='hash')
    other = repo.create(email='extension-other@example.test', password_hash='other-hash')
    first = tokens.issue(owner.id, expected_hash='hash')
    second = tokens.issue(owner.id, expected_hash='hash')
    other_token = tokens.issue(other.id)
    assert first != second
    assert tokens.issue(owner.id, reuse=True) == first
    assert tokens.user_for(first).id == owner.id
    tokens.revoke(first)
    assert tokens.user_for(first) is None
    assert tokens.user_for(second).id == owner.id
    assert tokens.user_for(other_token).id == other.id
    assert tokens.user_for('invented-token') is None
    assert tokens.user_for(None) is None


def test_stale_password_cannot_issue_new_extension_credentials(account_app):
    app, repo, _ = account_app
    tokens = app.extensions['extension_tokens_repository']
    user = repo.create(email='extension-race@example.test', password_hash='old-hash')
    existing = tokens.issue(user.id, expected_epoch=0)
    repo.change_password(user.id, expected_hash='old-hash', password_hash='new-hash')
    with pytest.raises(PasswordConflict):
        tokens.issue(user.id, reuse=True, expected_epoch=0)
    with pytest.raises(PasswordConflict):
        tokens.issue(user.id, expected_epoch=0)
    assert tokens.user_for(existing).id == user.id
    with pytest.raises(PasswordConflict):
        tokens.issue(user.id, expected_hash='old-hash')
    with pytest.raises(PasswordConflict):
        tokens.issue(999999)


def test_identity_partial_updates_keep_other_fields_and_owner(account_app):
    app, repo, _ = account_app
    profiles = app.extensions['identity_repository']
    one = repo.create(email='profile-one@example.test', password_hash='hash')
    two = repo.create(email='profile-two@example.test', password_hash='hash')
    assert profiles.get(one.id) is None
    created = profiles.get_or_create(one.id)
    assert profiles.get_or_create(one.id).id == created.id
    profiles.save(one.id, {'goals': 'Keep my goals', 'focus_areas': json.dumps(['Math']),
                           'assistant_name': 'Nova', 'completed': True})
    saved = profiles.save(one.id, {'availability': json.dumps({'Mon': '16:00-18:00'})})
    assert saved.goals == 'Keep my goals'
    assert saved.focus_areas == '["Math"]'
    assert saved.completed is True
    assert saved.user_id == one.id
    assert profiles.get_or_create(two.id).goals == ''
    import assistant_name
    assert assistant_name.for_user(one.id) == 'Nova'
    assert assistant_name.for_user(two.id) == ''


def test_account_preferences_preserve_other_fields_and_clear_sms_without_a_phone(account_app):
    _, repo, _ = account_app
    user = repo.create(email='preferences@example.test', password_hash='keep-hash')
    changed = repo.set_preferences(user.id, {'phone': '+12065550101', 'sms_reminders_opt_in': True,
        'push_reminders_opt_in': True, 'reminder_lead_minutes': 45})
    assert changed.sms_reminders_opt_in is True
    cleared = repo.set_preferences(user.id, {'phone': None})
    assert cleared.phone is None
    assert cleared.sms_reminders_opt_in is False
    changed = repo.set_preferences(user.id, {'sms_reminders_opt_in': True,
                                            'ai_personalization_opt_in': True})
    assert changed.sms_reminders_opt_in is False
    assert changed.push_reminders_opt_in is True
    assert changed.reminder_lead_minutes == 45
    assert changed.ai_personalization_opt_in is True
    assert changed.password_hash == 'keep-hash'
    assert repo.set_preferences(user.id, {}).sms_reminders_opt_in is False
    with pytest.raises(ValueError):
        repo.set_preferences(user.id, {'password_hash': 'overwrite'})
    with pytest.raises(LookupError):
        repo.set_preferences(999999, {'phone': None})


def test_identity_rejects_missing_owners_and_identity_overrides(account_app):
    app, repo, _ = account_app
    profiles = app.extensions['identity_repository']
    one = repo.create(email='profile-validation@example.test', password_hash='hash')
    with pytest.raises(ValueError):
        profiles.save(one.id, {'user_id': 42, 'goals': 'overwrite'})
    assert profiles.get(one.id) is None
    with pytest.raises(LookupError):
        profiles.get_or_create(999999)


def test_explicit_identity_patch_cannot_be_lost_to_a_stale_cached_record(account_app):
    app, repo, _ = account_app
    profiles = app.extensions['identity_repository']
    user = repo.create(email='profile-stale@example.test', password_hash='hash')
    cached = profiles.get_or_create(user.id)
    assert cached.goals == ''
    if isinstance(profiles, MongoIdentities):
        profiles.store.database.user_identities.update_one({'user_id': user.id},
            {'$set': {'goals': 'independent update', 'weekly_commitments': 'keep newer field'}})
    else:
        with profiles.db.engine.begin() as connection:
            connection.execute(profiles.model.__table__.update().where(
                profiles.model.user_id == user.id).values(
                    goals='independent update', weekly_commitments='keep newer field'))
    assert cached.goals == ''
    saved = profiles.save(user.id, {'goals': ''})
    assert saved.goals == ''
    assert saved.weekly_commitments == 'keep newer field'


def test_native_migrated_identity_preserves_unknown_fields_and_decodes_compressed_goals():
    from intelliplan.persistence.codec import pack_document
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'profiles', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_one({'id': 42})
    store.database.user_identities.insert_one(pack_document({
        'id': 90, 'user_id': 42, 'goals': 'A long restored goal. ' * 2000,
        'focus_areas': 'not valid json', 'availability': '[]',
        'created_at': '2026-01-01T00:00:00.123456', 'custom_legacy_field': 'keep'}))
    profiles = MongoIdentities(store)
    saved = profiles.save(42, {'grade_level': '10th grade'})
    assert saved.goals == 'A long restored goal. ' * 2000
    assert saved.created_at.microsecond == 123456
    assert saved.custom_legacy_field == 'keep'
    assert saved.focus_list() == []
    assert saved.avail_dict() == {}
    assert saved.class_list() == []
    profiles.save(42, {'goals': 'short replacement'})
    assert profiles.get(42).goals == 'short replacement'


def test_failed_signins_lock_atomically_and_stale_credentials_cannot_clear_failures(account_app):
    _, repo, _ = account_app
    user = repo.create(email='locked@example.test', password_hash='original')
    for attempt in range(1, 8):
        change = repo.failed_login(user.id)
        assert change.attempts == attempt
        assert change.locked_until is None
    assert not repo.clear_failures(user.id, expected_hash='stale')
    assert repo.get(user.id).failed_login_count == 7
    change = repo.failed_login(user.id)
    assert change.attempts == 8
    assert change.locked_until is not None
    assert repo.get(user.id).failed_login_count == 0
    assert repo.failed_login(user.id) is None
    assert not repo.clear_failures(user.id, expected_hash='original')
    assert repo.get(user.id).session_epoch == 0


def test_successful_authentication_clears_failed_attempts_and_records_audit(account_app):
    _, repo, _ = account_app
    user = repo.create(email='audit@example.test', password_hash='original')
    repo.failed_login(user.id)
    assert repo.clear_failures(user.id, expected_hash='original')
    assert repo.get(user.id).failed_login_count == 0
    repo.record_event(user_id=user.id, email=user.email, event='login_success',
                      detail='', ip='127.0.0.1', user_agent='synthetic agent')
    if isinstance(repo, MongoAccounts):
        event = repo.store.database.security_events.find_one({'user_id': user.id})
        assert event['event'] == 'login_success'
    else:
        assert repo.security_model.query.filter_by(user_id=user.id).one().event == 'login_success'


def test_google_signup_and_repeat_signin_resolve_one_account(account_app):
    _, repo, _ = account_app
    first, created = repo.resolve_google(google_id='provider-subject-1',
                                         email='google@example.test', name='Google Student')
    assert created is True
    assert first.password_hash == ''
    again, created = repo.resolve_google(google_id='provider-subject-1',
                                         email='google@example.test', name='New provider name')
    assert created is False
    assert again.id == first.id
    assert repo.count() == 1


def test_verified_google_identity_links_existing_email_without_replacing_password(account_app):
    _, repo, _ = account_app
    existing = repo.create(email='linked@example.test', name='Original name', password_hash='preserved-hash')
    linked, created = repo.resolve_google(google_id='provider-subject-2',
                                          email='LINKED@example.test', name='Provider name', allow_email_link=True)
    assert created is False
    assert linked.id == existing.id
    assert linked.password_hash == 'preserved-hash'
    assert linked.name == 'Original name'
    assert linked.google_id == 'provider-subject-2'


def test_google_link_cannot_replace_an_existing_identity(account_app):
    _, repo, _ = account_app
    original, _ = repo.resolve_google(google_id='provider-original',
                                      email='bound@example.test', name='Original')
    with pytest.raises(IdentityConflict):
        repo.resolve_google(google_id='provider-other', email='bound@example.test', name='Other', allow_email_link=True)
    assert repo.get(original.id).google_id == 'provider-original'
    assert repo.count() == 1


def test_google_link_reads_authoritative_binding_despite_cached_account(account_app):
    _, repo, _ = account_app
    cached = repo.create(email='stale@example.test', name='Original', password_hash='original')
    user_id = cached.id
    if isinstance(repo, MongoAccounts):
        repo.collection.update_one({'id': user_id}, {'$set': {'google_id': 'already-bound'}})
    else:
        with repo.db.engine.begin() as connection:
            connection.execute(repo.model.__table__.update().where(
                repo.model.id == user_id).values(google_id='already-bound'))
    assert cached.google_id is None
    with pytest.raises(IdentityConflict):
        repo.resolve_google(google_id='replacement', email='stale@example.test',
                            name='Replacement', allow_email_link=True)
    assert repo.get(user_id).google_id == 'already-bound'


def test_email_match_alone_cannot_link_google_without_authority(account_app):
    _, repo, _ = account_app
    user = repo.create(email='third-party@example.test', password_hash='original')
    with pytest.raises(IdentityConflict):
        repo.resolve_google(google_id='new-google-subject', email=user.email, name='Provider')
    assert repo.get(user.id).google_id is None


def test_authentication_returns_exact_epoch_before_a_later_password_reset(account_app):
    _, repo, _ = account_app
    user = repo.create(email='signin-race@example.test', password_hash='original')
    authentication = repo.clear_failures(user.id, expected_hash='original')
    assert authentication.session_epoch == 0
    repo.change_password(user.id, expected_hash='original', password_hash='new')
    assert authentication.session_epoch == 0
    assert repo.get(user.id).session_epoch == 1


def test_reset_is_single_use_and_changes_password_and_epoch_together(account_app):
    _, repo, _ = account_app
    user = repo.create(email='reset@example.test', name='Reset', password_hash='original')
    repo.issue_reset(user.id, token_hash='hashed-token', requested_ip='127.0.0.1')
    assert repo.recent_resets(user.id) == 1
    assert repo.reset_usable('hashed-token')
    changed = repo.complete_reset('hashed-token', password_hash='reset-password')
    assert changed.user_id == user.id
    assert changed.session_epoch == 1
    assert repo.get(user.id).password_hash == 'reset-password'
    assert not repo.reset_usable('hashed-token')
    assert repo.complete_reset('hashed-token', password_hash='replayed-password') is None
    assert repo.get(user.id).password_hash == 'reset-password'
    assert repo.get(user.id).session_epoch == 1


def test_new_reset_invalidates_older_links_and_expired_tokens_do_not_write(account_app):
    _, repo, _ = account_app
    user = repo.create(email='reset@example.test', name='Reset', password_hash='original')
    repo.issue_reset(user.id, token_hash='first-hash', requested_ip='127.0.0.1')
    repo.issue_reset(user.id, token_hash='second-hash', requested_ip='127.0.0.1')
    assert repo.recent_resets(user.id) == 2
    assert not repo.reset_usable('first-hash')
    assert repo.reset_usable('second-hash')
    assert repo.complete_reset('first-hash', password_hash='stale') is None
    repo.issue_reset(user.id, token_hash='expired-hash', requested_ip='127.0.0.1', ttl_minutes=-1)
    assert not repo.reset_usable('second-hash')
    assert not repo.reset_usable('expired-hash')
    assert repo.complete_reset('expired-hash', password_hash='expired') is None
    assert repo.get(user.id).password_hash == 'original'


def test_later_reset_cannot_restamp_stale_browser(account_app, monkeypatch):
    app, repo, _ = account_app
    user = repo.create(email="race@example.test", name="Student", password_hash="")
    original = repo.change_password

    def concurrent_reset(user_id, *, expected_hash, password_hash):
        first = original(user_id, expected_hash=expected_hash, password_hash=password_hash)
        original(user_id, expected_hash=password_hash, password_hash="later-reset-hash")
        return first

    monkeypatch.setattr(repo, "change_password", concurrent_reset)
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie["_user_id"] = str(user.id)
        cookie["_fresh"] = True
        cookie["auth_time"] = time.time()
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 200
    with client.session_transaction() as cookie:
        assert cookie["session_epoch"] == 1
    assert repo.get(user.id).session_epoch == 2


def test_migrated_account_keeps_all_fields_and_precise_dates():
    client = mongomock.MongoClient()
    store = MongoStore("mongodb://test", "accounts", client_factory=lambda *a, **k: client)
    store.database.users.insert_one({"id": 42, "email": "migrated@example.test",
        "password_hash": "original-hash", "session_epoch": None,
        "created_at": "2026-01-01T12:34:56.123456", "marketing_emails_opt_in": True,
        "marketing_consent_version": "original-version", "custom_legacy_field": "keep"})
    repo = MongoAccounts(store)
    repo.ensure_indexes()
    user = repo.get("42")
    assert user.get_id() == "42"
    assert user.created_at.microsecond == 123456
    assert user.marketing_emails_opt_in is True
    assert user.custom_legacy_field == "keep"
    changed = repo.change_password(42, expected_hash="original-hash", password_hash="new")
    assert changed.session_epoch == 1
    assert repo.get(42).custom_legacy_field == "keep"
    assert repo.get(42).marketing_consent_version == "original-version"
