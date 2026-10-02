import base64
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import html as html_module
import re
from urllib.parse import urlparse

SOAP_ACTION = "http://edupoint.com/webservices/ProcessWebServiceRequest"


_BIG_TITLE_RE = re.compile(r"\b(test|exam|midterm|final|project|essay|presentation|lab\s*report)\b", re.I)


def _compute_priority(days_until_due, points_possible, title=""):
    """Rank an assignment by combined urgency + weight + type.

    The old rule (anything due in <= 3 days = High) made every dashboard
    light up red. This version is balanced:

      High   = overdue, or due today/tomorrow, or a major assessment
               (test/exam/project) due within 3 days, or a big-point
               assignment (>= 80 pts) due within 4 days.
      Medium = due within 5 days, OR a major assessment within 7 days,
               OR a 30-79 pt assignment due within 7 days.
      Low    = everything else (small, far away, or low-stakes).
    """
    try:
        d = int(days_until_due)
    except (TypeError, ValueError):
        return "Medium"
    try:
        pts = float(points_possible or 0)
    except (TypeError, ValueError):
        pts = 0.0
    is_big = bool(title and _BIG_TITLE_RE.search(title))

    if d < 0:
        return "High"
    if d <= 1:
        return "High"
    if is_big and d <= 3:
        return "High"
    if pts >= 80 and d <= 4:
        return "High"

    if d <= 5:
        return "Medium"
    if is_big and d <= 7:
        return "Medium"
    if pts >= 30 and d <= 7:
        return "Medium"

    return "Low"

DISTRICT_URL_ALIASES = {
    "northshore": "https://wa-nor-psv.edupoint.com",
    "nsd": "https://wa-nor-psv.edupoint.com",
}

def normalize_district_url(district_url):
    """Accept common StudentVUE inputs and return the Synergy service host."""
    raw = (district_url or "").strip()
    if not raw:
        return ""
    alias = DISTRICT_URL_ALIASES.get(raw.lower())
    if alias:
        return alias
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    raw = raw.strip().rstrip("/")
    raw = re.sub(r"/(PXP2_Login_Student\.aspx|PXP2_Login\.aspx|Login_Student_PXP\.aspx)$", "", raw, flags=re.I)
    raw = re.sub(r"/(studentvue|parentvue|pxp2?|login)$", "", raw, flags=re.I)
    parsed = urlparse(raw)
    if not parsed.netloc:
        return raw
    return f"{parsed.scheme}://{parsed.netloc}"

def make_request(district_url, username, password, method, params="&lt;Parms/&gt;"):
    district_url = normalize_district_url(district_url)
    user_xml = html_module.escape(username or "", quote=True)
    pass_xml = html_module.escape(password or "", quote=True)
    url = f"{district_url}/Service/PXPCommunication.asmx"
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": SOAP_ACTION
    }
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ProcessWebServiceRequest xmlns="http://edupoint.com/webservices/">
      <userID>{user_xml}</userID>
      <password>{pass_xml}</password>
      <skipLoginLog>1</skipLoginLog>
      <parent>0</parent>
      <webServiceHandleName>PXPWebServices</webServiceHandleName>
      <methodName>{method}</methodName>
      <paramStr>{params}</paramStr>
    </ProcessWebServiceRequest>
  </soap:Body>
