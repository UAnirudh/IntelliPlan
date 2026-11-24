"""Canvas URL requests stay pinned from guest connection through sync."""

from __future__ import annotations

import pytest
import requests

import App
import canvas_helper


SCHOOL = "https://canvas.example.test"


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    with App.app.test_client() as value:
        yield value


def test_guest_canvas_validation_and_live_sync_use_pinned_https(client, monkeypatch):
    monkeypatch.setattr(App, "_resolves_to_public_host", lambda _url: True)
    monkeypatch.setattr(App, "get_dismissed_titles", lambda: set())
    monkeypatch.setattr(App, "get_test_titles", lambda: set())
    responses = [
        _Response(200, []),
        _Response(200, [{"id": 7, "name": "Biology"}]),
        _Response(200, [{
            "id": 12,
            "course_id": 7,
            "name": "Lab report",
            "due_at": "2099-03-14T12:00:00Z",
            "points_possible": 20,
        }]),
    ]
    calls = []

    def pinned(url, method, *, headers=None, timeout=None):
        calls.append((url, method, headers, timeout))
        return responses.pop(0)

    monkeypatch.setattr(App.net_guard, "pinned_https_request", pinned)

    connected = client.post("/login/canvas", data={
        "canvas_token": "synthetic-token", "canvas_url": SCHOOL,
    })
    assert connected.status_code == 302
    live = client.get("/live")

    assert live.status_code == 200
    assert [call[0] for call in calls] == [
        f"{SCHOOL}/api/v1/courses",
        f"{SCHOOL}/api/v1/courses",
        f"{SCHOOL}/api/v1/courses/7/assignments?per_page=100&include[]=submission",
    ]
    assert all(call[1] == "GET" for call in calls)
    assert all(call[2] == {"Authorization": "Bearer synthetic-token"} for call in calls)
    assert [row["title"] for row in live.get_json()] == ["Lab report"]


def test_canvas_validation_rejects_http_without_network_access(client, monkeypatch):
    monkeypatch.setattr(App, "_resolves_to_public_host", lambda _url: True)
    original = App.net_guard.pinned_https_request
    calls = []

    def pinned(url, method, **kwargs):
        calls.append((url, method))
        return original(url, method, **kwargs)

    monkeypatch.setattr(App.net_guard, "pinned_https_request", pinned)
    response = client.post("/login/canvas", data={
        "canvas_token": "synthetic-token", "canvas_url": "http://canvas.example.test",
    })

    assert response.status_code == 200
    assert "Could not reach" in response.get_data(as_text=True)
    assert calls == [("http://canvas.example.test/api/v1/courses", "GET")]


def test_canvas_transport_errors_keep_the_generic_connection_message(client, monkeypatch):
    monkeypatch.setattr(App, "_resolves_to_public_host", lambda _url: True)

    def unavailable(*_args, **_kwargs):
        raise requests.ConnectionError("private transport detail")

    monkeypatch.setattr(App.net_guard, "pinned_https_request", unavailable)
    response = client.post("/login/canvas", data={
        "canvas_token": "synthetic-token", "canvas_url": SCHOOL,
    })
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Could not reach" in body
    assert "private transport detail" not in body


def test_canvas_pagination_pins_each_same_origin_page_and_stops_cross_origin(monkeypatch):
    class Page(_Response):
        def __init__(self, payload, next_url=None):
            super().__init__(200, payload)
            self.links = {"next": {"url": next_url}} if next_url else {}

    calls = []

    def pinned(url, method, *, headers=None, timeout=None):
        calls.append((url, method))
        if len(calls) == 1:
            return Page([{"id": 1}], "https://canvas.example.test/api/v1/courses?page=2")
        return Page([{"id": 2}], "https://attacker.example.test/collect")

    monkeypatch.setattr(canvas_helper.net_guard, "pinned_https_request", pinned)
    rows = canvas_helper._get_list("https://canvas.example.test/api/v1/courses", {})

    assert rows == [{"id": 1}, {"id": 2}]
    assert calls == [
        ("https://canvas.example.test/api/v1/courses?per_page=100", "GET"),
        ("https://canvas.example.test/api/v1/courses?page=2", "GET"),
    ]


@pytest.mark.parametrize("next_url", [
    "https://canvas.example.test:443/api/v1/courses?page=2",
    "https://canvas.example.test/api/v1/courses?page=2",
])
def test_canvas_pagination_treats_implicit_and_explicit_default_ports_as_same_origin(
        monkeypatch, next_url):
    class Page(_Response):
        def __init__(self, payload, link=None):
            super().__init__(200, payload)
            self.links = {"next": {"url": link}} if link else {}

    calls = []

    def pinned(url, method, *, headers=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            return Page([{"id": 1}], next_url)
        return Page([{"id": 2}])

    monkeypatch.setattr(canvas_helper.net_guard, "pinned_https_request", pinned)
    rows = canvas_helper._get_list("https://canvas.example.test/api/v1/courses", {})

    assert rows == [{"id": 1}, {"id": 2}]
    assert calls == [
        "https://canvas.example.test/api/v1/courses?per_page=100",
        next_url,
    ]
