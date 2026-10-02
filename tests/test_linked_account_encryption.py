"""LMS credentials (district passwords, API secrets) are encrypted at rest."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

import App
import secret_box


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret_box.reset_cache()
    yield
    secret_box.reset_cache()


def test_credentials_are_sealed_and_round_trip(key):
    acct = App.LinkedAccount(user_id=1, name="SV", login_type="studentvue", is_active=True)
    acct.set_credentials({"sv_username": "s1", "sv_password": "hunter2"})
    assert acct.credentials.startswith(secret_box.PREFIX)
    assert "hunter2" not in acct.credentials
    assert acct.get_credentials() == {"sv_username": "s1", "sv_password": "hunter2"}


def test_rows_written_before_encryption_still_read(key):
    acct = App.LinkedAccount(user_id=1, name="SV", login_type="studentvue", is_active=True)
    acct.credentials = json.dumps({"sv_password": "legacy"})
    assert acct.get_credentials() == {"sv_password": "legacy"}


def test_dismissed_set_union_matches_by_normalized_title():
    dismissed = App._DismissedSet(["Lab &amp; Report"])
    merged = dismissed | {"Unit 2 Test"}
    assert "Lab & Report" in merged
    assert "unit 2 test" in merged
    assert "Other" not in merged
    assert "Unit 2 Test" in ({"Unit 2 Test"} | dismissed)
