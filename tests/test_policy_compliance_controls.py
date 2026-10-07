"""Regression checks for the concrete privacy controls, not legal certification."""
import uuid
from datetime import datetime

import pytest

import App
import ai_provider
import parental_notice
from account_uploads import owned_upload_paths, remove_uploads


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    enabled = App.limiter.enabled
    App.limiter.enabled = False
    with App.app.app_context():
        App.db.create_all()
    with App.app.test_client() as client:
        yield client
    App.limiter.enabled = enabled


def pending_user():
    with App.app.app_context():
        user = App.User(email=f"privacy-{uuid.uuid4().hex}@example.test",
                        password_hash="x", birth_year=datetime.utcnow().year - 12,
                        parent_email="parent@example.test", parent_consent_granted=False,
                        parent_consent_token=uuid.uuid4().hex)
        App.db.session.add(user)
        App.db.session.commit()
        return user.id, user.parent_consent_token


def sign_in(client, uid):
    with client.session_transaction() as session:
        session["_user_id"] = str(uid)
        session["_fresh"] = True


def test_parent_link_requires_informed_post_and_records_notice(client):
    uid, token = pending_user()
    page = client.get("/parent/consent", query_string={"token": token})
    assert page.status_code == 200
    assert b'checkbox' in page.data and b'method="post"' in page.data.lower()
    with App.app.app_context():
        assert not App.db.session.get(App.User, uid).parent_consent_granted
    assert client.post("/parent/consent", data={"token": token}).status_code == 400
    assert client.post("/parent/consent", data={"token": token, "acknowledged": "yes"}).status_code == 200
    with App.app.app_context():
        user = App.db.session.get(App.User, uid)
        assert user.parent_consent_granted
        assert user.parent_consent_at
        assert user.parent_consent_notice_version == parental_notice.NOTICE_VERSION
        assert user.parent_consent_token is None
    assert client.get("/parent/consent", query_string={"token": token}).status_code == 404


def test_parent_denial_get_does_not_delete_account(client):
    uid, token = pending_user()
    with App.app.app_context():
        App.db.session.add(App.PolicyAcknowledgement(user_id=uid, doc="privacy", version=3))
        App.db.session.commit()
    assert client.get("/parent/deny", query_string={"token": token}).status_code == 200
    assert client.post("/parent/deny", data={"token": token}).status_code == 400
    with App.app.app_context():
        assert App.db.session.get(App.User, uid) is not None
    assert client.post("/parent/deny", data={"token": token, "acknowledged": "yes"}).status_code == 200
    with App.app.app_context():
        assert App.db.session.get(App.User, uid) is None
        assert App.PolicyAcknowledgement.query.filter_by(user_id=uid).count() == 0


@pytest.mark.parametrize("path", ["/notes/upload", "/api/syllabus/import"])
def test_pending_signed_in_child_cannot_submit_learning_data(client, path):
    uid, _ = pending_user()
    sign_in(client, uid)
    response = client.post(path, json={"text": "private learning data"})
    assert response.status_code == 403
    assert response.get_json()["reason"] == "parent_consent_required"


def test_same_day_account_without_version_evidence_gets_current_notice(client):
    with App.app.app_context():
        user = App.User(email=f"notice-{uuid.uuid4().hex}@example.test",
                        password_hash="x", birth_year=2000, created_at=datetime.utcnow())
        App.db.session.add(user)
        App.db.session.commit()
        uid = user.id
    sign_in(client, uid)
    pending = client.get("/api/policy/pending").get_json()["pending"]
    assert {(p["doc"], p["version"]) for p in pending} == {("privacy", 3), ("terms", 2)}


