"""Home Access Center (HAC): login, Classwork parsing and the /login/hac route.

HAC has no API; hac_helper signs in the way a browser does and reads the
Classwork page. None of these tests touch a real district -- district hosts
are not reachable from CI and a test that depended on one would report on
that district's week, not on this code. The HTML under tests/fixtures/hac is
modeled on the markup the open-source HAC clients parse, with the variations
districts really produce (quick-view columns off, no category table, score
codes, SSO-only login pages).

What these pin:
  * a host failure never reads as a wrong password, and an SSO-only district
    never reads as either;
  * the parsed shapes are the ones studentvue_helper returns, so every route
    that already shows StudentVUE work shows HAC work unchanged;
  * the password is posted to the district and nowhere else, never trimmed,
    and never appears in an error.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import requests

import App
import hac_helper
from App import LinkedAccount, User, bcrypt, db

FIXTURES = Path(__file__).parent / "fixtures" / "hac"
BASE = "https://hac.example-isd.org"
TODAY = date(2026, 9, 30)
PASSWORD = " s3cret pass "  # leading/trailing spaces are part of it


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No DNS, no stale cache, and a fixed "today" for the fixture's dates."""
    monkeypatch.setattr(hac_helper, "_host_check", lambda url: True)
    monkeypatch.setattr(hac_helper, "_today", lambda: TODAY)
    hac_helper.clear_cache()
    yield
    hac_helper.clear_cache()


# ── A scripted district server ───────────────────────────────────────


class FakeResponse:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class FakeSession:
    """Answers (method, url-fragment) routes in order; records every call."""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []
        self.headers = {}

    def request(self, method, url, data=None, timeout=None, allow_redirects=True):
        assert allow_redirects is False, "redirects must be followed by hand"
        assert timeout, "every request needs a timeout"
        self.calls.append((method, url, data))
        for (m, fragment), answer in self.routes:
            if m == method and fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise requests.ConnectionError("no route for " + url)

    def close(self):
        pass


def use_server(monkeypatch, routes):
    fake = FakeSession(routes)
    monkeypatch.setattr(hac_helper, "_new_session", lambda: fake)
    return fake


def happy_routes():
    return [
        (("GET", "/HomeAccess/Account/LogOn"), FakeResponse(200, fixture("logon.html"))),
        (("POST", "/HomeAccess/Account/LogOn"),
         FakeResponse(302, "", {"Location": "/HomeAccess/Home/WeekView"})),
        (("GET", "/HomeAccess/Home/WeekView"), FakeResponse(200, fixture("weekview.html"))),
        (("GET", "/HomeAccess/Content/Student/Assignments.aspx"),
         FakeResponse(200, fixture("classwork.html"))),
    ]


# ── District URL normalization ───────────────────────────────────────


@pytest.mark.parametrize("raw, expected", [
    ("hac.example-isd.org", BASE),
    ("  hac.example-isd.org/  ", BASE),
    ("https://hac.example-isd.org/HomeAccess/Account/LogOn?ReturnUrl=%2fHomeAccess%2f", BASE),
    ("HTTPS://HAC.Example-ISD.org/HomeAccess/", BASE),
    ("hac.example-isd.org/HomeAccess/Content/Student/Assignments.aspx", BASE),
    # http is upgraded: this URL receives a password.
    ("http://hac.example-isd.org/HomeAccess", BASE),
    # A district that mounts HAC under a path keeps the path.
    ("https://portal.example-isd.org/hac/HomeAccess/Account/LogOn", "https://portal.example-isd.org/hac"),
    # Any other path is not part of HAC's address.
    ("https://hac.example-isd.org/parents/index.html", BASE),
    ("//hac.example-isd.org/HomeAccess", BASE),
    ("https://student@hac.example-isd.org/HomeAccess", BASE),
])
def test_normalize_district_url_accepts_what_students_paste(raw, expected):
    assert hac_helper.normalize_district_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", None, "ftp://hac.example-isd.org", "javascript:alert(1)",
    "localhost", "https://hac.example-isd.org:notaport/", "not a url at all",
])
def test_normalize_district_url_rejects_non_addresses(raw):
    assert hac_helper.normalize_district_url(raw) == ""


