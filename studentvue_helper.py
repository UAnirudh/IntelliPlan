import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import html
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


def parse_assignment_attributes(raw_attributes):
    return dict(re.findall(r'(\w+)="([^"]*)"', raw_attributes))


def is_submitted_ungraded_studentvue_assignment(attributes):
    submitted_flags = (
        attributes.get("Status", ""),
        attributes.get("SubmissionStatus", ""),
        attributes.get("Submitted", ""),
    )
    submitted_values = {value.strip().lower() for value in submitted_flags if value.strip()}
    if "submitted" not in submitted_values and "true" not in submitted_values:
        return False

    score_fields = (
        attributes.get("Score", ""),
        attributes.get("Points", ""),
    )
    for field in score_fields:
        field_text = field.strip()
        if not field_text:
            continue

        earned_points = field_text.split("/")[0].strip()
        if earned_points:
            return False

    return True

def get_assignments(district_url, username, password):
    result = make_request(
        district_url, username, password, "Gradebook",
        "&lt;Parms&gt;&lt;ChildIntID&gt;0&lt;/ChildIntID&gt;&lt;/Parms&gt;"
    )

    import html as html_module

    # Extract inner XML from SOAP wrapper using regex instead of parser
    inner_match = re.search(r'<ProcessWebServiceRequestResult>(.*?)</ProcessWebServiceRequestResult>', result, re.DOTALL)
    if not inner_match:
        print("Could not find inner result")
        return []

    gradebook_raw = html_module.unescape(inner_match.group(1))

    # Extract Course titles
    course_pattern = re.compile(r'<Course[^>]*Period="([^"]*)"[^>]*Title="([^"]*)"', re.DOTALL)
    assignment_pattern = re.compile(
        r'<Assignment\s+([^>]*)/?>',
        re.DOTALL
    )

    assignments = []
    today = datetime.now(timezone.utc)

    # Split by Course to associate assignments with courses
    course_blocks = re.split(r'(?=<Course\s)', gradebook_raw)

    for block in course_blocks:
        course_match = course_pattern.search(block)
        if not course_match:
            continue
        course_name = course_match.group(2)

        for a_match in assignment_pattern.finditer(block):
            attributes = parse_assignment_attributes(a_match.group(1))
            if is_submitted_ungraded_studentvue_assignment(attributes):
                continue

            title = attributes.get("Measure", "")
            due_date_str = attributes.get("DueDate", "")
            points_str = attributes.get("Points", "")

            if not title or not due_date_str:
                continue

            try:
                due_date = datetime.strptime(due_date_str, "%m/%d/%Y")
                due_date = due_date.replace(tzinfo=timezone.utc)
            except:
                continue

            days = (due_date - today).days
            if days <= 3: priority = "High"
            elif days <= 7: priority = "Medium"
            else: priority = "Low"

            try:
                points_possible = float(points_str.split("/")[-1].strip())
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
                "estimated_time": rounded_minutes
            })

    return sorted(assignments, key=lambda x: x["due_date"])


if __name__ == "__main__":
    assignments = get_assignments(
        "https://wa-nor-psv.edupoint.com",
        "2009716",
        "bluesnakesing5"
    )
    for a in assignments[:3]:
        print(a)
