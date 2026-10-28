"""Password-account email verification and school-domain enrollment tests."""

from datetime import datetime
import re
from uuid import uuid4

import App
from intelliplan.email_verification import consume_verification, send_verification
from intelliplan.learning import policy
from time_utils import utcnow


def _user(email, *, verified=False, birth_year=2000):
    user = App.User(email=email, password_hash="password-hash", name="Student",
                    birth_year=birth_year, parent_consent_granted=True)
    user.email_verified = verified
    App.db.session.add(user)
    App.db.session.flush()
    return user


def _school_domain(domain):
    models = policy.models()
    school = models["Organization"](name="Partner School", kind="school", policy={})
    App.db.session.add(school)
    App.db.session.flush()
    row = models["SchoolDomain"](
        organization_id=school.id, domain=domain, verification_hash="owner-proof-hash",
        verified_at=datetime(2026, 10, 1), created_by_id=1,
    )
    App.db.session.add(row)
    App.db.session.flush()
    return school


def _token_from_message(call):
    text_body = call[0][2]
    return re.search(r"/verify-email\?token=([^\s]+)", text_body).group(1)


def test_verified_token_is_hash_only_single_use_and_enrolls_matching_school(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or "mail-id")
    try:
        with App.app.app_context():
            App.db.create_all()
            domain = f"district-{uuid4().hex}.edu"
            school = _school_domain(domain)
            user = _user(f"student@{domain}")
            user_id = user.id
            assert send_verification(user, now=datetime(2026, 10, 20)) == (True, "sent")
            raw = _token_from_message(sent[0])
            with App.app.test_request_context():
                user = App.db.session.get(App.User, user_id)
                assert user.email_verification_hash not in raw
                assert user.email_verification_hash != raw.split(".", 1)[1]
                assert user.email_verification_hash
                assert consume_verification(raw, now=datetime(2026, 10, 20, 0, 1)) is True
                assert user.email_verified is True
                assert user.email_verification_hash is None
                membership = policy.models()["Membership"].query.filter_by(
                    user_id=user_id, organization_id=school.id, role="student").first()
                assert membership is not None
                assert consume_verification(raw, now=datetime(2026, 10, 20, 0, 2)) is False
    finally:
        App.limiter.enabled = previous


