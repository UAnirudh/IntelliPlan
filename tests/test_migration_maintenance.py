from intelliplan.maintenance import MigrationMaintenance


def test_maintenance_skips_the_application_and_sessions(monkeypatch):
    monkeypatch.setenv("INTELLIPLAN_MAINTENANCE", "1")
    calls = []

    def app(environ, start_response):
        calls.append("app")
        start_response("200 OK", [])
        return [b"normal"]

    def start_response(status, headers):
        calls.append((status, dict(headers)))

    response = MigrationMaintenance(app)({"PATH_INFO": "/dashboard", "REQUEST_METHOD": "GET"}, start_response)

    assert calls[0][0] == "503 Service Unavailable"
    assert calls[0][1]["Retry-After"] == "180"
    assert b"saved work is safe" in b"".join(response)
    assert "app" not in calls


def test_health_stays_probeable_during_maintenance(monkeypatch):
    monkeypatch.setenv("INTELLIPLAN_MAINTENANCE", "1")
    statuses = []
    response = MigrationMaintenance(lambda *_: (_ for _ in ()).throw(AssertionError("called")))(
        {"PATH_INFO": "/health", "REQUEST_METHOD": "GET"},
        lambda status, headers: statuses.append(status),
    )
    assert statuses == ["200 OK"]
    assert b"".join(response) == b'{"status":"maintenance"}'


def test_normal_traffic_passes_through(monkeypatch):
    monkeypatch.delenv("INTELLIPLAN_MAINTENANCE", raising=False)
    response = MigrationMaintenance(lambda *_: [b"normal"])(
        {"PATH_INFO": "/dashboard"}, lambda *_: None,
    )
    assert b"".join(response) == b"normal"
