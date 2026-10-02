"""Timetable import mapping, from recorded-shape payloads. No network.

The StudentVUE SOAP shape follows the ``StudentClassList`` reply the
open-source StudentVUE clients parse (``ClassListing`` rows plus
``TodayScheduleInfoData``). The JSON-API and Schoology shapes are the
camelCase / documented field names; neither has been confirmed against a
live district, which is why the mappers are tolerant and these tests cover
the tolerance.
"""

import html
import json
from datetime import date

from intelliplan.integrations.timetable_import import (
    build_studentvue_timetable,
    fetch_schoology_timetable,
    fetch_studentvue_timetable,
    map_schoology_sections,
    parse_photo_timetable,
    parse_rotation_label,
    parse_studentvue_class_list,
    studentvue_json_records,
)

CLASS_LIST_XML = """<StudentClassSchedule xmlns:xsd="http://www.w3.org/2001/XMLSchema" TermIndex="0" TermIndexName="Semester 1">
  <ClassLists>
    <ClassListing Period="1" CourseTitle="AP English" RoomName="204" Teacher="Smith, J" SectionGU="S1" />
    <ClassListing Period="2" CourseTitle="Chemistry" RoomName="Lab 3" Teacher="Lee, K" SectionGU="S2" />
    <ClassListing Period="3" CourseTitle="Art &amp; Design" RoomName="110" Teacher="Ono, M" SectionGU="S3" />
    <ClassListing Period="4" CourseTitle="Spanish II" RoomName="215" Teacher="Diaz, R" SectionGU="S4" />
  </ClassLists>
  <TodayScheduleInfoData SchoolDate="9/28/2026" OutputDate="Monday, September 28, 2026">
    <SchoolInfos>
      <SchoolInfo SchoolName="North HS" BellSchedName="A Day" SchoolID="1">
        <Classes>
          <ClassInfo Period="1" ClassName="AP English" StartTime="7:45 AM" EndTime="9:15 AM" TeacherName="Smith, J" RoomName="204" SectionGU="S1" />
          <ClassInfo Period="2" ClassName="Chemistry" StartTime="9:25 AM" EndTime="10:55 AM" TeacherName="Lee, K" RoomName="Lab 3" SectionGU="S2" />
        </Classes>
      </SchoolInfo>
    </SchoolInfos>
  </TodayScheduleInfoData>
</StudentClassSchedule>"""


def soap_envelope(inner):
    return (
        '<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body><ProcessWebServiceRequestResponse xmlns=\"http://edupoint.com/webservices/\">"
        f"<ProcessWebServiceRequestResult>{html.escape(inner)}</ProcessWebServiceRequestResult>"
        "</ProcessWebServiceRequestResponse></soap:Body></soap:Envelope>"
    )


def by_course(rows):
    return {r["course"]: r for r in rows}


# ── StudentVUE SOAP ──────────────────────────────────────────────────


def test_soap_class_list_parses_listing_and_today():
    parsed = parse_studentvue_class_list(soap_envelope(CLASS_LIST_XML))
    assert [r["course"] for r in parsed["listing"]] == ["AP English", "Chemistry", "Art & Design", "Spanish II"]
    assert parsed["listing"][1]["room"] == "Lab 3" and parsed["listing"][1]["teacher"] == "Lee, K"
    (today,) = parsed["days"]
    assert today["date"] == date(2026, 9, 28) and today["bell"] == "A Day"
    assert [(r["start"], r["end"]) for r in today["classes"]] == [("07:45", "09:15"), ("09:25", "10:55")]


def test_an_a_day_sample_splits_classes_into_a_and_b():
    parsed = parse_studentvue_class_list(CLASS_LIST_XML)
    result = build_studentvue_timetable(parsed["listing"], parsed["days"])
    rows = by_course(result["classes"])
    assert rows["AP English"]["rotation_days"] == [1]
    assert rows["AP English"]["start"] == "07:45" and rows["AP English"]["period"] == "1"
    assert rows["Art & Design"]["rotation_days"] == [2]
    assert rows["Art & Design"]["start"] == ""            # not seen today: no time yet
    assert result["rotation_hint"] == {"kind": "ab", "length": 2, "today_is": 1, "date": "2026-09-28"}
    assert result["bell"] == {"1": ["07:45", "09:15"], "2": ["09:25", "10:55"]}


