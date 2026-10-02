"""Serve a database migration notice before Flask opens a session."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from typing import Any


_NOTICE = (
    b"<!doctype html><html lang=en><meta charset=utf-8>"
    b"<meta name=viewport content='width=device-width,initial-scale=1'>"
    b"<title>IntelliPlan maintenance</title>"
    b"<body style='font:18px system-ui;max-width:36rem;margin:15vh auto;padding:1rem'>"
    b"<h1>IntelliPlan is briefly unavailable</h1>"
    b"<p>We are moving the database. Your saved work is safe. Please try again in a few minutes.</p>"
    b"</body></html>"
)
_HEALTH = b'{"status":"maintenance"}'


class MigrationMaintenance:
    """Intercept requests before Flask-Session can write to the old database."""

    def __init__(self, app: Callable[..., Iterable[bytes]]) -> None:
        self.app = app

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        if os.getenv("INTELLIPLAN_MAINTENANCE") != "1":
            return self.app(environ, start_response)

        is_health = environ.get("PATH_INFO") == "/health"
        body = _HEALTH if is_health else _NOTICE
        headers = [
            ("Content-Type", "application/json" if is_health else "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if not is_health:
            headers.append(("Retry-After", "180"))
        start_response("200 OK" if is_health else "503 Service Unavailable", headers)
        return [] if environ.get("REQUEST_METHOD") == "HEAD" else [body]
