import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import html as html_module
import re

SOAP_ACTION = "http://edupoint.com/webservices/ProcessWebServiceRequest"

def make_request(district_url, username, password, method, params="&lt;Parms/&gt;"):
    url = f"{district_url}/Service/PXPCommunication.asmx"
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": SOAP_ACTION
    }
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ProcessWebServiceRequest xmlns="http://edupoint.com/webservices/">
      <userID>{username}</userID>
      <password>{password}</password>
      <skipLoginLog>1</skipLoginLog>
      <parent>0</parent>
      <webServiceHandleName>PXPWebServices</webServiceHandleName>
      <methodName>{method}</methodName>
      <paramStr>{params}</paramStr>
    </ProcessWebServiceRequest>
  </soap:Body>
</soap:Envelope>"""
    response = requests.post(url, headers=headers, data=body, timeout=15)
    return response.text

def test_login(district_url, username, password):
    result = make_request(district_url, username, password, "StudentInfo")
    if "RT_ERROR" in result or "Invalid user" in result:
        return False
    return True

def get_courses(district_url, username, password):
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )
    
    inner_match = re.search(r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>', result, re.DOTALL)
    if not inner_match:
        return []
    
    gradebook_raw = html_module.unescape(inner_match.group(1))
    course_pattern = re.compile(r'<Course[^>]*Title="([^"]*)"', re.DOTALL)
    
    courses = []
    seen = set()
    for match in course_pattern.finditer(gradebook_raw):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            courses.append({"name": name})
    
    return courses

def get_assignments(district_url, username, password):
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )

    inner_match = re.search(r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>', result, re.DOTALL)
    if not inner_match:
        print("Could not find inner result")
        return []

    gradebook_raw = html_module.unescape(inner_match.group(1))

    course_pattern = re.compile(r'<Course[^>]*Period="([^"]*)"[^>]*Title="([^"]*)"', re.DOTALL)
    assignment_pattern = re.compile(
        r'<Assignment\s([^>]*?)(?:/>|>)',
        re.DOTALL
    )

    def get_attr(attrs_str, attr_name):
        match = re.search(rf'{attr_name}="([^"]*)"', attrs_str)
        return match.group(1) if match else ""

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
            except:
                continue

            days = (due_date - today).days

            # Skip assignments older than 14 days
            if days < -14:
                continue

            if days < 0:
                priority = "High"
            elif days <= 3:
                priority = "High"
            elif days <= 7:
                priority = "Medium"
            else:
                priority = "Low"

            try:
                points_possible = float(points_str.split("/")[-1].strip().split()[0])
            except:
                points_possible = 60

            raw_minutes = points_possible * 1.5
            rounded_minutes = round(raw_minutes / 30) * 30
            rounded_minutes = max(30, rounded_minutes)

            assignments.append({
                "title": title,
                "course": course_name,
                "due_date": due_date.strftime("%Y-%m-%d"),
                "points_possible": points_possible,
                "priority": priority,
                "estimated_time": rounded_minutes,
                "display_score": display_score
            })

    return sorted(assignments, key=lambda x: x["due_date"])

# if __name__ == "__main__":
#     assignments = get_assignments(
#         "https://wa-nor-psv.edupoint.com",
#         "2009716",
#         "bluesnakesing5"
#     )
#     for a in assignments[:3]:
#         print(a)


def get_grades_raw(district_url, username, password):
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )
    inner_match = re.search(r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>', result, re.DOTALL)
    if not inner_match:
        return
    gradebook_raw = html_module.unescape(inner_match.group(1))
    # Find first Mark element
    mark_match = re.search(r'<Mark\s[^>]*>', gradebook_raw)
    course_match = re.search(r'<Course\s[^>]*>', gradebook_raw)
    if mark_match:
        print("MARK:", mark_match.group(0)[:300])
    if course_match:
        print("COURSE:", course_match.group(0)[:300])


def get_grades(district_url, username, password):
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )
    inner_match = re.search(r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>', result, re.DOTALL)
    if not inner_match:
        return []

    gradebook_raw = html_module.unescape(inner_match.group(1))
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
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )
    inner_match = re.search(
        r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>',
        result, re.DOTALL
    )
    if not inner_match:
        return []

    gradebook_raw = html_module.unescape(inner_match.group(1))
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
        return match.group(1) if match else ""

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
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )
    inner_match = re.search(
        r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>',
        result, re.DOTALL
    )
    if not inner_match:
        print("No result found")
        return
    raw = html_module.unescape(inner_match.group(1))
    # Print first 3000 chars to see structure
    print(raw[:3000])

def get_missing_assignments(district_url, username, password):
    """Pull assignments with low/partial scores like 4/10 or 0/10."""
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )
    inner_match = re.search(
        r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>',
        result, re.DOTALL
    )
    if not inner_match:
        return []

    gradebook_raw = html_module.unescape(inner_match.group(1))
    course_blocks = re.split(r'(?=<Course\s)', gradebook_raw)
    assignment_pattern = re.compile(r'<Assignment\s([^>]*?)(?:/>|>)', re.DOTALL)

    def get_attr(attrs_str, attr_name):
        match = re.search(rf'{attr_name}="([^"]*)"', attrs_str)
        return match.group(1) if match else ""

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

            if not title or not points_str or "/" not in points_str:
                continue

            # Parse earned/possible
            parts = points_str.split("/")
            try:
                earned = float(parts[0].strip().split()[0])
                possible = float(parts[1].strip().split()[0])
            except:
                continue

            if possible <= 0:
                continue

            pct = earned / possible

            # Flag missing (0 score) or low scores (below 60%)
            is_missing = (
                earned == 0 and display_score not in ("Not Graded", "Not Due", "")
            ) or (
                pct < 0.6 and display_score not in ("Not Graded", "Not Due", "")
            )

            if not is_missing:
                continue

            try:
                due_date = datetime.strptime(due_date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
            except:
                due_date = ""

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
                "score_label": f"{int(earned)}/{int(possible)}",
                "color": PRIORITY_COLORS["High"],
                "difficulty": "Medium"
            })

    return missing

def get_missing_assignments(district_url, username, password):
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )
    inner_match = re.search(
        r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>',
        result, re.DOTALL
    )
    if not inner_match:
        return []

    gradebook_raw = html_module.unescape(inner_match.group(1))
    course_blocks = re.split(r'(?=<Course\s)', gradebook_raw)
    assignment_pattern = re.compile(r'<Assignment\s([^>]*?)(?:/>|>)', re.DOTALL)

    def get_attr(attrs_str, attr_name):
        match = re.search(rf'{attr_name}="([^"]*)"', attrs_str)
        return match.group(1) if match else ""

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
            score_type = get_attr(attrs, "ScoreType")

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
        "https://wa-nor-psv.edupoint.com",
        "2009716",
        "bluesnakesing5"
    )