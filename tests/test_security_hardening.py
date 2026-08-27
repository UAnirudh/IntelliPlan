"""Brute-force resistance, CSRF depth, session fixation, and the audit trail.

What was already in place before this: parameterised queries throughout,
bcrypt hashing, HttpOnly/Secure/SameSite session cookies, HSTS and the other
security headers, a 25MB body cap, per-IP rate limits on sign-in, a generic
"invalid email or password" that does not enumerate accounts, and an upload
extension whitelist.

What was not, and is tested here: nothing bounded guessing against a single
*account* (rate limits are per IP, which a distributed attempt steps around),
a session id planted before sign-in stayed valid after it, security events
existed only as stdout prints, and unknown origins got a wildcard CORS
header.
"""

import os
from datetime import datetime, timedelta

import pytest

import App
import request_guards
from App import SecurityEvent, User, bcrypt, db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False          # exercising lockout, not rate limits
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    SecurityEvent.query.filter(SecurityEvent.email.like("sec+%")).delete(
        synchronize_session=False)
    User.query.filter(User.email.like("sec+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def account():
    with App.app.app_context():
        user = User(email="sec+a@example.com",
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        return user.id


def sign_in(client, password, email="sec+a@example.com"):
    return client.post("/login/account",
                       data={"email": email, "password": password},
                       headers={"Origin": "http://localhost"})


def reload_user(uid):
    with App.app.app_context():
        return db.session.get(User, uid)


def events(name):
    with App.app.app_context():
        return SecurityEvent.query.filter_by(event=name).count()


# ── Account lockout ──────────────────────────────────────────

def test_a_wrong_password_is_counted(client, account):
    sign_in(client, "wrong")
    assert reload_user(account).failed_login_count == 1


def test_the_right_password_clears_the_streak(client, account):
    sign_in(client, "wrong")
    sign_in(client, PASSWORD)
    assert reload_user(account).failed_login_count == 0


def test_enough_failures_lock_the_account(client, account):
    for _ in range(App.LOGIN_LOCKOUT_THRESHOLD):
        sign_in(client, "wrong")
    user = reload_user(account)
    assert user.login_locked_until is not None
    assert App.account_lock_remaining(user) > 0


def test_a_locked_account_refuses_even_the_correct_password(client, account):
    """The point of a lockout is that guessing correctly late still fails."""
    for _ in range(App.LOGIN_LOCKOUT_THRESHOLD):
        sign_in(client, "wrong")
    page = sign_in(client, PASSWORD).data.decode("utf-8", "ignore")
    assert "Too many failed sign-ins" in page


def test_the_lock_expires(client, account):
    with App.app.app_context():
        user = db.session.get(User, account)
        user.login_locked_until = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert App.account_lock_remaining(reload_user(account)) == 0


def test_a_failure_against_an_unknown_address_locks_nothing(client):
    """There is no account to lock, and inventing one would be a way to find
    out which addresses exist."""
    sign_in(client, "wrong", email="sec+ghost@example.com")
    assert events("login_failed_unknown_account") >= 1


def test_the_error_is_the_same_either_way(client, account):
    """Distinguishing "no such account" from "wrong password" hands over a
    list of who has an account here."""
    unknown = sign_in(client, "wrong", email="sec+ghost@example.com")
    known = sign_in(client, "wrong")
    assert b"Invalid email or password" in unknown.data
    assert b"Invalid email or password" in known.data


# ── Session fixation ─────────────────────────────────────────

def test_pre_login_session_state_does_not_survive_sign_in(client, account):
    """A session id planted before sign-in must not come out the other side
    still carrying what the attacker put in it."""
    with client.session_transaction() as s:
        s["planted_by_attacker"] = "value"
    sign_in(client, PASSWORD)
    with client.session_transaction() as s:
        assert "planted_by_attacker" not in s


def test_the_flow_state_sign_in_depends_on_is_kept(client, account):
    """Rotating must not break the group-invite and OAuth return paths that
    legitimately span the sign-in boundary."""
    with client.session_transaction() as s:
        s["oauth_return_to"] = "/connect"
        s["junk"] = "drop me"
    sign_in(client, PASSWORD)
    with client.session_transaction() as s:
        assert s.get("oauth_return_to") == "/connect"
        assert "junk" not in s


# ── The audit trail ──────────────────────────────────────────

def test_a_successful_sign_in_is_recorded(client, account):
    sign_in(client, PASSWORD)
    assert events("login_success") >= 1


def test_a_lockout_is_recorded(client, account):
    for _ in range(App.LOGIN_LOCKOUT_THRESHOLD):
        sign_in(client, "wrong")
    assert events("account_locked") >= 1


def test_the_trail_keeps_no_password(client, account):
    """An audit log that captured the attempted password would be a worse
    liability than the thing it audits."""
    sign_in(client, PASSWORD)
    sign_in(client, "wrong")
    with App.app.app_context():
        for row in SecurityEvent.query.all():
            assert PASSWORD not in (row.detail or "")
            assert PASSWORD not in (row.user_agent or "")


def test_logging_never_breaks_the_request(client, monkeypatch, account):
    """An audit write that takes down sign-in has made things less safe."""
    def explode(*args, **kwargs):
        raise RuntimeError("audit backend down")
    monkeypatch.setattr(App.db.session, "add", explode)
    App.log_security_event("test_event", email="sec+x@example.com")  # must not raise


# ── CSRF: cross-site writes ──────────────────────────────────

def test_a_post_from_another_site_is_refused(client):
    r = client.post("/api/cookies/consent", json={"granted": []},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_a_post_from_our_own_pages_goes_through(client):
    r = client.post("/api/cookies/consent", json={"granted": []},
                    headers={"Origin": "http://localhost"})
    assert r.status_code == 200


def test_a_non_browser_client_is_unaffected(client):
    """No Origin and no Referer is curl or the mobile app, neither of which
    can be made to carry somebody else's cookie."""
    assert client.post("/api/cookies/consent", json={"granted": []}).status_code == 200


def test_reads_are_never_blocked(client):
    r = client.get("/api/cookies/consent", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_oauth_callbacks_stay_reachable(client):
    """Providers redirect back to us cross-origin by design."""
    for path in ("/oauth/google/callback", "/api/lms/callback/blackboard"):
        assert request_guards.is_exempt(path)


def test_the_extension_is_allowed(client):
    with App.app.test_request_context(
            "/api/x", method="POST",
            headers={"Origin": "chrome-extension://abcdef"}):
        assert request_guards.cross_site_violation() is None


def test_a_blocked_attempt_is_recorded(client):
    client.post("/api/cookies/consent", json={"granted": []},
                headers={"Origin": "https://evil.example"})
    assert events("csrf_blocked") >= 1


def test_the_guard_can_be_switched_off(client, monkeypatch):
    """A false positive on a real integration needs a lever that is not a
    redeploy."""
    monkeypatch.setenv("CSRF_GUARD", "0")
    r = client.post("/api/cookies/consent", json={"granted": []},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


# ── CORS ─────────────────────────────────────────────────────

def test_an_unknown_origin_gets_no_cors_grant(client):
    """It used to fall back to "*", letting any site read every
    unauthenticated API response."""
    r = client.get("/api/cookies/consent", headers={"Origin": "https://evil.example"})
    assert r.headers.get("Access-Control-Allow-Origin") != "*"
    assert "Access-Control-Allow-Origin" not in r.headers


def test_a_known_origin_is_echoed(client):
    r = client.get("/api/cookies/consent", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"


def test_html_pages_carry_no_cors_header(client):
    assert "Access-Control-Allow-Origin" not in client.get("/").headers


# ── Controls that were already in place ──────────────────────

def test_the_session_cookie_flags_are_set(client):
    assert App.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert App.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert App.app.config["REMEMBER_COOKIE_HTTPONLY"] is True


def test_a_body_size_ceiling_exists(client):
    assert App.app.config["MAX_CONTENT_LENGTH"] == 25 * 1024 * 1024


def test_the_security_headers_are_present(client):
    headers = client.get("/").headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert "Strict-Transport-Security" in headers
    assert "Referrer-Policy" in headers
    assert "Permissions-Policy" in headers


def test_uploads_are_restricted_to_a_whitelist():
    assert App.NOTE_ALLOWED_EXTENSIONS
    for bad in ("exe", "php", "js", "sh", "html"):
        assert bad not in App.NOTE_ALLOWED_EXTENSIONS


def test_no_local_database_is_tracked_in_git():
    """Both were tracked while empty; running the app locally fills them with
    real accounts and password hashes."""
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], capture_output=True,
                             text=True, cwd=os.path.dirname(os.path.dirname(
                                 os.path.abspath(__file__)))).stdout
    assert not [f for f in tracked.splitlines() if f.endswith((".db", ".sqlite3"))]
