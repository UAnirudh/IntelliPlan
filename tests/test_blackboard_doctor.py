"""The Blackboard configuration doctor.

A deployment can hold eight BLACKBOARD_* variables spanning two protocols,
and the one failure they all produce is the same opaque "invalid client_id"
after the student has signed in. The doctor separates the causes without
anyone having to paste a secret anywhere.
"""

import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "blackboard_doctor",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "blackboard_doctor.py"),
)
doctor = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(doctor)

KEY = "aaaaaaaa-1111-2222-3333-444444444444"
APP_ID = "bbbbbbbb-1111-2222-3333-444444444444"
REDIRECT = "https://intelliplan.tech/api/lms/callback/blackboard"

ALL_VARS = doctor.REST_VARS + doctor.LTI_VARS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """The doctor reads real environment variables; start from nothing."""
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch, **overrides):
    values = {
        "BLACKBOARD_APP_KEY": KEY,
        "BLACKBOARD_APP_SECRET": "secret",
        "BLACKBOARD_APPLICATION_ID": APP_ID,
        "BLACKBOARD_REDIRECT_URI": REDIRECT,
        "BLACKBOARD_SCOPE": "read offline",
    }
    values.update(overrides)
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return {name: (os.getenv(name) or "").strip() for name in ALL_VARS}


def test_a_correct_configuration_reports_no_problems(monkeypatch, capsys):
    values = configure(monkeypatch)
    assert doctor.check_credentials(values) == []


def test_the_fatal_swap_is_reported(monkeypatch, capsys):
    """Application ID in the key slot fails on every Learn instance."""
    values = configure(monkeypatch, BLACKBOARD_APP_KEY=APP_ID)
    problems = doctor.check_credentials(values)
    assert "key equals application id" in problems
    assert "must NOT be the Application ID" in capsys.readouterr().out


def test_the_lti_client_id_sitting_alongside_the_key_is_not_a_problem(monkeypatch, capsys):
    """BLACKBOARD_CLIENT_ID commonly holds the LTI Client ID, which is the
    same value as the Application ID. That is expected, not a misconfiguration
    — the key still wins."""
    values = configure(monkeypatch, BLACKBOARD_CLIENT_ID=APP_ID)
    assert doctor.check_credentials(values) == []
    out = capsys.readouterr().out
    assert "is being ignored" in out
    assert "LTI Client ID and the Application ID" in out


def test_relying_on_the_legacy_alias_is_flagged(monkeypatch, capsys):
    """With no APP_KEY the legacy alias becomes the client_id, which is only
    correct if it holds the key rather than the Application ID."""
    values = configure(monkeypatch, BLACKBOARD_APP_KEY=None,
                       BLACKBOARD_CLIENT_ID=KEY)
    problems = doctor.check_credentials(values)
    assert "key via legacy alias" in problems


def test_a_missing_key_is_fatal(monkeypatch, capsys):
    values = configure(monkeypatch, BLACKBOARD_APP_KEY=None)
    assert "no key" in doctor.check_credentials(values)


def test_a_missing_secret_is_fatal(monkeypatch, capsys):
    values = configure(monkeypatch, BLACKBOARD_APP_SECRET=None)
    assert "no secret" in doctor.check_credentials(values)


def test_a_key_and_secret_from_different_variables_is_flagged(monkeypatch, capsys):
    """A half-migrated deployment: BLACKBOARD_APP_KEY set, but the secret
    still coming from the legacy BLACKBOARD_CLIENT_SECRET. Only correct if
    both came from the same portal application."""
    values = configure(monkeypatch, BLACKBOARD_APP_SECRET=None,
                       BLACKBOARD_CLIENT_SECRET="legacy-secret")
    problems = doctor.check_credentials(values)
    assert "key and secret come from different variables" in problems
    assert "same portal application" in capsys.readouterr().out


def test_a_matched_pair_is_not_flagged(monkeypatch, capsys):
    values = configure(monkeypatch, BLACKBOARD_CLIENT_SECRET="legacy-secret")
    assert "key and secret come from different variables" not in \
        doctor.check_credentials(values)


def test_a_missing_application_id_is_flagged(monkeypatch, capsys):
    """Without it the student gets "contact support" instead of the ID their
    administrator actually needs."""
    values = configure(monkeypatch, BLACKBOARD_APPLICATION_ID=None)
    assert "no application id" in doctor.check_credentials(values)


def test_a_scope_without_offline_is_flagged(monkeypatch, capsys):
    values = configure(monkeypatch, BLACKBOARD_SCOPE="read")
    problems = doctor.check_credentials(values)
    assert "scope missing offline" in problems
    assert "expire in an hour" in capsys.readouterr().out


def test_the_default_scope_is_treated_as_offline(monkeypatch, capsys):
    values = configure(monkeypatch, BLACKBOARD_SCOPE=None)
    assert "scope missing offline" not in doctor.check_credentials(values)


def test_a_redirect_uri_on_the_wrong_path_is_flagged(monkeypatch, capsys):
    values = configure(monkeypatch,
                       BLACKBOARD_REDIRECT_URI="https://intelliplan.tech/oauth")
    assert "redirect uri path" in doctor.check_credentials(values)


def test_lti_values_are_called_out_as_unused(monkeypatch, capsys):
    values = configure(monkeypatch, BLACKBOARD_ISSUER="https://blackboard.com")
    doctor.check_credentials(values)
    out = capsys.readouterr().out
    assert "not LTI" in out
    assert "REST API Integrations" in out


def test_no_secret_value_is_ever_printed(monkeypatch, capsys):
    configure(monkeypatch, BLACKBOARD_APP_SECRET="super-secret-value")
    doctor.report_vars()
    out = capsys.readouterr().out
    assert "super-secret-value" not in out
    assert KEY not in out
    assert "set (18 chars)" in out