# ── Login page reading ───────────────────────────────────────────────


def test_extracts_the_request_verification_token():
    assert hac_helper.extract_verification_token(fixture("logon.html")) == "CfDJ8Example-Token_value-123"
    assert hac_helper.extract_verification_token("<html><body>nothing</body></html>") == ""


def test_login_payload_uses_the_fields_the_open_source_clients_post():
    payload, action = hac_helper.build_login_payload(fixture("logon.html"), "s123456", PASSWORD)
    assert action == "/HomeAccess/Account/LogOn?ReturnUrl=%2fHomeAccess%2f"
    assert payload["__RequestVerificationToken"] == "CfDJ8Example-Token_value-123"
    assert payload["Database"] == "10"
    assert payload["VerificationOption"] == "UsernamePassword"
    assert payload["SCKTY00328510CustomEnabled"] == "True"
    assert payload["SCKTY00436568CustomEnabled"] == "True"
    assert payload["tempUN"] == "" and payload["tempPW"] == ""
    assert payload["LogOnDetails.UserName"] == "s123456"
    # Never trimmed: whitespace in a password is part of the password.
    assert payload["LogOnDetails.Password"] == PASSWORD


def test_login_payload_prefers_the_districts_own_database_value():
    page = """<form action="/HomeAccess/Account/LogOn" method="post">
      <input name="__RequestVerificationToken" type="hidden" value="tok" />
      <select name="Database"><option value="10">A ISD</option>
      <option value="40" selected>B ISD</option></select>
      <input name="LogOnDetails.UserName" type="text" />
      <input name="LogOnDetails.Password" type="password" />
    </form>"""
    payload, _ = hac_helper.build_login_payload(page, "u", "p")
    assert payload["Database"] == "40"


def test_login_payload_tolerates_a_form_with_no_database_field_or_wrapper():
    """Some skins render the inputs without a Database select, and one we
    have seen renders them outside any <form>. Both still produce a post."""
    page = """<div><input name="__RequestVerificationToken" type="hidden" value="tok" />
      <input id="LogOnDetails_UserName" name="LogOnDetails.UserName" type="text" />
      <input name="LogOnDetails.Password" type="password" /></div>"""
    payload, action = hac_helper.build_login_payload(page, "u", "p")
    assert payload is not None
    assert action == ""
    assert payload["__RequestVerificationToken"] == "tok"
    assert payload["Database"] == "10"


def test_an_sso_only_page_has_no_password_form():
    payload, _ = hac_helper.build_login_payload(fixture("logon_sso.html"), "u", "p")
    assert payload is None
    assert hac_helper.looks_like_sso_page(fixture("logon_sso.html"))
    assert not hac_helper.looks_like_sso_page(fixture("weekview.html"))


def test_logged_out_detection_uses_form_fields_not_prose():
    assert hac_helper.is_logged_out_page(fixture("logon_error.html"))
    assert hac_helper.is_logged_out_page(fixture("logon.html"))
    # "Welcome to ..." banner and an assignment named "Log On Journal":
    # prose that a text marker would misread as the login page.
    assert not hac_helper.is_logged_out_page(fixture("weekview.html"))
    assert not hac_helper.is_logged_out_page(fixture("classwork.html"))
    assert hac_helper.is_logged_out_page("", f"{BASE}/HomeAccess/Account/LogOn?logonError=true")


# ── validate_login end to end against a scripted server ──────────────


def test_validate_login_success(monkeypatch):
    server = use_server(monkeypatch, happy_routes())
    assert hac_helper.validate_login(BASE, "s123456", PASSWORD) == "ok"

    posts = [c for c in server.calls if c[0] == "POST"]
    assert len(posts) == 1
    method, url, data = posts[0]
    assert url == f"{BASE}/HomeAccess/Account/LogOn?ReturnUrl=%2fHomeAccess%2f"
    assert data["LogOnDetails.Password"] == PASSWORD
    assert data["__RequestVerificationToken"] == "CfDJ8Example-Token_value-123"
    # The password goes to the district host and nowhere else.
    for m, u, d in server.calls:
        assert u.startswith(BASE)


