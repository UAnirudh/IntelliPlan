"""Google Drive: find a student's own documents, read them, write new ones.

Same OAuth client as sign-in and Google Calendar (``GOOGLE_CLIENT_ID``), and
the same redirect URI. Drive is an *incremental* grant on that client -- see
``google_calendar_helper.scopes_for`` -- not a second Google app, because a
second app would mean a second consent screen to get through Google's
verification and a second set of credentials for the owner to manage.

Scopes, and why there are two modes
-----------------------------------
``drive.file`` lets IntelliPlan create files and read the ones it created (or
that the student explicitly opened with it). It is a *non-sensitive* scope:
no Google review. ``drive.readonly`` lets it search and read everything in
the student's Drive, which is what makes "find my notes for this essay"
work -- and it is a *restricted* scope, which means Google verification plus
an annual third-party security assessment (CASA) before more than 100 people
can use it. ``GOOGLE_DRIVE_SCOPE_MODE`` picks between them; see
docs/INTEGRATIONS_SETUP.md for the tradeoff.

The Docs API scope (``documents``) is deliberately not requested. A study
guide is created by uploading HTML to Drive with conversion to a Google Doc,
which ``drive.file`` already covers, so asking for ``documents`` would be one
more line on the consent screen buying nothing.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from html import escape

import requests

from cloud_token_client import ProviderError, TokenClient, TokenExpired, read_capped

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URI = "https://oauth2.googleapis.com/token"

SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
FOLDER = "application/vnd.google-apps.folder"

#: Google-native types have no bytes to download; they are exported. The
#: value is the export format we can read as text.
EXPORT_AS = {
    GOOGLE_DOC: "text/plain",
    GOOGLE_SLIDES: "text/plain",
    GOOGLE_SHEET: "text/csv",
}

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


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def configured() -> bool:
    """True once the owner has switched Drive on.

    The Google client id is already set in production for sign-in, so its
    presence alone cannot mean "Drive is ready": the Drive API also has to be
    enabled in the Cloud project and the scopes added to the consent screen.
    ``GOOGLE_DRIVE_ENABLED`` is the owner saying they have done that. Without
    it the Connect button stays hidden instead of leading to a Google error.
    """
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET")
                and _truthy(os.getenv("GOOGLE_DRIVE_ENABLED")))


def scope_mode() -> str:
    """``"readonly"`` (search the whole Drive) or ``"file"`` (only files
    IntelliPlan created). Anything unrecognised falls back to ``readonly``,
    which is what the feature is for; the owner opts down deliberately."""
    mode = (os.getenv("GOOGLE_DRIVE_SCOPE_MODE") or "readonly").strip().lower()
    return "file" if mode == "file" else "readonly"


def drive_scopes() -> list:
    scopes = [SCOPE_FILE]
    if scope_mode() == "readonly":
        scopes.append(SCOPE_READONLY)
    return scopes


def has_drive_scope(token: dict) -> bool:
    granted = set((token or {}).get("scopes") or [])
    return bool(granted.intersection({SCOPE_FILE, SCOPE_READONLY}))


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


def search_files(client: DriveClient, terms, limit: int = 15) -> list:
    """Candidate files for ``terms``, normalised to the shape the matcher ranks.

    No ``orderBy``: Drive refuses to sort a ``fullText`` query, so recency is
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
    plain text are downloaded (capped) and parsed with the same extractor the
    Canvas attachment path uses. Anything else is skipped without a download.
    """
    from assignment_materials import extract_text, kind_for

    mime = str(item.get("mime") or "")
    path = _file_path(item.get("id"))
    if mime in EXPORT_AS:
        response = client.request("GET", f"{path}/export",
                                  params={"mimeType": EXPORT_AS[mime]}, stream=True)
        with response:
            if response.status_code != 200:
                return ""
            body = read_capped(response, MAX_DOWNLOAD_BYTES)
        if body is None:
            return ""
        text = body.decode("utf-8-sig", errors="replace")
    else:
        kind = kind_for(item.get("name"), mime)
        if not kind:
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
            text = extract_text(body, kind)
        except Exception as exc:
            print(f"[google-drive] could not extract {kind}: {type(exc).__name__}")
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

