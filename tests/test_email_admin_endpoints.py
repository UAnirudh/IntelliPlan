"""The two admin preview endpoints, over HTTP.

These exist to answer "why did/didn't this student get that email" without
sending anything, so the thing worth testing is that they respond at all to
the way a person actually reaches them: a GET with query parameters and no
body.

That is exactly how they were broken. `request.json` aborts with a 415 when
the request carries no JSON body, and `request.args.get(...) or request.json`
only short-circuits when the query argument is present — so a plain
`GET ?user_id=3` still hit it on the `email` line and 415'd the whole
request. Every unit test of the underlying functions passed the entire time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

import App as app_module


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        for model in (
            app_module.EmailSend,
            app_module.ManualTask,
            app_module.User,
        ):
            model.query.delete()
        app_module.db.session.commit()
        yield


def make_user(days_old=20, admin=False, **overrides):
    email = "admin@example.test" if admin else f"a-{uuid.uuid4().hex[:8]}@example.test"
    defaults = {
        "email": email,
        "name": "Sam",
        "birth_year": 2005,
        "role": "student",
        "password_hash": "x",
        "email_reminders_opt_in": True,
        "created_at": datetime.utcnow() - timedelta(days=days_old),
    }
    defaults.update(overrides)
    user = app_module.User(**defaults)
    app_module.db.session.add(user)
    app_module.db.session.commit()
    return user


@pytest.fixture
def admin_client(ctx, monkeypatch):
    """A client signed in as an admin, the way require_admin expects."""
    admin = make_user(days_old=90, admin=True)
    monkeypatch.setattr(app_module, "ADMIN_EMAILS", {admin.email.lower()}, raising=False)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin.id)
        session["_fresh"] = True
    return client


def test_onboarding_preview_answers_a_bodyless_get(admin_client):
    user = make_user(days_old=3)
    response = admin_client.get(f"/api/admin/email/onboarding-preview?user_id={user.id}")
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    payload = response.get_json()
    assert sorted(payload["progress"]) == ["connected", "planned", "studied"]
    assert payload["user_id"] == user.id


def test_onboarding_preview_accepts_an_email_instead_of_an_id(admin_client):
    """The `email` branch is the one that used to reach request.json."""
    user = make_user(days_old=3)
    response = admin_client.get(
        f"/api/admin/email/onboarding-preview?email={user.email}"
    )
    assert response.status_code == 200
    assert response.get_json()["user_id"] == user.id


def test_drafts_preview_lists_the_actual_items(admin_client):
    user = make_user(days_old=20)
    app_module.db.session.add(
        app_module.ManualTask(
            user_id=user.id,
            title="Finish lab report",
            due_date=(datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d"),
            done=False,
        )
    )
    app_module.db.session.commit()

    response = admin_client.get(f"/api/admin/email/drafts-preview?user_id={user.id}")
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    payload = response.get_json()
    assert [d["title"] for d in payload["drafts"]] == ["Finish lab report"]
    assert payload["eligible"] is True


def test_drafts_preview_with_no_arguments_dry_runs_the_sweep(admin_client):
    response = admin_client.get("/api/admin/email/drafts-preview")
    assert response.status_code == 200
    summary = response.get_json()["summary"]
    assert summary["dry_run"] is True
    assert summary["sent"] == 0


def test_both_previews_reject_an_unknown_user(admin_client):
    for path in ("onboarding-preview", "drafts-preview"):
        response = admin_client.get(f"/api/admin/email/{path}?user_id=99999999")
        assert response.status_code == 404, path


def test_the_previews_are_admin_only(ctx):
    """They read one named student's account state, so they are not a
    signed-in-user endpoint."""
    anonymous = app_module.app.test_client()
    for path in ("onboarding-preview", "drafts-preview"):
        response = anonymous.get(f"/api/admin/email/{path}")
        assert response.status_code in (301, 302, 401, 403), (
            f"{path} -> {response.status_code}"
        )