def test_validate_login_bad_password(monkeypatch):
    use_server(monkeypatch, [
        (("GET", "/HomeAccess/Account/LogOn"), FakeResponse(200, fixture("logon.html"))),
        (("POST", "/HomeAccess/Account/LogOn"), FakeResponse(200, fixture("logon_error.html"))),
    ])
    assert hac_helper.validate_login(BASE, "s123456", "wrong") == "invalid_credentials"


def test_validate_login_bad_password_via_redirect_back_to_logon(monkeypatch):
    use_server(monkeypatch, [
        (("POST", "/HomeAccess/Account/LogOn"),
         FakeResponse(302, "", {"Location": "/HomeAccess/Account/LogOn?logonError=true"})),
        (("GET", "/HomeAccess/Account/LogOn"), FakeResponse(200, fixture("logon.html"))),
    ])
    assert hac_helper.validate_login(BASE, "s123456", "wrong") == "invalid_credentials"


def test_validate_login_sso_only_page(monkeypatch):
    use_server(monkeypatch, [
        (("GET", "/HomeAccess/Account/LogOn"), FakeResponse(200, fixture("logon_sso.html"))),
    ])
    assert hac_helper.validate_login(BASE, "s123456", PASSWORD) == "sso_required"


def test_validate_login_redirect_to_identity_provider_is_sso(monkeypatch):
    server = use_server(monkeypatch, [
        (("GET", "/HomeAccess/Account/LogOn"),
         FakeResponse(302, "", {"Location": "https://launchpad.classlink.com/exampleisd"})),
    ])
    assert hac_helper.validate_login(BASE, "s123456", PASSWORD) == "sso_required"
    # And it never followed the student to the identity provider.
    assert all("classlink" not in url for _, url, _ in server.calls)


def test_a_redirect_to_another_hac_host_is_followed(monkeypatch):
    """A district moving hac. to homeaccess. is ordinary, not SSO."""
    other = "https://homeaccess.example-isd.org"
    use_server(monkeypatch, [
        (("GET", f"{BASE}/HomeAccess/Account/LogOn"),
         FakeResponse(301, "", {"Location": f"{other}/HomeAccess/Account/LogOn"})),
        (("GET", f"{other}/HomeAccess/Account/LogOn"), FakeResponse(200, fixture("logon.html"))),
        (("POST", f"{other}/HomeAccess/Account/LogOn"),
         FakeResponse(302, "", {"Location": "/HomeAccess/Home/WeekView"})),
        (("GET", f"{other}/HomeAccess/Home/WeekView"), FakeResponse(200, fixture("weekview.html"))),
    ])
    assert hac_helper.validate_login(BASE, "s123456", PASSWORD) == "ok"


@pytest.mark.parametrize("answer", [
    requests.ConnectionError("offline"),
    requests.Timeout("slow"),
    FakeResponse(503, "Service Unavailable"),
    FakeResponse(404, "Not Found"),
    FakeResponse(200, "<html><body><h1>Example ISD</h1><p>Welcome!</p></body></html>"),
])
def test_host_failures_are_connection_failed_not_bad_password(monkeypatch, answer):
    use_server(monkeypatch, [(("GET", "/HomeAccess/Account/LogOn"), answer)])
    assert hac_helper.validate_login(BASE, "s123456", PASSWORD) == "connection_failed"


def test_a_private_host_is_never_fetched(monkeypatch):
    server = use_server(monkeypatch, happy_routes())
    monkeypatch.setattr(hac_helper, "_host_check", lambda url: False)
    assert hac_helper.validate_login(BASE, "s123456", PASSWORD) == "connection_failed"
    assert server.calls == []


