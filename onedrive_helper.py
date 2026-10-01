"""Microsoft identity and OneDrive file operations using delegated Graph access."""

from __future__ import annotations

import os
import base64
import hashlib
import secrets
from urllib.parse import urlencode, quote

import requests
from intelliplan.integrations.document_content import CloudDocumentError

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("openid", "profile", "offline_access", "User.Read", "Files.ReadWrite")


def configured() -> bool:
    return bool(os.getenv("MICROSOFT_CLIENT_ID") and os.getenv("MICROSOFT_CLIENT_SECRET")
                and os.getenv("MICROSOFT_REDIRECT_URI"))


def get_auth_url(state: str) -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    params = {
        "client_id": os.environ["MICROSOFT_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": os.environ["MICROSOFT_REDIRECT_URI"],
        "response_mode": "query",
        "scope": " ".join(SCOPES),
        "state": state,
        "prompt": "select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORITY}/authorize?{urlencode(params)}", code_verifier


def _token_request(payload: dict) -> dict:
    response = requests.post(f"{AUTHORITY}/token", data={
        "client_id": os.environ["MICROSOFT_CLIENT_ID"],
        "client_secret": os.environ["MICROSOFT_CLIENT_SECRET"],
        "scope": " ".join(SCOPES),
        **payload,
    }, timeout=15)
    response.raise_for_status()
    return response.json()


def exchange_code(code: str, code_verifier: str | None = None) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": os.environ["MICROSOFT_REDIRECT_URI"],
    }
    if code_verifier:
        payload["code_verifier"] = code_verifier
    return _token_request(payload)


def refresh_token(token: dict) -> dict:
    refreshed = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": token.get("refresh_token", ""),
    })
    return {**token, **refreshed,
            "refresh_token": refreshed.get("refresh_token") or token.get("refresh_token", "")}


def _headers(token: dict, extra: dict | None = None) -> dict:
    access_token = token.get("access_token")
    if not access_token:
        raise CloudDocumentError("OneDrive is not connected. Please reconnect it.")
    return {"Authorization": f"Bearer {access_token}", **(extra or {})}


def graph_get(token: dict, path: str, params: dict | None = None) -> dict:
    response = requests.get(f"{GRAPH}{path}", params=params, headers=_headers(token), timeout=20)
    response.raise_for_status()
    return response.json()


def profile(token: dict) -> dict:
    return graph_get(token, "/me", {"$select": "displayName,mail,userPrincipalName"})


def search_files(token: dict, query: str = "") -> list[dict]:
    if query.strip():
        escaped = query.strip().replace("'", "''")[:100]
        path = f"/me/drive/root/search(q='{quote(escaped, safe='')}')"
    else:
        path = "/me/drive/root/children"
    result = graph_get(token, path, {"$select": "id,name,file,folder,webUrl,eTag,size,lastModifiedDateTime", "$top": "100"})
    files = result.get("value", [])
    next_url = result.get("@odata.nextLink")
    while next_url and len(files) < 300:
        response = requests.get(next_url, headers=_headers(token), timeout=20)
        response.raise_for_status()
        result = response.json()
        files.extend(result.get("value", []))
        next_url = result.get("@odata.nextLink")
    return files[:300]


def get_file(token: dict, item_id: str) -> dict:
    response = graph_get(token, f"/me/drive/items/{quote(item_id, safe='')}", {
        "$select": "id,name,file,folder,webUrl,eTag,size,lastModifiedDateTime",
    })
    if response.get("folder"):
        raise CloudDocumentError("Choose a file, not a folder.")
    return response


def download_file(token: dict, item_id: str, etag: str | None = None) -> tuple[dict, bytes]:
    info = get_file(token, item_id)
    if int(info.get("size") or 0) > 2 * 1024 * 1024:
        raise CloudDocumentError("This file is larger than IntelliPlan can import (2 MB).")
    response = requests.get(
        f"{GRAPH}/me/drive/items/{quote(item_id, safe='')}/content",
        headers=_headers(token, {"If-Match": etag} if etag else None),
        timeout=30, stream=True,
    )
    if response.status_code == 412:
        response.close()
        raise RuntimeError("This OneDrive file changed. Reload it before saving to avoid overwriting newer edits.")
    try:
        response.raise_for_status()
        from intelliplan.integrations.document_content import read_limited_response
        content = read_limited_response(response)
    except Exception:
        response.close()
        raise
    return info, content


def replace_text_file(token: dict, item_id: str, content: str, etag: str) -> dict:
    info = get_file(token, item_id)
    suffix = (info.get("name") or "").lower().rsplit(".", 1)[-1]
    if suffix not in {"txt", "md", "csv"}:
        raise CloudDocumentError("OneDrive editing is enabled for text, Markdown, and CSV files. Word documents can be imported as study context.")
    response = requests.put(
        f"{GRAPH}/me/drive/items/{quote(item_id, safe='')}/content",
        data=content.encode("utf-8"),
        headers=_headers(token, {"Content-Type": "text/plain; charset=utf-8", "If-Match": etag}),
        timeout=30,
    )
    if response.status_code == 412:
        response.close()
        raise RuntimeError("This OneDrive file changed. Reload it before saving to avoid overwriting newer edits.")
    response.raise_for_status()
    return response.json()
