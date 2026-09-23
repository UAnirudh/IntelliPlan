"""StudentVUE login behavior that must not turn a valid password into a failure."""

from __future__ import annotations

import requests
import pytest

import App
import studentvue_helper


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as test_client:
        yield test_client
    App.limiter.enabled = True


def test_studentvue_request_preserves_password_whitespace(monkeypatch):
    captured = {}

    class Response:
        text = "<ProcessWebServiceRequestResult />"

        def raise_for_status(self):
            return None

    def post(*args, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(studentvue_helper.requests, "post", post)

    studentvue_helper.make_request(
        "https://district-psv.edupoint.com", "student", " password ", "StudentInfo"
    )

    assert "<password> password </password>" in captured["data"]


def test_studentvue_connection_problem_is_not_reported_as_bad_password(client, monkeypatch):
    monkeypatch.setattr(App, "validate_login", lambda *args: "connection_failed")

    response = client.post("/login/studentvue", data={
        "district_url": "https://district-psv.edupoint.com",
        "username": "student",
        "password": "password",
    })

    body = response.get_data(as_text=True)
    assert "Could not reach a StudentVUE service" in body
    assert "Student ID or password was not accepted" not in body


def test_studentvue_login_route_does_not_trim_a_password(client, monkeypatch):
    captured = {}

    def validate(district_url, username, password):
        captured["password"] = password
        return "invalid_credentials"

    monkeypatch.setattr(App, "validate_login", validate)

    client.post("/login/studentvue", data={
        "district_url": "https://district-psv.edupoint.com",
        "username": "student",
        "password": " password ",
    })

    assert captured["password"] == " password "


def test_studentvue_rejects_only_explicit_authentication_failures(monkeypatch):
    monkeypatch.setattr(studentvue_helper, "make_request", lambda *args: "RT_ERROR")
    assert studentvue_helper.validate_login("https://district.example", "student", "password") == "invalid_credentials"

    def unreachable(*args):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(studentvue_helper, "make_request", unreachable)
    assert studentvue_helper.validate_login("https://district.example", "student", "password") == "connection_failed"


def test_studentvue_login_does_not_preselect_another_district(client):
    body = client.get("/login/studentvue").get_data(as_text=True)
    assert 'value="https://wa-nor-psv.edupoint.com"' not in body
    assert 'placeholder="https://district-psv.edupoint.com"' in body
