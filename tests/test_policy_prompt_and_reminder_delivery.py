"""Terms prompt on next sign-in, and the reminder paths that were not delivering.

* A Google sign-up never saw the signup form's terms checkbox. It is asked to
  accept the documents themselves, with the short version of each, not shown
  a change log for text it never read.
* Every Google sign-up's welcome email was refused as ``unknown_age``: it was
  sent the moment the account existed, before the age step. It now goes out
  once the age is known.
* A desktop install that was uninstalled kept counting push reminders as
  delivered. The app now checks in when it polls, and a silent install stops
  counting.
* The notification ticker held its lease for three minutes after every run,
  so reminders went out every three minutes instead of every minute.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import App
import policy_versions
from App import PolicyAcknowledgement, PushSubscription, User, db
from time_utils import utcnow


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
        yield c
    App.limiter.enabled = True


def make_user(email, **fields):
    with App.app.app_context():
        old = User.query.filter_by(email=email).first()
        if old is not None:
            PolicyAcknowledgement.query.filter_by(user_id=old.id).delete(synchronize_session=False)
            PushSubscription.query.filter_by(user_id=old.id).delete(synchronize_session=False)
            db.session.delete(old)
            db.session.commit()
        user = User(email=email, password_hash="", **fields)
        db.session.add(user)
        db.session.commit()
        return user.id


def sign_in(client, uid):
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True


def pending(client):
    return client.get("/api/policy/pending").get_json()


# ── The terms prompt ─────────────────────────────────────────


def test_a_google_signup_is_asked_to_accept_the_documents_themselves(client):
    uid = make_user("prompt+google@example.com", google_id="g-prompt-1",
                    birth_year=2005, created_at=utcnow())
    sign_in(client, uid)

    docs = pending(client)["pending"]

    assert {d["doc"] for d in docs} == {"terms", "privacy"}
    for doc in docs:
        assert doc["first_time"] is True
        assert doc["tldr"] == policy_versions.DOC_TLDR[doc["doc"]]
        assert doc["clauses"] == []
        assert doc["version"] == policy_versions.current_version(doc["doc"])


def test_an_existing_account_gets_the_changes_with_a_tldr(client):
    uid = make_user("prompt+old@example.com", birth_year=2005,
                    created_at=datetime(2025, 3, 1))
    sign_in(client, uid)

    privacy = next(d for d in pending(client)["pending"] if d["doc"] == "privacy")

    assert privacy["first_time"] is False
    assert privacy["version"] == 4
    assert privacy["tldr"], "the change notice leads with a TL;DR"
    # The short version of the whole document rides along for after they accept.
    assert privacy["doc_tldr"] == policy_versions.DOC_TLDR["privacy"]
    headings = [c["heading"] for c in privacy["clauses"]]
    assert any("Optional reminders data" in h for h in headings)


def test_someone_who_accepted_v3_is_asked_only_about_v4(client):
    uid = make_user("prompt+v3@example.com", birth_year=2005,
                    created_at=datetime(2025, 3, 1))
    with App.app.app_context():
        db.session.add(PolicyAcknowledgement(user_id=uid, doc="privacy", version=3))
        db.session.add(PolicyAcknowledgement(user_id=uid, doc="terms", version=2))
        db.session.commit()
    sign_in(client, uid)

    docs = pending(client)["pending"]

    assert [(d["doc"], d["from_version"], d["version"]) for d in docs] == [("privacy", 3, 4)]


def test_accepting_both_clears_the_prompt(client):
    uid = make_user("prompt+accept@example.com", google_id="g-prompt-2",
                    birth_year=2005, created_at=utcnow())
    sign_in(client, uid)

    for doc in pending(client)["pending"]:
        r = client.post("/api/policy/acknowledge",
                        json={"doc": doc["doc"], "version": doc["version"]})
        assert r.status_code == 200

    assert pending(client)["pending"] == []


def test_an_account_without_a_birth_year_is_flagged(client):
    uid = make_user("prompt+noage@example.com", created_at=datetime(2025, 3, 1))
    sign_in(client, uid)
    assert pending(client)["needs_birth_year"] is True


def test_an_account_with_a_birth_year_is_not_flagged(client):
    uid = make_user("prompt+age@example.com", birth_year=2004,
                    created_at=datetime(2025, 3, 1))
    sign_in(client, uid)
    assert pending(client)["needs_birth_year"] is False


def test_the_notice_shows_a_tldr_after_accepting_and_asks_for_age(client):
    uid = make_user("prompt+page@example.com", birth_year=2004)
    sign_in(client, uid)
    html = client.get("/settings", follow_redirects=True).get_data(as_text=True)

    assert "You're all set" in html
    assert "doc_tldr" in html
    assert "needs_birth_year" in html
    assert "/account/age?next=" in html


def test_every_tldr_line_is_short():
    for lines in policy_versions.DOC_TLDR.values():
        assert 3 <= len(lines) <= 5
        for line in lines:
            assert len(line) <= 160


# ── The published policy matches the notice ─────────────────


def privacy_v4():
    return next(v for v in policy_versions.PRIVACY_VERSIONS if v["version"] == 4)


def test_the_new_reminder_wording_is_on_the_legal_page(client):
    html = client.get("/legal").get_data(as_text=True)
    for clause in privacy_v4()["clauses"]:
        new_text = clause["after"].replace("Optional reminders data — ", "")
        assert new_text in html, clause["heading"]


def test_the_old_reminder_wording_is_gone(client):
    html = client.get("/legal").get_data(as_text=True)
    assert "Send reminders by SMS or browser push, only when you opt in." not in html
    assert "You can revoke either at any time." not in html


def test_the_legal_page_leads_with_each_tldr(client):
    html = client.get("/legal").get_data(as_text=True)
    for lines in policy_versions.DOC_TLDR.values():
        for line in lines:
            assert line.replace("'", "&#39;") in html


# ── Welcome email once the age is known ──────────────────────


@pytest.fixture
def welcomed(monkeypatch):
    sent = []
    monkeypatch.setattr(App, "send_welcome_email_on_signup", sent.append)
    return sent


def test_a_new_google_account_is_welcomed_after_the_age_step(client, welcomed):
    uid = make_user("welcome+google@example.com", google_id="g-welcome-1",
                    created_at=utcnow())
    sign_in(client, uid)

    r = client.post("/account/age", data={"birth_year": "2005"})

    assert r.status_code == 302
    assert welcomed == [uid]


def test_an_old_account_adding_its_age_is_not_welcomed(client, welcomed):
    uid = make_user("welcome+old@example.com", google_id="g-welcome-2",
                    created_at=utcnow() - timedelta(days=60))
    sign_in(client, uid)

    client.post("/account/age", data={"birth_year": "2005"})

    assert welcomed == []


def test_a_child_is_not_welcomed_until_a_parent_approves(client, welcomed):
    uid = make_user("welcome+child@example.com", google_id="g-welcome-3",
                    created_at=utcnow())
    sign_in(client, uid)
    client.post("/account/age", data={"birth_year": str(utcnow().year - 10),
                                      "parent_email": "parent+welcome@example.com"})
    assert welcomed == []

    with App.app.app_context():
        token = db.session.get(User, uid).parent_consent_token
    with App.app.test_client() as parent:
        r = parent.post("/parent/consent", data={"token": token, "acknowledged": "yes"})
    assert r.status_code == 200
    assert welcomed == [uid]


# ── Desktop installs that went away ──────────────────────────

INSTALL = "f0e1d2c3b4a5968778695a4b3c2d1e0f"


def desktop_row(uid, *, last_seen=None, created_at=None):
    with App.app.app_context():
        PushSubscription.query.filter_by(user_id=uid).delete(synchronize_session=False)
        payload = {"last_seen": last_seen.isoformat(timespec="seconds")} if last_seen else {}
        row = PushSubscription(user_id=uid, endpoint=f"desktop:{INSTALL}",
                               subscription_json=json.dumps(payload),
                               created_at=created_at or utcnow())
        db.session.add(row)
        db.session.commit()


def desktop_rows(uid):
    with App.app.app_context():
        return PushSubscription.query.filter_by(user_id=uid).all()


def test_polling_the_feed_records_that_the_install_is_alive(client):
    uid = make_user("desk+poll@example.com", birth_year=2004)
    desktop_row(uid, created_at=utcnow() - timedelta(days=20))
    sign_in(client, uid)

    assert client.get(f"/api/notifications/desktop-feed?install={INSTALL}").status_code == 200

    [row] = desktop_rows(uid)
    seen = App._desktop_last_seen(row)
    assert utcnow() - seen < timedelta(minutes=1)


def test_another_accounts_install_id_is_not_touched(client):
    owner = make_user("desk+owner@example.com", birth_year=2004)
    other = make_user("desk+other@example.com", birth_year=2004)
    desktop_row(owner, created_at=utcnow() - timedelta(days=20))
    sign_in(client, other)

    client.get(f"/api/notifications/desktop-feed?install={INSTALL}")

    [row] = desktop_rows(owner)
    assert json.loads(row.subscription_json) == {}


def test_a_recently_seen_install_counts_as_delivered(client):
    uid = make_user("desk+alive@example.com", birth_year=2004)
    desktop_row(uid, last_seen=utcnow() - timedelta(hours=3))
    with App.app.app_context():
        assert App._send_push_to_user(uid, {"title": "t", "body": "b", "url": "/"}) == 1


def test_a_silent_install_stops_counting(client):
    uid = make_user("desk+quiet@example.com", birth_year=2004)
    desktop_row(uid, last_seen=utcnow() - timedelta(days=8))
    with App.app.app_context():
        assert App._send_push_to_user(uid, {"title": "t", "body": "b", "url": "/"}) == 0
    assert len(desktop_rows(uid)) == 1, "kept for a while in case the laptop was just off"


def test_an_install_silent_for_a_month_is_removed(client):
    uid = make_user("desk+gone@example.com", birth_year=2004)
    desktop_row(uid, last_seen=utcnow() - timedelta(days=31))
    with App.app.app_context():
        assert App._send_push_to_user(uid, {"title": "t", "body": "b", "url": "/"}) == 0
    assert desktop_rows(uid) == []


def test_the_poller_sends_its_install_id():
    with open("static/js/ip-desktop-feed.js", encoding="utf-8") as f:
        source = f.read()
    assert "ip_desktopInstallId" in source
    assert "?install=" in source


# ── The ticker runs every minute, not every lease ────────────


def test_a_finished_tick_frees_the_lease_for_the_next_minute(monkeypatch):
    import notifications_glue
    from intelliplan.notifications.models import register_lease

    monkeypatch.setattr(notifications_glue, "sweep_all", lambda: {"users": 0, "queued": 0})
    monkeypatch.setattr(notifications_glue, "sweep_streaks", lambda: {"checked": 0, "queued": 0})

    class _Flushed:
        def as_dict(self):
            return {"sent": 0}

    class _Dispatcher:
        def flush(self):
            return _Flushed()

    monkeypatch.setattr(notifications_glue, "get_dispatcher", lambda: _Dispatcher())

    with App.app.app_context():
        lease = register_lease(db)
        row = db.session.get(lease, "notifications")
        if row is not None:
            row.expires_at = utcnow() - timedelta(seconds=1)
            db.session.commit()

        assert notifications_glue._tick_once(App.app) is not None

        db.session.expire_all()
        row = db.session.get(lease, "notifications")
        assert row.expires_at - utcnow() <= timedelta(seconds=notifications_glue.TICK_SECONDS)
