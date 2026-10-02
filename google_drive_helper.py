"""Google Drive and Docs for a student's study files.

Two layers live here:

* **Selected files** (top half): the Picker-driven import and Google Docs
  editing behind /study-files. Plain token-dict functions.
* **Assignment matching and study guides** (bottom half): full-text search,
  capped text extraction and Doc creation for ``document_matcher``. These
  take a :class:`DriveClient`, which renews its own token and hands the
  renewed one back to App for storage.

Both use the same OAuth client as sign-in and Google Calendar
(``GOOGLE_CLIENT_ID``) and the same redirect URI; Drive is an incremental
grant on it, not a second Google app.

Scopes
------
``drive.file`` (non-sensitive, no Google review) is always requested: it
covers files IntelliPlan creates and files the student picks with Google
Picker. ``drive.readonly`` (restricted, needs verification plus a CASA
security assessment) is added only when ``GOOGLE_DRIVE_SCOPE_MODE=readonly``,
which lets assignment matching search the student's whole Drive. See
docs/INTEGRATIONS_SETUP.md for the tradeoff.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from html import escape
from urllib.parse import quote

import requests

from cloud_token_client import ProviderError, TokenClient, TokenExpired, read_capped
from intelliplan.integrations.document_content import CloudDocumentError

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DOCS_API = "https://docs.googleapis.com/v1"
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
EXPORT_TYPES = {
    GOOGLE_DOC: "text/plain",
    GOOGLE_SHEET: "text/csv",
    GOOGLE_SLIDES: "text/plain",
}


def has_drive_scope(token: dict) -> bool:
    return DRIVE_SCOPE in set(token.get("scopes") or [])


def _headers(token: dict) -> dict:
    access_token = token.get("token") or token.get("access_token")
    if not access_token:
        raise CloudDocumentError("Google Drive is not connected. Please reconnect it.")
    return {"Authorization": f"Bearer {access_token}"}


def get_file(token: dict, file_id: str) -> dict:
    response = requests.get(
        f"{DRIVE_API}/files/{quote(file_id, safe='')}",
        params={"fields": "id,name,mimeType,webViewLink,modifiedTime,version,capabilities(canEdit),size,trashed",
                "supportsAllDrives": "true"},
        headers=_headers(token), timeout=15,
    )
    response.raise_for_status()
    info = response.json()
    if info.get("trashed"):
        raise CloudDocumentError("This file is in the Drive trash.")
    return info


def search_files(token: dict, query: str = "", page_token: str | None = None) -> dict:
    terms = ["trashed = false"]
    if query.strip():
        safe = query.strip().replace("\\", "\\\\").replace("'", "\\'")[:100]
        terms.append(f"name contains '{safe}'")
    params = {
        "q": " and ".join(terms),
        "pageSize": 50,
        "orderBy": "modifiedTime desc",
        "fields": "nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime,version,capabilities(canEdit))",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    if page_token:
        params["pageToken"] = page_token
    response = requests.get(f"{DRIVE_API}/files", params=params, headers=_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


def download_text(token: dict, info: dict) -> str:
    file_id = quote(str(info.get("id") or ""), safe="")
    mime = info.get("mimeType") or ""
    if int(info.get("size") or 0) > 2 * 1024 * 1024:
        raise CloudDocumentError("This file is larger than IntelliPlan can import (2 MB).")
    if mime in EXPORT_TYPES:
        response = requests.get(
            f"{DRIVE_API}/files/{file_id}/export",
            params={"mimeType": EXPORT_TYPES[mime]},
            headers=_headers(token), timeout=30, stream=True,
        )
    else:
        response = requests.get(
            f"{DRIVE_API}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers=_headers(token), timeout=30, stream=True,
        )
    try:
        response.raise_for_status()
        from intelliplan.integrations.document_content import extract_text, read_limited_response
        content = read_limited_response(response)
    except Exception:
        response.close()
        raise
    return extract_text(info.get("name") or "", mime, content)


def read_google_doc(token: dict, file_id: str) -> dict:
    response = requests.get(
        f"{DOCS_API}/documents/{quote(file_id, safe='')}",
        headers=_headers(token), timeout=20, stream=True,
    )
    try:
        response.raise_for_status()
        from intelliplan.integrations.document_content import read_limited_response
        document = json.loads(read_limited_response(response))
    except Exception:
        response.close()
        raise
    pieces = []
    end_index = 1
    for element in (document.get("body") or {}).get("content", []):
        end_index = max(end_index, int(element.get("endIndex") or 1))
        paragraph = element.get("paragraph")
        if paragraph:
            pieces.extend(
                item.get("textRun", {}).get("content", "")
                for item in paragraph.get("elements", [])
            )
        elif any(key in element for key in ("table", "tableOfContents", "sectionBreak", "paragraphStyle")):
            raise CloudDocumentError("This Google Doc has tables or layout elements and is available as read-only text.")
    return {"revision_id": document.get("revisionId"), "text": "".join(pieces), "end_index": end_index}


def replace_google_doc_text(token: dict, file_id: str, content: str, revision_id: str) -> str:
    if not revision_id:
        raise CloudDocumentError("Refresh the document before saving your edits.")
    current = read_google_doc(token, file_id)
    if current["revision_id"] != revision_id:
        raise RuntimeError("This Google Doc changed in Drive. Reload it before saving to avoid overwriting newer edits.")
    end_index = current["end_index"]
    requests_body = []
    if end_index > 2:
        requests_body.append({"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end_index - 1}}})
    if content:
        requests_body.append({"insertText": {"location": {"index": 1}, "text": content}})
    response = requests.post(
        f"{DOCS_API}/documents/{quote(file_id, safe='')}:batchUpdate",
        json={"requests": requests_body, "writeControl": {"requiredRevisionId": revision_id}},
        headers={**_headers(token), "Content-Type": "application/json"}, timeout=25,
    )
    if response.status_code == 400 and "revision" in response.text.lower():
        raise RuntimeError("This Google Doc changed in Drive. Reload it before saving to avoid overwriting newer edits.")
    response.raise_for_status()
    return (response.json().get("writeControl") or {}).get("requiredRevisionId") or ""


# ── Assignment matching and study guides ─────────────────────────────

API = DRIVE_API
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URI = "https://oauth2.googleapis.com/token"

SCOPE_FILE = DRIVE_SCOPE
SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

FOLDER = "application/vnd.google-apps.folder"

#: What a search is allowed to return. Limiting the query to types we can
#: read means the result slots are not spent on photos and videos that would
#: be skipped anyway.
READABLE_MIME_TYPES = (
    GOOGLE_DOC,
    GOOGLE_SLIDES,
    GOOGLE_SHEET,
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/markdown",
)

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 12000
FOLDER_NAME = "IntelliPlan"
#: Marks the folder IntelliPlan made. Under ``drive.file`` it is the only
#: "IntelliPlan" folder we could write into anyway, and under
#: ``drive.readonly`` a student's own folder of that name must not be picked
#: -- we can see it but not add to it.
APP_PROPERTY = ("intelliplan", "folder")


def configured() -> bool:
    """Drive rides on the sign-in client, so it is available exactly when
    that client is configured -- the same test /oauth/google-drive applies."""
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def scope_mode() -> str:
    """``"file"`` (default: files IntelliPlan created or the student picked)
    or ``"readonly"`` (search the whole Drive). The restricted scope is
    opt-in because requesting it before Google has verified the app caps the
    integration at 100 users and shows every student a warning screen."""
    mode = (os.getenv("GOOGLE_DRIVE_SCOPE_MODE") or "file").strip().lower()
    return "readonly" if mode == "readonly" else "file"


def drive_scopes() -> list:
    scopes = [SCOPE_FILE]
    if scope_mode() == "readonly":
        scopes.append(SCOPE_READONLY)
    return scopes


def can_search_everything(token: dict) -> bool:
    return SCOPE_READONLY in set((token or {}).get("scopes") or [])


class DriveClient(TokenClient):
    # Google rows written by google_calendar_helper use "token".
    access_key = "token"

    def _refresh(self, token):
        response = requests.post(TOKEN_URI, timeout=15, data={
            "refresh_token": token.get("refresh_token", ""),
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
            "grant_type": "refresh_token",
        })
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400 or "error" in data or not data.get("access_token"):
            # invalid_grant: revoked, expired (7-day testing-mode tokens), or
            # the password changed. Only a reconnect fixes any of those.
            raise TokenExpired(f"Google refresh rejected ({data.get('error') or response.status_code})")
        out = {"token": data["access_token"], "expires_in": data.get("expires_in") or 3600}
        if data.get("refresh_token"):
            out["refresh_token"] = data["refresh_token"]
        return out


# ── Search ────────────────────────────────────────────────────────────


def _q_literal(text: str) -> str:
    """Escape a value for a Drive query string literal.

    Drive's query language quotes with single quotes and escapes with a
    backslash. An unescaped apostrophe in an assignment title ("Frankenstein's
    creature") would otherwise end the literal and make the whole query a 400.
    """
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


def build_search_query(terms, *, readable_only: bool = True) -> str:
    """The ``q`` for ``files.list``: any term in the full text, not trashed.

    Terms are OR-ed rather than AND-ed. A student's notes for "Photosynthesis
    lab report" rarely contain all three words, and ranking (in the matcher)
    is where closeness is decided -- this just has to cast the net.
    """
    if isinstance(terms, str):
        terms = [terms]
    clean = []
    for term in terms or []:
        term = re.sub(r"\s+", " ", str(term or "")).strip()[:80]
        if term and term.lower() not in {t.lower() for t in clean}:
            clean.append(term)
    if not clean:
        raise ValueError("at least one search term is required")
    text = " or ".join(f"fullText contains '{_q_literal(t)}'" for t in clean[:6])
    query = f"({text}) and trashed = false"
    if readable_only:
        mimes = " or ".join(f"mimeType = '{m}'" for m in READABLE_MIME_TYPES)
        query = f"({text}) and ({mimes}) and trashed = false"
    return query


def search_full_text(client: DriveClient, terms, limit: int = 15) -> list:
    """Candidate files for ``terms``, normalised to the shape the matcher ranks.

    Separate from :func:`search_files` (the /study-files browser, which
    matches names and sorts by date): this looks *inside* documents. No
    ``orderBy``: Drive refuses to sort a ``fullText`` query, so recency is
    applied by the matcher from ``modifiedTime`` instead.
    """
    data = client.json("GET", f"{API}/files", params={
        "q": build_search_query(terms),
        "pageSize": max(1, min(50, int(limit or 15))),
        "spaces": "drive",
        "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)",
    })
    out = []
    for item in data.get("files") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        out.append({
            "provider": "google_drive",
            "id": str(item["id"]),
            "name": str(item.get("name") or "Untitled")[:200],
            "mime": str(item.get("mimeType") or ""),
            "modified": str(item.get("modifiedTime") or ""),
            "url": str(item.get("webViewLink") or ""),
            "size": int(item.get("size") or 0) if str(item.get("size") or "").isdigit() else 0,
        })
    return out


# ── Reading ───────────────────────────────────────────────────────────


def _file_path(file_id: str) -> str:
    # Ids are opaque, but they end up in a URL path; anything outside
    # Drive's id alphabet is refused rather than escaped.
    if not re.fullmatch(r"[A-Za-z0-9_\-]{5,200}", str(file_id or "")):
        raise ProviderError("invalid Drive file id")
    return f"{API}/files/{file_id}"


def fetch_text(client: DriveClient, item: dict, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Plain text of one file, or ``""`` when it cannot be read cheaply.

    Google Docs/Slides/Sheets are exported as text. PDFs, .docx, .pptx and
    plain text are downloaded (capped) and parsed by the same extractor the
    /study-files import uses. Anything else is skipped without a download.
    """
    from intelliplan.integrations.document_content import extract_text, is_supported

    mime = str(item.get("mime") or "")
    path = _file_path(item.get("id"))
    if mime in EXPORT_TYPES:
        response = client.request("GET", f"{path}/export",
                                  params={"mimeType": EXPORT_TYPES[mime]}, stream=True)
        with response:
            if response.status_code != 200:
                return ""
            body = read_capped(response, MAX_DOWNLOAD_BYTES)
        if body is None:
            return ""
        text = body.decode("utf-8-sig", errors="replace")
    else:
        if not is_supported(item.get("name"), mime):
            return ""
        if item.get("size") and int(item["size"]) > MAX_DOWNLOAD_BYTES:
            return ""
        response = client.request("GET", path, params={"alt": "media"}, stream=True)
        with response:
            if response.status_code != 200:
                return ""
            body = read_capped(response, MAX_DOWNLOAD_BYTES)
        if body is None:
            return ""
        try:
            text = extract_text(item.get("name") or "", mime, body)
        except Exception as exc:
            # Includes "no extractable text" (a scanned PDF): skip, not fail.
            print(f"[google-drive] could not extract text: {type(exc).__name__}")
            return ""
    return re.sub(r"\s+", " ", text or "").strip()[:max_chars]


# ── Writing ───────────────────────────────────────────────────────────


def ensure_folder(client: DriveClient) -> str:
    """Id of the IntelliPlan folder in the student's Drive, creating it once."""
    key, value = APP_PROPERTY
    data = client.json("GET", f"{API}/files", params={
        "q": (f"mimeType = '{FOLDER}' and name = '{FOLDER_NAME}' and trashed = false "
              f"and appProperties has {{ key='{key}' and value='{value}' }}"),
        "pageSize": 1,
        "spaces": "drive",
        "fields": "files(id)",
    })
    found = data.get("files") or []
    if found and found[0].get("id"):
        return str(found[0]["id"])
    created = client.json("POST", f"{API}/files", params={"fields": "id"},
                          json={"name": FOLDER_NAME, "mimeType": FOLDER,
                                "appProperties": {key: value}})
    if not created.get("id"):
        raise ProviderError("Drive did not return the new folder")
    return str(created["id"])


def multipart_body(metadata: dict, content: str, content_type: str = "text/html"):
    """``(bytes, content_type_header)`` for a Drive multipart upload.

    Built by hand because ``requests`` only speaks multipart/form-data, and
    Drive's simple-upload endpoint wants multipart/related: a JSON metadata
    part followed by the media part.
    """
    boundary = "intelliplan-" + secrets.token_hex(12)
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {content_type}; charset=UTF-8\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    return body, f"multipart/related; boundary={boundary}"


def markdown_to_html(markdown: str, title: str = "") -> str:
    """Just enough Markdown for a study guide: headings, bullets, checkboxes,
    paragraphs. Drive's HTML import turns these into real Docs headings and
    lists, which a Markdown upload would not do reliably."""
    lines = str(markdown or "").splitlines()
    out = [f"<html><head><meta charset='utf-8'><title>{escape(title)}</title></head><body>"]
    in_list = False
    for raw in lines:
        line = raw.rstrip()
        bullet = re.match(r"^\s*[-*]\s+(\[[ xX]\]\s+)?(.*)$", line)
        if bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            box = "&#9744; " if bullet.group(1) else ""
            out.append(f"<li>{box}{_inline(bullet.group(2))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif line.strip():
            out.append(f"<p>{_inline(line.strip())}</p>")
    if in_list:
        out.append("</ul>")
    out.append("</body></html>")
    return "\n".join(out)


def _inline(text: str) -> str:
    """Escape, then render **bold** and [label](https://link)."""
    safe = escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\[([^\]]+)\]\((https://[^)\s]+)\)", r'<a href="\2">\1</a>', safe)
    return safe


def create_document(client: DriveClient, title: str, markdown: str) -> dict:
    """Create a Google Doc in the IntelliPlan folder. Returns id/name/url."""
    title = re.sub(r"\s+", " ", str(title or "Study guide")).strip()[:180] or "Study guide"
    folder_id = ensure_folder(client)
    metadata = {"name": title, "mimeType": GOOGLE_DOC, "parents": [folder_id]}
    body, content_type = multipart_body(metadata, markdown_to_html(markdown, title))
    response = client.request(
        "POST", UPLOAD, params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
        data=body, headers={"Content-Type": content_type})
    if response.status_code >= 400:
        raise ProviderError(f"Drive upload failed with HTTP {response.status_code}",
                            response.status_code)
    data = response.json()
    return {"provider": "google_drive", "id": data.get("id"),
            "name": data.get("name") or title, "url": data.get("webViewLink") or ""}

