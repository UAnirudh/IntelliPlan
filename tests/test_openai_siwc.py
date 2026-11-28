"""Security checks for the gated, identity-only Sign in with ChatGPT routes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa

import App
import secret_box
from intelliplan.premium import openai_siwc as siwc


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    with App.app.test_client() as test_client:
        with App.app.app_context():
            App.db.create_all()
        yield test_client
    secret_box.reset_cache()


def _rsa_jwk(key, kid="test-key"):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid=kid, use="sig", alg="RS256")
    return jwk


class _Response:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        pass

    def json(self):
        return self.value


class _Http:
    def __init__(self, jwks):
        self.jwks = jwks

    def get(self, url, **kwargs):
        assert url == "https://auth.openai.com/.well-known/jwks.json"
        assert kwargs["allow_redirects"] is False
        return _Response({"keys": self.jwks})


def _signed_token(key, *, issuer=siwc.ISSUER, audience="oaiapp_test",
                  nonce="expected-nonce", azp=None, signing_key=None):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": issuer, "aud": audience, "sub": "openai-subject",
        "iat": now, "exp": now + timedelta(minutes=5), "nonce": nonce,
    }
    if azp is not None:
        claims["azp"] = azp
    return jwt.encode(claims, signing_key or key, algorithm="RS256",
                      headers={"kid": "test-key"})


@pytest.mark.parametrize("case", ["issuer", "audience", "signature", "nonce", "azp"])
def test_identity_token_rejects_untrusted_claims_and_signatures(case):
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_key = signing_key
    kwargs = {}
    expected_nonce = "expected-nonce"
    if case == "issuer":
        kwargs["issuer"] = "https://attacker.example"
    elif case == "audience":
        kwargs["audience"] = "another-client"
    elif case == "nonce":
        kwargs["nonce"] = "wrong-nonce"
    elif case == "azp":
        kwargs["azp"] = "another-client"
    elif case == "signature":
        jwks_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _signed_token(signing_key, **kwargs)
    metadata = {"issuer": siwc.ISSUER,
                "jwks_uri": "https://auth.openai.com/.well-known/jwks.json"}
    with pytest.raises((jwt.InvalidTokenError, ValueError)):
        siwc._verify_id_token(token, expected_nonce, "oaiapp_test", metadata,
                              http=_Http([_rsa_jwk(jwks_key)]))


def test_identity_token_verifies_signature_issuer_audience_and_nonce():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _signed_token(key)
    metadata = {"issuer": siwc.ISSUER,
                "jwks_uri": "https://auth.openai.com/.well-known/jwks.json"}
    claims = siwc._verify_id_token(token, "expected-nonce", "oaiapp_test", metadata,
                                   http=_Http([_rsa_jwk(key)]))
    assert claims["sub"] == "openai-subject"


def _login(client, email="siwc+student@example.com"):
    with App.app.app_context():
        user = App.User.query.filter_by(email=email).first()
        if user is None:
            user = App.User(email=email, password_hash="")
            App.db.session.add(user)
            App.db.session.commit()
        uid = user.id
    with client.session_transaction(base_url="https://intelliplan.tech") as state:
        state["_user_id"] = str(uid)
        state["_fresh"] = True
    return uid


def test_disabled_or_unconfigured_routes_are_hidden(client, monkeypatch):
    assert siwc.CALLBACK_URI == "https://intelliplan.tech/auth/openai/callback"
    _login(client, "siwc+disabled@example.com")
    monkeypatch.delenv("OPENAI_SIWC_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_CLIENT_ID", raising=False)
    response = client.get("/auth/openai", base_url="https://intelliplan.tech")
    assert response.status_code == 404, response.headers.get("Location")
    assert client.get("/auth/openai/callback?state=x&code=y",
                      base_url="https://intelliplan.tech").status_code == 404
    monkeypatch.setenv("OPENAI_SIWC_ENABLED", "true")
    assert client.get("/auth/openai", base_url="https://intelliplan.tech").status_code == 404
    settings = client.get("/settings/ai", base_url="https://intelliplan.tech").get_data(as_text=True)
    assert "data-oai-siwc-button" not in settings


def _configure_routes(client, monkeypatch):
    monkeypatch.setenv("OPENAI_SIWC_ENABLED", "true")
    monkeypatch.setenv("OPENAI_CLIENT_ID", "oaiapp_test")
    monkeypatch.setenv("OPENAI_TOKEN_AUTH_METHOD", "none")
    monkeypatch.delenv("OPENAI_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret_box.reset_cache()
    monkeypatch.setattr(siwc, "_discovery", lambda: {
        "issuer": siwc.ISSUER,
        "authorization_endpoint": "https://auth.openai.com/api/accounts/authorize",
        "token_endpoint": "https://auth.openai.com/api/accounts/oauth/token",
        "jwks_uri": "https://auth.openai.com/.well-known/jwks.json",
    })
    monkeypatch.setattr(siwc, "_exchange_code", lambda *a, **k: "opaque-id-token")
    monkeypatch.setattr(siwc, "_verify_id_token", lambda *a, **k: {"sub": "fixture-subject"})


def test_state_mismatch_consumes_the_transaction_and_cannot_be_replayed(client, monkeypatch):
    user_id = _login(client)
    _configure_routes(client, monkeypatch)
    started = client.get("/auth/openai", base_url="https://intelliplan.tech")
    assert started.status_code == 302
    callback = "/auth/openai/callback?state=wrong&code=synthetic"
    assert client.get(callback, base_url="https://intelliplan.tech").status_code == 400
    location = started.headers["Location"]
    from urllib.parse import parse_qs, urlsplit
    real_state = parse_qs(urlsplit(location).query)["state"][0]
    assert client.get(f"/auth/openai/callback?state={real_state}&code=synthetic",
                      base_url="https://intelliplan.tech").status_code == 400
    with App.app.app_context():
        assert App.OpenAISignInTransaction.query.filter_by(user_id=user_id).count() == 0
    secret_box.reset_cache()


def test_provider_cancellation_returns_to_settings_without_reflecting_error(client, monkeypatch):
    user_id = _login(client, "siwc+cancelled@example.com")
    _configure_routes(client, monkeypatch)
    started = client.get("/auth/openai", base_url="https://intelliplan.tech")
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
    response = client.get(
        f"/auth/openai/callback?state={state}&error=access_denied&error_description=do-not-reflect",
        base_url="https://intelliplan.tech",
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/settings/ai?openai_identity=cancelled"
    with App.app.app_context():
        assert App.OpenAISignInTransaction.query.filter_by(user_id=user_id).count() == 0
        assert App.OpenAIIdentityLink.query.filter_by(user_id=user_id).count() == 0
    secret_box.reset_cache()


def test_new_sign_in_purges_abandoned_expired_transactions(client, monkeypatch):
    user_id = _login(client, "siwc+purge@example.com")
    _configure_routes(client, monkeypatch)
    expired_browser_hash = "expired-browser-" + "a" * 48
    with App.app.app_context():
        expired = App.OpenAISignInTransaction(
            user_id=user_id, state_hash="expired-state-" + "b" * 48,
            browser_hash=expired_browser_hash, nonce="expired-nonce",
            code_verifier="expired-verifier",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
        )
        App.db.session.add(expired)
        App.db.session.commit()
    response = client.get("/auth/openai", base_url="https://intelliplan.tech")
    assert response.status_code == 302
    with App.app.app_context():
        assert App.OpenAISignInTransaction.query.filter_by(
            browser_hash=expired_browser_hash).count() == 0
    secret_box.reset_cache()


def test_successful_identity_link_is_single_use_and_stores_no_tokens(client, monkeypatch):
    user_id = _login(client, "siwc+replay@example.com")
    _configure_routes(client, monkeypatch)
    started = client.get("/auth/openai", base_url="https://intelliplan.tech")
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
    auth_params = parse_qs(urlsplit(started.headers["Location"]).query)
    assert auth_params["scope"] == ["openid"]
    assert auth_params["redirect_uri"] == [siwc.CALLBACK_URI]
    linked = client.get(f"/auth/openai/callback?state={state}&code=synthetic",
                        base_url="https://intelliplan.tech", follow_redirects=True)
    assert linked.status_code == 200
    assert b"ChatGPT identity linked. Plan use remains unavailable" in linked.data
    assert client.get(f"/auth/openai/callback?state={state}&code=synthetic",
                      base_url="https://intelliplan.tech").status_code == 400
    with App.app.app_context():
        assert App.OpenAISignInTransaction.query.filter_by(user_id=user_id).count() == 0
        link = App.OpenAIIdentityLink.query.filter_by(user_id=user_id).one()
        assert (link.issuer, link.client_id, link.subject) == (
            siwc.ISSUER, "oaiapp_test", "fixture-subject")
    secret_box.reset_cache()


@pytest.mark.parametrize("failure_at", ["exchange", "verify"])
def test_failed_exchange_or_identity_validation_deletes_transaction(client, monkeypatch, failure_at):
    user_id = _login(client, f"siwc+failure-{failure_at}@example.com")
    _configure_routes(client, monkeypatch)
    if failure_at == "exchange":
        monkeypatch.setattr(siwc, "_exchange_code", lambda *a, **k: (_ for _ in ()).throw(ValueError("exchange failed")))
    else:
        monkeypatch.setattr(siwc, "_verify_id_token", lambda *a, **k: (_ for _ in ()).throw(jwt.InvalidTokenError("invalid")))
    started = client.get("/auth/openai", base_url="https://intelliplan.tech")
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
    response = client.get(f"/auth/openai/callback?state={state}&code=synthetic",
                          base_url="https://intelliplan.tech")
    assert response.status_code == 400
    with App.app.app_context():
        assert App.OpenAISignInTransaction.query.filter_by(user_id=user_id).count() == 0
        assert App.OpenAIIdentityLink.query.filter_by(user_id=user_id).count() == 0
    secret_box.reset_cache()


def test_identity_link_cannot_be_silently_reassigned_to_another_user(client, monkeypatch):
    _login(client, "siwc+original@example.com")
    with App.app.app_context():
        other = App.User(email="siwc+other@example.com", password_hash="")
        App.db.session.add(other)
        App.db.session.commit()
        other_id = other.id
        App.db.session.add(App.OpenAIIdentityLink(
            user_id=other_id, issuer=siwc.ISSUER, client_id="oaiapp_test",
            subject="conflict-subject"))
        App.db.session.commit()
    _configure_routes(client, monkeypatch)
    monkeypatch.setattr(siwc, "_verify_id_token", lambda *a, **k: {"sub": "conflict-subject"})
    started = client.get("/auth/openai", base_url="https://intelliplan.tech")
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
    response = client.get(f"/auth/openai/callback?state={state}&code=synthetic",
                          base_url="https://intelliplan.tech")
    assert response.status_code == 409
    with App.app.app_context():
        link = App.OpenAIIdentityLink.query.filter_by(subject="conflict-subject").one()
        assert link.user_id == other_id
    secret_box.reset_cache()



def test_linked_status_and_disconnect_work_when_new_links_are_disabled(client, monkeypatch):
    site = "https://intelliplan.tech"
    endpoint = "/auth/openai/disconnect"
    assert client.post(endpoint, base_url=site).status_code == 401
    other_id = _login(client, "siwc+other-disconnect@example.com")
    user_id = _login(client, "siwc+disconnect@example.com")
    for uid in (other_id, user_id):
        with App.app.app_context():
            App.db.session.add(App.OpenAIIdentityLink(
                user_id=uid, issuer=siwc.ISSUER, client_id="oaiapp_test", subject=f"disconnect-{uid}"
            ))
            App.db.session.commit()

    monkeypatch.setenv("OPENAI_SIWC_ENABLED", "false")
    settings = client.get("/settings/ai", base_url=site)
    assert b"ChatGPT identity connected" in settings.data
    assert b"Disconnect ChatGPT identity" in settings.data
    assert f"disconnect-{user_id}".encode() not in settings.data
    assert client.post(endpoint, base_url=site, headers={"Origin": "https://external.example"}).status_code == 403
    assert b"ChatGPT identity connected" in client.get("/settings/ai", base_url=site).data

    result = client.post(endpoint, base_url=site, headers={"Origin": site}, follow_redirects=True)
    assert result.status_code == 200
    assert b"ChatGPT identity disconnected." in result.data
    assert b"ChatGPT identity connected" not in result.data
    assert client.post(endpoint, base_url=site, headers={"Origin": site}).status_code == 303
    with App.app.app_context():
        assert App.OpenAIIdentityLink.query.filter_by(user_id=other_id).count() == 1


def test_disconnect_cancels_callback_during_provider_exchange(client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    site = "https://intelliplan.tech"
    user_id = _login(client, "siwc+exchange-disconnect@example.com")
    _configure_routes(client, monkeypatch)
    started = client.get("/auth/openai", base_url=site)
    state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]

    def disconnect_during_exchange(*args, **kwargs):
        with App.app.app_context():
            marker = App.OpenAISignInTransaction.query.filter_by(user_id=user_id).one()
            assert (marker.nonce, marker.code_verifier) == ("", "")
        other_tab = App.app.test_client()
        _login(other_tab, "siwc+exchange-disconnect@example.com")
        assert other_tab.post("/auth/openai/disconnect", base_url=site, headers={"Origin": site}).status_code == 303
        return "synthetic-id-token"

    monkeypatch.setattr(siwc, "_exchange_code", disconnect_during_exchange)
    assert client.get(f"/auth/openai/callback?state={state}&code=synthetic", base_url=site).status_code == 400
    with App.app.app_context():
        assert App.OpenAIIdentityLink.query.filter_by(user_id=user_id).count() == 0
        assert App.OpenAISignInTransaction.query.filter_by(user_id=user_id).count() == 0


@pytest.mark.parametrize(("method", "secret"), [
    ("none", "synthetic-secret"),
    ("client_secret_basic", ""),
    ("client_secret_basic", "   "),
    ("unknown", ""),
])
def test_invalid_client_auth_hides_sign_in_before_redirect(client, monkeypatch, method, secret):
    site = "https://intelliplan.tech"
    _login(client, "siwc+invalid-auth@example.com")
    _configure_routes(client, monkeypatch)
    monkeypatch.setenv("OPENAI_TOKEN_AUTH_METHOD", method)
    monkeypatch.setenv("OPENAI_CLIENT_SECRET", secret)

    assert client.get("/auth/openai", base_url=site).status_code == 404
    assert client.get("/auth/openai/callback", base_url=site).status_code == 404
    assert b"data-oai-siwc-button" not in client.get("/settings/ai", base_url=site).data


def test_configured_confidential_client_can_start_sign_in(client, monkeypatch):
    site = "https://intelliplan.tech"
    _login(client, "siwc+confidential@example.com")
    _configure_routes(client, monkeypatch)
    monkeypatch.setenv("OPENAI_TOKEN_AUTH_METHOD", "client_secret_basic")
    monkeypatch.setenv("OPENAI_CLIENT_SECRET", "synthetic-secret")

    result = client.get("/auth/openai", base_url=site)
    assert result.status_code == 302
    assert "synthetic-secret" not in result.headers["Location"]
