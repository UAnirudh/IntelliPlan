"""Gated, identity-only Sign in with ChatGPT support for hosted IntelliPlan.

This implements OpenID identity linking only. It never requests or stores an
OpenAI access/refresh token and never requests ChatGPT plan-use scopes. That
requires a separate approved product configuration and adapter.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import requests
from flask import Blueprint, abort, current_app, redirect, request, session
from flask_login import current_user
from sqlalchemy import update

import secret_box

CALLBACK_URI = "https://intelliplan.tech/auth/openai/callback"
ISSUER = "https://auth.openai.com"
DISCOVERY_URI = f"{ISSUER}/.well-known/openid-configuration"
TRANSACTION_TTL = timedelta(minutes=10)
_BROWSER_SESSION_KEY = "openai_siwc_browser_id"
_IDENTITY_SCOPES = "openid profile email"


def enabled_config() -> tuple[bool, str]:
    """Return whether the explicitly enabled client configuration is usable."""
    client_id = (os.getenv("OPENAI_CLIENT_ID") or "").strip()
    enabled = (os.getenv("OPENAI_SIWC_ENABLED") or "").strip().lower() == "true"
    if not enabled or not client_id or len(client_id) > 255:
        return False, ""
    if any(char.isspace() for char in client_id):
        return False, ""
    return True, client_id


def register_models(db: Any) -> tuple[type, type]:
    """Register durable, one-time transactions and verified identity links."""
    class OpenAISignInTransaction(db.Model):
        __tablename__ = "openai_siwc_transactions"
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        state_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
        browser_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
        nonce = db.Column(secret_box.EncryptedText, nullable=False)
        code_verifier = db.Column(secret_box.EncryptedText, nullable=False)
        expires_at = db.Column(db.DateTime, nullable=False, index=True)
        consumed_at = db.Column(db.DateTime, nullable=True)

    class OpenAIIdentityLink(db.Model):
        __tablename__ = "openai_identity_links"
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
        issuer = db.Column(db.String(255), nullable=False)
        client_id = db.Column(db.String(255), nullable=False)
        subject = db.Column(db.String(255), nullable=False)
        linked_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
        __table_args__ = (db.UniqueConstraint("issuer", "client_id", "subject", name="uq_openai_identity"),)

    return OpenAISignInTransaction, OpenAIIdentityLink


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _require_feature() -> str:
    enabled, client_id = enabled_config()
    if not enabled:
        abort(404)
    return client_id


def _require_safe_storage() -> None:
    # secret_box intentionally degrades for general app availability. OAuth
    # transactions contain replay-sensitive verifiers, so this route must not.
    if not secret_box.is_enabled():
        abort(503)


def _authenticated_user() -> None:
    if not getattr(current_user, "is_authenticated", False):
        abort(401)


def _discovery(http=requests) -> dict[str, Any]:
    response = http.get(DISCOVERY_URI, timeout=8, allow_redirects=False)
    response.raise_for_status()
    metadata = response.json()
    if not isinstance(metadata, dict) or metadata.get("issuer") != ISSUER:
        raise ValueError("OpenAI identity metadata is invalid")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = metadata.get(name)
        parsed = urllib.parse.urlsplit(value or "")
        if (parsed.scheme != "https" or parsed.hostname != "auth.openai.com"
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError("OpenAI identity metadata is invalid")
    return metadata


def _build_authorization_url(metadata: dict[str, Any], client_id: str,
                             state: str, nonce: str, verifier: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "client_id": client_id,
        "redirect_uri": CALLBACK_URI,
        "response_type": "code",
        "scope": _IDENTITY_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return metadata["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def _exchange_code(code: str, verifier: str, metadata: dict[str, Any], client_id: str,
                   http=requests) -> str:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK_URI,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    headers = {"Accept": "application/json"}
    method = (os.getenv("OPENAI_TOKEN_AUTH_METHOD") or "none").strip().lower()
    if method == "client_secret_basic":
        secret = os.getenv("OPENAI_CLIENT_SECRET")
        if not secret:
            raise ValueError("OpenAI confidential client is not configured")
        def encode(value: str) -> str:
            return urllib.parse.urlencode({"v": value})[2:]
        basic = base64.b64encode(f"{encode(client_id)}:{encode(secret)}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    elif method != "none" or os.getenv("OPENAI_CLIENT_SECRET"):
        # A configured secret without explicit confidential-client mode is an
        # operator mistake; never silently send it using a public-client flow.
        raise ValueError("OpenAI token authentication configuration is invalid")
    response = http.post(metadata["token_endpoint"], data=form, headers=headers,
                         timeout=10, allow_redirects=False)
    response.raise_for_status()
    tokens = response.json()
    identity_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(identity_token, str) or not identity_token:
        raise ValueError("OpenAI did not return an identity token")
    # Any access/refresh token in the response is deliberately ignored.
    return identity_token


def _verify_id_token(token: str, nonce: str, client_id: str,
                     metadata: dict[str, Any], http=requests) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid or header.get("alg") != "RS256":
        raise ValueError("OpenAI identity token signing algorithm is not accepted")
    response = http.get(metadata["jwks_uri"], timeout=8, allow_redirects=False)
    response.raise_for_status()
    keys = response.json().get("keys", [])
    matches = [key for key in keys if key.get("kid") == kid and key.get("kty") == "RSA"
               and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"]
    if len(matches) != 1:
        raise ValueError("OpenAI identity signing key is unavailable")
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(matches[0]))
    claims = jwt.decode(
        token, public_key, algorithms=["RS256"], issuer=metadata["issuer"],
        audience=client_id, options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
        leeway=5,
    )
    returned_nonce = claims.get("nonce")
    if not isinstance(returned_nonce, str) or not hmac.compare_digest(returned_nonce, nonce):
        raise ValueError("OpenAI identity nonce does not match")
    if not isinstance(claims.get("sub"), str) or not claims["sub"] or len(claims["sub"]) > 255:
        raise ValueError("OpenAI identity subject is invalid")
    audiences = claims.get("aud")
    if "azp" in claims and claims.get("azp") != client_id:
        raise ValueError("OpenAI identity authorized party is invalid")
    if isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != client_id:
        raise ValueError("OpenAI identity authorized party is invalid")
    return claims


def install(app: Any, db: Any, transaction_model: type, identity_model: type) -> None:
    bp = Blueprint("openai_siwc", __name__)

    @bp.errorhandler(404)
    def hidden_when_disabled(_error):
        # The app's site-wide 404 page may redirect external-looking requests
        # to the dashboard. Keep disabled OAuth endpoints consistently dark.
        return "Not Found", 404

    @bp.after_request
    def prevent_auth_artifact_caching(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @bp.get("/auth/openai")
    def start():
        client_id = _require_feature()
        _require_safe_storage()
        _authenticated_user()
        try:
            metadata = _discovery()
        except Exception:
            abort(503)
        browser_id = session.get(_BROWSER_SESSION_KEY)
        if not browser_id:
            browser_id = secrets.token_urlsafe(32)
            session[_BROWSER_SESSION_KEY] = browser_id
        state, nonce, verifier = (secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                                  secrets.token_urlsafe(64))
        try:
            # A browser can have only one outstanding transaction. Replacing
            # it is safe and prevents two tabs from racing different state.
            transaction_model.query.filter(transaction_model.expires_at <= _now()).delete()
            transaction_model.query.filter_by(browser_hash=_sha256(browser_id)).delete()
            db.session.add(transaction_model(
                state_hash=_sha256(state), browser_hash=_sha256(browser_id),
                user_id=current_user.id,
                nonce=nonce, code_verifier=verifier,
                expires_at=_now() + TRANSACTION_TTL,
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
            abort(503)
        return redirect(_build_authorization_url(metadata, client_id, state, nonce, verifier))

    @bp.get("/auth/openai/callback")
    def callback():
        client_id = _require_feature()
        _require_safe_storage()
        _authenticated_user()
        browser_id = session.pop(_BROWSER_SESSION_KEY, None)
        if not browser_id:
            abort(400)
        # Compare-and-swap makes this transaction single-use across workers.
        now = _now()
        try:
            result = db.session.execute(
                update(transaction_model)
                .where(transaction_model.browser_hash == _sha256(browser_id),
                       transaction_model.user_id == current_user.id,
                       transaction_model.consumed_at.is_(None),
                       transaction_model.expires_at > now)
                .values(consumed_at=now)
            )
            if result.rowcount != 1:
                db.session.rollback()
                abort(400)
            db.session.commit()
            transaction = transaction_model.query.filter_by(browser_hash=_sha256(browser_id)).first()
            returned_state = request.args.get("state", "")
            state_matches = bool(
                transaction and hmac.compare_digest(transaction.state_hash, _sha256(returned_state))
            )
            nonce = transaction.nonce if transaction else None
            verifier = transaction.code_verifier if transaction else None
            if transaction:
                # The state is one-use. Once its verifier and nonce are in
                # local variables, remove the durable secret-bearing row
                # before any network request or claim validation can fail.
                db.session.delete(transaction)
                db.session.commit()
            if not state_matches:
                abort(400)
            if request.args.get("error"):
                # Do not reflect provider-controlled error text into a URL or
                # page. Keep consent cancellation distinct from a provider
                # failure while returning the user to the normal settings UI.
                result = "cancelled" if request.args.get("error") == "access_denied" else "failed"
                return redirect(f"/settings/ai?openai_identity={result}")
            code = request.args.get("code", "")
            if not code or len(code) > 4096 or not nonce or not verifier:
                abort(400)
            metadata = _discovery()
            id_token = _exchange_code(code, verifier, metadata, client_id)
            claims = _verify_id_token(id_token, nonce, client_id, metadata)
            existing_subject = identity_model.query.filter_by(
                issuer=ISSUER, client_id=client_id, subject=claims["sub"]
            ).first()
            if existing_subject and existing_subject.user_id != current_user.id:
                abort(409)
            existing_user = identity_model.query.filter_by(user_id=current_user.id).first()
            if existing_user and (existing_user.issuer, existing_user.client_id, existing_user.subject) != (
                    ISSUER, client_id, claims["sub"]):
                abort(409)
            if not existing_user:
                db.session.add(identity_model(user_id=current_user.id, issuer=ISSUER,
                                              client_id=client_id, subject=claims["sub"]))
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            if getattr(exc, "code", None) in (400, 409):
                raise
            # Never log response bodies, codes, tokens, claim contents or secrets.
            current_app.logger.warning("OpenAI identity link failed (%s)", type(exc).__name__)
            abort(400)
        return redirect("/settings/ai?openai_identity=linked")

    app.register_blueprint(bp)
