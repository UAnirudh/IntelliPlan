"""OneDrive through Microsoft Graph: search, read, and create documents.

Reuses the Outlook app registration (``MICROSOFT_CLIENT_ID`` and friends,
read through ``outlook_calendar_helper``) and its redirect URI. The token is
a *separate* grant with its own scope set, stored in its own row: Microsoft
refresh tokens are scoped to what was consented, and keeping Files access
off the calendar token means disconnecting one never breaks the other.

Scope choice: ``Files.ReadWrite`` rather than ``Files.Read`` plus
``Files.ReadWrite.AppFolder``. The app-folder scope would confine new study
guides to ``Apps/IntelliPlan``, a folder students do not know exists; a
plain ``IntelliPlan`` folder at the root of their OneDrive is where they will
look. Neither needs tenant-admin consent for a personal account; a school
tenant can still require it (see docs/INTEGRATIONS_SETUP.md).
"""

from __future__ import annotations

import os
import re
from io import BytesIO
from urllib.parse import quote

import requests

import outlook_calendar_helper as outlook
from cloud_token_client import ProviderError, TokenClient, TokenExpired, read_capped

GRAPH = outlook.GRAPH
SCOPES = ("openid", "profile", "offline_access", "User.Read", "Files.ReadWrite")
FOLDER_NAME = "IntelliPlan"
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 12000


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def configured() -> bool:
    """Registration present *and* the owner has switched OneDrive on.

    The Microsoft client id may already be set for Outlook. OneDrive needs
    the Files permission added to that registration first, so it waits for
    ``ONEDRIVE_ENABLED`` rather than offering a button that ends on a
    Microsoft consent error.
    """
    return outlook.configured() and _truthy(os.getenv("ONEDRIVE_ENABLED"))


def get_auth_url(state: str) -> str:
    return outlook.get_auth_url(state, scopes=SCOPES)


def exchange_code(code: str) -> dict:
    return outlook.exchange_code(code, scopes=SCOPES)


class OneDriveClient(TokenClient):
    access_key = "access_token"

    def _refresh(self, token):
        try:
            fresh = outlook._token_request(
                {"grant_type": "refresh_token", "refresh_token": token.get("refresh_token", "")},
                SCOPES)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", 0)
            raise TokenExpired(f"Microsoft refresh rejected ({status})") from None
        return fresh


# ── Search ────────────────────────────────────────────────────────────


def search_path(text: str) -> str:
    """``/me/drive/root/search(q='…')`` with the term safely embedded.

    OData string literals escape a single quote by doubling it, and the whole
    thing is then percent-encoded because it sits in the URL path. Getting
    either wrong turns "Romeo & Juliet's balcony" into a 400.
    """
    term = re.sub(r"\s+", " ", str(text or "")).strip()[:120]
    if not term:
        raise ValueError("a search term is required")
    literal = term.replace("'", "''")
    return f"/me/drive/root/search(q='{quote(literal, safe='')}')"


def search_files(client: OneDriveClient, text: str, limit: int = 15) -> list:
    """Files (not folders) matching ``text`` in name or content."""
    data = client.json("GET", GRAPH + search_path(text), params={
        "$top": str(max(1, min(50, int(limit or 15)))),
        "$select": "id,name,file,folder,size,lastModifiedDateTime,webUrl,parentReference",
    })
    out = []
    for item in data.get("value") or []:
        if not isinstance(item, dict) or not item.get("id") or item.get("folder"):
            continue
        out.append({
            "provider": "onedrive",
            "id": str(item["id"]),
            "name": str(item.get("name") or "Untitled")[:200],
            "mime": str((item.get("file") or {}).get("mimeType") or ""),
            "modified": str(item.get("lastModifiedDateTime") or ""),
            "url": str(item.get("webUrl") or ""),
            "size": int(item.get("size") or 0),
            # Needed to fetch content for items shared from someone else's
            # drive; /me/drive/items/{id} would 404 on those.
            "drive_id": str((item.get("parentReference") or {}).get("driveId") or ""),
        })
    return out


# ── Reading ───────────────────────────────────────────────────────────


