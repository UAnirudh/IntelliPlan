"""Home Access Center (HAC) helper — mirrors the surface of studentvue_helper.

Why this exists
---------------
Home Access Center is the student/parent portal of eSchoolPLUS (SunGard, now
PowerSchool). It is what most large Texas districts run -- Katy, Cy-Fair,
Round Rock, Frisco, Klein, Conroe -- and plenty outside Texas. A student in
one of those districts had no way into IntelliPlan except smart-paste or the
browser extension, because StudentVUE, Canvas and Schoology are the only
district logins we spoke.

HAC has no API. There is no SOAP service like StudentVUE's and no token like
Canvas's; every open-source client (HomeAccessCenterAPI, hac-kit, haccli,
gradiate-api, the old QuickHAC apps) does the same thing a browser does:

  1. GET  /HomeAccess/Account/LogOn   -> anti-forgery token + cookies
  2. POST /HomeAccess/Account/LogOn   -> the login form
  3. GET  /HomeAccess/Content/Student/Assignments.aspx  -> the "Classwork"
     page, one ``div.AssignmentClass`` per course, each holding a table of
     assignments and (usually) a table of grading categories.

This module does exactly that and nothing more, then reshapes the result into
the dicts studentvue_helper returns (``get_assignments``, ``get_grades``,
``get_gradebook_detail``, ``get_missing_assignments``, ``get_courses``) so
every route that already knows how to show a StudentVUE student's work shows
a HAC student's work unchanged.

What it deliberately does not do
--------------------------------
* It never logs a password, and never puts one in an exception message. The
  only place a password travels is the POST body to the district's own host.
* It does not follow the district anywhere else. The district URL comes from
  whoever is at the keyboard and the fetch happens server-side, so every hop
  is checked with net_guard (the same rule the calendar-feed import uses) and
  redirects are followed by hand. A redirect *off* the district's HAC host
  before we have logged in is how single-sign-on districts hand the student
  to Google / Microsoft / ClassLink -- that is reported as ``sso_required``,
  not as a wrong password, because no password we could send would work.
* It does not add a dependency. BeautifulSoup is not in requirements.txt, and
  HAC's markup is regular enough (ASP.NET WebForms grids) that the stdlib
  ``html.parser`` plus a forty-line tree is sufficient.
"""

from __future__ import annotations

import copy
import hashlib
import re
import threading
import time
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

import net_guard
from studentvue_helper import _compute_priority, _estimate_minutes

LOGIN_PATH = "/HomeAccess/Account/LogOn"
ASSIGNMENTS_PATH = "/HomeAccess/Content/Student/Assignments.aspx"

#: Seconds for each request. HAC servers are district-hosted and some are
#: slow at the start of a grading period, but a login page that has not
#: answered in 15s is not going to.
TIMEOUT = 15

#: Some districts sit HAC behind a WAF that rejects the python-requests
#: default agent outright (HTTP 403), which would read as "could not reach".
#: Every working open-source client sends a browser agent for this reason.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 IntelliPlan-HAC/1.0"
)

#: The field set every open-source HAC client posts. ``Database`` is the
#: district database selector (10 on single-district installs, which is all
#: of them in practice); the ``SCKTY...CustomEnabled`` flags and the empty
#: ``tempUN``/``tempPW`` decoys are rendered by the LogOn view and a server
#: that expects them is happier with them present. These are *defaults*:
#: whatever the district's own login form actually renders (its hidden
#: inputs, its selected Database option) overrides them, so a district whose
#: form differs is posted what a browser would post.
DEFAULT_LOGIN_FIELDS = {
    "SCKTY00328510CustomEnabled": "True",
    "SCKTY00436568CustomEnabled": "True",
    "Database": "10",
    "VerificationOption": "UsernamePassword",
    "tempUN": "",
    "tempPW": "",
}

