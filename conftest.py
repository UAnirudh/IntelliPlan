"""Test-session environment defaults, applied before any test module runs.

App.py refuses to boot without a real SECRET_KEY, and it is right to: a
fallback string baked into a public repo would let anyone forge session
cookies and password-reset tokens for any account. But that check runs at
*import* time, so a bare `pytest` with the variable unset dies during
collection on the first module that imports App -- and pytest reports that as
a collection error against every module that imports App, which reads as "a
lot of tests are failing" rather than as the one missing variable it is.

pytest imports the rootdir conftest before it imports any test module, so
these land in os.environ in time for App's import-time checks.

setdefault, not assignment: CI passes both variables explicitly and those
values must win. The same goes for anyone pointing the suite at a scratch
Postgres instead of in-memory SQLite.
"""

import os

import pytest

# Ephemeral and test-only. Not a fallback for the app itself -- App.py still
# refuses to start in production without a real one.
os.environ.setdefault("SECRET_KEY", "pytest-ephemeral-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("FLASK_ENV", "testing")
# Unit tests invoke reminder jobs explicitly. A daemon tick must not race
# their shared SQLite connection or fixture teardown.
os.environ.setdefault("NOTIFICATIONS_INPROCESS_CRON", "0")
os.environ.setdefault("FOLLOWTHROUGH_INPROCESS_CRON", "0")


@pytest.fixture(autouse=True)
def finish_welcome_delivery_before_request_continues(monkeypatch):
    """Keep signup delivery inside the lifetime of each disposable fixture.

    SQLite's in-memory StaticPool shares one connection across sessions.
    An unrelated delivery thread closing its session can roll back a test's
    transaction. Dedicated welcome tests still exercise delivery; their
    inline Thread fixture overrides this wrapper.
    """
    import threading

    original_start = threading.Thread.start

    def start(thread, *args, **kwargs):
        result = original_start(thread, *args, **kwargs)
        if thread.name.startswith(("ip-welcome-", "ip-verify-email-")):
            thread.join()
        return result

    monkeypatch.setattr(threading.Thread, "start", start)
