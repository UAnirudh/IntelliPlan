"""Encryption at rest for third-party OAuth tokens.

A student who connects Canvas, Google, Notion, Blackboard or Moodle hands
IntelliPlan a live credential for their coursework and calendar. Those were
stored as plaintext, so a database backup, a snapshot, a read replica, or a
SQL-injection bug anywhere in the app handed over working access to every
connected account. Passwords were always hashed; these had nothing.

The load-bearing property is the last test in the first section: the secret
must not appear in the row. Everything else is about turning that on without
logging existing students out of their integrations.
"""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

import App
import secret_box
from App import GoogleIntegration, User, db

SECRET = "ya29.a0AfB_very-real-looking-google-token"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret_box.reset_cache()
    yield
    secret_box.reset_cache()


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    secret_box.reset_cache()
    yield
    secret_box.reset_cache()


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("enc+%")).all()]
    if ids:
        GoogleIntegration.query.filter(
            GoogleIntegration.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("enc+%")).delete(synchronize_session=False)
    db.session.commit()


def store_token(value=SECRET, email="enc+a@example.com"):
    """Write a token through the ORM and return its row id."""
    user = User(email=email, password_hash="")
    db.session.add(user)
    db.session.commit()
    row = GoogleIntegration(user_id=user.id, token_data=value,
                            account_email=email)
    db.session.add(row)
    db.session.commit()
    return row.id


def raw_stored(row_id):
    """What is actually in the column, bypassing transparent decryption."""
    return db.session.execute(
        text("SELECT token_data FROM google_integrations WHERE id = :i"),
        {"i": row_id}).scalar()


# ── The point of the exercise ────────────────────────────────

def test_the_token_is_not_readable_in_the_database(client, key):
    """The whole reason this exists. A backup or a snapshot must not contain
    a working credential."""
    with App.app.app_context():
        row_id = store_token()
        assert SECRET not in raw_stored(row_id)


def test_it_round_trips_through_the_orm(client, key):
    """Callers read ``row.token_data`` and must not have to know."""
    with App.app.app_context():
        row_id = store_token()
        db.session.expire_all()
        assert db.session.get(GoogleIntegration, row_id).token_data == SECRET


def test_the_stored_value_is_marked_as_ours(client, key):
    with App.app.app_context():
        assert raw_stored(store_token()).startswith(secret_box.PREFIX)


# ── Turning it on without breaking existing students ─────────

def test_a_token_written_before_encryption_still_reads(client, key):
    """Existing rows are plaintext. If they stopped working, switching this
    on would disconnect every integration in the product."""
    with App.app.app_context():
        row_id = store_token()
        db.session.execute(
            text("UPDATE google_integrations SET token_data = :v WHERE id = :i"),
            {"v": SECRET, "i": row_id})       # simulate a pre-encryption row
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(GoogleIntegration, row_id).token_data == SECRET


def test_rewriting_a_legacy_row_encrypts_it(client, key):
    """Rows migrate as they are touched; the backfill script handles the
    rest."""
    with App.app.app_context():
        row_id = store_token()
        db.session.execute(
            text("UPDATE google_integrations SET token_data = :v WHERE id = :i"),
            {"v": SECRET, "i": row_id})
        db.session.commit()
        db.session.expire_all()

        from sqlalchemy.orm.attributes import flag_modified
        row = db.session.get(GoogleIntegration, row_id)
        row.token_data = row.token_data
        # Re-assigning an identical value does not mark the attribute dirty,
        # so without this SQLAlchemy emits no UPDATE and the row stays
        # plaintext. The backfill script hit exactly this.
        flag_modified(row, "token_data")
        db.session.commit()
        assert secret_box.is_encrypted(raw_stored(row_id))


def test_encrypting_twice_does_not_double_wrap(client, key):
    """Fernet output differs on every call, so this checks the shape rather
    than equality: a second pass leaves the value alone, and a single decrypt
    is enough to get the plaintext back."""
    once = secret_box.encrypt(SECRET)
    assert secret_box.encrypt(once) == once
    assert secret_box.decrypt(once) == SECRET


# ── No key configured ────────────────────────────────────────

def test_without_a_key_everything_still_works(client, no_key):
    """Development and CI have no key. The app must run, storing plaintext
    exactly as it did before."""
    assert secret_box.is_enabled() is False
    with App.app.app_context():
        row_id = store_token(email="enc+b@example.com")
        assert db.session.get(GoogleIntegration, row_id).token_data == SECRET


def test_a_malformed_key_does_not_silently_look_encrypted(monkeypatch):
    """A bad key downgrading to plaintext is survivable; doing it while
    reporting success is not."""
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    secret_box.reset_cache()
    assert secret_box.is_enabled() is False
    assert secret_box.encrypt(SECRET) == SECRET
    secret_box.reset_cache()


# ── Key rotation ─────────────────────────────────────────────

def test_a_value_encrypted_with_an_old_key_still_reads(monkeypatch):
    """Rotation is listing the new key first and keeping the old one after."""
    old = Fernet.generate_key().decode()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", old)
    secret_box.reset_cache()
    ciphertext = secret_box.encrypt(SECRET)

    new = Fernet.generate_key().decode()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", f"{new},{old}")
    secret_box.reset_cache()
    assert secret_box.decrypt(ciphertext) == SECRET

    # New writes use the first key.
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", new)
    secret_box.reset_cache()
    assert secret_box.decrypt(secret_box.encrypt(SECRET)) == SECRET
    secret_box.reset_cache()


def test_a_value_whose_key_is_gone_fails_soft(monkeypatch):
    """Realistic cause is a key rotated away. Returning None fails that one
    integration; raising would 500 every page the token touches."""
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret_box.reset_cache()
    ciphertext = secret_box.encrypt(SECRET)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret_box.reset_cache()
    assert secret_box.decrypt(ciphertext) is None
    secret_box.reset_cache()


# ── Edges ────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, ""])
def test_empty_values_pass_through(key, value):
    assert secret_box.encrypt(value) == value
    assert secret_box.decrypt(value) == value


def test_plaintext_is_recognised_as_not_ours(key):
    assert secret_box.is_encrypted(SECRET) is False
    assert secret_box.is_encrypted(secret_box.encrypt(SECRET)) is True


# ── Coverage: every credential column is encrypted ───────────

def test_every_token_column_uses_the_encrypted_type():
    """A new integration that stores a raw token would reintroduce exactly
    the problem this closed."""
    expected = {
        "GoogleIntegration": ["token_data"],
        "NotionIntegration": ["token"],
        "CanvasIntegration": ["access_token", "refresh_token"],
        "ClassroomIntegration": ["access_token", "refresh_token"],
        "BlackboardIntegration": ["access_token", "refresh_token"],
        "MoodleIntegration": ["ws_token"],
    }
    for model_name, columns in expected.items():
        model = getattr(App, model_name)
        for column in columns:
            kind = model.__table__.c[column].type
            assert isinstance(kind, secret_box.EncryptedText), \
                f"{model_name}.{column} stores credentials unencrypted"