#: Attribute values that identify the *logged-out* LogOn view. Form field
#: names, not prose: page text is whatever a district types into a banner,
#: and an assignment titled "Welcome to AP Lang" once made a text marker
#: report a good login as a bad password in another client.
_LOGGED_OUT_FIELDS = ("LogOnDetails.UserName", "LogOnDetails_UserName")

#: Markers of a login page that hands off to an identity provider instead of
#: taking a password. Checked only when the page has *no* password form.
_SSO_MARKERS = (
    "saml", "/sso", "sso/", "singlesignon", "single sign-on", "single sign on",
    "externallogin", "adfs", "login.microsoftonline.com", "accounts.google.com",
    "classlink", "clever.com", "okta", "openid",
)

#: Score codes eSchoolPLUS districts use in place of a number. They are
#: district-configurable, so this is the common set rather than a closed one;
#: an unknown code is shown as-is and treated as not yet graded.
_MISSING_CODES = {"Z", "M", "MI", "MSG", "MIS", "MISSING", "NS", "NHI"}
_EXCUSED_CODES = {"X", "EX", "EXC", "EXE", "EXM", "EXEMPT", "EXCUSED"}
_INCOMPLETE_CODES = {"I", "INC", "INCOMPLETE"}
_BLANK_SCORES = {"", "-", "--", "N/A", "NA"}

#: Parsed Classwork is cached briefly. The dashboard asks for assignments and
#: then missing work in the same request, and the gradebook asks for grades
#: and then detail: without this each is a fresh login against a district
#: server that did nothing to deserve four logins a page view.
_CACHE_TTL_SECONDS = 90
_CACHE_MAX = 256
_cache: dict = {}
_cache_lock = threading.Lock()

#: Indirection so tests can stand in for DNS. Production always checks.
_host_check = net_guard.resolves_to_public_host

#: Indirection so tests can pin "today" against a fixture's fixed dates.
_today = date.today


class HACError(Exception):
    """Base error. ``status`` matches validate_login's return values."""

    status = "connection_failed"


class HACConnectionError(HACError):
    status = "connection_failed"


class HACInvalidCredentials(HACError):
    status = "invalid_credentials"


class HACSSORequired(HACError):
    status = "sso_required"


# ── District URL ──────────────────────────────────────────────────────


