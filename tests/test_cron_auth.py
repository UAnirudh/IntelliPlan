"""One secret, either header, on every cron endpoint.

``/cron/notifications`` shipped reading ``X-Cron-Token`` while the endpoints
in App.py read ``X-Cron-Secret``. Same secret, same purpose, two spellings —
and using the wrong one returns an auth error, which sends the operator
looking at the secret rather than the header. Both are accepted everywhere
now, and these tests are what keep it that way.

The failure bodies matter too. "unauthorized" alone cannot tell an unset
shell variable apart from a wrong secret, and those have opposite fixes.
"""

from __future__ import annotations

import pytest

import App


CRON_ENDPOINTS = [
    "/cron/notifications",
    "/cron/send-reminders",
    "/cron/lifecycle-emails",
]

SECRET = "test-cron-secret-value"


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setenv("CRON_SECRET", SECRET)
    monkeypatch.delenv("CRON_TOKEN", raising=False)
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.mark.parametrize("endpoint", CRON_ENDPOINTS)
@pytest.mark.parametrize("header", ["X-Cron-Secret", "X-Cron-Token"])
def test_every_endpoint_accepts_either_header(client, endpoint, header):
    r = client.post(endpoint, headers={header: SECRET})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert r.get_json()["status"] == "ok"


@pytest.mark.parametrize("endpoint", CRON_ENDPOINTS)
def test_the_query_fallback_still_works(client, endpoint):
    """For schedulers that cannot set a header. Discouraged — query strings
    reach access logs — but removing it would break existing setups."""
    r = client.post(f"{endpoint}?secret={SECRET}")
    assert r.status_code == 200


@pytest.mark.parametrize("endpoint", CRON_ENDPOINTS)
def test_a_wrong_secret_is_refused(client, endpoint):
    r = client.post(endpoint, headers={"X-Cron-Secret": "not-the-secret"})
    assert r.status_code in (401, 403)
    assert "does not match" in r.get_json()["message"]


@pytest.mark.parametrize("endpoint", CRON_ENDPOINTS)
def test_sending_no_secret_says_so_rather_than_just_unauthorized(client, endpoint):
    """The overwhelmingly common cause is an unset shell variable —
    $CRON_SECRET instead of $env:CRON_SECRET in PowerShell, or a value that
    only ever lived in .env. Naming that saves an hour."""
    r = client.post(endpoint)
    assert r.status_code in (401, 403)
    message = r.get_json()["message"]
    assert "no cron secret was sent" in message
    assert "$env:CRON_SECRET" in message


@pytest.mark.parametrize("endpoint", CRON_ENDPOINTS)
def test_the_refusal_is_json_not_an_html_error_page(client, endpoint):
    """These are machine endpoints. /cron/notifications used to abort(403),
    which rendered the site's styled error page — a scheduler's log filled
    with a stylesheet and the reason nowhere in it."""
    r = client.post(endpoint)
    assert r.mimetype == "application/json"
    assert "<!DOCTYPE html>" not in r.get_data(as_text=True)


@pytest.mark.parametrize("endpoint", CRON_ENDPOINTS)
def test_an_unconfigured_server_says_that_instead_of_unauthorized(client, endpoint, monkeypatch):
    """503, not 401: nothing the caller sends can fix this one."""
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("CRON_TOKEN", raising=False)
    r = client.post(endpoint, headers={"X-Cron-Secret": SECRET})
    assert r.status_code == 503
    assert "not configured" in r.get_json()["message"]


@pytest.mark.parametrize("endpoint", CRON_ENDPOINTS)
def test_an_empty_header_is_not_mistaken_for_a_match(client, endpoint, monkeypatch):
    """The exact shape of the PowerShell failure: the header is present but
    its value is the empty expansion of an unset variable. An empty secret
    must never authorise, even against an empty expected value."""
    r = client.post(endpoint, headers={"X-Cron-Secret": ""})
    assert r.status_code in (401, 403)
