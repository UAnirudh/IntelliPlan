"""A bearer-token HTTP client that renews its own access token.

Google Drive and OneDrive both hand out one-hour access tokens with a
long-lived refresh token beside them. Every call site used to have to
remember to check the expiry, refresh, retry, and then persist whatever came
back -- and the Outlook and Google Calendar paths each did a slightly
different subset of that. One missed persist means the next request starts
from a dead token again, and the student sees "reconnect" for no reason.

So the client owns the whole cycle:

* refresh *before* a request when the stored expiry is close,
* refresh once *after* a 401 (clocks drift, tokens get revoked early),
* call ``on_refresh(token)`` so the caller can write the new token back to
  its encrypted row -- the client never touches the database itself.

Tokens are never logged or put into exception messages. A failed refresh
raises :class:`TokenExpired`, which carries no credential, so the caller can
tell "reconnect this account" apart from "the provider is having a bad day".
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import requests

#: Seconds of slack before expiry at which a token is treated as stale.
#: A request that starts with ten seconds left can easily finish after it.
REFRESH_MARGIN = 90

DEFAULT_TIMEOUT = (5, 20)


class TokenExpired(Exception):
    """The access token is dead and could not be renewed. Reconnect needed."""


class ProviderError(Exception):
    """The provider answered, but not with something we can use."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class TokenClient:
    """Base for provider clients. Subclasses set ``access_key`` and
    implement ``_refresh``."""

    #: Which key in the token dict holds the access token. Google's existing
    #: rows say ``token`` (that is what google-auth serialises); Microsoft's
    #: say ``access_token``. Kept per-provider so stored rows need no rewrite.
    access_key = "access_token"

    def __init__(self, token: dict, on_refresh: Optional[Callable[[dict], None]] = None):
        self.token = dict(token or {})
        self.on_refresh = on_refresh
        self.refreshed = False

    # -- token lifecycle --------------------------------------------------

    def _refresh(self, token: dict) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def _needs_refresh(self) -> bool:
        if not self.token.get(self.access_key):
            return True
        try:
            expires_at = float(self.token.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        # A token without a recorded expiry is used as-is; the 401 retry
        # below catches it if it has in fact lapsed. Refreshing on every
        # call instead would burn a token-endpoint round trip per request.
        return bool(expires_at) and expires_at <= time.time() + REFRESH_MARGIN

    def refresh(self) -> dict:
        if not self.token.get("refresh_token"):
            raise TokenExpired("no refresh token")
        try:
            fresh = self._refresh(self.token)
        except TokenExpired:
            raise
        except Exception as exc:
            # The exception text from a token endpoint can echo the request
            # body, which contains the refresh token. Only the type survives.
            raise TokenExpired(f"refresh failed ({type(exc).__name__})") from None
        if not fresh or not fresh.get(self.access_key):
            raise TokenExpired("refresh returned no access token")
        merged = {**self.token, **fresh}
        # Providers may or may not rotate the refresh token. Dropping the old
        # one when they do not would make this the last refresh that works.
        merged["refresh_token"] = fresh.get("refresh_token") or self.token.get("refresh_token")
        if fresh.get("expires_in"):
            merged["expires_at"] = time.time() + int(fresh.get("expires_in") or 3600)
        self.token = merged
        self.refreshed = True
        if self.on_refresh:
            try:
                self.on_refresh(dict(merged))
            except Exception as exc:
                # Failing to persist is not a reason to fail this request --
                # the in-memory token still works for the rest of it.
                print(f"[cloud-docs] could not persist refreshed token: {type(exc).__name__}")
        return merged

    def headers(self, extra: Optional[dict] = None) -> dict:
        out = {"Authorization": f"Bearer {self.token.get(self.access_key, '')}"}
        if extra:
            out.update(extra)
        return out

    def request(self, method: str, url: str, *, headers: Optional[dict] = None,
                timeout=DEFAULT_TIMEOUT, **kwargs) -> requests.Response:
        """Send one request, renewing the token before or after as needed."""
        if self._needs_refresh():
            self.refresh()
        response = requests.request(method, url, headers=self.headers(headers),
                                    timeout=timeout, **kwargs)
        if response.status_code == 401 and not self.refreshed:
            self.refresh()
            response = requests.request(method, url, headers=self.headers(headers),
                                        timeout=timeout, **kwargs)
        if response.status_code == 401:
            raise TokenExpired("provider rejected the renewed token")
        return response

    def json(self, method: str, url: str, **kwargs) -> dict:
        response = self.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise ProviderError(f"{method} failed with HTTP {response.status_code}",
                                response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise ProviderError("provider returned non-JSON", response.status_code) from None
        if not isinstance(data, dict):
            raise ProviderError("provider returned an unexpected shape", response.status_code)
        return data


def read_capped(response: requests.Response, max_bytes: int) -> Optional[bytes]:
    """The body of a (streamed) response, or None if it exceeds ``max_bytes``.

    Stops reading at the cap rather than downloading a 200 MB lecture video
    to find out it was too big.
    """
    try:
        declared = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > max_bytes:
        return None
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        body.extend(chunk)
        if len(body) > max_bytes:
            return None
    return bytes(body)
