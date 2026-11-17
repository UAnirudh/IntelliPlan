"""Native encrypted provider credentials and stale-request protection."""
from flask import Flask
from flask_login import LoginManager
from cryptography.fernet import Fernet
import mongomock
import pytest

import premium_glue
import secret_box
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_ai_accounts import MongoAIAccounts
from intelliplan.premium import byok


KEY = 'sk-synthetic' + 'a' * 30
REPLACEMENT = 'sk-synthetic' + 'b' * 30


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('BYOK_ENABLED', '1')
    secret_box.reset_cache()
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-ai-accounts')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'providers', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1, 'birth_year': 1990}, {'id': 2, 'birth_year': 1990},
                                     {'id': 3, 'birth_year': 2015}])
    repo = MongoAIAccounts(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    app.register_blueprint(premium_glue.premium_bp)
    monkeypatch.setattr(byok, 'verify', lambda provider, key: byok.Check(True, 'Synthetic verified'))
    yield app, store, repo
    secret_box.reset_cache()


def sign_in(app, user_id=1):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(user_id)
        cookie['_fresh'] = True
    return client


def test_native_link_list_usage_and_owner_scoped_unlink(environment):
    app, store, repo = environment
    client = sign_in(app)
    response = client.post('/api/ai-accounts', json={'provider': 'openai', 'api_key': KEY})
    assert response.status_code == 200
    assert KEY not in response.get_data(as_text=True)
    raw = store.database.linked_ai_accounts.find_one({'user_id': 1})
    assert secret_box.is_encrypted(raw['api_key'])
    assert secret_box.decrypt(raw['api_key']) == KEY
    with app.app_context():
        row = premium_glue.linked_accounts(1, active_only=True)[0]
        assert KEY not in repr(row)
        premium_glue._mark_key(row, ok=True)
    assert repo.list(1)[0].last_used_at is not None
    assert sign_in(app, 2).get('/api/ai-accounts').get_json()['accounts'] == []
    assert sign_in(app, 2).delete('/api/ai-accounts/openai').status_code == 200
    assert len(repo.list(1)) == 1
    assert client.delete('/api/ai-accounts/openai').status_code == 200
    assert repo.list(1) == []
    assert 'sqlalchemy' not in app.extensions


def test_stale_provider_result_cannot_disable_replacement_or_recreate_unlinked_key(environment):
    app, store, repo = environment
    old = repo.link(1, 'openai', KEY)
    repo.link(1, 'openai', REPLACEMENT)
    assert not repo.mark(old, ok=False, error=KEY)
    current = repo.list(1)[0]
    assert current.status == 'active'
    assert repo.mark(current, ok=False, error='Provider rejected ' + REPLACEMENT)
    saved = repo.list(1)[0]
    assert REPLACEMENT not in saved.last_error
    assert repo.list(1, active_only=True) == []
    repo.link(1, 'openai', REPLACEMENT)
    assert not repo.mark(current, ok=False)
    repo.unlink(1, 'openai')
    assert not repo.mark(saved, ok=True)
    assert repo.list(1) == []


def test_encryption_required_and_adult_eligibility_rechecked_at_write(environment, monkeypatch):
    app, store, repo = environment
    assert sign_in(app, 3).post('/api/ai-accounts', json={'provider': 'openai', 'api_key': KEY}).status_code == 403
    with pytest.raises(PermissionError):
        repo.link(3, 'openai', KEY)
    monkeypatch.setattr(secret_box, 'encrypt', lambda value: value)
    with pytest.raises(RuntimeError):
        repo.link(1, 'openai', KEY)
    assert store.database.linked_ai_accounts.count_documents({}) == 0


def test_partial_key_replacement_preserves_unknown_fields_and_legacy_secret(environment):
    app, store, repo = environment
    store.database.linked_ai_accounts.insert_one({'id': 100, 'user_id': 1, 'provider': 'openai',
        'api_key': KEY, 'status': 'active', 'future_field': 'retain'})
    assert repo.list(1, active_only=True)[0].api_key == KEY
    repo.link(1, 'openai', REPLACEMENT)
    raw = store.database.linked_ai_accounts.find_one({'id': 100})
    assert raw['future_field'] == 'retain'
    assert secret_box.is_encrypted(raw['api_key'])
    assert store.database.linked_ai_accounts.count_documents({'user_id': 1}) == 1
    store.database.users.delete_one({'id': 1})
    with pytest.raises(LookupError):
        repo.link(1, 'openai', KEY)
