"""Calendar sockets time out and bearer reads cannot change Graph destinations."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from threading import Event, Thread
from unittest.mock import Mock

from google.auth.exceptions import TransportError
from google.oauth2.credentials import Credentials
import httplib2
import pytest

import google_calendar_helper as google
import outlook_calendar_helper as outlook


def test_google_uses_isolated_timed_transports_and_bundled_discovery():
    first, first_creds = google.get_calendar_service({'token': 'first-synthetic-token'})
    second, second_creds = google.get_calendar_service({'token': 'second-synthetic-token'})
    try:
        assert first._http.timeout == second._http.timeout == 15
        assert first._http is not second._http
        assert first._http.http is not second._http.http
        assert first._http.credentials is first_creds
        assert second._http.credentials is second_creds
        assert first_creds.token != second_creds.token
    finally:
        first.close()
        second.close()


def test_rejected_google_token_is_refreshed_at_most_once():
    service, creds = google.get_calendar_service({'token': 'synthetic-token'})
    creds.before_request = Mock()
    creds.refresh = Mock()
    service._http.http.request = Mock(return_value=(httplib2.Response({'status': '401'}), b'{}'))
    try:
        response, _ = service._http.request('https://www.googleapis.com/calendar/v3/freeBusy', 'POST')
        assert response.status == 401
        assert creds.refresh.call_count == 1
        assert service._http.http.request.call_count == 2
    finally:
        service.close()


@contextmanager
def stalled_server():
    """Loopback-only server accepts a request then withholds its response."""
    release, received = Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.set()
            release.wait(2)

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/', received
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('refresh', [False, True])
def test_real_google_transport_times_out_api_and_credential_refresh(monkeypatch, refresh):
    # Shorten only the fixture; the separate construction test asserts 15s.
    monkeypatch.setattr(google, 'CALENDAR_HTTP_TIMEOUT_SECONDS', 0.1)
    with stalled_server() as (url, received):
        if refresh:
            creds = Credentials(token='expired-synthetic-token', refresh_token='synthetic-refresh',
                                token_uri=url, client_id='synthetic-id', client_secret='synthetic-secret',
                                expiry=datetime.utcnow() - timedelta(hours=1))
            monkeypatch.setattr(google, 'Credentials', lambda **kwargs: creds)
        service, _ = google.get_calendar_service({'token': 'synthetic-token'})
        # Environment proxies must never receive this disposable loopback fixture.
        service._http.http.proxy_info = None
        try:
            with pytest.raises((socket.timeout, TransportError)):
                service._http.request(url)
            assert received.is_set()
        finally:
            service.close()


@pytest.mark.parametrize('url', [
    'https://other.example/v1.0/me',
    'https://graph.microsoft.com.evil.example/v1.0/me',
    'https://graph.microsoft.com:444/v1.0/me',
    'https://user:password@graph.microsoft.com/v1.0/me',
    'https://graph.microsoft.com/v1.0evil/me',
    'http://graph.microsoft.com/v1.0/me',
    '//other.example/v1.0/me',
    'https://graph.microsoft.com/v1.0/me#fragment',
    'https://graph.microsoft.com/v1.0/\\other.example',
    'https://graph.microsoft.com/v1.0/me\n',
    'https://graph.microsoft.com:invalid/v1.0/me',
    '/../beta/me',
    'https://graph.microsoft.com/v1.0/%2e%2e/beta/me',
    'https://graph.microsoft.com/v1.0/%2E%2e%2fbeta/me',
    'https://graph.microsoft.com/v1.0/%5cother.example',
    'https://graph.microsoft.com/v1.0/me%0a',
    None,
])
def test_graph_rejects_untrusted_urls_before_attaching_bearer(monkeypatch, url):
    send = Mock(side_effect=AssertionError('untrusted URL must not be requested'))
    monkeypatch.setattr(outlook.requests, 'get', send)
    with pytest.raises(ValueError, match='invalid URL'):
        outlook.graph_get({'access_token': 'synthetic-private-token'}, url)
    send.assert_not_called()


def test_valid_paging_url_remains_opaque_and_does_not_follow_redirects(monkeypatch):
    url = outlook.GRAPH + '/me/calendarView?$skiptoken=a%2Bb%2F%3D&$top=250'
    reply = Mock(status_code=200)
    reply.json.return_value = {'value': []}
    send = Mock(return_value=reply)
    monkeypatch.setattr(outlook.requests, 'get', send)
    assert outlook.graph_get({'access_token': 'synthetic-token'}, url) == {'value': []}
    send.assert_called_once_with(url, params=None, headers=outlook._headers({'access_token': 'synthetic-token'}),
                                 timeout=15, allow_redirects=False)


@pytest.mark.parametrize('status', [301, 302, 307, 308])
def test_graph_redirect_is_an_error_without_reading_response_body(monkeypatch, status):
    reply = Mock(status_code=status)
    monkeypatch.setattr(outlook.requests, 'get', Mock(return_value=reply))
    with pytest.raises(ValueError, match='unexpected redirect'):
        outlook.graph_get({'access_token': 'synthetic-token'}, '/me/calendarView')
    reply.json.assert_not_called()


def test_best_effort_outlook_stops_without_a_sixth_page_request(monkeypatch):
    fetch = Mock(return_value={'value': [], '@odata.nextLink': outlook.GRAPH + '/me/calendarView?$skip=1'})
    monkeypatch.setattr(outlook, 'graph_get', fetch)
    assert outlook.busy_minutes_by_date({}, date(2026, 10, 16)) == {}
    assert fetch.call_count == 5


@pytest.mark.parametrize('strict', [False, True])
def test_calendar_view_rejects_invalid_paging_before_fetching_it(monkeypatch, strict):
    fetch = Mock(return_value={'value': [], '@odata.nextLink': outlook.GRAPH + 'evil/me'})
    monkeypatch.setattr(outlook, 'graph_get', fetch)
    if strict:
        with pytest.raises(ValueError, match='availability is incomplete'):
            outlook.busy_minutes_by_date({}, date(2026, 10, 16), require_complete=True)
    else:
        assert outlook.busy_minutes_by_date({}, date(2026, 10, 16)) == {}
    fetch.assert_called_once()
