"""reCAPTCHA on the forms strangers can reach.

Three endpoints are reachable without signing in and each is abusable
differently: registration mints accounts, password reset sends mail to an
address the requester names, and sign-in is where credential stuffing lands.

Two decisions carry most of the weight and are pinned here. Sign-in only
demands a challenge after repeated failures, so honest students are not taxed
daily to slow an attacker who has guessed nothing. And a verifier that cannot
be reached fails *open*, because an outage at Google must not become an
outage here.

Nothing in this file touches the network: ``verify`` is stubbed throughout.
"""

import pytest

import App
import bot_protection
from App import User, bcrypt, db

PASSWORD = "the-right-password"
HEADERS = {"Origin": "http://localhost"}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("RECAPTCHA_SITE_KEY", "site-key-for-tests")
    monkeypatch.setenv("RECAPTCHA_SECRET_KEY", "secret-key-for-tests")


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("RECAPTCHA_SITE_KEY", raising=False)
    monkeypatch.delenv("RECAPTCHA_SECRET_KEY", raising=False)


@pytest.fixture
def passes(monkeypatch):
    monkeypatch.setattr(bot_protection, "verify",
                        lambda *a, **k: (True, "stubbed pass"))


@pytest.fixture
def fails(monkeypatch):
    monkeypatch.setattr(bot_protection, "verify",
                        lambda *a, **k: (False, "stubbed reject"))


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    # SQLite reuses row ids after a delete, so a fresh account can inherit
    # reset tokens another test file left behind for the same id. Clear them
    # rather than let the throttle look already-exhausted.
    ids = [u.id for u in User.query.filter(User.email.like("cap+%")).all()]
    if ids:
        App.PasswordResetToken.query.filter(
            App.PasswordResetToken.user_id.in_(ids)).delete(
                synchronize_session=False)
    User.query.filter(User.email.like("cap+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def account():
    with App.app.app_context():
        user = User(email="cap+a@example.com",
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        return user.id


def attempt_login(client, password):
    return client.post("/login/account",
                       data={"email": "cap+a@example.com", "password": password},
                       headers=HEADERS)


def fail_login(client, times):
    for _ in range(times):
        attempt_login(client, "wrong")


# ── Configuration ────────────────────────────────────────────

def test_it_is_off_until_both_keys_are_present(no_keys):
    assert bot_protection.is_enabled() is False


def test_one_key_alone_is_not_enough(monkeypatch, no_keys):
    """A site key with no secret would reject every submission."""
    monkeypatch.setenv("RECAPTCHA_SITE_KEY", "only-half")
    assert bot_protection.is_enabled() is False


def test_with_no_keys_nothing_is_rendered_or_required(client, no_keys):
    """Development and CI need no special case, and no form is left with a
    dead placeholder."""
    assert b"g-recaptcha" not in client.get("/register").data
    assert App.check_recaptcha("register") is None


# ── Where the widget appears ─────────────────────────────────

def test_registration_carries_it(client, keys):
    assert b"g-recaptcha" in client.get("/register").data


def test_password_reset_carries_it(client, keys):
    assert b"g-recaptcha" in client.get("/forgot-password").data


def test_a_clean_sign_in_page_does_not(client, keys):
    """Charging every honest student a checkbox every day, to slow an
    attacker who has not guessed anything, is a bad trade."""
    assert b"g-recaptcha" not in client.get("/login/account").data


# ── Sign-in: progressive ─────────────────────────────────────

def test_the_first_failure_is_not_challenged(client, keys, fails, account):
    """Enforcing here would refuse a token the form had not yet offered."""
    body = attempt_login(client, "wrong").data
    assert b"Invalid email or password" in body
    assert b"not a robot" not in body


def test_but_the_widget_appears_from_that_first_failure(client, keys, fails, account):
    """It has to be on the page before the attempt that requires it."""
    assert b"g-recaptcha" in attempt_login(client, "wrong").data


def test_after_repeated_failures_it_is_enforced(client, keys, fails, account):
    fail_login(client, App.RECAPTCHA_LOGIN_AFTER_FAILURES)
    assert b"not a robot" in attempt_login(client, PASSWORD).data


def test_a_valid_challenge_lets_the_sign_in_through(client, keys, passes, account):
    fail_login(client, App.RECAPTCHA_LOGIN_AFTER_FAILURES)
    assert attempt_login(client, PASSWORD).status_code == 302


def test_a_successful_sign_in_resets_the_requirement(client, keys, passes, account):
    """The failure count clears on success, so the next visit is unchallenged
    again."""
    fail_login(client, App.RECAPTCHA_LOGIN_AFTER_FAILURES)
    attempt_login(client, PASSWORD)
    with App.app.app_context():
        assert db.session.get(User, account).failed_login_count == 0


# ── Registration and reset: always ───────────────────────────

def test_a_failed_challenge_blocks_registration(client, keys, fails):
    before = _user_count()
    client.post("/register", data={
        "email": "cap+bot@example.com", "password": "abcdefgh",
        "confirm_password": "abcdefgh", "birth_year": "2005",
    }, headers=HEADERS)
    assert _user_count() == before


def test_form_errors_are_reported_before_the_challenge(client, keys, fails):
    """Checking the captcha first meant a mistyped password was hidden
    behind the robot check: fix that, resubmit, and only then learn the
    passwords did not match."""
    body = client.post("/register", data={
        "email": "cap+mismatch@example.com", "password": "abcdefgh",
        "confirm_password": "different", "birth_year": "2005",
    }, headers=HEADERS).data
    assert b"Passwords do not match" in body
    assert b"not a robot" not in body


def test_a_short_password_is_reported_before_the_challenge(client, keys, fails):
    body = client.post("/register", data={
        "email": "cap+short@example.com", "password": "abc",
        "confirm_password": "abc", "birth_year": "2005",
    }, headers=HEADERS).data
    assert b"at least 8 characters" in body
    assert b"not a robot" not in body


def test_a_failed_challenge_blocks_a_reset_request(client, keys, fails, account):
    with App.app.app_context():
        before = App.PasswordResetToken.query.filter_by(user_id=account).count()
    body = client.post("/forgot-password",
                       data={"email": "cap+a@example.com"},
                       headers=HEADERS).data
    assert b"not a robot" in body
    with App.app.app_context():
        # Asserting on the delta, not a total: the point is that this request
        # minted nothing.
        assert App.PasswordResetToken.query.filter_by(
            user_id=account).count() == before


def test_a_passing_challenge_lets_a_reset_through(client, keys, passes, account):
    body = client.post("/forgot-password",
                       data={"email": "cap+a@example.com"},
                       headers=HEADERS).data
    assert b"If an account exists" in body


def _user_count():
    with App.app.app_context():
        return User.query.filter(User.email.like("cap+%")).count()


# ── Verification behaviour ───────────────────────────────────

def test_a_missing_token_is_refused(client, keys):
    ok, reason = bot_protection.verify("")
    assert ok is False
    assert "no token" in reason


def test_an_unreachable_verifier_fails_open(client, keys, monkeypatch):
    """An outage at Google must not stop anyone signing in. A bot that can
    also take down recaptcha.net was never going to be stopped by this."""
    def boom(*args, **kwargs):
        raise OSError("network down")
    monkeypatch.setattr(bot_protection.urllib.request, "urlopen", boom)
    ok, reason = bot_protection.verify("some-token")
    assert ok is True
    assert "unreachable" in reason


def _stub_response(monkeypatch, payload):
    import json as _json

    class _Resp:
        def read(self):
            return _json.dumps(payload).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bot_protection.urllib.request, "urlopen",
                        lambda *a, **k: _Resp())


def test_google_saying_no_is_refused(client, keys, monkeypatch):
    _stub_response(monkeypatch, {"success": False,
                                 "error-codes": ["invalid-input-response"]})
    ok, reason = bot_protection.verify("bad-token")
    assert ok is False
    assert "invalid-input-response" in reason


def test_a_v2_success_needs_no_score(client, keys, monkeypatch):
    """v2 returns no score, and success is the whole answer."""
    _stub_response(monkeypatch, {"success": True})
    assert bot_protection.verify("t")[0] is True


def test_a_low_v3_score_is_refused(client, keys, monkeypatch):
    _stub_response(monkeypatch, {"success": True, "score": 0.1})
    ok, reason = bot_protection.verify("t")
    assert ok is False
    assert "below" in reason


def test_a_high_v3_score_passes(client, keys, monkeypatch):
    _stub_response(monkeypatch, {"success": True, "score": 0.9})
    assert bot_protection.verify("t")[0] is True


def test_the_score_floor_is_configurable(client, keys, monkeypatch):
    monkeypatch.setenv("RECAPTCHA_SCORE_THRESHOLD", "0.05")
    _stub_response(monkeypatch, {"success": True, "score": 0.1})
    assert bot_protection.verify("t")[0] is True


def test_a_token_minted_for_another_form_is_refused(client, keys, monkeypatch):
    """v3 signs the action, so a token from the newsletter box cannot be
    replayed against sign-in."""
    _stub_response(monkeypatch, {"success": True, "score": 0.9,
                                 "action": "newsletter"})
    ok, reason = bot_protection.verify("t", expected_action="login")
    assert ok is False
    assert "action" in reason


# ── The page has to be able to load it ───────────────────────

def test_the_policy_allows_the_script_and_the_challenge_frame(client, keys):
    """Auth pages ship frame-src 'none', which would leave a widget that
    cannot draw and a form nobody can submit."""
    response = client.get("/login/account")
    csp = (response.headers.get("Content-Security-Policy-Report-Only")
           or response.headers.get("Content-Security-Policy") or "")
    script_src = csp.split("script-src")[1].split(";")[0]
    frame_src = csp.split("frame-src")[1].split(";")[0]
    assert "https://www.google.com" in script_src
    assert "https://www.gstatic.com" in script_src
    assert "https://www.google.com" in frame_src


def test_the_failure_message_does_not_say_which_check_failed(client, keys, fails,
                                                             account):
    """Naming the reason is free tuning feedback for whoever is trying to get
    past it."""
    fail_login(client, App.RECAPTCHA_LOGIN_AFTER_FAILURES)
    body = attempt_login(client, PASSWORD).data.decode("utf-8", "ignore")
    assert "stubbed reject" not in body
    assert "score" not in body.lower().split("not a robot")[-1][:200]