def test_developer_key_or_vertex_configuration_is_not_permission(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-developer-key")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.delenv("GEMINI_MINORS_CONTRACT_APPROVED", raising=False)
    assert not ai_provider.gemini_available()
    with pytest.raises(ai_provider.AIUnavailable):
        ai_provider._gemini_client()
    monkeypatch.setenv("GEMINI_MINORS_CONTRACT_APPROVED", "1")
    assert ai_provider.gemini_available()
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    assert not ai_provider.gemini_available()


def test_missing_email_provider_does_not_log_consent_links(monkeypatch, capsys):
    monkeypatch.setattr(App, "_send_email_via_resend", lambda *args, **kwargs: False)
    monkeypatch.setattr(App, "_smtp_config", lambda: (None, 587, None, None, None))
    assert App._send_email("private@example.test", "Approval", "token=private-bearer") is False
    output = capsys.readouterr().out
    assert "private@example.test" not in output
    assert "private-bearer" not in output


def test_uploaded_file_deletion_is_limited_to_owned_names(tmp_path):
    notes = tmp_path / "notes"
    lessons = tmp_path / "lessons"
    own = notes / "user_7"
    other = notes / "user_8"
    for directory in (own, other, lessons):
        directory.mkdir(parents=True)
    owned_note = own / "note.pdf"
    owned_lesson = lessons / "owned.mp3"
    protected = other / "note.pdf"
    for path in (owned_note, owned_lesson, protected):
        path.write_bytes(b"data")
    remove_uploads(owned_upload_paths(7, str(notes), str(lessons), ["note.pdf"], ["owned.mp3"]))
    assert not owned_note.exists() and not owned_lesson.exists()
    assert protected.exists()
    with pytest.raises(OSError):
        owned_upload_paths(7, str(notes), str(lessons), ["../user_8/note.pdf"], [])


def test_directory_badges_use_provider_images(client):
    html = client.get("/").data.decode()
    for host, alt in (("1000saas", "Featured on 1000 Saas"), ("saashunt", "Featured on SaasHunt"), ("devhub", "Featured on DevHub")):
        assert f'https://{host}.best/projects/intelliplan?utm_source=badge' in html
        assert alt in html
    assert "https://boostdomainrating.com/item/intelliplan.tech?utm_source=badge" in html
    assert 'src="https://boostdomainrating.com/api/badge/intelliplan.tech"' in html
    assert 'src="https://1000saas.best/images/badges/featured-on-dark.svg"' in html
    assert 'src="https://saashunt.best/images/badges/featured-on-dark.svg"' in html
    assert 'src="https://r2.direasy-multi-tenant.focusapps.app/uploads/' in html
    assert 'referrerpolicy="no-referrer"' in html


@pytest.mark.parametrize("sql_failure", [False, True])
def test_account_file_cleanup_and_database_rollback(client, tmp_path, monkeypatch, sql_failure):
    from flask_login import login_user
    from sqlalchemy import event

    notes = tmp_path / "notes"
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    monkeypatch.setitem(App.app.config, "NOTES_UPLOAD_FOLDER", str(notes))
    monkeypatch.setattr(App, "LESSON_UPLOAD_FOLDER", str(lessons))
    with App.app.app_context():
        user = App.User(email=f"erase-{uuid.uuid4().hex}@example.test", password_hash="x", birth_year=2000)
        App.db.session.add(user)
        App.db.session.flush()
        uid = user.id
        own = notes / f"user_{uid}"
        own.mkdir(parents=True)
        original = own / "note.pdf"
        original.write_bytes(b"private note")
        App.db.session.add(App.CourseNote(user_id=uid, course_name="Math", note_date="2026-10-02",
                                          title="Note", stored_filename="note.pdf"))
        App.db.session.commit()
        engine = App.db.engine

        def fail_user_delete(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("DELETE FROM users WHERE id"):
                raise RuntimeError("injected deletion failure")

        if sql_failure:
            event.listen(engine, "before_cursor_execute", fail_user_delete)
        try:
            with App.app.test_request_context():
                login_user(user)
                result = App._account_delete_impl()
                response, status = result if isinstance(result, tuple) else (result, result.status_code)
                assert status == (500 if sql_failure else 200)
        finally:
            if sql_failure:
                event.remove(engine, "before_cursor_execute", fail_user_delete)
        App.db.session.expire_all()
        assert (App.db.session.get(App.User, uid) is not None) == sql_failure
        assert original.exists() == sql_failure
        assert not list(own.glob(".delete-*"))
