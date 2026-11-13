"""Network-level proofs for an isolated, synthetic Canvas pilot. No live calls."""
import json

import pytest
import requests
from requests.adapters import BaseAdapter

import school_pilot as pilot

ORIGIN = 'https://lakesideschool.instructure.com'


@pytest.fixture(autouse=True)
def pilot_env(monkeypatch):
    monkeypatch.setenv('SCHOOL_PILOT', '1')
    monkeypatch.setenv('SCHOOL_PILOT_COURSE_URL', ORIGIN + '/courses/4716')
    monkeypatch.delenv('SCHOOL_PILOT_AI', raising=False)
    monkeypatch.delenv('SCHOOL_PILOT_GROQ_ZDR_CONFIRMED', raising=False)
    monkeypatch.delenv('SCHOOL_PILOT_CONTRACT_APPROVED', raising=False)
    pilot.install()


class Capture(BaseAdapter):
    def __init__(self, payload=None, status=200, headers=None):
        self.calls = []
        self.payload = payload if payload is not None else {'id': 4716, 'name': 'Synthetic pilot'}
        self.status = status
        self.headers = headers or {}

    def send(self, request, **kwargs):
        self.calls.append(request)
        response = requests.Response()
        response.status_code = self.status
        response._content = json.dumps(self.payload).encode()
        response.headers.update(self.headers)
        response.request = request
        response.url = request.url
        return response

    def close(self):
        pass


def connection(adapter):
    client = requests.Session()
    client.trust_env = False
    client.mount('https://', adapter)
    client.mount('http://', adapter)
    return client


def test_course_enumeration_is_rewritten_before_network():
    adapter = Capture(headers={'Link': '<https://example.test/leak>; rel="next"'})
    response = connection(adapter).get(ORIGIN + '/api/v1/courses',
        params={'include[]': ['teachers', 'total_scores'], 'per_page': 100})
    assert adapter.calls[0].url == ORIGIN + '/api/v1/courses/4716?include%5B%5D=total_scores'
    assert response.json() == [{'id': 4716, 'name': 'Synthetic pilot'}]
    assert 'Link' not in response.headers


@pytest.mark.parametrize('path', [
    '/api/v1/courses/4717/assignments', '/api/v1/courses/47160',
    '/api/v1/courses/4716/students', '/api/v1/courses/4716/enrollments',
    '/api/v1/users/self/courses', '/api/v1/users/self/upcoming_events',
    '/api/v1/files/123', '/api/v1/courses/4716/../4717/assignments',
    '/api/v1/courses/4716%2fassignments', '/api/v1/courses/4716/assignments?as_user_id=9',
    '/api/v1/courses/4716/students/submissions?student_ids[]=123',
])
def test_out_of_scope_reads_never_reach_transport(path):
    adapter = Capture()
    with pytest.raises(pilot.PilotBlocked):
        connection(adapter).get(ORIGIN + path)
    assert adapter.calls == []


@pytest.mark.parametrize('url', [
    'https://other.instructure.com/api/v1/courses/4716',
    'https://lakesideschool.instructure.com.evil.test/api/v1/courses/4716',
    'http://lakesideschool.instructure.com/api/v1/courses/4716',
    'https://user:password@lakesideschool.instructure.com/api/v1/courses/4716',
    'https://generativelanguage.googleapis.com/v1beta/models',
    'https://api.anthropic.com/v1/messages', 'https://api.groq.com/openai/v1/chat/completions',
])
def test_unapproved_destinations_never_reach_transport(url):
    adapter = Capture()
    with pytest.raises(pilot.PilotBlocked):
        connection(adapter).post(url, json={'private': 'synthetic'})
    assert adapter.calls == []


def test_submission_fallback_is_always_self_only():
    url, wrap = pilot.target('GET', ORIGIN + '/api/v1/courses/4716/students/submissions')
    assert url.endswith('?student_ids%5B%5D=self')
    assert not wrap


def test_pagination_cannot_escape_course():
    import canvas_helper
    adapter = Capture(payload=[{'id': 1}], headers={
        'Link': f'<{ORIGIN}/api/v1/courses/999/assignments?page=2>; rel="next"'})
    client = connection(adapter)
    from unittest.mock import patch
    with patch.object(canvas_helper.requests, 'get', client.get):
        rows = canvas_helper._get_list(ORIGIN + '/api/v1/courses/4716/assignments', {})
    assert rows == [{'id': 1}]
    assert len(adapter.calls) == 1