def test_a_failed_fetch_never_carries_the_password(monkeypatch):
    use_server(monkeypatch, [
        (("GET", "/HomeAccess/Account/LogOn"),
         requests.ConnectionError(f"failed posting {PASSWORD}")),
    ])
    with pytest.raises(hac_helper.HACError) as exc:
        hac_helper.get_assignments(BASE, "s123456", PASSWORD)
    assert PASSWORD.strip() not in str(exc.value)


# ── Classwork parsing ────────────────────────────────────────────────


@pytest.mark.parametrize("raw, value, status", [
    ("95.00", 95.0, "graded"),
    ("  95.00\n", 95.0, "graded"),
    ("86.00%", 86.0, "graded"),
    ("0.00", 0.0, "graded"),
    ("9/10", 9.0, "graded"),
    ("", None, "ungraded"),
    ("  ", None, "ungraded"),
    ("N/A", None, "ungraded"),
    ("Z", None, "missing"),
    ("M", None, "missing"),
    ("X", None, "excused"),
    ("EXC", None, "excused"),
    ("I", None, "incomplete"),
    ("L", None, "other"),
])
def test_parse_score(raw, value, status):
    got_value, got_status, _ = hac_helper.parse_score(raw)
    assert (got_value, got_status) == (value, status)


def test_parse_classwork_courses_and_averages():
    courses = hac_helper.parse_classwork(fixture("classwork.html"))
    assert [c["course"] for c in courses] == ["AP Biology", "Algebra II Honors", "Spanish III"]

    bio, alg, spanish = courses
    assert (bio["code"], bio["section"]) == ("0520", "3")
    assert bio["average"] == 88.0
    assert bio["exact_average"] == 88.4
    assert bio["last_updated"] == "2026-09-29"
    assert alg["average"] == 93.5 and alg["exact_average"] is None
    assert spanish["average"] is None
    assert spanish["assignments"] == [] and spanish["categories"] == []


def test_parse_classwork_assignment_rows():
    bio, alg, _ = hac_helper.parse_classwork(fixture("classwork.html"))
    rows = {a["title"]: a for a in bio["assignments"]}
    # The entity is decoded and HAC's hidden "*" label is not in the title.
    assert set(rows) == {
        "Unit 3 Test", "Reading & Notes Ch. 7", "Cell Lab Report",
        "Osmosis Worksheet", "Field Trip Form", "Chapter 4 Quiz",
    }

    lab = rows["Cell Lab Report"]
    assert lab["due_date"] == "2026-09-25"
    assert lab["assigned_date"] == "2026-09-22"
    assert lab["category"] == "Minor Grade"
    assert (lab["score"], lab["status"], lab["total_points"]) == (95.0, "graded", 100.0)
    assert lab["percent"] == 95.0 and lab["weight"] == 1.0
    assert lab["can_be_dropped"] is True

    test = rows["Unit 3 Test"]
    assert (test["score"], test["status"], test["score_raw"]) == (None, "ungraded", "")
    assert rows["Osmosis Worksheet"]["status"] == "missing"
    assert rows["Osmosis Worksheet"]["score_raw"] == "Z"
    assert rows["Field Trip Form"]["status"] == "excused"

    # Six-column skin: columns found by header, fraction fills Total Points.
    alg_rows = {a["title"]: a for a in alg["assignments"]}
    hw5 = alg_rows["Homework 5"]
    assert (hw5["score"], hw5["total_points"], hw5["status"]) == (9.0, 10.0, "graded")
    assert hw5["weight"] is None and hw5["percent"] is None
    assert alg_rows["Bell Ringer"]["status"] == "incomplete"


def test_parse_classwork_categories_skip_the_totals_row():
    bio = hac_helper.parse_classwork(fixture("classwork.html"))[0]
    assert [c["name"] for c in bio["categories"]] == ["Daily", "Minor Grade", "Major Grade"]
    minor = bio["categories"][1]
    assert minor == {
        "name": "Minor Grade", "points": 106.0, "max_points": 125.0,
        "percent": 84.8, "weight": 30.0, "category_points": 25.44,
    }


def test_script_bodies_do_not_become_courses():
    """The fixture's <script> contains AssignmentClass markup in a string."""
    assert len(hac_helper.parse_classwork(fixture("classwork.html"))) == 3