def test_a_bell_name_alone_is_not_evidence_of_rotation():
    """Every class met today: 'A Day' names the bell, nothing rotates."""
    listing = [{"course": "Math", "period": "1", "external_id": "", "start": "", "end": ""}]
    today = [{"date": date(2026, 9, 28), "bell": "A Day",
              "classes": [{"course": "Math", "period": "1", "external_id": "", "start": "08:00", "end": "09:00"}]}]
    result = build_studentvue_timetable(listing, today)
    assert not result["classes"][0].get("rotation_days")
    assert result["classes"][0]["start"] == "08:00"


def test_hidden_class_times_are_not_imported():
    xml = ('<StudentClassSchedule><TodayScheduleInfoData SchoolDate="9/28/2026"><SchoolInfos><SchoolInfo>'
           '<Classes><ClassInfo Period="1" ClassName="Math" StartTime="8:00 AM" EndTime="9:00 AM" '
           'HideClassStartEndTime="true" /></Classes></SchoolInfo></SchoolInfos></TodayScheduleInfoData>'
           '</StudentClassSchedule>')
    parsed = parse_studentvue_class_list(xml)
    assert parsed["days"] == [] or parsed["days"][0]["classes"][0]["start"] == ""


def test_malformed_xml_falls_back_to_attribute_scan():
    broken = CLASS_LIST_XML.replace("</ClassLists>", "")  # no longer well-formed
    parsed = parse_studentvue_class_list(broken)
    assert len(parsed["listing"]) == 4
    assert parsed["days"][0]["bell"] == "A Day"


class FakeSoapHelper:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def make_request(self, district_url, username, password, method, params):
        self.calls.append(method)
        if self.error:
            raise self.error
        return self.reply


def test_fetch_uses_soap_when_the_district_serves_it():
    helper = FakeSoapHelper(reply=soap_envelope(CLASS_LIST_XML))
    out = fetch_studentvue_timetable("https://x.edupoint.com", "u", "p", helper=helper)
    assert helper.calls == ["StudentClassList"]
    assert out["via"] == "soap" and len(out["classes"]) == 4


# ── StudentVUE JSON API ──────────────────────────────────────────────


class FakeJsonHelper(FakeSoapHelper):
    """SOAP answers RT_ERROR (method retired); the JSON API has the data."""

    def __init__(self, days):
        super().__init__(reply=soap_envelope('<RT_ERROR ERROR_MESSAGE="Invalid" />'))
        self.days = days
        self.json_calls = []

    def json_login(self, district_url, username, password):
        return "tok"

    def json_call(self, district_url, token, method, fields):
        self.json_calls.append((method, fields.get("date")))
        if method == "StudentClassList":
            return {"classLists": [
                {"period": "1", "courseTitle": "Algebra II", "roomName": "101", "teacher": "Kim", "sectionGU": "A1"},
                {"period": "2", "courseTitle": "Band", "roomName": "Music", "teacher": "Fox", "sectionGU": "A2"},
            ]}
        return self.days.get(fields.get("date"), {})


def test_json_fallback_samples_days_to_find_meeting_days():
    day = lambda cls: {"schoolDate": "", "classes": cls}  # noqa: E731
    algebra = {"period": "1", "className": "Algebra II", "startTime": "08:00", "endTime": "08:50", "sectionGU": "A1"}
    band = {"period": "2", "className": "Band", "startTime": "09:00", "endTime": "09:50", "sectionGU": "A2"}
    helper = FakeJsonHelper({
        "09/28/2026": day([algebra, band]),  # Mon
        "09/29/2026": day([algebra]),        # Tue
        "09/30/2026": day([algebra, band]),  # Wed
        "10/01/2026": day([algebra]),        # Thu
        "10/02/2026": day([algebra, band]),  # Fri
    })
    out = fetch_studentvue_timetable("https://x", "u", "p", today=date(2026, 9, 28), helper=helper)
    assert out["via"] == "json"
    rows = by_course(out["classes"])
    assert rows["Band"]["weekdays"] == ["Mon", "Wed", "Fri"]
    assert rows["Band"]["start"] == "09:00"
    assert rows["Algebra II"]["weekdays"] == []  # every sampled day = every day
    assert ("GetStudentClasesForGivenDay", "09/28/2026") in helper.json_calls


