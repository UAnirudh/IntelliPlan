"""Refuse to fetch URLs that point back inside our own network.

Several login flows take a URL from whoever is at the keyboard and then make
a *server-side* request to it: the Canvas token check hits the school's
Canvas, the calendar-feed connect downloads the feed, the Blackboard
preflight probes the institution host. All three are unauthenticated, so
without a check on the target they are a server-side request forgery
primitive -- paste ``http://169.254.169.254/latest/meta-data/`` or an
internal hostname and IntelliPlan fetches it and reports back what it found.

Even when the body never reaches the caller, the *distinction* between the
error messages ("could not reach" versus "returned an error (403)") is a
working port scanner for anything reachable from the app server.

This module is the single place that decision is made, so a new flow that
takes a URL has something to call rather than a precedent to copy wrongly.
"""

from __future__ import annotations

import ipaddress
import ssl
import socket
import urllib.parse

import requests
import urllib3

#: A school's LMS is on the web, on a web port. Allowing arbitrary ports
#: turns a URL field into a port scanner even against public hosts.
ALLOWED_PORTS = {80, 443}

#: Redirects are followed by hand so each hop is checked. A feed that needs
#: more hops than this is misconfigured.
MAX_REDIRECTS = 5


class BlockedURL(Exception):
    """The URL is syntactically fine but must not be fetched."""


def _address_is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 is loopback wearing a v6 hat, and the v6 flags
        # below do not catch it.
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolves_to_public_host(url: str) -> bool:
    """True when ``url`` is http(s), on a web port, and every address behind
    its hostname is publicly routable.

    Every address, not the first: a hostname an attacker controls can resolve
    to one public address and one loopback address, and which one a fetch
    picks is not ours to predict.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False

    if parsed.scheme.lower() not in ("http", "https"):
        return False

    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        # A malformed port ("https://x:notaport/") raises here.
        return False
    if not host:
        return False

    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    if port not in ALLOWED_PORTS:
        return False

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # Unresolvable. The caller reports it as an unreachable host, which
        # is both true and the same advice either way: check the address.
        return False

    return bool(infos) and all(_address_is_public(info[4][0]) for info in infos)


def require_public_url(url: str) -> str:
    """``url`` if it is safe to fetch, otherwise raise :class:`BlockedURL`."""
    if not resolves_to_public_host(url):
        raise BlockedURL(url)
    return url


def pinned_https_request(url: str, method: str, *, headers=None, data=None, timeout=15):
    """Make one HTTPS request to the exact public IPs validated in this call.

    A separate DNS check followed by ``requests`` is vulnerable to rebinding:
    the HTTP client resolves the name a second time. This connects to the
    validated address while retaining the original Host header, TLS SNI and
    certificate hostname verification. Redirects are never followed.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise BlockedURL(url) from None
    if (parsed.scheme.lower() != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443)):
        raise BlockedURL(url)
    host = parsed.hostname
    port = port or 443
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise requests.ConnectionError("The integration host could not be resolved.") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or not all(_address_is_public(address) for address in addresses):
        raise BlockedURL(url)

    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    request_headers = dict(headers or {})
    request_headers["Host"] = parsed.netloc
    last_error = None
    for address in addresses:
        pool = urllib3.HTTPSConnectionPool(
            host=address,
            port=port,
            server_hostname=host,
            assert_hostname=host,
            cert_reqs=ssl.CERT_REQUIRED,
            timeout=urllib3.Timeout(connect=timeout, read=timeout),
        )
        try:
            raw = pool.request(
                method,
                target,
                body=data,
                headers=request_headers,
                redirect=False,
                retries=False,
            )
            response = requests.Response()
            response.status_code = raw.status
            response._content = raw.data
            response.headers = requests.structures.CaseInsensitiveDict(raw.headers)
            response.url = url
            response.reason = raw.reason
            response.encoding = requests.utils.get_encoding_from_headers(response.headers)
            response.request = requests.Request(method, url, headers=request_headers, data=data).prepare()
            return response
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            last_error = exc
        finally:
            pool.close()
    raise requests.ConnectionError("The integration host could not be reached securely.") from last_error