def _item_path(item: dict) -> str:
    item_id = str(item.get("id") or "")
    drive_id = str(item.get("drive_id") or "")
    safe = re.compile(r"[A-Za-z0-9!_\-\.]{3,200}")
    if not safe.fullmatch(item_id) or (drive_id and not safe.fullmatch(drive_id)):
        raise ProviderError("invalid OneDrive item id")
    if drive_id:
        return f"{GRAPH}/drives/{drive_id}/items/{item_id}"
    return f"{GRAPH}/me/drive/items/{item_id}"


def fetch_text(client: OneDriveClient, item: dict, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Plain text of one OneDrive file, or ``""`` when it is not cheap to read.

    ``/content`` answers with a 302 to a short-lived pre-authenticated
    download URL on another host. ``requests`` follows it and, because the
    host changes, drops our Authorization header on the way -- which is
    exactly right: that URL needs no token and should never see one.
    """
    from assignment_materials import extract_text, kind_for

    kind = kind_for(item.get("name"), item.get("mime"))
    if not kind:
        return ""
    if item.get("size") and int(item["size"]) > MAX_DOWNLOAD_BYTES:
        return ""
    response = client.request("GET", _item_path(item) + "/content", stream=True)
    with response:
        if response.status_code != 200:
            return ""
        body = read_capped(response, MAX_DOWNLOAD_BYTES)
    if body is None:
        return ""
    try:
        text = extract_text(body, kind)
    except Exception as exc:
        print(f"[onedrive] could not extract {kind}: {type(exc).__name__}")
        return ""
    return re.sub(r"\s+", " ", text or "").strip()[:max_chars]


# ── Writing ───────────────────────────────────────────────────────────


def _safe_filename(title: str, ext: str) -> str:
    # OneDrive rejects " * : < > ? / \ | and names ending in a dot or space.
    name = re.sub(r'[\\/:*?"<>|#%]+', " ", str(title or "Study guide"))
    name = re.sub(r"\s+", " ", name).strip(" .")[:120] or "Study guide"
    return f"{name}.{ext}"


def markdown_to_docx(markdown: str, title: str = "") -> bytes | None:
    """A real Word document from the study-guide Markdown, via python-docx
    (already a dependency for reading .docx). None if it is unavailable, in
    which case the caller uploads the Markdown as-is."""
    try:
        from docx import Document
    except Exception:
        return None
    doc = Document()
    for raw in str(markdown or "").splitlines():
        line = raw.rstrip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        bullet = re.match(r"^\s*[-*]\s+(\[[ xX]\]\s+)?(.*)$", line)
        if heading:
            doc.add_heading(_plain(heading.group(2)), level=len(heading.group(1)))
        elif bullet:
            prefix = "☐ " if bullet.group(1) else ""
            doc.add_paragraph(prefix + _plain(bullet.group(2)), style="List Bullet")
        elif line.strip():
            doc.add_paragraph(_plain(line.strip()))
    if title:
        doc.core_properties.title = title[:200]
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def _plain(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return re.sub(r"\[([^\]]+)\]\((https://[^)\s]+)\)", r"\1 (\2)", text)


def upload_path(filename: str) -> str:
    """Path-addressed upload into /IntelliPlan, creating the folder implicitly.

    Graph creates missing parent folders on a path-based PUT, so there is no
    separate "make the folder" round trip. ``conflictBehavior=rename`` makes a
    second guide for the same assignment "Name 1.docx" instead of silently
    overwriting the one the student may have written in.
    """
    return (f"{GRAPH}/me/drive/root:/{quote(FOLDER_NAME)}/{quote(filename)}:/content"
            "?@microsoft.graph.conflictBehavior=rename")


def create_document(client: OneDriveClient, title: str, markdown: str) -> dict:
    """Upload a study guide as .docx (or .md) into /IntelliPlan."""
    body = markdown_to_docx(markdown, title)
    if body is not None:
        filename = _safe_filename(title, "docx")
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        filename = _safe_filename(title, "md")
        body = str(markdown or "").encode("utf-8")
        content_type = "text/markdown"
    # Simple upload is limited to 250 MB; a study guide is a few kilobytes.
    response = client.request("PUT", upload_path(filename), data=body,
                              headers={"Content-Type": content_type})
    if response.status_code >= 400:
        raise ProviderError(f"OneDrive upload failed with HTTP {response.status_code}",
                            response.status_code)
    data = response.json()
    return {"provider": "onedrive", "id": data.get("id"),
            "name": data.get("name") or filename, "url": data.get("webUrl") or ""}
