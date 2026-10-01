"""Least-privilege Google Drive and Docs operations for selected study files."""

from __future__ import annotations

import json
from urllib.parse import quote

import requests

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
