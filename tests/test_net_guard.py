"""Refusing to fetch URLs that point back inside our own network.

Three login flows take a URL from whoever is at the keyboard and then make a
server-side request to it, all three unauthenticated. Without a check on the
target that is a server-side request forgery primitive: paste
``http://169.254.169.254/latest/meta-data/`` and IntelliPlan fetches cloud
credentials; paste ``http://127.0.0.1:6379/`` and it probes the app server's
own services. Even with the body withheld, the difference between "could
not reach" and "returned an error (403)" is a working port scanner.

These tests use literal addresses and a stub resolver, so nothing here
depends on DNS or on the network.
"""

from __future__ import annotations

import pytest

import net_guard


def resolving_to(monkeypatch, *addresses):
    """Pin getaddrinfo so a hostname test needs no DNS."""
    monkeypatch.setattr(
        net_guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (addr, 0)) for addr in addresses])


# ── Addresses that must never be fetched ─────────────────────────────


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.ics",
    "http://127.0.0.1:80/a.ics",
    "https://[::1]/a.ics",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://[fd00::1]/a.ics",                     # unique-local v6
    "http://10.0.0.5/a.ics",
    "http://172.16.4.2/a.ics",
    "http://192.168.1.1/a.ics",
    "http://0.0.0.0/a.ics",
    "http://[::ffff:127.0.0.1]/a.ics",            # loopback wearing a v6 hat
])
def test_internal_addresses_are_refused(url):
    assert net_guard.resolves_to_public_host(url) is False


def test_a_public_address_is_allowed():
    assert net_guard.resolves_to_public_host("https://93.184.216.34/a.ics") is True


# ── Schemes and ports ────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://93.184.216.34/a",
    "ftp://93.184.216.34/a.ics",
    "data:text/calendar,BEGIN:VCALENDAR",
])
def test_only_http_and_https_are_fetched(url):
    assert net_guard.resolves_to_public_host(url) is False


def test_a_non_web_port_is_refused_even_on_a_public_host():
    """Otherwise a URL field scans ports on any host that will answer."""
    assert net_guard.resolves_to_public_host("http://93.184.216.34:6379/") is False


def test_the_default_port_is_inferred_from_the_scheme():
    assert net_guard.resolves_to_public_host("https://93.184.216.34/a") is True
    assert net_guard.resolves_to_public_host("http://93.184.216.34/a") is True


def test_a_malformed_port_is_refused_rather_than_raising():
    assert net_guard.resolves_to_public_host("https://example.test:notaport/a") is False


def test_nonsense_is_refused():
    for url in ("", "   ", "not a url", "https://"):
        assert net_guard.resolves_to_public_host(url) is False


# ── Hostnames ────────────────────────────────────────────────────────


def test_a_hostname_resolving_to_loopback_is_refused(monkeypatch):
    """The attack that beats a string blocklist: a name you control, an A
    record pointing at 127.0.0.1."""
    resolving_to(monkeypatch, "127.0.0.1")
    assert net_guard.resolves_to_public_host("https://evil.test/a.ics") is False


def test_one_bad_address_among_good_ones_refuses_the_whole_host(monkeypatch):
    """Which address a fetch picks is not ours to predict, so any private
    answer disqualifies the name."""
    resolving_to(monkeypatch, "93.184.216.34", "127.0.0.1")
    assert net_guard.resolves_to_public_host("https://mixed.test/a.ics") is False


def test_a_hostname_resolving_publicly_is_allowed(monkeypatch):
    resolving_to(monkeypatch, "93.184.216.34")
    assert net_guard.resolves_to_public_host("https://school.test/a.ics") is True


def test_an_unresolvable_hostname_is_refused(monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", boom)
    assert net_guard.resolves_to_public_host("https://nowhere.test/a.ics") is False


def test_an_empty_resolution_is_refused(monkeypatch):
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda *a, **k: [])
    assert net_guard.resolves_to_public_host("https://empty.test/a.ics") is False


# ── require_public_url ───────────────────────────────────────────────


def test_require_public_url_returns_a_safe_url(monkeypatch):
    resolving_to(monkeypatch, "93.184.216.34")
    assert net_guard.require_public_url("https://ok.test/a") == "https://ok.test/a"


def test_require_public_url_raises_on_an_internal_one():
    with pytest.raises(net_guard.BlockedURL):
        net_guard.require_public_url("http://169.254.169.254/")


def test_pinned_stream_connects_to_the_validated_address_and_streams(monkeypatch):
    calls = []

    def resolve(host, port, *args, **kwargs):
        calls.append((host, port))
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    class Raw:
        status = 200
        headers = {"Content-Length": "5"}
        reason = "OK"

        def stream(self, chunk_size, decode_content=True):
            yield b"hello"

        def close(self):
            pass

    pools = []

    class Pool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.request_args = None
            self.closed = False
            pools.append(self)

        def request(self, *args, **kwargs):
            self.request_args = (args, kwargs)
            return Raw()

        def close(self):
            self.closed = True

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(net_guard.urllib3, "HTTPSConnectionPool", Pool)

    with net_guard.pinned_https_stream("https://school.test/files/1?ticket=x", "GET",
                                      timeout=(5, 12)) as response:
        assert response.status_code == 200
        assert b"".join(response.iter_content(chunk_size=5)) == b"hello"

    assert calls == [("school.test", 443)]
    assert pools[0].kwargs["host"] == "93.184.216.34"
    assert pools[0].kwargs["server_hostname"] == "school.test"
    assert pools[0].request_args[1]["preload_content"] is False
    assert pools[0].request_args[1]["redirect"] is False
    assert pools[0].closed


def test_pinned_stream_rejects_a_private_answer_before_connecting(monkeypatch):
    resolving_to(monkeypatch, "93.184.216.34", "169.254.169.254")
    monkeypatch.setattr(net_guard.urllib3, "HTTPSConnectionPool",
                        lambda **kwargs: pytest.fail("must not connect"))
    with pytest.raises(net_guard.BlockedURL):
        with net_guard.pinned_https_stream("https://school.test/file", "GET"):
            pytest.fail("must not return a response")
