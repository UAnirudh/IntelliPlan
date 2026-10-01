from __future__ import annotations

import pytest

import App


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as value:
        yield value
    App.limiter.enabled = True


@pytest.fixture
def student(request):
    from App import CloudDocumentLink, CourseNote, GoogleDriveIntegration, User, db

    with App.app.app_context():
        user = User(
            email="cloudfiles+test@example.com",
            name="Cloud File Tester",
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    def cleanup():
        with App.app.app_context():
            CloudDocumentLink.query.filter_by(user_id=user_id).delete()
            CourseNote.query.filter_by(user_id=user_id).delete()
            GoogleDriveIntegration.query.filter_by(user_id=user_id).delete()
            App.OneDriveIntegration.query.filter_by(user_id=user_id).delete()
            User.query.filter_by(id=user_id).delete()
            db.session.commit()

    request.addfinalizer(cleanup)
    return user_id


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_cloud_document_api_requires_a_signed_in_student(client):
    assert client.get("/api/cloud-documents").status_code == 401
    assert client.get("/api/cloud-documents/status").status_code == 401


def test_signed_in_student_can_open_the_study_files_page(client, student):
    login(client, student)
    response = client.get("/study-files")
    assert response.status_code == 200
    assert "Google Drive and Docs" in response.get_data(as_text=True)
    assert "OneDrive" in response.get_data(as_text=True)


def test_imported_file_becomes_a_course_note_for_study_context(client, student, monkeypatch):
    login(client, student)
    with App.app.app_context():
        App.db.session.add(App.GoogleDriveIntegration(
            user_id=student, token_data='{"token":"test"}',
            account_email="student@gmail.com", account_name="Student",
        ))
        App.db.session.commit()
    monkeypatch.setattr(App, "_cloud_file_token", lambda provider, account_email=None: {"token": "test"})
    monkeypatch.setattr(App, "_cloud_file_text", lambda *args: (
        {"name": "Cell Biology.md", "mimeType": "text/markdown", "webViewLink": "https://drive.google.com/file/d/test"},
        "Mitochondria generate ATP.", "revision-1", False, "",
    ))

    response = client.post("/api/cloud-documents/import", json={
        "provider": "google_drive", "external_id": "drive-file-1",
    })

    assert response.status_code == 200
    listed = client.get("/api/cloud-documents").get_json()["documents"]
    assert len(listed) == 1
    assert listed[0]["title"] == "Cell Biology.md"
    assert listed[0]["external_id"] == "drive-file-1"
    with App.app.app_context():
        note = App.CourseNote.query.filter_by(user_id=student).one()
        assert note.text_content == "Mitochondria generate ATP."
        assert note.course_source == "google_drive"
        link = App.CloudDocumentLink.query.filter_by(user_id=student).one()
        assert link.account_email == "student@gmail.com"


def test_another_students_imported_file_cannot_be_opened(client, student, request):
    from App import CloudDocumentLink, CourseNote, db

    with App.app.app_context():
        other = App.User(
            email="cloudfiles+other@example.com", name="Other Student",
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
        )
        db.session.add(other)
        db.session.flush()
        note = CourseNote(
            user_id=other.id, course_name="OneDrive", course_id="file-2",
            course_source="onedrive", note_date="2026-09-30", title="Private notes",
            original_filename="private.txt", text_content="not yours",
        )
        db.session.add(note)
        db.session.flush()
        link = CloudDocumentLink(
            user_id=other.id, note_id=note.id, provider="onedrive",
            external_file_id="private-file", mime_type="text/plain",
        )
        db.session.add(link)
        db.session.commit()
        link_id, other_id, note_id = link.id, other.id, note.id

    def cleanup_other():
        with App.app.app_context():
            CloudDocumentLink.query.filter_by(user_id=other_id).delete()
            CourseNote.query.filter_by(user_id=other_id).delete()
            App.User.query.filter_by(id=other_id).delete()
            db.session.commit()

    request.addfinalizer(cleanup_other)
    login(client, student)
    response = client.get(f"/api/cloud-documents/{link_id}")
    assert response.status_code == 404
    with App.app.app_context():
        assert CourseNote.query.filter_by(id=note_id, user_id=other_id).one().text_content == "not yours"