</soap:Envelope>"""
    response = requests.post(url, headers=headers, data=body, timeout=15)
    response.raise_for_status()
    return response.text


# ── StudentVUE JSON API (the "StudentVUE (New)" app) ─────────────────────
#
# Edupoint is retiring the SOAP student-data methods district by district.
# Where they are off, the SOAP login still answers — with an RT_ERROR — so a
# student with a perfectly good password was told it was wrong. Northshore
# (NSD) moved its families to the new StudentVUE app, which talks to this
# JSON API instead. SOAP stays first because most districts still serve it;
# the JSON path is the fallback that keeps each district working as it is
# switched over.
JSON_API_HEADERS = {
    "Content-Type": "application/json",
    # Sent verbatim by the official app — a leftover from the SOAP days.
    "User-Agent": "ksoap",
    "AppNameOSAndVersion": "StudentVUE|Android|1.9.16",
}


class JsonApiUnavailable(Exception):
    """The district host does not serve the JSON API (or answered with junk)."""


def _json_url(district_url, method):
    return f"{normalize_district_url(district_url)}/api/v1/mobile/PXPWebServices/{method}"


def _json_arguments(request_fields):
    # The real parameters are a JSON object serialized to a *string* at
    # arguments.request — not a nested object.
    return json.dumps({"arguments": {"request": json.dumps(request_fields)}})


def _json_body(response):
    if response.status_code != 200:
        # The API answers 200 even for failures; anything else means this
        # host isn't serving it (404 on districts without it, 401 on a dead
        # token).
        raise JsonApiUnavailable(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise JsonApiUnavailable("non-JSON response")


def json_login(district_url, username, password):
    """Return an access token, or None when the district rejected the credentials.

    Raises JsonApiUnavailable when the host doesn't speak the JSON API and
    requests.RequestException when it can't be reached at all.
    """
    basic = base64.b64encode(f"{username or ''}:{password or ''}".encode("utf-8")).decode("ascii")
    response = requests.post(
        _json_url(district_url, "AttemptLogin"),
        headers={**JSON_API_HEADERS, "Authorization": f"Basic {basic}"},
        # The app nulls the credentials in the body; they travel only in
        # the Basic header.
        data=_json_arguments({"userID": None, "password": None, "userType": "Student"}),
        timeout=15,
    )
    body = _json_body(response)
    if not isinstance(body, dict):
        raise JsonApiUnavailable("unexpected login payload")
    # Success is a bare token object — no error/data envelope.
    if body.get("access_token"):
        return body["access_token"]
    if body.get("error"):
        return None
    raise JsonApiUnavailable("login response had neither a token nor an error")


def json_call(district_url, access_token, method, request_fields):
    """Call one JSON API method and return its `data` object.

    Every call answers HTTP 200 even on failure; the body's `error` field is
    the real status. Code 2100 means "feature not enabled at this school",
    which is empty data, not a failure.
    """
    response = requests.post(
        _json_url(district_url, method),
        headers={**JSON_API_HEADERS, "Authorization": f"Bearer {access_token}"},
        data=_json_arguments(request_fields),
        timeout=20,
    )
    body = _json_body(response)
    if not isinstance(body, dict):
        raise JsonApiUnavailable("unexpected payload")
    error = body.get("error")
    if error:
        code = str(error.get("code")) if isinstance(error, dict) else ""
        if code == "2100":
            return {}
        raise JsonApiUnavailable(f"{method} error {code}")
    return body.get("data") or {}


def _xml_attrs(pairs):
    return " ".join(
        f'{name}="{html_module.escape(str(value), quote=True)}"'
        for name, value in pairs
        if value is not None and not isinstance(value, (dict, list))
    )


def _mdY(value):
    """The gradebook parsers read MM/DD/YYYY; the JSON API may send ISO dates."""
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt, width in (("%m/%d/%Y", 10), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:width], fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return text


# JSON keys are the SOAP attribute names in camelCase, with a few friendlier
# aliases. They are emitted first and in SOAP order because the parsers below
# are order-sensitive — `Score=` also matches inside `DisplayScore=`, so Score
# has to come first.
_ASSIGNMENT_KEY_ORDER = (
    ("Measure", ("measure", "assignmentName", "name")),
    ("Type", ("type", "category")),
    ("Date", ("date", "assignedDate")),
    ("DueDate", ("dueDate",)),
    ("Score", ("score",)),
    ("ScoreType", ("scoreType",)),
    ("Points", ("points",)),
    ("Notes", ("notes",)),
    ("MeasureDescription", ("measureDescription", "description")),
    ("Point", ("point", "pointsEarned")),
    ("PointPossible", ("pointPossible", "pointsPossible")),
    ("DisplayScore", ("displayScore",)),
)


def _assignment_xml(item):
    pairs, used = [], set()
    for attr, keys in _ASSIGNMENT_KEY_ORDER:
        used.update(keys)
        for key in keys:
            if item.get(key) is not None:
                value = item[key]
                if attr in ("DueDate", "Date"):
                    value = _mdY(value)
                pairs.append((attr, value))
                break
    for key, value in item.items():
        if key and key not in used:
            pairs.append((key[0].upper() + key[1:], value))
    return f"<Assignment {_xml_attrs(pairs)} />"


def _grade_calcs(summary):
    rows = summary
    if isinstance(summary, dict):
        rows = (summary.get("assignmentGradeCalc") or summary.get("assignmentGradeCalcs")
                or summary.get("gradeCalcs") or [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append("<AssignmentGradeCalc " + _xml_attrs([
            ("Type", row.get("type", "")),
            ("Weight", row.get("weight", "")),
            ("Points", row.get("points", "")),
            ("PointsPossible", row.get("pointsPossible", "")),
            ("WeightedPct", row.get("weightedPct", "")),
            ("CalculatedMark", row.get("calculatedMark", "")),
        ]) + " />")
    return out


def json_gradebook_to_xml(gradebook):
    """Render a JSON traditionalGradebook as the XML the SOAP parsers read.

    Every gradebook consumer in this module (and the grade pages built on
    them) was written against the SOAP payload. Translating the new payload
    into that shape keeps one set of parsers instead of two that drift.
    """
    parts = ["<Gradebook>"]
    for course in (gradebook or {}).get("courses") or []:
        if not isinstance(course, dict):
            continue
        parts.append("<Course " + _xml_attrs([
            ("Period", course.get("period", "")),
            ("Title", course.get("title") or course.get("courseName") or ""),
            ("Room", course.get("room", "")),
            ("Staff", course.get("staff", "")),
            ("StaffEMail", course.get("staffEMail", "")),
        ]) + "><Marks>")
        for mark in course.get("marks") or []:
            if not isinstance(mark, dict):
                continue
            parts.append("<Mark " + _xml_attrs([
                ("MarkName", mark.get("markName", "")),
                ("CalculatedScoreString", mark.get("calculatedScoreString", "")),
                ("CalculatedScoreRaw", mark.get("calculatedScoreRaw", "")),
            ]) + "><GradeCalculationSummary>")
            parts.extend(_grade_calcs(mark.get("gradeCalculationSummary")))
            parts.append("</GradeCalculationSummary><Assignments>")
            for item in mark.get("assignments") or []:
                if isinstance(item, dict):
                    parts.append(_assignment_xml(item))
            parts.append("</Assignments></Mark>")
        parts.append("</Marks></Course>")
    parts.append("</Gradebook>")
    return "".join(parts)


def _json_gradebook_raw(district_url, username, password):
    token = json_login(district_url, username, password)
    if not token:
        return None
    data = json_call(district_url, token, "Gradebook", {
        "reportPeriod": "", "concurrentSchOrgYearGU": "",
        "childIntID": 0, "languageCode": "en",
    })
    gradebook = data.get("traditionalGradebook")
    if not gradebook:
        return None
    return json_gradebook_to_xml(gradebook)


_SOAP_RESULT_RE = re.compile(
    r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>', re.DOTALL)


def _gradebook_raw(district_url, username, password):
    """Unescaped gradebook XML from whichever API this district still serves."""
    soap_error = None
    try:
        result = make_request(
            district_url, username, password, "Gradebook",
            "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
        )
        inner = _SOAP_RESULT_RE.search(result)
        if inner:
            raw = html_module.unescape(inner.group(1))
            if "RT_ERROR" not in raw:
                return raw
    except requests.RequestException as exc:
        soap_error = exc
    try:
        return _json_gradebook_raw(district_url, username, password)
    except (JsonApiUnavailable, requests.RequestException):
        if soap_error is not None:
            raise soap_error
        return None


def test_login(district_url, username, password):
    """Compatibility wrapper for callers that only need a boolean."""
    return validate_login(district_url, username, password) == "ok"


def validate_login(district_url, username, password):
    """Validate StudentVUE credentials without mistaking a host failure for a password failure.

    A district's SOAP endpoint can be unavailable, blocked, or absent even
    when a student's password is correct.  The login screen needs to surface
    that distinction so students do not repeatedly reset valid passwords.
    """
    soap_status = _validate_soap(district_url, username, password)
    if soap_status == "ok":
        return "ok"
    # SOAP said no — but on a district that has switched off the SOAP
    # student methods, "no" is all it can say. The JSON API is the one the
    # current StudentVUE app uses, so its answer is the authoritative one.
    try:
        token = json_login(district_url, username, password)
    except Exception:
        return soap_status
    return "ok" if token else "invalid_credentials"


def _validate_soap(district_url, username, password):
    try:
        result = make_request(district_url, username, password, "StudentInfo")
    except requests.RequestException:
        return "connection_failed"
    except Exception:
        # Keep an unexpected integration failure out of the credential error
        # path and out of the user-facing response.
        return "connection_failed"

    invalid_markers = ("RT_ERROR", "Invalid user", "Invalid User")
    if any(marker in result for marker in invalid_markers):
        return "invalid_credentials"

    # A reachable district login page can return HTML with a 200 status.  It
    # is not a successful SOAP login unless it contains the expected result.
    if "ProcessWebServiceRequestResult" not in result:
        return "connection_failed"

    return "ok"

def get_courses(district_url, username, password):
    gradebook_raw = _gradebook_raw(district_url, username, password)
    if not gradebook_raw:
        return []
    course_pattern = re.compile(r'<Course[^>]*Title="([^"]*)"', re.DOTALL)
    
    courses = []
    seen = set()
    for match in course_pattern.finditer(gradebook_raw):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            courses.append({"name": name})
    
    return courses

def _estimate_minutes(title, description, category, points_possible):
    """Minutes this assignment is likely to take.

    Uses the shared sizing module on whatever the gradebook gave us, and falls
    back to the old points heuristic when the description says nothing
    measurable.
    """
    try:
        from intelliplan.intelligence.sizing import size_from_metadata

        sized = size_from_metadata(
            title=title,
            kind=(category or "").strip().lower(),
            description=description or "",
            points_possible=points_possible,
        )
        if sized.is_measured:
            return sized.minutes
    except Exception:
        pass
    return max(30, round(float(points_possible) * 1.5 / 30) * 30)


def get_assignments(district_url, username, password):
    gradebook_raw = _gradebook_raw(district_url, username, password)
    if not gradebook_raw:
        print("No result found")
        return []

    course_pattern = re.compile(r'<Course[^>]*Period="([^"]*)"[^>]*Title="([^"]*)"', re.DOTALL)
    assignment_pattern = re.compile(
        r'<Assignment\s([^>]*?)(?:/>|>)',
        re.DOTALL
    )

    def get_attr(attrs_str, attr_name):
        match = re.search(rf'{attr_name}="([^"]*)"', attrs_str)
        # Values are still XML-escaped after the payload is unwrapped;
        # without this, "Read & annotate" reached the planner as "&amp;".
        return html_module.unescape(match.group(1)) if match else ""

    assignments = []
    today = datetime.now(timezone.utc)
    course_blocks = re.split(r'(?=<Course\s)', gradebook_raw)

    for block in course_blocks:
        course_match = course_pattern.search(block)
        if not course_match:
            continue
        course_name = course_match.group(2)

        for a_match in assignment_pattern.finditer(block):
            attrs = a_match.group(1)

            title = get_attr(attrs, "Measure")
            due_date_str = get_attr(attrs, "DueDate")
            points_str = get_attr(attrs, "Points")
            display_score = get_attr(attrs, "DisplayScore")
            score = get_attr(attrs, "Score")

            if not due_date_str or not title:
                continue

            # Skip already graded assignments — they're done
            if score and display_score not in ("Not Graded", "Not Due", ""):
                continue

            # Skip explicitly "Not Graded" — submitted but awaiting grade
            if display_score == "Not Graded":
                continue

            try:
                due_date = datetime.strptime(due_date_str, "%m/%d/%Y")
                due_date = due_date.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            days = (due_date - today).days

            # Skip assignments older than 14 days
            if days < -14:
                continue

            try:
                points_possible = float(points_str.split("/")[-1].strip().split()[0])
            except Exception:
                points_possible = 60

            priority = _compute_priority(days, points_possible, title)

            # StudentVue's gradebook carries a description and a category on
            # every assignment. Both were parsed nowhere and both are real
            # sizing signals: "Read pp. 88-114, answer 1-12" in the
            # description is worth more than any guess from the point value,
            # and Type ("Homework", "Test") beats inferring the kind from
            # words in the title. This is the whole of what the SOAP surface
            # offers — there is no rubric and no word count, so anything
            # beyond this would be invention.
            description = " ".join(
                p for p in (
                    get_attr(attrs, "MeasureDescription"),
                    get_attr(attrs, "Notes"),
                ) if p
            ).strip()
            category = get_attr(attrs, "Type")

            assignments.append({
                "title": title,
                "course": course_name,
                "due_date": due_date.strftime("%Y-%m-%d"),
                "points_possible": points_possible,
                "priority": priority,
                "estimated_time": _estimate_minutes(
                    title, description, category, points_possible
                ),
                "display_score": display_score,
                "description": description[:4000],
                "category": category,
            })

    return sorted(assignments, key=lambda x: x["due_date"])

# if __name__ == "__main__":
#     assignments = get_assignments(
#         "https://wa-nor-psv.edupoint.com",
#         "student-id",
#         "password"
#     )
#     for a in assignments[:3]:
#         print(a)


def get_grades_raw(district_url, username, password):
    gradebook_raw = _gradebook_raw(district_url, username, password)
    if not gradebook_raw:
        return
    # Find first Mark element
    mark_match = re.search(r'<Mark\s[^>]*>', gradebook_raw)
    course_match = re.search(r'<Course\s[^>]*>', gradebook_raw)
    if mark_match:
        print("MARK:", mark_match.group(0)[:300])
    if course_match:
        print("COURSE:", course_match.group(0)[:300])


def get_grades(district_url, username, password):
    gradebook_raw = _gradebook_raw(district_url, username, password)
    if not gradebook_raw:
        return []
    course_pattern = re.compile(r'<Course\s[^>]*Title="([^"]*)"[^>]*Staff="([^"]*)"', re.DOTALL)
    mark_pattern = re.compile(r'<Mark\s[^>]*MarkName="([^"]*)"[^>]*CalculatedScoreString="([^"]*)"[^>]*CalculatedScoreRaw="([^"]*)"', re.DOTALL)

    grades = []
    course_blocks = re.split(r'(?=<Course\s)', gradebook_raw)

    for block in course_blocks:
        course_match = course_pattern.search(block)
        if not course_match:
            continue
        course_name = course_match.group(1)
        teacher = course_match.group(2)

        mark_match = mark_pattern.search(block)
        if not mark_match:
            continue

        letter = mark_match.group(2)
        raw = mark_match.group(3)

        try:
            percentage = round(float(raw), 1)
        except:
            percentage = None

        if letter == "N/A" or not letter:
            continue

        grades.append({
            "course": course_name,
            "teacher": teacher,
            "letter": letter,
            "percentage": percentage
        })

    return grades


def get_gradebook_detail(district_url, username, password):
    gradebook_raw = _gradebook_raw(district_url, username, password)
    if not gradebook_raw:
        return []
    course_blocks = re.split(r'(?=<Course\s)', gradebook_raw)

    courses = []
    course_pattern = re.compile(r'<Course\s[^>]*Title="([^"]*)"[^>]*Staff="([^"]*)"', re.DOTALL)
    mark_pattern = re.compile(
        r'<Mark\s[^>]*MarkName="([^"]*)"[^>]*CalculatedScoreString="([^"]*)"[^>]*CalculatedScoreRaw="([^"]*)"',
        re.DOTALL
    )
    calc_pattern = re.compile(
        r'<AssignmentGradeCalc\s[^>]*Type="([^"]*)"[^>]*Weight="([^"]*)"[^>]*Points="([^"]*)"[^>]*PointsPossible="([^"]*)"[^>]*WeightedPct="([^"]*)"[^>]*CalculatedMark="([^"]*)"',
        re.DOTALL
    )
    assignment_pattern = re.compile(r'<Assignment\s([^>]*?)(?:/>|>)', re.DOTALL)

    def get_attr(attrs_str, attr_name):
        match = re.search(rf'{attr_name}="([^"]*)"', attrs_str)
        # Values are still XML-escaped after the payload is unwrapped;
        # without this, "Read & annotate" reached the planner as "&amp;".
        return html_module.unescape(match.group(1)) if match else ""

    def parse_float(s):
        try:
            return float(re.sub(r'[^0-9.]', '', s))
        except:
            return None

    for block in course_blocks:
        course_match = course_pattern.search(block)
        if not course_match:
            continue
        course_name = course_match.group(1)
        teacher = course_match.group(2)

        mark_match = mark_pattern.search(block)
        if not mark_match:
            continue
        letter = mark_match.group(2)
        raw_score = mark_match.group(3)
        try:
            percentage = round(float(raw_score), 2)
        except:
            percentage = None

        if not letter or letter == "N/A":
            continue

        # Parse categories with weights
        categories = {}
        for calc in calc_pattern.finditer(block):
            cat_type = calc.group(1)
            if cat_type == "TOTAL":
                continue
            categories[cat_type] = {
                "type": cat_type,
                "weight": parse_float(calc.group(2)),
                "points": parse_float(calc.group(3)),
                "points_possible": parse_float(calc.group(4)),
                "weighted_pct": parse_float(calc.group(5)),
                "mark": calc.group(6),
            }

        # Parse assignments
        assignments = []
        for a_match in assignment_pattern.finditer(block):
            attrs = a_match.group(1)
            title = get_attr(attrs, "Measure")
            due_date_str = get_attr(attrs, "DueDate")
            point_earned = get_attr(attrs, "Point")
            point_possible = get_attr(attrs, "PointPossible")
            display_score = get_attr(attrs, "DisplayScore")
            score = get_attr(attrs, "Score")
            cat_type = get_attr(attrs, "Type")
            description = get_attr(attrs, "MeasureDescription")

            if not title:
                continue

            earned = parse_float(point_earned)
            possible = parse_float(point_possible)

            graded = (
                earned is not None and
                possible is not None and
                display_score not in ("Not Graded", "Not Due", "") and
                score not in ("", "Not Graded")
            )

            try:
                due_date = datetime.strptime(due_date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
            except:
                due_date = None

            assignments.append({
                "title": title,
                "due_date": due_date,
                "points_earned": earned,
                "points_possible": possible,
                "display_score": display_score,
                "graded": graded,
                "category": cat_type,
                "description": description,
            })

        courses.append({
            "course": course_name,
            "teacher": teacher,
            "letter": letter,
            "percentage": percentage,
            "categories": list(categories.values()),
            "assignments": assignments,
        })

    return courses

def debug_gradebook(district_url, username, password):
    raw = _gradebook_raw(district_url, username, password)
    if not raw:
        print("No result found")
        return
    # Print first 3000 chars to see structure
    print(raw[:3000])

def get_missing_assignments(district_url, username, password):
    """Pull assignments with low/partial scores like 4/10 or 0/10."""
    gradebook_raw = _gradebook_raw(district_url, username, password)
    if not gradebook_raw:
        return []
    course_blocks = re.split(r'(?=<Course\s)', gradebook_raw)
    assignment_pattern = re.compile(r'<Assignment\s([^>]*?)(?:/>|>)', re.DOTALL)

    def get_attr(attrs_str, attr_name):
        match = re.search(rf'{attr_name}="([^"]*)"', attrs_str)
        # Values are still XML-escaped after the payload is unwrapped;
        # without this, "Read & annotate" reached the planner as "&amp;".
        return html_module.unescape(match.group(1)) if match else ""

    missing = []
    PRIORITY_COLORS = {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#22c55e"}

    for block in course_blocks:
        course_match = re.search(r'<Course\s[^>]*Title="([^"]*)"', block)
        if not course_match:
            continue
        course_name = course_match.group(1)

        for a_match in assignment_pattern.finditer(block):
            attrs = a_match.group(1)
            title = get_attr(attrs, "Measure")
            points_str = get_attr(attrs, "Points")
            display_score = get_attr(attrs, "DisplayScore")
            score_str = get_attr(attrs, "Score")
            due_date_str = get_attr(attrs, "DueDate")

            if not title:
                continue

            # Skip ungraded/pending
            if display_score in ("Not Graded", "Not Due", ""):
                continue
            if score_str in ("", "Not Graded"):
                continue

            # Parse points
            earned = None
            possible = None
            if "/" in points_str:
                parts = points_str.split("/")
                try:
                    earned = float(parts[0].strip().split()[0])
                    possible = float(parts[1].strip().split()[0])
                except:
                    pass

            if earned is None or possible is None or possible <= 0:
                continue

            pct = earned / possible

            # Flag: zero score OR below 60% (missing or very low)
            is_missing = earned == 0 or pct < 0.6

            if not is_missing:
                continue

            try:
                due_date = datetime.strptime(due_date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
            except:
                due_date = ""

            label = "Missing" if earned == 0 else f"{int(earned)}/{int(possible)}"

            missing.append({
                "title": title,
                "course": course_name,
                "due_date": due_date,
                "points_earned": earned,
                "points_possible": possible,
                "display_score": display_score,
                "priority": "High",
                "estimated_time": 60,
                "source": "studentvue_missing",
                "is_missing": True,
                "score_label": label,
                "color": PRIORITY_COLORS["High"],
                "difficulty": "Medium",
                "estimated_time": 60,
            })

    return missing

if __name__ == "__main__":
    debug_gradebook(
        "https://example-psv.edupoint.com",
        "student-id",
        "password"
    )