def test_an_empty_or_foreign_page_parses_to_nothing():
    assert hac_helper.parse_classwork("") == []
    assert hac_helper.parse_classwork(fixture("weekview.html")) == []


# ── studentvue_helper-compatible outputs ─────────────────────────────


SV_ASSIGNMENT_KEYS = {
    "title", "course", "due_date", "points_possible", "priority",
    "estimated_time", "display_score", "description", "category",
}


def test_get_assignments_shape_and_filtering(monkeypatch):
    use_server(monkeypatch, happy_routes())
    items = hac_helper.get_assignments(BASE, "s123456", PASSWORD)

    # Ungraded (and incomplete) work only, due no more than 14 days ago,
    # sorted by due date -- StudentVUE's rules.
    assert [a["title"] for a in items] == [
        "Bell Ringer", "Reading & Notes Ch. 7", "Homework 6", "Unit 3 Test",
    ]
    for a in items:
        assert set(a) == SV_ASSIGNMENT_KEYS
    by_title = {a["title"]: a for a in items}
    test = by_title["Unit 3 Test"]
    assert test["course"] == "AP Biology"
    assert test["due_date"] == "2026-10-06"
    assert test["points_possible"] == 100.0
    assert test["category"] == "Major Grade"
    assert test["priority"] == "Medium"  # a test six days out
    assert by_title["Reading & Notes Ch. 7"]["priority"] == "High"  # due tomorrow
    assert isinstance(test["estimated_time"], int) and test["estimated_time"] > 0


def test_get_grades_shape(monkeypatch):
    use_server(monkeypatch, happy_routes())
    assert hac_helper.get_grades(BASE, "s123456", PASSWORD) == [
        {"course": "AP Biology", "teacher": "", "letter": "B", "percentage": 88.4},
        {"course": "Algebra II Honors", "teacher": "", "letter": "A", "percentage": 93.5},
    ]


def test_get_gradebook_detail_shape(monkeypatch):
    use_server(monkeypatch, happy_routes())
    detail = hac_helper.get_gradebook_detail(BASE, "s123456", PASSWORD)
    assert [c["course"] for c in detail] == ["AP Biology", "Algebra II Honors"]
    bio = detail[0]
    assert set(bio) == {"course", "teacher", "letter", "percentage", "categories", "assignments"}
    assert bio["percentage"] == 88.4
    assert set(bio["categories"][0]) == {"type", "weight", "points", "points_possible", "weighted_pct", "mark"}
    assert bio["categories"][1]["weighted_pct"] == 25.44

    rows = {a["title"]: a for a in bio["assignments"]}
    for a in rows.values():
        assert {"title", "due_date", "points_earned", "points_possible",
                "display_score", "graded", "category", "description"} <= set(a)
    assert rows["Cell Lab Report"]["graded"] is True
    assert rows["Cell Lab Report"]["points_earned"] == 95.0
    # A missing code counts as zero in the district's own average.
    assert rows["Osmosis Worksheet"]["points_earned"] == 0.0
    assert rows["Osmosis Worksheet"]["graded"] is True
    assert rows["Osmosis Worksheet"]["display_score"] == "Missing"
    assert rows["Osmosis Worksheet"]["is_missing"] is True
    assert rows["Field Trip Form"]["graded"] is False
    assert rows["Field Trip Form"]["display_score"] == "Excused"
    assert rows["Unit 3 Test"]["display_score"] == "Not Graded"
    assert rows["Unit 3 Test"]["points_earned"] is None


def test_get_missing_assignments(monkeypatch):
    use_server(monkeypatch, happy_routes())
    missing = hac_helper.get_missing_assignments(BASE, "s123456", PASSWORD)
    assert [(m["title"], m["score_label"]) for m in missing] == [
        ("Osmosis Worksheet", "Missing"),
        ("Chapter 4 Quiz", "11/25"),
    ]
    for m in missing:
        assert m["source"] == "hac_missing" and m["is_missing"] is True
        assert m["priority"] == "High"


