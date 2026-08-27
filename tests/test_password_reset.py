"""Password reset.

There was no reset flow at all: a student who forgot their password had no
route back into their own account. That is a product gap before it is a
security one, but building it opens four security questions at once — links
that expire, links that work once, a form that does not reveal who has an
account, and a reset that actually ends the access it is meant to revoke.
"""

from datetime import datetime, timedelta

import pytest

import App
from App import PasswordResetToken, SecurityEvent, User, bcrypt, db

OLD = "the-old-password"
NEW = "a-brand-new-password"
HEADERS = {"Origin": "http://localhost"}


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False        # testing the flow, not the throttle
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("rst+%")).all()]
    if ids:
        PasswordResetToken.query.filter(
            PasswordResetToken.user_id.in_(ids)).delete(synchronize_session=False)
    SecurityEvent.query.filter(SecurityEvent.email.like("rst+%")).delete(
        synchronize_session=False)
    User.query.filter(User.email.like("rst+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def account():
    with App.app.app_context():
        user = User(email="rst+a@example.com",
                    password_hash=bcrypt.generate_password_hash(OLD).decode())
        db.session.add(user)
        db.session.commit()
        return user.id


def request_reset(client, email="rst+a@example.com"):
    return client.post("/forgot-password", data={"email": email}, headers=HEADERS)


def issue(uid):
    """Mint a link the way the route does, and return the raw token."""
    with App.app.app_context():
        return App.issue_password_reset(db.session.get(User, uid))


def submit(client, token, password=NEW, confirm=None):
    return client.post("/reset-password", data={
        "token": token, "password": password,
        "confirm_password": confirm if confirm is not None else password,
    }, headers=HEADERS)


def password_now(uid):
    with App.app.app_context():
        return db.session.get(User, uid).password_hash


# ── The flow exists and works ────────────────────────────────

def test_the_form_is_reachable_and_linked_from_sign_in(client):
    assert client.get("/forgot-password").status_code == 200
    assert b'href="/forgot-password"' in client.get("/login/account").data


def test_a_reset_actually_changes_the_password(client, account):
    before = password_now(account)
    assert submit(client, issue(account)).status_code == 302
    after = password_now(account)
    assert after != before
    with App.app.app_context():
        assert bcrypt.check_password_hash(after, NEW)


def test_the_new_password_signs_in(client, account):
    submit(client, issue(account))
    client.get("/logout")
    r = client.post("/login/account",
                    data={"email": "rst+a@example.com", "password": NEW},
                    headers=HEADERS)
    assert r.status_code == 302


def test_the_old_password_stops_working(client, account):
    submit(client, issue(account))
    client.get("/logout")
    r = client.post("/login/account",
                    data={"email": "rst+a@example.com", "password": OLD},
                    headers=HEADERS)
    assert b"Invalid email or password" in r.data


# ── Links expire and work once ───────────────────────────────

def test_a_link_works_only_once(client, account):
    token = issue(account)
    assert submit(client, token).status_code == 302
    again = submit(client, token, password="second-attempt-password")
    assert b"expired or has already been used" in again.data


def test_an_expired_link_is_refused(client, account):
    token = issue(account)
    with App.app.app_context():
        row = PasswordResetToken.query.filter_by(
            token_hash=App._hash_reset_token(token)).one()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert b"expired or has already been used" in submit(client, token).data
    assert bcrypt.check_password_hash(password_now(account), OLD)


def test_requesting_again_invalidates_the_earlier_link(client, account):
    """A stolen inbox should not get more than one live link, and people
    expect the newest email to be the one that works."""
    first = issue(account)
    second = issue(account)
    assert b"expired or has already been used" in submit(client, first).data
    assert submit(client, second).status_code == 302


def test_a_made_up_token_is_refused(client, account):
    assert b"expired or has already been used" in submit(client, "not-a-real-token").data
    assert bcrypt.check_password_hash(password_now(account), OLD)


def test_the_token_is_stored_hashed(client, account):
    """A database read must not yield working reset links for every account
    with one outstanding."""
    token = issue(account)
    with App.app.app_context():
        rows = PasswordResetToken.query.all()
        assert rows
        for row in rows:
            assert row.token_hash != token
            assert len(row.token_hash) == 64


# ── The form reveals nothing ─────────────────────────────────

def test_a_known_and_an_unknown_address_read_identically(client, account):
    """Otherwise the form is a free membership oracle, reachable without
    signing in."""
    known = request_reset(client).data
    unknown = request_reset(client, "rst+nobody@example.com").data
    assert b"If an account exists" in known
    assert b"If an account exists" in unknown


def test_a_blank_submission_says_the_same_thing(client):
    assert b"If an account exists" in request_reset(client, "").data


def test_no_link_is_minted_for_an_address_with_no_account(client):
    with App.app.app_context():
        before = PasswordResetToken.query.count()
    request_reset(client, "rst+nobody@example.com")
    with App.app.app_context():
        # Scoped to the change this request caused: asserting on a global
        # count makes the test depend on what every other test left behind.
        assert PasswordResetToken.query.count() == before


def test_a_google_only_account_is_told_where_to_go(client):
    """It has no password to reset. Sending them to wait for a link that
    never arrives is the difference between getting in and not."""
    with App.app.app_context():
        db.session.add(User(email="rst+g@example.com", password_hash="",
                            google_id="g-123"))
        db.session.commit()
    body = request_reset(client, "rst+g@example.com").data
    assert b"Continue with Google" in body


# ── Throttling ───────────────────────────────────────────────

def test_an_account_stops_minting_links_after_a_few(client, account):
    """Per-account ceiling on top of the per-IP limit: without it our mail
    server is a way to flood somebody else's inbox."""
    for _ in range(App.PASSWORD_RESET_MAX_PER_HOUR + 3):
        request_reset(client)
    with App.app.app_context():
        minted = PasswordResetToken.query.filter_by(user_id=account).count()
        assert minted <= App.PASSWORD_RESET_MAX_PER_HOUR


def test_throttling_still_looks_the_same_to_the_caller(client, account):
    for _ in range(App.PASSWORD_RESET_MAX_PER_HOUR + 2):
        last = request_reset(client)
    assert b"If an account exists" in last.data


# ── The reset ends other access ──────────────────────────────

def test_a_session_from_before_the_reset_is_signed_out(client, account):
    """A reset is how someone recovers a compromised account. If a session
    captured beforehand keeps working, the reset achieved nothing."""
    attacker = App.app.test_client()
    with attacker.session_transaction() as s:
        s["_user_id"] = str(account)
        s["_fresh"] = True
        s["session_epoch"] = 0
    assert attacker.get("/command-center").status_code in (200, 302)

    submit(client, issue(account))

    landed = attacker.get("/command-center", follow_redirects=False)
    assert landed.status_code == 302
    assert "/login" in landed.headers["Location"]


def test_a_reset_clears_a_lockout(client, account):
    """They have just proved control of the inbox, so the lockout has done
    its job."""
    with App.app.app_context():
        user = db.session.get(User, account)
        user.login_locked_until = datetime.utcnow() + timedelta(minutes=30)
        user.failed_login_count = 8
        db.session.commit()

    submit(client, issue(account))

    with App.app.app_context():
        user = db.session.get(User, account)
        assert user.login_locked_until is None
        assert user.failed_login_count == 0


# ── Input rules ──────────────────────────────────────────────

def test_a_short_password_is_refused(client, account):
    assert b"at least 8 characters" in submit(client, issue(account),
                                              password="short").data


def test_a_mismatch_is_refused(client, account):
    token = issue(account)
    assert b"don" in submit(client, token, password=NEW,
                            confirm="something-else").data
    assert bcrypt.check_password_hash(password_now(account), OLD)


def test_a_rejected_attempt_does_not_burn_the_link(client, account):
    """A typo must not cost them the email."""
    token = issue(account)
    submit(client, token, password="short")
    assert submit(client, token).status_code == 302


# ── The audit trail ──────────────────────────────────────────

def test_the_request_and_completion_are_recorded(client, account):
    request_reset(client)
    submit(client, issue(account))
    with App.app.app_context():
        events = {e.event for e in SecurityEvent.query.all()}
        assert "password_reset_requested" in events
        assert "password_reset_completed" in events


def test_the_trail_never_holds_the_token_or_password(client, account):
    token = issue(account)
    submit(client, token)
    with App.app.app_context():
        for row in SecurityEvent.query.all():
            assert token not in (row.detail or "")
            assert NEW not in (row.detail or "")