def test_redirect_is_not_followed():
    adapter = Capture(status=302, headers={'Location': ORIGIN + '/api/v1/courses/999'})
    with pytest.raises(pilot.PilotBlocked):
        connection(adapter).get(ORIGIN + '/api/v1/courses/4716')
    assert len(adapter.calls) == 1


def test_writes_denied_before_transport():
    adapter = Capture()
    with pytest.raises(pilot.PilotBlocked):
        connection(adapter).post(ORIGIN + '/api/v1/courses/4716/assignments', json={})
    assert adapter.calls == []


def test_missing_or_malformed_configuration_denies(monkeypatch):
    for url in ['', ORIGIN + '/courses/', ORIGIN + '/courses/4716?x=1']:
        monkeypatch.setenv('SCHOOL_PILOT_COURSE_URL', url)
        with pytest.raises(pilot.PilotBlocked):
            pilot.target('GET', ORIGIN + '/api/v1/courses')


def test_groq_requires_both_attestations_and_only_text(monkeypatch):
    monkeypatch.setenv('SCHOOL_PILOT_AI', 'groq')
    assert not pilot.provider_allowed('groq')
    monkeypatch.setenv('SCHOOL_PILOT_GROQ_ZDR_CONFIRMED', '1')
    assert not pilot.provider_allowed('groq')
    monkeypatch.setenv('SCHOOL_PILOT_CONTRACT_APPROVED', '1')
    assert pilot.provider_allowed('groq')
    assert not pilot.provider_allowed('gemini')
    for endpoint in ['batches', 'files', 'fine_tunings', 'audio/transcriptions']:
        with pytest.raises(pilot.PilotBlocked):
            pilot.target('POST', 'https://api.groq.com/openai/v1/' + endpoint)
    assert pilot.target('POST', 'https://api.groq.com/openai/v1/chat/completions')[0].endswith('chat/completions')


def test_httpx_sdk_denied_before_transport():
    httpx = pytest.importorskip('httpx')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(pilot.PilotBlocked):
            client.post('https://api.groq.com/openai/v1/chat/completions', json={})
    assert calls == []


def test_normal_deployment_unchanged(monkeypatch):
    monkeypatch.delenv('SCHOOL_PILOT', raising=False)
    adapter = Capture(payload=[{'id': 1}, {'id': 2}])
    response = connection(adapter).get('https://other.test/api/v1/courses')
    assert len(response.json()) == 2
    assert pilot.provider_allowed('gemini')


def test_method_override_denied():
    adapter = Capture()
    with pytest.raises(pilot.PilotBlocked):
        connection(adapter).get(ORIGIN + '/api/v1/courses/4716',
                                headers={'X-HTTP-Method-Override': 'DELETE'})
    assert adapter.calls == []


def test_approved_httpx_text_and_redirect_denial(monkeypatch):
    httpx = pytest.importorskip('httpx')
    monkeypatch.setenv('SCHOOL_PILOT_AI', 'groq')
    monkeypatch.setenv('SCHOOL_PILOT_GROQ_ZDR_CONFIRMED', '1')
    monkeypatch.setenv('SCHOOL_PILOT_CONTRACT_APPROVED', '1')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': 'https://other.test/'})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(pilot.PilotBlocked):
            client.post('https://api.groq.com/openai/v1/chat/completions', json={})
    assert len(calls) == 1


def test_async_sdk_denial_never_sends():
    import asyncio
    httpx = pytest.importorskip('httpx')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(pilot.PilotBlocked):
                await client.post('https://api.groq.com/openai/v1/chat/completions', json={})
    asyncio.run(run())
    assert calls == []


def test_provider_chain_and_media_deny_even_anonymous_calls():
    from intelliplan.learning.enforcement import filter_providers, permitted_processing
    assert filter_providers([('groq', 'model'), ('claude', 'model')]) == []
    assert not permitted_processing('groq')
    assert not permitted_processing('fish_audio')


def test_analytics_disabled_even_when_consent_gate_allows(monkeypatch):
    import analytics
    monkeypatch.setattr(analytics, '_gate', lambda: True)
    assert not analytics._permitted()
