"""Release checks must not accept healthy traffic from the previous deployment."""
import io
import json
from http.client import IncompleteRead
from urllib.error import URLError

import pytest

import App
from scripts import wait_for_deployment as rollout

REVISION = 'a' * 40


def ready(**changes):
    return dict(revision=REVISION, status='ok', migration_done=True, users_schema_ok=True, **changes)


def response(data):
    stream = io.BytesIO(json.dumps(data).encode())
    stream.status = 200
    return stream


def fake_time(monkeypatch):
    now = [0]
    monkeypatch.setattr(rollout.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(rollout.time, 'sleep', lambda duration: now.__setitem__(0, now[0] + duration))


@pytest.mark.parametrize('data', [
    {'revision': 'b' * 40, 'status': 'ok', 'migration_done': True, 'users_schema_ok': True},
    {'status': 'ok', 'migration_done': True, 'users_schema_ok': True},
    {'revision': REVISION, 'status': 'ok', 'migration_done': False, 'users_schema_ok': True},
    {'revision': REVISION, 'status': 'ok', 'migration_done': True, 'users_schema_ok': False},
    [],
])
def test_previous_or_unhealthy_deployment_cannot_pass(monkeypatch, data):
    fake_time(monkeypatch)
    monkeypatch.setattr(rollout, 'urlopen', lambda *a, **kw: response(data))
    assert rollout.wait_for_deployment(REVISION, timeout=2, interval=1) is False


def test_wait_recovers_from_network_failure_and_old_revision(monkeypatch):
    fake_time(monkeypatch)
    outcomes = [URLError('not ready'), response({'revision': 'b' * 40}), response(ready())]
    def fetch(request, **kwargs):
        assert request.get_header('Cache-control') == 'no-cache'
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(rollout, 'urlopen', fetch)
    assert rollout.wait_for_deployment(REVISION, timeout=4, interval=1) is True
    assert outcomes == []


def test_invalid_json_does_not_pass(monkeypatch):
    fake_time(monkeypatch)
    monkeypatch.setattr(rollout, 'urlopen', lambda *a, **kw: io.BytesIO(b'bad response'))
    assert rollout.wait_for_deployment(REVISION, timeout=2, interval=1) is False


def test_truncated_response_recovers_within_wait(monkeypatch):
    fake_time(monkeypatch)
    class Truncated(io.BytesIO):
        status = 200
        def read(self, *args):
            raise IncompleteRead(b'{', 100)
    outcomes = [Truncated(), response(ready())]
    monkeypatch.setattr(rollout, 'urlopen', lambda *a, **kw: outcomes.pop(0))
    assert rollout.wait_for_deployment(REVISION, timeout=3, interval=1) is True
    assert outcomes == []


@pytest.mark.parametrize('revision', ['', 'short', 'B' * 40])
def test_invalid_expected_revision_is_rejected(revision):
    with pytest.raises(ValueError):
        rollout.wait_for_deployment(revision)


@pytest.mark.parametrize('value', [0, -1, float('inf'), float('nan')])
def test_unbounded_wait_is_rejected(value):
    with pytest.raises(ValueError):
        rollout.wait_for_deployment(REVISION, timeout=value)


@pytest.mark.parametrize('revision,expected', [(REVISION, REVISION), ('bad', None), ('', None)])
def test_public_health_reports_valid_commit_without_cache(monkeypatch, revision, expected):
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', revision)
    monkeypatch.setattr(App, '_existing_columns', lambda table: {'id', 'email', 'password_hash',
                                                                'referral_code', 'referred_by_id'})
    with App.app.test_request_context('/health'):
        result = App.health_check()
        assert result.json['revision'] == expected
        assert result.headers['Cache-Control'] == 'no-store'