def test_json_records_ignore_non_class_objects():
    payload = {"meta": {"name": "North HS"}, "todayScheduleInfoData": {
        "schoolDate": "9/28/2026", "schoolInfos": [{"bellSchedName": "Day 3", "classes": [
            {"period": "4", "className": "Physics", "startTime": "1:05 PM", "endTime": "1:55 PM"},
        ]}]}}
    parsed = studentvue_json_records(payload)
    assert [r["course"] for r in parsed["rows"]] == ["Physics"]
    assert parsed["bell"] == "Day 3" and parsed["date"] == date(2026, 9, 28)
    assert parsed["rows"][0]["start"] == "13:05"


def test_rotation_labels():
    assert parse_rotation_label("A Day") == ("ab", 1)
    assert parse_rotation_label("Day B Bell") == ("ab", 2)
    assert parse_rotation_label("Day 4") == ("cycle", 4)
    assert parse_rotation_label("Week 2 Schedule") == ("week", 2)
    assert parse_rotation_label("Regular") is None


# ── Schoology ────────────────────────────────────────────────────────


SECTIONS = [
    {"id": "901", "course_title": "Biology", "section_title": "Period 3", "location": "B12",
     "meeting_days": ["1", "3", "5"], "start_time": "10:05:00", "end_time": "10:55:00"},
    {"id": "902", "course_title": "Debate", "section_title": "Debate - 7th period",
     "meeting_days": ["Tue", "Thu"], "start_time": "2:00pm", "end_time": "2:50pm"},
    {"id": "903", "course_title": "Advisory", "section_title": "Adv"},
    {"id": "904", "course_title": ""},
]


def test_schoology_sections_map_to_rows():
    rows = by_course(map_schoology_sections(SECTIONS))
    assert set(rows) == {"Biology", "Debate", "Advisory"}
    bio = rows["Biology"]
    assert (bio["period"], bio["room"], bio["start"], bio["end"]) == ("3", "B12", "10:05", "10:55")
    assert bio["weekdays"] == ["Mon", "Wed", "Fri"]  # Sunday-based numbers → Mon-based names
    assert rows["Debate"]["period"] == "7" and rows["Debate"]["weekdays"] == ["Tue", "Thu"]
    assert rows["Debate"]["start"] == "14:00"
    assert rows["Advisory"]["start"] == "" and rows["Advisory"]["external_id"] == "903"


def test_schoology_fetch_builds_a_bell():
    class Helper:
        def make_schoology_request(self, key, secret, endpoint):
            assert endpoint == "/sections"
            return {"section": SECTIONS}

    out = fetch_schoology_timetable("k", "s", helper=Helper())
    assert out["bell"]["3"] == ["10:05", "10:55"]


# ── Photo ────────────────────────────────────────────────────────────


def test_photo_output_is_parsed_from_fenced_json():
    text = "```json\n" + json.dumps({
        "rotation": {"kind": "ab", "length": 2},
        "classes": [
            {"course": "History", "period": "5", "room": "301", "teacher": "Ng",
             "start": "12:40", "end": "1:30 PM", "days": ["Mon", "Wed"], "rotation_days": ["B"]},
            {"course": ""},
        ],
    }) + "\n```"
    out = parse_photo_timetable(text)
    (row,) = out["classes"]
    assert row["end"] == "13:30" and row["weekdays"] == ["Mon", "Wed"] and row["rotation_days"] == [2]
    assert row["source"] == "photo"
    assert out["rotation_hint"]["kind"] == "ab"


def test_photo_garbage_yields_nothing():
    assert parse_photo_timetable("I can't read this image.") == {"classes": [], "rotation_hint": None}


def test_class_list_refuses_entity_expansion_and_still_parses_safely():
    # A district reply is untrusted input. An entity bomb must not be
    # expanded; the regex fallback still reads the plain attributes.
    from intelliplan.integrations.timetable_import import parse_studentvue_class_list

    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<StudentClassSchedule><ClassLists>'
        '<ClassListing Period="1" CourseTitle="Biology &b;" RoomName="12" Teacher="Lee" />'
        '</ClassLists></StudentClassSchedule>'
    )
    parsed = parse_studentvue_class_list(bomb)
    titles = [row.get("course") or row.get("name") or row.get("title") for row in parsed["listing"]]
    assert all("aaaaaaaaaa" not in (t or "") for t in titles)
