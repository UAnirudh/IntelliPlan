from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

import google_drive_helper as drive
import google_calendar_helper as google_oauth
import onedrive_helper as onedrive
from intelliplan.integrations.document_content import extract_text, read_limited_response


def test_onedrive_authorization_uses_state_and_pkce(monkeypatch):
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "client")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MICROSOFT_REDIRECT_URI", "https://example.test/callback")
    url, verifier = onedrive.get_auth_url("state-value")
    params = parse_qs(urlparse(url).query)
    assert params["state"] == ["state-value"]
    assert params["code_challenge_method"] == ["S256"]
    assert "Files.ReadWrite" in params["scope"][0]
    assert verifier and verifier not in url


def test_onedrive_code_exchange_sends_the_pkce_verifier(monkeypatch):
    monkeypatch.setenv("MICROSOFT_REDIRECT_URI", "https://example.test/callback")
    calls = []
    monkeypatch.setattr(onedrive, "_token_request", lambda payload: calls.append(payload) or {"access_token": "token"})
    result = onedrive.exchange_code("auth-code", "verifier")
    assert result["access_token"] == "token"
    assert calls[0]["code_verifier"] == "verifier"


def test_google_drive_scope_is_limited_to_picker_selected_files():
    assert drive.has_drive_scope({"scopes": [drive.DRIVE_SCOPE]})
    assert not drive.has_drive_scope({"scopes": ["https://www.googleapis.com/auth/drive.readonly"]})


def test_google_drive_oauth_requests_picker_scope_without_calendar_access(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client")
    url, verifier = google_oauth.get_auth_url("state-value", purpose="drive")
    params = parse_qs(urlparse(url).query)
    assert drive.DRIVE_SCOPE in params["scope"][0].split()
    assert "https://www.googleapis.com/auth/calendar.events" not in params["scope"][0].split()
    assert params["code_challenge_method"] == ["S256"]
    assert verifier and verifier not in url


def test_google_doc_save_rejects_a_stale_revision_before_writing(monkeypatch):
    monkeypatch.setattr(drive, "read_google_doc", lambda *_: {"revision_id": "new-revision", "end_index": 2})
    writes = []
    monkeypatch.setattr(drive.requests, "post", lambda *a, **k: writes.append(a))

    with pytest.raises(RuntimeError, match="changed in Drive"):
        drive.replace_google_doc_text({"token": "test"}, "doc-id", "new content", "old-revision")

    assert writes == []


def test_google_doc_save_uses_revision_guard(monkeypatch):
    monkeypatch.setattr(drive, "read_google_doc", lambda *_: {"revision_id": "revision-1", "end_index": 12})

    class Response:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {"writeControl": {"requiredRevisionId": "revision-2"}}

    calls = []
    monkeypatch.setattr(drive.requests, "post", lambda *a, **kw: (calls.append((a, kw)) or Response()))
    result = drive.replace_google_doc_text({"token": "test"}, "doc-id", "replacement", "revision-1")

    assert result == "revision-2"
    assert calls[0][1]["json"]["writeControl"] == {"requiredRevisionId": "revision-1"}


def test_onedrive_edit_only_accepts_text_formats(monkeypatch):
    monkeypatch.setattr(onedrive, "get_file", lambda *_: {"name": "notes.docx", "eTag": "tag"})
    with pytest.raises(ValueError, match="Word documents can be imported"):
        onedrive.replace_text_file({"access_token": "test"}, "file-id", "new", "tag")


def test_onedrive_save_uses_etag_as_if_match(monkeypatch):
    monkeypatch.setattr(onedrive, "get_file", lambda *_: {"name": "notes.md", "eTag": "tag-1"})

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"eTag": "tag-2"}

    calls = []
    monkeypatch.setattr(onedrive.requests, "put", lambda *a, **kw: (calls.append((a, kw)) or Response()))
    updated = onedrive.replace_text_file({"access_token": "test"}, "file-id", "new", "tag-1")

    assert updated["eTag"] == "tag-2"
    assert calls[0][1]["headers"]["If-Match"] == "tag-1"


def test_document_text_extraction_is_bounded_and_rejects_unknown_types():
    assert extract_text("notes.md", "text/markdown", b"# Revision\n") == "# Revision"
    with pytest.raises(ValueError, match="can import Google Docs"):
        extract_text("notes.bin", "application/octet-stream", b"data")
    assert len(extract_text("notes.txt", "text/plain", b"x" * 60_000)) == 50_000
    with pytest.raises(ValueError, match="larger than IntelliPlan"):
        extract_text("notes.txt", "text/plain", b"x" * (2 * 1024 * 1024 + 1))


def test_streamed_cloud_download_stops_at_the_document_limit():
    class Response:
        closed = False

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"x" * (2 * 1024 * 1024)
            yield b"y"

        def close(self):
            self.closed = True

    response = Response()
    with pytest.raises(ValueError, match="larger than IntelliPlan"):
        read_limited_response(response)
    assert response.closed