def test_get_courses(monkeypatch):
    use_server(monkeypatch, happy_routes())
    assert hac_helper.get_courses(BASE, "s123456", PASSWORD) == [
        {"name": "AP Biology"}, {"name": "Algebra II Honors"}, {"name": "Spanish III"},
    ]


def test_repeat_calls_reuse_one_login(monkeypatch):
    """Assignments then missing work is one page view, not two logins."""
    server = use_server(monkeypatch, happy_routes())
    hac_helper.get_assignments(BASE, "s123456", PASSWORD)
    hac_helper.get_missing_assignments(BASE, "s123456", PASSWORD)
    hac_helper.get_grades(BASE, "s123456", PASSWORD)
    assert sum(1 for c in server.calls if c[0] == "POST") == 1
    # A different password is a different entry, never a cache hit.
    hac_helper.get_grades(BASE, "s123456", "other")
    assert sum(1 for c in server.calls if c[0] == "POST") == 2


def test_a_session_that_does_not_stick_is_a_credential_failure(monkeypatch):
    routes = happy_routes()
    routes[-1] = (("GET", "/HomeAccess/Content/Student/Assignments.aspx"),
                  FakeResponse(200, fixture("logon.html")))
    use_server(monkeypatch, routes)
    with pytest.raises(hac_helper.HACInvalidCredentials):
        hac_helper.get_grades(BASE, "s123456", PASSWORD)


# ── The /login/hac route ─────────────────────────────────────────────


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


def test_login_page_renders_with_a_text_url_field(client):
    r = client.get("/login/hac")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'action="/login/hac"' in body
    # type="url" would refuse "hac.district.org" in the browser before our
    # normalisation ever saw it.
    assert 'type="text"' in body and 'inputmode="url"' in body
    assert 'name="district_url"' in body
    assert 'type="url"' not in body


def test_login_page_is_noindex(client):
    r = client.get("/login/hac")
    assert "noindex" in r.headers.get("X-Robots-Tag", "")


def test_login_success_redirects_like_studentvue(client, monkeypatch):
    captured = {}

    def validate(district_url, username, password):
        captured.update(url=district_url, user=username, pw=password)
        return "ok"

    monkeypatch.setattr(hac_helper, "validate_login", validate)
    r = client.post("/login/hac", data={
        "district_url": "hac.example-isd.org/HomeAccess/Account/LogOn",
        "username": " s123456 ",
        "password": PASSWORD,
    })
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/command-center")
    assert captured == {"url": BASE, "user": "s123456", "pw": PASSWORD}
    with client.session_transaction() as sess:
        assert sess["login_type"] == "hac"
        assert sess["hac_district_url"] == BASE
        assert sess["hac_username"] == "s123456"
        assert sess["hac_password"] == PASSWORD


def test_login_connection_problem_is_not_reported_as_bad_password(client, monkeypatch):
    monkeypatch.setattr(hac_helper, "validate_login", lambda *a: "connection_failed")
    body = client.post("/login/hac", data={
        "district_url": "hac.example-isd.org", "username": "s", "password": "p",
    }).get_data(as_text=True)
    assert "Could not reach Home Access Center" in body
    assert "was not accepted" not in body


def test_login_bad_credentials_message(client, monkeypatch):
    monkeypatch.setattr(hac_helper, "validate_login", lambda *a: "invalid_credentials")
    body = client.post("/login/hac", data={
        "district_url": "hac.example-isd.org", "username": "s", "password": "p",
    }).get_data(as_text=True)
    assert "Username or password was not accepted by Home Access Center" in body
    assert "Could not reach" not in body


def test_login_sso_message(client, monkeypatch):
    monkeypatch.setattr(hac_helper, "validate_login", lambda *a: "sso_required")
    body = client.post("/login/hac", data={
        "district_url": "hac.example-isd.org", "username": "s", "password": "p",
    }).get_data(as_text=True)
    assert "single sign-on" in body
    assert "was not accepted" not in body