def normalize_district_url(district_url):
    """Accept whatever a student pastes and return the HAC base URL.

    Students paste the address bar, which is anything from ``hac.cfisd.net``
    to ``https://homeaccess.katyisd.org/HomeAccess/Account/LogOn?ReturnUrl=
    %2fHomeAccess%2f``. The return value is the scheme and host (plus any
    path a district mounts HAC under, e.g. ``/hac`` in front of
    ``/HomeAccess``), without ``/HomeAccess`` itself, so endpoint paths can be
    appended. Always https: this URL receives a password.

    Returns "" for input that is not a usable web address.
    """
    raw = (district_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    elif "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not host:
        return ""
    if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
        return ""

    path = parsed.path or ""
    m = re.search(r"/homeaccess(?:/|$)", path, re.I)
    # Anything before /HomeAccess is the district's mount point and must be
    # kept; any other path ("/", "/parents") is not part of HAC's address.
    prefix = path[:m.start()].rstrip("/") if m else ""
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    return f"https://{netloc}{prefix}"


# ── A small HTML tree on the stdlib parser ────────────────────────────


_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []

    def get(self, name, default=""):
        value = self.attrs.get(name)
        return default if value is None else value

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def text(self):
        parts = []
        stack = [self]
        # Iterative walk: a deep district page must not hit recursion limits.
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                parts.append(node)
                continue
            if node.tag in ("script", "style"):
                continue
            stack.extend(reversed(node.children))
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def iter(self):
        stack = [self]
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                continue
            yield node
            stack.extend(reversed([c for c in node.children if not isinstance(c, str)]))

    def find_all(self, tag=None, cls=None, id_contains=None, attr=None):
        out = []
        for node in self.iter():
            if node is self:
                continue
            if tag and node.tag != tag:
                continue
            if cls and cls not in node.classes():
                continue
            if id_contains and id_contains.lower() not in (node.attrs.get("id") or "").lower():
                continue
            if attr and node.attrs.get(attr[0]) != attr[1]:
                continue
            out.append(node)
        return out

    def find(self, **kwargs):
        found = self.find_all(**kwargs)
        return found[0] if found else None

    def cells(self):
        """Direct ``td``/``th`` children of a row, in order."""
        return [c for c in self.children if not isinstance(c, str) and c.tag in ("td", "th")]


class _TreeBuilder(HTMLParser):
    """Build a _Node tree, tolerating the unclosed tags real pages have."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self.stack = [self.root]

    def _close_until(self, names, stop_at=()):
        # Close an implicitly-ended element (a <td> followed by another
        # <td>) without walking out of its table.
        for i in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[i].tag
            if tag in stop_at:
                return
            if tag in names:
                del self.stack[i:]
                return

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("td", "th"):
            self._close_until({"td", "th"}, stop_at=("tr", "table"))
        elif tag == "tr":
            self._close_until({"tr"}, stop_at=("table", "tbody", "thead"))
        elif tag == "option":
            self._close_until({"option"}, stop_at=("select",))
        node = _Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        node = _Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # A stray close tag with nothing open to match is ignored, as a
        # browser would.

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(html):
    builder = _TreeBuilder()
    try:
        builder.feed(html or "")
        builder.close()
    except Exception:
        # html.parser is lenient, but a truncated response can still trip
        # it; whatever was built before that is still the page.
        pass
    return builder.root


# ── Login page reading ────────────────────────────────────────────────


def _has_login_field(doc):
    for node in doc.find_all(tag="input"):
        if node.get("name") in _LOGGED_OUT_FIELDS or node.get("id") in _LOGGED_OUT_FIELDS:
            return True
    return False


def is_logged_out_page(html, url=""):
    """True when ``html`` is HAC's LogOn view rather than a signed-in page.

    A failed login answers 200 with the form rendered again, so the status
    code says nothing; the username input is the tell. The URL is a second
    signal for districts that redirect back to ``LogOn?logonError=true``.
    """
    if "/account/logon" in (urlparse(url or "").path or "").lower():
        return True
    return _has_login_field(parse_html(html))


def looks_like_sso_page(html):
    lowered = (html or "").lower()
    return any(marker in lowered for marker in _SSO_MARKERS)


def extract_verification_token(html):
    """Return the ``__RequestVerificationToken`` hidden input's value, or ""."""
    for node in parse_html(html).find_all(tag="input"):
        if node.get("name") == "__RequestVerificationToken":
            return node.get("value")
    return ""


def _find_login_form(doc):
    """The form that holds the username field, or the page itself when a
    district's skin renders the inputs without a wrapping form."""
    for form in doc.find_all(tag="form"):
        if _has_login_field(form):
            return form
    return doc if _has_login_field(doc) else None


def build_login_payload(login_html, username, password):
    """Build the POST body a browser would send from this LogOn page.

    Starts from the field set the open-source clients send, then overlays
    whatever the district's form actually renders -- its hidden inputs
    (including ``__RequestVerificationToken``), its selected ``Database``
    option, a checked ``VerificationOption`` -- so a district that differs is
    not sent a guess over its own values. Returns ``(payload, action)`` where
    ``action`` is the form's action attribute ("" when absent), or
    ``(None, "")`` when the page has no password form.
    """
    doc = parse_html(login_html)
    form = _find_login_form(doc)
    if form is None:
        return None, ""

    payload = dict(DEFAULT_LOGIN_FIELDS)
    for node in form.find_all(tag="input"):
        name = node.get("name")
        if not name:
            continue
        kind = node.get("type", "text").lower()
        if kind == "hidden":
            payload[name] = node.get("value")
        elif kind in ("radio", "checkbox") and "checked" in node.attrs:
            payload[name] = node.get("value", "on")
    for select in form.find_all(tag="select"):
        name = select.get("name")
        if not name:
            continue
        options = select.find_all(tag="option")
        chosen = next((o for o in options if "selected" in o.attrs), options[0] if options else None)
        if chosen is not None:
            payload[name] = chosen.get("value") or chosen.text()

    # The credentials and the only verification mode we can complete. Set
    # last so nothing in the page can overwrite them.
    payload["VerificationOption"] = "UsernamePassword"
    payload["LogOnDetails.UserName"] = username
    payload["LogOnDetails.Password"] = password
    return payload, (form.get("action") if form.tag == "form" else "")


# ── HTTP ──────────────────────────────────────────────────────────────


def _new_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def _request(session, method, url, *, data=None, before_login=False):
    """Fetch ``url`` following redirects by hand, checking every hop.

    Returns ``(response, final_url)``. A redirect to another host is allowed
    only when it lands on another HAC (``/HomeAccess/`` in the path) -- a
    district moving ``hac.`` to ``homeaccess.`` is ordinary. A redirect off
    HAC before login is an identity-provider hand-off (SSO); after login it
    is simply not a page we can read.
    """
    home_host = urlparse(url).hostname
    checked = set()
    for _ in range(net_guard.MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise HACConnectionError("unsupported redirect")
        if parsed.hostname != home_host and "/homeaccess" not in (parsed.path or "").lower():
            if before_login:
                raise HACSSORequired("redirected to another sign-in host")
            raise HACConnectionError("redirected away from HAC")
        if parsed.hostname not in checked:
            if not _host_check(url):
                raise HACConnectionError("host not reachable")
            checked.add(parsed.hostname)
        try:
            resp = session.request(
                method, url, data=data, timeout=TIMEOUT, allow_redirects=False,
            )
        except requests.RequestException as exc:
            # Only the exception's class name: requests can put the URL in
            # its message, and callers print these.
            raise HACConnectionError(type(exc).__name__) from None
        location = (resp.headers or {}).get("Location")
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            if resp.status_code in (301, 302, 303):
                method, data = "GET", None
            continue
        return resp, url
    raise HACConnectionError("too many redirects")


def _login(session, base_url, username, password):
    """Sign ``session`` in. Raises a HACError subclass on any failure."""
    login_url = base_url + LOGIN_PATH
    resp, final_url = _request(session, "GET", login_url, before_login=True)
    if resp.status_code >= 400:
        raise HACConnectionError(f"login page returned {resp.status_code}")
    html = resp.text or ""

    payload, action = build_login_payload(html, username, password)
    if payload is None:
        # No password form. Either the district signs in only through an
        # identity provider, or this is not a HAC server at all.
        if looks_like_sso_page(html):
            raise HACSSORequired("login page offers only single sign-on")
        raise HACConnectionError("no HAC login form at that address")

    post_url = urljoin(final_url, action) if action else final_url
    if urlparse(post_url).hostname != urlparse(final_url).hostname:
        # A form that posts the password to some other host is not one we
        # submit a student's password to.
        post_url = final_url
    resp, landed = _request(session, "POST", post_url, data=payload, before_login=True)
    if resp.status_code >= 400:
        raise HACConnectionError(f"login returned {resp.status_code}")
    if is_logged_out_page(resp.text or "", landed):
        raise HACInvalidCredentials("login form shown again")
    return landed


def _fetch_classwork_html(district_url, username, password):
    base = normalize_district_url(district_url)
    if not base:
        raise HACConnectionError("no district URL")
    session = _new_session()
    try:
        landed = _login(session, base, username, password)
        # A district that moved hosts during login keeps its session cookie
        # on the new host, so ask the new host for the page.
        origin = urlparse(landed)
        page_base = base
        if origin.hostname and origin.hostname != urlparse(base).hostname:
            page_base = f"{origin.scheme}://{origin.netloc}"
        resp, final_url = _request(session, "GET", page_base + ASSIGNMENTS_PATH)
        if resp.status_code >= 400:
            raise HACConnectionError(f"classwork returned {resp.status_code}")
        html = resp.text or ""
        if is_logged_out_page(html, final_url):
            # The POST looked fine but the session did not stick. Treat as a
            # credential failure: it is what a student can act on.
            raise HACInvalidCredentials("session not established")
        return html
    finally:
        session.close()


# ── Classwork parsing ─────────────────────────────────────────────────


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _to_number(text):
    """"95.00" -> 95.0, "86.00%" -> 86.0, "1,000" -> 1000.0, else None."""
    t = _clean(text).replace(",", "").replace("%", "").strip()
    if re.fullmatch(r"-?\d*\.?\d+", t):
        try:
            return float(t)
        except ValueError:
            return None
    return None


def _iso_date(text):
    """"9/8/2026" or "09/08/2026" (anywhere in the text) -> "2026-09-08"."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return None


def parse_score(raw):
    """Classify a Score cell. Returns ``(value, status, fraction_total)``.

    ``status`` is one of graded / ungraded / missing / excused / incomplete /
    other. HAC scores are numbers ("95.00"), blank for not-yet-graded, a
    fraction on a few skins ("9/10"), or a district code ("Z" for a zero
    that counts, "X" for excused). ``fraction_total`` is the denominator of
    a fraction score, for skins whose Total Points column is empty.
    """
    text = _clean(raw).replace("*", "").strip()
    if text.upper() in _BLANK_SCORES:
        return None, "ungraded", None
    number = _to_number(text)
    if number is not None:
        return number, "graded", None
    frac = re.fullmatch(r"(-?\d*\.?\d+)\s*/\s*(\d*\.?\d+)", text)
    if frac:
        return float(frac.group(1)), "graded", float(frac.group(2))
    code = text.upper()
    if code in _MISSING_CODES:
        return None, "missing", None
    if code in _EXCUSED_CODES:
        return None, "excused", None
    if code in _INCOMPLETE_CODES:
        return None, "incomplete", None
    return None, "other", None


def _column_key(text):
    # "/ Maximum Points" -> "maximum points"; "Student's Points" -> "students points"
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", (text or "").lower())).strip()


def _read_grid(table):
    """Data rows of a HAC grid, each a {column key: cell node} dict.

    Keyed by header text rather than position: districts hide or reorder
    the quick-view columns (Weight, Percentage), and a positional parser
    that guesses wrong fails silently with plausible-looking numbers.
    """
    if table is None:
        return []
    rows = table.find_all(tag="tr")
    header = next((r for r in rows if "sg-asp-table-header-row" in r.classes()), None)
    if header is None and rows:
        header = rows[0]
    if header is None:
        return []
    keys = [_column_key(c.text()) for c in header.cells()]
    # Summary rows ("Total Points:") carry no data-row class. Only filter on
    # the class when the skin uses it at all.
    classed = any("sg-asp-table-data-row" in r.classes() for r in rows)
    out = []
    for row in rows:
        if row is header:
            continue
        if classed and "sg-asp-table-data-row" not in row.classes():
            continue
        cells = row.cells()
        if not cells:
            continue
        out.append({keys[i] if i < len(keys) else f"col{i}": cell for i, cell in enumerate(cells)})
    return out


def _cell(row, *names, fallback_index=None):
    for name in names:
        if name in row:
            return row[name]
    if fallback_index is not None:
        values = list(row.values())
        if fallback_index < len(values):
            return values[fallback_index]
    return None


def _detail(title_attr, label):
    m = re.search(rf"{re.escape(label)}:\s*(\S*)", title_attr or "")
    return m.group(1) if m else ""


def _parse_assignments(table):
    out = []
    for row in _read_grid(table):
        name_cell = _cell(row, "assignment", "description", "name", fallback_index=2)
        link = name_cell.find(tag="a") if name_cell is not None else None
        title = _clean(link.text() if link is not None else "") or \
            _clean((name_cell.text() if name_cell is not None else "").replace("*", ""))
        if not title:
            continue
        details = link.get("title") if link is not None else ""

        def text_of(*names, idx=None):
            node = _cell(row, *names, fallback_index=idx)
            return _clean(node.text()) if node is not None else ""

        score_raw = text_of("score", "earned points", idx=4)
        score, status, frac_total = parse_score(score_raw)
        total = _to_number(text_of("total points", "possible points", "max points", idx=5))
        if total is None:
            total = frac_total
        if total is None:
            total = _to_number(_detail(details, "Max Points"))
        out.append({
            "title": title,
            "category": text_of("category", idx=3),
            "due_date": _iso_date(text_of("date due", "due date", idx=0)),
            "assigned_date": _iso_date(text_of("date assigned", "assigned date", idx=1)),
            "score_raw": score_raw.replace("*", "").strip(),
            "score": score,
            "status": status,
            "total_points": total,
            "weight": _to_number(text_of("weight")),
            "percent": _to_number(text_of("percentage", "percent")),
            "extra_credit": _detail(details, "Extra Credit") not in ("", "N"),
            "can_be_dropped": _detail(details, "Can Be Dropped") == "Y",
        })
    return out


def _parse_categories(table):
    out = []
    for row in _read_grid(table):
        def text_of(*names):
            node = _cell(row, *names)
            return _clean(node.text()) if node is not None else ""

        name = text_of("category")
        if not name:
            continue
        out.append({
            "name": name,
            "points": _to_number(text_of("students points")),
            "max_points": _to_number(text_of("maximum points")),
            "percent": _to_number(text_of("percent")),
            "weight": _to_number(text_of("category weight", "weight")),
            "category_points": _to_number(text_of("category points")),
        })
    return out


def _split_heading(heading):
    """"1001000 - 4    English Literature" -> ("1001000", "4", "English Literature")."""
    m = re.match(r"^(\S+)\s+-\s+(\S+)\s+(.+)$", heading or "")
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    return "", "", heading or ""


def parse_classwork(html):
    """Parse HAC's Classwork page (Content/Student/Assignments.aspx).

    Returns one dict per course::

        {"course", "code", "section", "heading", "average", "exact_average",
         "last_updated", "assignments": [...], "categories": [...]}

    A page with no ``div.AssignmentClass`` returns [] -- that is also what a
    student with no classes this grading period sees, so it is not an error.
    """
    doc = parse_html(html)
    courses = []
    for block in doc.find_all(tag="div", cls="AssignmentClass"):
        heading_node = block.find(tag="a", cls="sg-header-heading")
        if heading_node is None:
            heading_node = next(
                (n for n in block.find_all(cls="sg-header-heading")
                 if "lblhdraverage" not in (n.get("id") or "").lower()),
                None,
            )
        heading = _clean(heading_node.text() if heading_node is not None else "")
        if not heading:
            continue
        code, section, name = _split_heading(heading)

        avg_node = block.find(id_contains="lblHdrAverage")
        if avg_node is None:
            avg_node = next(
                (n for n in block.find_all(tag="span", cls="sg-header-heading")
                 if "sg-right" in n.classes()),
                None,
            )
        avg_text = _clean(avg_node.text() if avg_node is not None else "")
        # The last number, not the first: some skins label the header
        # "Cycle 2 Average 91.50", and the cycle is not the grade.
        avg_numbers = re.findall(r"-?\d+(?:\.\d+)?", avg_text)
        exact_node = block.find(id_contains="lblOverallAverage")
        updated_node = block.find(id_contains="lblLastUpdDate")

        assign_table = block.find(tag="table", id_contains="dgCourseAssignments")
        if assign_table is None:
            assign_table = next(
                (t for t in block.find_all(tag="table", cls="sg-asp-table")
                 if "categor" not in (t.get("id") or "").lower()),
                None,
            )
        cat_table = block.find(tag="table", id_contains="dgCourseCategories")

        courses.append({
            "course": name,
            "code": code,
            "section": section,
            "heading": heading,
            "average": float(avg_numbers[-1]) if avg_numbers else None,
            "exact_average": _to_number(exact_node.text()) if exact_node is not None else None,
            "last_updated": _iso_date(updated_node.text()) if updated_node is not None else None,
            "assignments": _parse_assignments(assign_table),
            "categories": _parse_categories(cat_table),
        })
    return courses


# ── Cache + public API (studentvue_helper-compatible shapes) ──────────


def _cache_key(district_url, username, password):
    digest = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    return (normalize_district_url(district_url), (username or "").strip().lower(), digest)


def _load_classwork(district_url, username, password):
    key = _cache_key(district_url, username, password)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return copy.deepcopy(hit[1])
    courses = parse_classwork(_fetch_classwork_html(district_url, username, password))
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            for stale in [k for k, v in _cache.items() if v[0] <= now] or list(_cache)[:1]:
                _cache.pop(stale, None)
        _cache[key] = (now + _CACHE_TTL_SECONDS, courses)
    return copy.deepcopy(courses)


def clear_cache():
    with _cache_lock:
        _cache.clear()


def validate_login(district_url, username, password):
    """Validate HAC credentials without mistaking a host failure for a password failure.

    Returns "ok", "invalid_credentials", "connection_failed" or
    "sso_required" -- the last because a district that signs students in
    through Google or ClassLink will refuse every password we send, and
    telling the student "wrong password" would send them to reset a
    password that is fine.
    """
    base = normalize_district_url(district_url)
    if not base:
        return "connection_failed"
    session = _new_session()
    try:
        _login(session, base, username, password)
    except HACError as exc:
        return exc.status
    except Exception:
        # An unexpected parsing or transport failure is not evidence about
        # the password.
        return "connection_failed"
    finally:
        session.close()
    return "ok"


def test_login(district_url, username, password):
    """Compatibility wrapper for callers that only need a boolean."""
    return validate_login(district_url, username, password) == "ok"


def _course_percentage(course):
    value = course.get("exact_average")
    if value is None:
        value = course.get("average")
    return round(value, 2) if value is not None else None


def _letter_for(pct):
    """HAC reports numbers, not letters; the grade views expect both. The
    common Texas/US scale, since the district's own scale is not on the
    page."""
    if pct is None:
        return ""
    if pct >= 90:
        return "A"
    if pct >= 80:
        return "B"
    if pct >= 70:
        return "C"
    if pct >= 60:
        return "D"
    return "F"


def _display_score(a):
    status = a.get("status")
    if status == "graded":
        return a.get("score_raw") or ""
    if status == "missing":
        return "Missing"
    if status == "excused":
        return "Excused"
    if status == "incomplete":
        return "Incomplete"
    if status == "ungraded":
        return "Not Graded"
    return a.get("score_raw") or ""


def get_courses(district_url, username, password):
    seen, out = set(), []
    for c in _load_classwork(district_url, username, password):
        name = c.get("course")
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name})
    return out


def get_assignments(district_url, username, password):
    """Upcoming and recently-due work that has no grade yet.

    Same rules as studentvue_helper.get_assignments: graded work is done,
    anything due more than 14 days ago is history, and priority and time
    come from the same shared functions so a HAC assignment ranks exactly
    like the StudentVUE assignment it would have been.
    """
    today = _today()
    out = []
    for course in _load_classwork(district_url, username, password):
        for a in course.get("assignments", []):
            if a.get("status") not in ("ungraded", "incomplete"):
                continue
            due = a.get("due_date")
            if not due or not a.get("title"):
                continue
            try:
                due_d = datetime.strptime(due, "%Y-%m-%d").date()
            except ValueError:
                continue
            days = (due_d - today).days
            if days < -14:
                continue
            points_possible = a.get("total_points")
            if points_possible is None:
                points_possible = 60
            title = a["title"]
            category = a.get("category") or ""
            out.append({
                "title": title,
                "course": course.get("course", ""),
                "due_date": due,
                "points_possible": points_possible,
                "priority": _compute_priority(days, points_possible, title),
                # HAC's Classwork grid carries no description, so sizing runs
                # on the title, category and points alone.
                "estimated_time": _estimate_minutes(title, "", category, points_possible),
                "display_score": "",
                "description": "",
                "category": category,
            })
    return sorted(out, key=lambda x: x["due_date"])


def get_grades(district_url, username, password):
    grades = []
    for course in _load_classwork(district_url, username, password):
        pct = _course_percentage(course)
        if pct is None:
            # Mirrors StudentVUE's "N/A" mark: no average yet, no row.
            continue
        grades.append({
            "course": course.get("course", ""),
            "teacher": "",
            "letter": _letter_for(pct),
            "percentage": round(pct, 1),
        })
    return grades


def get_gradebook_detail(district_url, username, password):
    courses = []
    for course in _load_classwork(district_url, username, password):
        pct = _course_percentage(course)
        if pct is None:
            continue
        categories = [{
            "type": cat["name"],
            "weight": cat.get("weight"),
            "points": cat.get("points"),
            "points_possible": cat.get("max_points"),
            # StudentVUE's WeightedPct is the category's contribution to the
            # course grade; HAC calls the same number "Category Points".
            "weighted_pct": cat.get("category_points"),
            "mark": _letter_for(cat.get("percent")),
        } for cat in course.get("categories", [])]

        assignments = []
        for a in course.get("assignments", []):
            status = a.get("status")
            earned = a.get("score") if status == "graded" else None
            if status == "missing":
                # A HAC missing code counts as a zero in the district's own
                # average, so the what-if calculator must count it too.
                earned = 0.0
            assignments.append({
                "title": a["title"],
                "due_date": a.get("due_date"),
                "points_earned": earned,
                "points_possible": a.get("total_points"),
                "display_score": _display_score(a),
                "graded": status in ("graded", "missing") and a.get("total_points") is not None,
                "category": a.get("category") or "",
                "description": "",
                "is_missing": status == "missing",
            })

        courses.append({
            "course": course.get("course", ""),
            "teacher": "",
            "letter": _letter_for(pct),
            "percentage": pct,
            "categories": categories,
            "assignments": assignments,
        })
    return courses


def get_missing_assignments(district_url, username, password):
    """Missing and low-scored work, in studentvue_helper's shape.

    Flags a district missing code, a zero, or a score under 60% -- the same
    threshold StudentVUE uses -- because that is work worth redoing.
    """
    out = []
    for course in _load_classwork(district_url, username, password):
        for a in course.get("assignments", []):
            status = a.get("status")
            possible = a.get("total_points")
            if status == "missing":
                earned = 0.0
            elif status == "graded":
                earned = a.get("score")
            else:
                continue
            if earned is None or not possible or possible <= 0:
                continue
            if not (status == "missing" or earned == 0 or earned / possible < 0.6):
                continue
            label = "Missing" if earned == 0 else f"{earned:g}/{possible:g}"
            out.append({
                "title": a["title"],
                "course": course.get("course", ""),
                "due_date": a.get("due_date") or "",
                "points_earned": earned,
                "points_possible": possible,
                "display_score": _display_score(a),
                "priority": "High",
                "estimated_time": 60,
                "source": "hac_missing",
                "is_missing": True,
                "score_label": label,
                "color": "#ef4444",
                "difficulty": "Medium",
            })
    return out
