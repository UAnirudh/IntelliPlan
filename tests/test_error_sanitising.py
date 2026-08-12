"""API error messages must not leak internals.

Around 45 handlers returned ``str(e)`` straight to the caller. For a
hand-written ``ValueError("Due date must be in the future")`` that is
exactly right. For a database error it is a schema dump: SQLAlchemy's
``str()`` contains the failing SQL statement and the table's column list,
and several of these endpoints are unauthenticated.

The fix keeps short human-sounding text and replaces anything that looks
like machinery, so the useful half of the behaviour survives.
"""

from __future__ import annotations

import re

import pytest
import sqlalchemy.exc as sa_exc

import App as app_module
from App import API_ERROR_MESSAGES, safe_error_message

GENERIC = API_ERROR_MESSAGES["generic"]

#: If any of these reach a client, the sanitiser has failed.
FORBIDDEN = re.compile(
    r"select\s|insert\s|update\s|delete\s|\bcolumn\b|traceback|site-packages|"
    r"sqlalchemy|psycopg|sqlite3|password=|token=|secret=|/usr/|file \"",
    re.IGNORECASE,
)


# ── Opaque by type ────────────────────────────────────────────────────


def test_database_errors_are_never_returned_verbatim():
    exc = sa_exc.OperationalError(
        "SELECT users.id, users.email, users.utc_offset_minutes FROM users",
        {},
        Exception("no such column: users.utc_offset_minutes"),
    )
    assert safe_error_message(exc) == GENERIC


@pytest.mark.parametrize("exc", [
    KeyError("token"),
    AttributeError("'NoneType' object has no attribute 'id'"),
    TypeError("unsupported operand"),
    ImportError("No module named 'secretlib'"),
    OSError("[Errno 2] No such file or directory: '/srv/app/.env'"),
])
def test_internal_exception_types_are_replaced(exc):
    assert safe_error_message(exc) == GENERIC


# ── Opaque by content ─────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    'Traceback (most recent call last): File "/usr/lib/app.py", line 3',
    "connection failed: password=hunter2",
    "SELECT * FROM users WHERE email = 'a@b.com'",
    "could not find column notification_kinds in table users",
    "/usr/local/lib/python3.11/site-packages/sqlalchemy/engine.py",
])
def test_leaky_text_is_replaced_even_from_a_harmless_type(text):
    assert safe_error_message(Exception(text)) == GENERIC


def test_overlong_messages_are_replaced():
    assert safe_error_message(Exception("x" * 400)) == GENERIC


def test_empty_and_none_fall_back():
    assert safe_error_message(Exception("")) == GENERIC
    assert safe_error_message(None) == GENERIC


# ── The half worth keeping ────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Due date must be in the future",
    "Canvas token rejected. Check Settings.",
    "That schedule name is already taken",
    "Password must be at least 8 characters",
])
def test_useful_validation_messages_survive(text):
    assert safe_error_message(Exception(text)) == text


def test_a_custom_fallback_is_respected():
    assert safe_error_message(KeyError("x"), "Notion is not connected.") == "Notion is not connected."


def test_only_the_first_line_is_returned():
    out = safe_error_message(Exception("Bad input\nsecond line with detail"))
    assert out == "Bad input"


def test_output_is_length_capped():
    assert len(safe_error_message(Exception("a" * 250), limit=50)) <= 50


# ── No handler still returns a raw exception ──────────────────────────


def test_no_handler_returns_str_of_an_exception():
    """Guards the whole sweep, not one call site.

    A new handler written the old way reintroduces the vulnerability
    silently, so this reads the source rather than trusting review.
    """
    source = open(app_module.__file__, encoding="utf-8").read()
    offenders = re.findall(r'"(?:message|error)"\s*:\s*str\(\s*(?:e|_e|ex|exc)\s*\)', source)
    assert not offenders, f"{len(offenders)} handler(s) still return raw exception text"


# ── End to end ────────────────────────────────────────────────────────


def test_an_unauthenticated_endpoint_does_not_leak_on_failure():
    """/extension/login is reachable by anyone; its 500 used to carry SQL."""
    app_module.app.config["TESTING"] = False
    client = app_module.app.test_client()
    response = client.post(
        "/extension/login",
        json={"email": "nobody@invalid.test", "password": "wrong"},
    )
    assert response.status_code in (400, 401, 500)
    body = response.get_data(as_text=True)
    assert not FORBIDDEN.search(body), body[:200]