def test_expired_token_and_changed_email_cannot_verify_or_enroll(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
    try:
        with App.app.app_context():
            App.db.create_all()
            domain = f"campus-{uuid4().hex}.edu"
            school = _school_domain(domain)
            user = _user(f"student@{domain}")
            expired_user_id = user.id
            assert send_verification(user, now=datetime(2026, 10, 20))[0]
            expired_token = _token_from_message(sent[-1])
            assert consume_verification(expired_token, now=datetime(2026, 10, 21, 1)) is False
            assert App.db.session.get(App.User, expired_user_id).email_verified is False

            changed = _user(f"second@{domain}")
            changed_id = changed.id
            assert send_verification(changed, now=datetime(2026, 10, 22))[0]
            token = _token_from_message(sent[-1])
            changed.email = f"second-{uuid4().hex}@elsewhere.test"
            App.db.session.commit()
            assert consume_verification(token, now=datetime(2026, 10, 22, 0, 1)) is False
            assert App.db.session.get(App.User, changed_id).email_verified is False
            assert policy.models()["Membership"].query.filter_by(
                user_id=changed_id, organization_id=school.id).count() == 0
    finally:
        App.limiter.enabled = previous


def test_resend_endpoint_does_not_disclose_account_existence(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
    try:
        with App.app.app_context():
            App.db.create_all()
            address = f"registered-{uuid4().hex}@example.test"
            user = _user(address)
            App.db.session.commit()
            user_id = user.id
        client = App.app.test_client()
        unknown = client.post("/api/auth/email-verification/resend",
                              json={"email": f"missing-{uuid4().hex}@example.test"})
        known = client.post("/api/auth/email-verification/resend", json={"email": address})
        assert unknown.status_code == known.status_code == 200
        assert unknown.get_json() == known.get_json()
        assert len(sent) == 1
        assert sent[0][0][0] == address
        with App.app.app_context():
            stored = App.db.session.get(App.User, user_id)
            assert stored.email_verification_hash
            # The opaque token appears in the delivered link, never the DB.
            assert stored.email_verification_hash not in sent[0][0][2]
    finally:
        App.limiter.enabled = previous


def test_getting_email_link_does_not_consume_token(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
    try:
        with App.app.app_context():
            App.db.create_all()
            domain = f"links-{uuid4().hex}.edu"
            _school_domain(domain)
            user = _user(f"student@{domain}")
            user_id = user.id
            assert send_verification(user, now=utcnow())[0]
            token = _token_from_message(sent[0])
        client = App.app.test_client()
        response = client.get(f"/verify-email?token={token}")
        assert response.status_code == 200
        assert b"Confirm email" in response.data
        with App.app.app_context():
            assert App.db.session.get(App.User, user_id).email_verified is False
            assert App.db.session.get(App.User, user_id).email_verification_hash
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
    finally:
        App.limiter.enabled = previous


def test_confirm_post_consumes_token_once(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
    try:
        with App.app.app_context():
            App.db.create_all()
            user = _user(f"post-{uuid4().hex}@example.test")
            user_id = user.id
            assert send_verification(user, now=utcnow())[0]
            token = _token_from_message(sent[0])
        client = App.app.test_client()
        headers = {"Referer": "http://localhost/verify-email"}
        response = client.post("/verify-email", data={"token": token}, headers=headers)
        assert response.status_code == 200
        assert b"Email confirmed" in response.data
        with App.app.app_context():
            assert App.db.session.get(App.User, user_id).email_verified is True
        replay = client.post("/verify-email", data={"token": token}, headers=headers)
        assert replay.status_code == 200
        assert b"That link has expired" in replay.data
    finally:
        App.limiter.enabled = previous


def test_resend_cooldown_and_delivery_failure(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
    try:
        with App.app.app_context():
            App.db.create_all()
            user = _user(f"cooldown-{uuid4().hex}@example.test")
            assert send_verification(user, now=datetime(2026, 10, 24)) == (True, "sent")
            first_hash = user.email_verification_hash
            assert send_verification(user, now=datetime(2026, 10, 24, 0, 10)) == (False, "cooldown")
            assert len(sent) == 1
            assert user.email_verification_hash == first_hash

            user2 = _user(f"failure-{uuid4().hex}@example.test")
            monkeypatch.setattr(App, "_send_email", lambda *a, **kw: False)
            assert send_verification(user2, now=datetime(2026, 10, 24)) == (False, "delivery_failed")
            failed_hash = user2.email_verification_hash
            monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
            assert send_verification(user2, now=datetime(2026, 10, 24, 0, 5)) == (False, "cooldown")
            assert user2.email_verification_hash == failed_hash
    finally:
        App.limiter.enabled = previous


def test_age_and_parent_eligibility_fail_closed(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
    try:
        with App.app.app_context():
            App.db.create_all()
            unknown_age = _user(f"unknown-{uuid4().hex}@example.test", birth_year=None)
            underage = _user(f"child-{uuid4().hex}@example.test", birth_year=2015)
            underage.parent_consent_granted = False
            App.db.session.commit()
            now = datetime(2026, 10, 25)
            assert send_verification(unknown_age, now=now) == (False, "unknown_age")
            assert send_verification(underage, now=now) == (False, "under_13_no_parental_consent")
            assert not sent
            assert unknown_age.email_verification_hash is None
            assert underage.email_verification_hash is None
    finally:
        App.limiter.enabled = previous


def test_verified_email_without_matching_school_domain_does_not_enroll(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    sent = []
    monkeypatch.setattr(App, "_send_email", lambda *a, **kw: sent.append((a, kw)) or True)
    try:
        with App.app.app_context():
            App.db.create_all()
            school_domain = f"school-{uuid4().hex}.edu"
            school = _school_domain(school_domain)
            user = _user(f"student-{uuid4().hex}@other-{uuid4().hex}.edu")
            user_id = user.id
            assert send_verification(user, now=datetime(2026, 10, 26))[0]
            token = _token_from_message(sent[0])
            assert consume_verification(token, now=datetime(2026, 10, 26, 0, 1)) is True
            assert App.db.session.get(App.User, user_id).email_verified is True
            assert policy.models()["Membership"].query.filter_by(
                user_id=user_id, organization_id=school.id).count() == 0
    finally:
        App.limiter.enabled = previous
