"""Opt-in, deployment-wide network boundary for an isolated school pilot.

This is defense in depth, not a substitute for a Canvas test-only identity,
network firewall, provider settings, or school approval. Never enable on the
shared production deployment: the restrictions apply to every user and job.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


class PilotBlocked(requests.exceptions.RequestException):
    """A pilot request was refused before transmission. Contains no secrets."""


def enabled():
    value = os.getenv('SCHOOL_PILOT', '').strip().lower()
    if value not in {'', '0', 'false', '1', 'true'}:
        raise PilotBlocked('Invalid SCHOOL_PILOT configuration.')
    return value in {'1', 'true'}


def configuration():
    """Validate a single HTTPS origin and numeric course, with no defaults."""
    parsed = urlsplit(os.getenv('SCHOOL_PILOT_COURSE_URL', ''))
    match = re.fullmatch(r'/courses/([1-9][0-9]*)/?', parsed.path)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment or not match):
        raise PilotBlocked('Set SCHOOL_PILOT_COURSE_URL to one HTTPS Canvas course URL.')
    return parsed.hostname.lower(), match.group(1)


def provider_allowed(provider):
    if not enabled():
        return True
    configuration()
    # These are explicit operator attestations, NOT verification of Groq's console.
    return (provider == 'groq'
            and os.getenv('SCHOOL_PILOT_AI', 'off') == 'groq'
            and os.getenv('SCHOOL_PILOT_GROQ_ZDR_CONFIRMED') == '1'
            and os.getenv('SCHOOL_PILOT_CONTRACT_APPROVED') == '1')


def target(method, url):
    """Return (approved URL, wrap single course as list); deny everything else."""
    if not enabled():
        return str(url), False
    host, course = configuration()
    parsed = urlsplit(str(url))
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment):
        raise PilotBlocked('Pilot requires an approved HTTPS destination.')
    method = method.upper()
    if parsed.hostname == 'api.groq.com':
        if (provider_allowed('groq') and method == 'POST'
                and parsed.path == '/openai/v1/chat/completions' and not parsed.query):
            return str(url), False
        raise PilotBlocked('Pilot external AI is disabled or this endpoint is restricted.')
    if parsed.hostname != host:
        raise PilotBlocked('Destination is outside the school pilot.')
    if parsed.path == '/login/oauth2/token' and method in {'POST', 'DELETE'}:
        return str(url), False
    if method != 'GET':
        raise PilotBlocked('Pilot Canvas data access is read-only.')
    path = parsed.path
    wrap = path == '/api/v1/courses'
    if wrap:
        path = f'/api/v1/courses/{course}'
    root = f'/api/v1/courses/{course}'
    if not re.fullmatch(re.escape(root) + r'(?:/assignments(?:/[1-9][0-9]*)?|/assignment_groups|/students/submissions)?', path):
        raise PilotBlocked('Canvas resource is outside the approved pilot course or endpoint set.')
    # Reject unexpected filters, enrollment/teacher expansions and user impersonation.
    query = parse_qsl(parsed.query, keep_blank_values=True)
    allowed_keys = {'per_page', 'page', 'include[]', 'enrollment_state', 'student_ids[]'}
    safe_includes = {'total_scores', 'submission', 'assignments', 'assignment'}
    clean = []
    for key, value in query:
        if key not in allowed_keys:
            raise PilotBlocked('Unsupported Canvas query in school pilot.')
        if key == 'include[]' and value not in safe_includes:
            continue
        if key == 'student_ids[]':
            if value != 'self':
                raise PilotBlocked('Pilot may read only the test account submission.')
            continue
        if wrap and key in {'page', 'per_page', 'enrollment_state'}:
            continue
        clean.append((key, value))
    if path.endswith('/students/submissions'):
        clean.append(('student_ids[]', 'self'))
    return urlunsplit(('https', host, path, urlencode(clean), '')), wrap


def install():
    """Guard requests and httpx sends, including pagination and SDK fallback.

    Check each outgoing request at send time, even when SDK clients predate
    installation. Do not follow redirects in pilot mode. Calls using other
    transports still require a deployment firewall (documented in the runbook).
    """
    if getattr(requests.Session.send, '_school_pilot', False):
        return
    original = requests.Session.send

    def send(self, request, **kwargs):
        url, wrap = target(request.method, request.url)
        if not enabled():
            return original(self, request, **kwargs)
        if any(key.lower() in {'x-http-method-override', 'x-method-override'} for key in request.headers):
            raise PilotBlocked('Method overrides are disabled in school pilot.')
        request.url = url
        kwargs['allow_redirects'] = False
        response = original(self, request, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise PilotBlocked('Redirects are disabled in school pilot.')
        if wrap and response.status_code == 200:
            data = response.json()
            if not isinstance(data, dict) or str(data.get('id')) != configuration()[1]:
                response.close()
                raise PilotBlocked('Canvas returned unexpected pilot course metadata.')
            response._content = json.dumps([data]).encode('utf-8')
            response.headers.pop('Link', None)
            response.headers.pop('Content-Encoding', None)
            response.headers['Content-Length'] = str(len(response._content))
        return response

    send._school_pilot = True
    requests.Session.send = send
    try:
        import httpx
    except ImportError:
        return
    original_sync = httpx.Client.send
    original_async = httpx.AsyncClient.send

    def check_httpx(request):
        if enabled():
            # Canvas currently uses requests. Do not silently offer an untested
            # second Canvas transport with different list/redirect semantics.
            if request.url.host != 'api.groq.com':
                raise PilotBlocked('Only approved Groq text requests may use httpx in pilot.')
            target(request.method, str(request.url))

    def sync_send(self, request, **kwargs):
        check_httpx(request)
        if enabled():
            kwargs['follow_redirects'] = False
        response = original_sync(self, request, **kwargs)
        if enabled() and 300 <= response.status_code < 400:
            response.close()
            raise PilotBlocked('Redirects are disabled in school pilot.')
        return response

    async def async_send(self, request, **kwargs):
        check_httpx(request)
        if enabled():
            kwargs['follow_redirects'] = False
        response = await original_async(self, request, **kwargs)
        if enabled() and 300 <= response.status_code < 400:
            await response.aclose()
            raise PilotBlocked('Redirects are disabled in school pilot.')
        return response

    httpx.Client.send = sync_send
    httpx.AsyncClient.send = async_send