def test_login_requires_every_field_and_a_real_address(client, monkeypatch):
    calls = []
    monkeypatch.setattr(hac_helper, "validate_login", lambda *a: calls.append(a) or "ok")
    body = client.post("/login/hac", data={"district_url": "", "username": "s", "password": "p"}).get_data(as_text=True)
    assert "Please fill in all fields." in body
    body = client.post("/login/hac", data={"district_url": "ftp://x.y", "username": "s", "password": "p"}).get_data(as_text=True)
    assert "Could not reach Home Access Center" in body
    assert calls == []


def test_login_options_offer_hac(client):
    assert "/login/hac" in client.get("/login").get_data(as_text=True)


def test_guest_hac_session_feeds_grades_and_assignments(client, monkeypatch):
    """The dispatch: a guest's session creds reach hac_helper, and the
    result comes back through the same routes StudentVUE uses."""
    seen = []

    def grades(url, user, pw):
        seen.append((url, user, pw))
        return [{"course": "AP Biology", "teacher": "", "letter": "B", "percentage": 88.4}]

    def assignments(url, user, pw):
        return [{"title": "Unit 3 Test", "course": "AP Biology", "due_date": "2026-10-06",
                 "points_possible": 100.0, "priority": "Medium", "estimated_time": 60,
                 "display_score": "", "description": "", "category": "Major Grade"}]

    monkeypatch.setattr(hac_helper, "get_grades", grades)
    monkeypatch.setattr(hac_helper, "get_assignments", assignments)
    monkeypatch.setattr(hac_helper, "get_gradebook_detail", lambda *a: [{"course": "AP Biology"}])
    monkeypatch.setattr(hac_helper, "get_missing_assignments", lambda *a: [])
    with client.session_transaction() as sess:
        sess["login_type"] = "hac"
        sess["hac_district_url"] = BASE
        sess["hac_username"] = "s123456"
        sess["hac_password"] = PASSWORD

    assert client.get("/grades/data").get_json()[0]["course"] == "AP Biology"
    assert seen == [(BASE, "s123456", PASSWORD)]
    # /live is not asserted here: on main it fails for every LMS before
    # dispatching (``_DismissedSet | set`` raises TypeError), a separate bug.
    assert client.get("/gradebook/detail").get_json() == [{"course": "AP Biology"}]
    assert client.get("/missing/data").get_json() == []


def test_grades_fall_back_when_the_district_is_down(client, monkeypatch):
    def boom(*a):
        raise hac_helper.HACConnectionError("down")

    monkeypatch.setattr(hac_helper, "get_grades", boom)
    with client.session_transaction() as sess:
        sess["login_type"] = "hac"
        sess["hac_district_url"] = BASE
        sess["hac_username"] = "s"
        sess["hac_password"] = "p"
    r = client.get("/grades/data")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)


# ── Signed-in: LinkedAccount + the Command Center collector ─────────


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("hac+%")).all()]
    if ids:
        LinkedAccount.query.filter(LinkedAccount.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("hac+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def db_ready():
    with App.app.app_context():
        db.create_all()
        _wipe()
    yield
    with App.app.app_context():
        _wipe()


def test_collector_tags_hac_work_for_a_linked_account(db_ready, monkeypatch):
    use_server(monkeypatch, happy_routes())
    with App.app.app_context():
        user = User(email="hac+a@example.com",
                    password_hash=bcrypt.generate_password_hash("pw-hac-test").decode())
        db.session.add(user)
        db.session.commit()
        acct = LinkedAccount(user_id=user.id, login_type="hac", is_active=True, name="HAC")
        acct.set_credentials({"hac_district_url": BASE, "hac_username": "s123456",
                              "hac_password": PASSWORD})
        db.session.add(acct)
        db.session.commit()
        tasks = App.collect_lms_assignments_for_user(user.id, use_cache=False)

    by_title = {t["title"]: t for t in tasks}
    assert by_title["Unit 3 Test"]["source"] == "hac"
    assert by_title["Osmosis Worksheet"]["source"] == "hac_missing"
    assert by_title["Chapter 4 Quiz"]["source"] == "hac_missing"
    assert "Cell Lab Report" not in by_title
