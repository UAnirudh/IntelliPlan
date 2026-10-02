"""StudentVUE districts that have moved to the JSON API (e.g. Northshore / NSD).

Edupoint switches off the SOAP student-data methods district by district.
There the SOAP login answers RT_ERROR for a correct password, so the helper
has to ask the JSON API — the one the current StudentVUE app uses — before
telling a student their password is wrong.
"""

from __future__ import annotations

import base64
import json

import pytest
import requests

import studentvue_helper as sv


class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


SOAP_DISABLED = (
    "<soap:Envelope><soap:Body><ProcessWebServiceRequestResponse>"
    "<ProcessWebServiceRequestResult>&lt;RT_ERROR ERROR_MESSAGE=&quot;This "
    "service is no longer available&quot; /&gt;</ProcessWebServiceRequestResult>"
    "</ProcessWebServiceRequestResponse></soap:Body></soap:Envelope>"
)

GRADEBOOK = {
    "type": "Traditional",
    "reportingPeriods": [{"index": "0", "gradePeriod": "S1"}],
    "courses": [{
        "period": "2",
        "title": "AP Biology",
        "staff": "R. Franklin",
        "marks": [{
            "markName": "S1",
            "calculatedScoreString": "A-",
            "calculatedScoreRaw": "91.4",
            "gradeCalculationSummary": {"assignmentGradeCalc": [
                {"type": "Labs", "weight": "40%", "points": "90", "pointsPossible": "100",
                 "weightedPct": "36%", "calculatedMark": "A-"},
            ]},
            "assignments": [
                {"measure": "Cell Respiration Lab Report", "type": "Labs",
                 "dueDate": "2099-10-14T00:00:00", "score": "Not Graded",
                 "displayScore": "Not Due", "points": "50 Points Possible",
                 "measureDescription": "Write up & graph results"},
                {"measure": "Unit 1 Test", "type": "Tests", "dueDate": "09/20/2026",
                 "score": "8 out of 10", "displayScore": "8 out of 10",
                 "points": "8 / 10", "point": "8", "pointPossible": "10"},
            ],
        }],
    }],
}


def _router(monkeypatch, soap_text=SOAP_DISABLED, login_body=None, gradebook_body=None,
            json_status=200):
    calls = []

    def post(url, headers=None, data=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "data": data})
        if url.endswith("/Service/PXPCommunication.asmx"):
            return FakeResponse(text=soap_text)
        if json_status != 200:
            return FakeResponse(status=json_status, text="<html>not found</html>")
        if url.endswith("/AttemptLogin"):
            return FakeResponse(body=login_body)
        if url.endswith("/Gradebook"):
            return FakeResponse(body=gradebook_body)
        raise AssertionError(url)

    monkeypatch.setattr(sv.requests, "post", post)
    return calls


def test_json_login_rescues_a_district_that_turned_off_soap(monkeypatch):
    calls = _router(monkeypatch, login_body={"access_token": "tok", "refresh_token": "r"})

    status = sv.validate_login("northshore", "s1234567", "pa:ss word")

    assert status == "ok"
    login = next(c for c in calls if c["url"].endswith("/AttemptLogin"))
    assert login["url"] == "https://wa-nor-psv.edupoint.com/api/v1/mobile/PXPWebServices/AttemptLogin"
    # Credentials travel only in the Basic header, unmodified.
    expected = base64.b64encode(b"s1234567:pa:ss word").decode()
    assert login["headers"]["Authorization"] == f"Basic {expected}"
    request = json.loads(json.loads(login["data"])["arguments"]["request"])
    assert request == {"userID": None, "password": None, "userType": "Student"}


def test_json_error_envelope_is_a_real_bad_password(monkeypatch):
    _router(monkeypatch, login_body={"error": {"code": "1", "message": "Invalid user id or password"}, "data": None})
    assert sv.validate_login("https://district-psv.edupoint.com", "s", "wrong") == "invalid_credentials"


def test_districts_without_the_json_api_keep_the_soap_answer(monkeypatch):
    _router(monkeypatch, json_status=404)
    assert sv.validate_login("https://district-psv.edupoint.com", "s", "wrong") == "invalid_credentials"


def test_soap_success_does_not_touch_the_json_api(monkeypatch):
    calls = _router(monkeypatch, soap_text="<ProcessWebServiceRequestResult>&lt;StudentInfo /&gt;</ProcessWebServiceRequestResult>")
    assert sv.validate_login("https://district-psv.edupoint.com", "s", "p") == "ok"
    assert all("/api/v1/" not in c["url"] for c in calls)


def test_json_gradebook_feeds_the_existing_parsers(monkeypatch):
    calls = _router(monkeypatch, login_body={"access_token": "tok"},
                    gradebook_body={"error": None, "data": {"traditionalGradebook": GRADEBOOK}})

    assignments = sv.get_assignments("northshore", "s", "p")
    grades = sv.get_grades("northshore", "s", "p")
    detail = sv.get_gradebook_detail("northshore", "s", "p")
    missing = sv.get_missing_assignments("northshore", "s", "p")

    gradebook_call = next(c for c in calls if c["url"].endswith("/Gradebook"))
    assert gradebook_call["headers"]["Authorization"] == "Bearer tok"

    # The ungraded lab is upcoming work; the graded test is not.
    assert [a["title"] for a in assignments] == ["Cell Respiration Lab Report"]
    lab = assignments[0]
    assert lab["course"] == "AP Biology"
    assert lab["due_date"] == "2099-10-14"
    assert lab["points_possible"] == 50
    assert lab["description"] == "Write up & graph results"
    assert lab["category"] == "Labs"

    assert grades == [{"course": "AP Biology", "teacher": "R. Franklin", "letter": "A-", "percentage": 91.4}]

    course = detail[0]
    assert course["categories"][0]["type"] == "Labs"
    assert course["categories"][0]["weight"] == 40.0
    test = next(a for a in course["assignments"] if a["title"] == "Unit 1 Test")
    assert test["graded"] is True and test["points_earned"] == 8.0 and test["points_possible"] == 10.0

    assert missing == []  # 8/10 is not missing


def test_gradebook_feature_off_at_school_is_empty_not_an_error(monkeypatch):
    _router(monkeypatch, login_body={"access_token": "tok"},
            gradebook_body={"error": {"code": "2100", "message": "Grade Book data not available"}, "data": None})
    assert sv.get_assignments("northshore", "s", "p") == []
    assert sv.get_grades("northshore", "s", "p") == []


def test_unreachable_district_still_raises_the_soap_error(monkeypatch):
    def post(url, **kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(sv.requests, "post", post)
    with pytest.raises(requests.ConnectionError):
        sv.get_assignments("https://district-psv.edupoint.com", "s", "p")


def test_attribute_values_are_escaped():
    xml = sv.json_gradebook_to_xml({"courses": [{"title": 'Lit "A" & <B>', "staff": "X", "marks": []}]})
    assert 'Title="Lit &quot;A&quot; &amp; &lt;B&gt;"' in xml
