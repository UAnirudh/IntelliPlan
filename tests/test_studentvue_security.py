"""The StudentVUE credential flow must only send secrets to public HTTPS hosts."""

import ssl

import pytest

import App
import net_guard
import studentvue_helper


@pytest.mark.parametrize("url", [
    "http://school.example",
    "https://127.0.0.1",
    "https://user:pass@school.example",
    "https://school.example:8443",
])
def test_studentvue_rejects_unsafe_district_urls_before_sending_credentials(url, monkeypatch):
    monkeypatch.setattr(
        studentvue_helper.net_guard.urllib3.HTTPSConnectionPool,
        "__init__",
        lambda *args, **kwargs: pytest.fail("credentials must not be sent to an unsafe host"),
    )

    with pytest.raises(net_guard.BlockedURL):
        studentvue_helper.make_request(url, "synthetic-user", "synthetic-password", "StudentInfo")


def test_studentvue_rejects_private_dns_answers(monkeypatch):
    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.7", 0))],
    )
    monkeypatch.setattr(
        studentvue_helper.net_guard.urllib3.HTTPSConnectionPool,
        "__init__",
        lambda *args, **kwargs: pytest.fail("credentials must not be sent to a private address"),
    )

    with pytest.raises(net_guard.BlockedURL):
        studentvue_helper.json_login("https://school.example", "synthetic-user", "synthetic-password")


def test_studentvue_credential_requests_pin_dns_and_do_not_follow_redirects(monkeypatch):
    dns_calls = []

    def fake_resolve(*args, **kwargs):
        dns_calls.append(args[0])
        address = "8.8.8.8" if len(dns_calls) == 1 else "127.0.0.1"
        return [(None, None, None, None, (address, 443))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_resolve)
    captured = {}

    class RawResponse:
        status = 200
        data = b"ok"
        headers = {}
        reason = "OK"

    class Pool:
        def __init__(self, *args, **kwargs):
            captured["pool_args"] = args
            captured["pool_kwargs"] = kwargs

        def request(self, *args, **kwargs):
            captured["request_kwargs"] = kwargs
            return RawResponse()

        def close(self):
            return None

    monkeypatch.setattr(studentvue_helper.net_guard.urllib3, "HTTPSConnectionPool", Pool)
    response_text = studentvue_helper.make_request(
        "https://school.example", "synthetic-user", "synthetic-password", "StudentInfo"
    )
    assert response_text == "ok"
    assert captured["pool_kwargs"]["host"] == "8.8.8.8"
    assert captured["pool_kwargs"]["server_hostname"] == "school.example"
    assert captured["pool_kwargs"]["assert_hostname"] == "school.example"
    assert captured["pool_kwargs"]["cert_reqs"] == ssl.CERT_REQUIRED
    assert captured["request_kwargs"]["redirect"] is False
    assert dns_calls == ["school.example"]


def test_legacy_http_studentvue_session_is_cleared_and_requires_reconnect():
    from flask import session

    with App.app.test_request_context("/"):
        session["login_type"] = "studentvue"
        session["sv_username"] = "synthetic-user"
        session["sv_password"] = "synthetic-password"
        session["sv_district_url"] = "http://school.example"

        assert App.get_active_account() is None
        assert "sv_username" not in session
        assert "sv_password" not in session
        assert "sv_district_url" not in session
        assert session["studentvue_reconnect_required"] is True
