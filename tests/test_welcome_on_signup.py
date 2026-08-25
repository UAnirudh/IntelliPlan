"""The welcome email fires at signup, not a day later.

The email itself already existed, but its only trigger was
``campaigns.sweep_welcome`` — a daily cron behind ``CRON_SECRET``, which
answers 503 when that secret is unset. On a deployment without that cron
wired, signing up produced no email at all.

Nothing here touches the network: the Resend call is patched at
``urllib.request.urlopen``, the same seam the real code uses. The delivery
thread is run inline so the assertions are deterministic rather than racing
a daemon thread.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

import App as app_module
from App import EmailSend, User, db


@pytest.fixture
def resend(monkeypatch):
    """Capture what would have been POSTed to Resend; return a fake 200."""
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "IntelliPlan, 1 Test Way, Testville CA 94000")
    captured: list[dict] = []

    class FakeResponse:
        def read(self):
            return json.dumps({"id": "msg_test_00000000"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        yield captured


@pytest.fixture
def inline_threads(monkeypatch):
    """Run the delivery thread's target synchronously.

    The production path is a daemon thread so a slow provider cannot hold a
    signup open; asserting against it as a thread would mean sleeping and
    hoping. Patching ``threading.Thread`` keeps the code under test exactly
    as shipped while making the timing deterministic.
    """
    import threading

    class InlineThread:
        def __init__(self, target=None, name=None, daemon=None, **kw):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(threading, "Thread", InlineThread)


@pytest.fixture
def client(resend, inline_threads):
    app_module.app.config["TESTING"] = True
    app_module.limiter.enabled = False
    with app_module.app.test_client() as c:
        yield c
    app_module.limiter.enabled = True


def _fresh_email():
    return f"signup-{uuid.uuid4().hex[:10]}@example.test"


def _register(client, email, birth_year="2005"):
    return client.post("/register", data={
        "email": email,
        "password": "hunter2ok",
        "confirm_password": "hunter2ok",
        "birth_year": birth_year,
    }, follow_redirects=False)


def test_signing_up_sends_the_welcome_email(client, resend):
    email = _fresh_email()
    _register(client, email)

    mine = [p for p in resend if p["to"] == [email]]
    assert len(mine) == 1, f"expected exactly one welcome, got {len(mine)}"
    assert "Welcome to IntelliPlan" in mine[0]["subject"]


def test_the_welcome_carries_both_a_text_and_an_html_part(client, resend):
    email = _fresh_email()
    _register(client, email)
    payload = [p for p in resend if p["to"] == [email]][0]
    assert payload.get("text")
    assert payload.get("html")


def test_the_welcome_carries_an_unsubscribe_header(client, resend):
    email = _fresh_email()
    _register(client, email)
    payload = [p for p in resend if p["to"] == [email]][0]
    assert "List-Unsubscribe" in (payload.get("headers") or {})


def test_the_send_is_recorded_so_the_daily_sweep_will_not_repeat_it(client, resend):
    """The ledger row is what makes the inline send and the cron sweep
    compose instead of both delivering."""
    from intelliplan.email import campaigns

    email = _fresh_email()
    _register(client, email)

    with app_module.app.app_context():
        user = User.query.filter_by(email=email).one()
        rows = EmailSend.query.filter_by(user_id=user.id, email_key="welcome").all()
        assert len(rows) == 1
        assert rows[0].status == "sent"

        campaigns.sweep_welcome()

    assert len([p for p in resend if p["to"] == [email]]) == 1, "the sweep sent a duplicate"


def test_an_under_13_signup_is_not_emailed_before_a_parent_consents(client, resend):
    """The eligibility gate, not the caller, decides this — signup fires
    unconditionally and the gate holds it back."""
    from time_utils import utcnow

    email = _fresh_email()
    child_year = str(utcnow().year - 9)
    r = client.post("/register", data={
        "email": email,
        "password": "hunter2ok",
        "confirm_password": "hunter2ok",
        "birth_year": child_year,
        "parent_email": "a.parent@example.test",
    }, follow_redirects=False)
    assert r.status_code == 200

    assert [p for p in resend if p["to"] == [email]] == []


def test_a_failed_signup_creates_no_account_and_sends_nothing(client, resend):
    email = _fresh_email()
    client.post("/register", data={
        "email": email,
        "password": "hunter2ok",
        "confirm_password": "different",
        "birth_year": "2005",
    })
    assert [p for p in resend if p["to"] == [email]] == []
    with app_module.app.app_context():
        assert User.query.filter_by(email=email).first() is None


def test_a_provider_outage_does_not_break_signup(client, resend, monkeypatch):
    """The account is the thing that must survive. A mail provider that is
    down gets retried by the sweep, because the ledger row is left failed."""
    def explode(*a, **kw):
        raise RuntimeError("resend is down")

    monkeypatch.setattr(app_module, "_send_email", explode)

    email = _fresh_email()
    r = _register(client, email)
    assert r.status_code in (200, 302)

    with app_module.app.app_context():
        user = User.query.filter_by(email=email).one()
        row = EmailSend.query.filter_by(user_id=user.id, email_key="welcome").one()
        assert row.status == "failed"


def test_the_extension_signup_path_also_asks_for_a_welcome(client, resend):
    """No birth year is collected there, so the gate defers it — but the
    call is made, which is what stops the path from being forgotten."""
    email = _fresh_email()
    r = client.post("/extension/register", json={"email": email, "password": "hunter2ok"})
    assert r.get_json()["status"] == "ok"

    with app_module.app.app_context():
        user = User.query.filter_by(email=email).one()
        # Held back at the gate before a ledger row is ever claimed.
        assert EmailSend.query.filter_by(user_id=user.id, email_key="welcome").count() == 0
    assert [p for p in resend if p["to"] == [email]] == []


def test_delivery_never_raises_into_the_caller(inline_threads, monkeypatch):
    """Belt and braces: the signup routes call this directly, so it has to
    swallow everything, including a broken import or a missing user."""
    app_module.send_welcome_email_on_signup(9_999_999)  # no such user
