"""Tests for schedule-response parsing and truncated-JSON recovery.

Background: gemini-2.5-flash bills internal reasoning against
max_output_tokens. An uncapped hard prompt measured 7541 thinking / 444 output
on an 8000 budget and returned JSON cut off mid-string, which surfaced to the
student as "The AI returned an invalid schedule."
"""

import json

import pytest

from App import _parse_schedule_json, _repair_truncated_json

GOOD = {
    "schedule": [
        {"date": "2026-07-27", "day_name": "Monday", "total_hours": 2,
         "blocks": [{"assignment": "Essay", "course": "English",
                     "duration_minutes": 45, "time_slot": "7:00 PM - 7:45 PM",
                     "notes": "Outline it", "is_break": False}],
         "daily_tip": "Start with the outline."}],
    "overview": "A plan", "total_study_time": "45 minutes",
}

# Captured verbatim from a real failing gemini-2.5-flash call (finish_reason
# MAX_TOKENS) — cut off inside a string value.
REAL_TRUNCATED = """{
  "schedule": [
    {
      "date": "2026-07-28",
      "day_name": "Tuesday",
      "total_hours": 2,
      "blocks": [
        {
          "assignment": "Assignment 1",
          "course": "Math",
          "duration_minutes": 45,
          "time_slot": "5:00 PM - 5:45 PM",
          "notes": "Work the first five problems.",
          "is_break": false
        },
        {
          "assignment": "Break",
          "course": "N/"""


# ── happy paths ───────────────────────────────────────────────────


def test_plain_json_parses():
    assert _parse_schedule_json(json.dumps(GOOD))["overview"] == "A plan"


def test_code_fenced_json_parses():
    assert _parse_schedule_json("```json\n" + json.dumps(GOOD) + "\n```")["overview"] == "A plan"


def test_json_embedded_in_prose_is_extracted():
    raw = "Here is your schedule:\n" + json.dumps(GOOD) + "\nHope that helps!"
    assert _parse_schedule_json(raw)["overview"] == "A plan"


# ── truncation recovery ───────────────────────────────────────────


def test_real_truncated_response_is_recovered():
    data = _parse_schedule_json(REAL_TRUNCATED)
    assert len(data["schedule"]) == 1
    blocks = data["schedule"][0]["blocks"]
    # The complete block survives; the half-written one is dropped.
    assert len(blocks) == 1
    assert blocks[0]["assignment"] == "Assignment 1"


def test_repair_closes_open_brackets():
    data = _repair_truncated_json('{"schedule": [{"date": "x", "blocks": [{"a": 1}')
    assert data["schedule"][0]["blocks"] == [{"a": 1}]


def test_repair_ignores_brackets_inside_strings():
    raw = '{"schedule": [{"date": "a}]b", "blocks": [{"n": "c{["}]'
    data = _repair_truncated_json(raw)
    assert data["schedule"][0]["date"] == "a}]b"


def test_repair_respects_escaped_quotes():
    raw = '{"schedule": [{"date": "d", "blocks": [{"n": "say \\"hi\\""}]'
    data = _repair_truncated_json(raw)
    assert data["schedule"][0]["blocks"][0]["n"] == 'say "hi"'


def test_repair_returns_none_for_complete_json():
    assert _repair_truncated_json('{"a": 1}') is None


def test_repair_returns_none_when_nothing_is_complete():
    assert _repair_truncated_json('{"schedule": [{"date": "unfinis') is None


# ── rejection: caller must retry, not ship an empty plan ──────────


def test_unparseable_text_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_schedule_json("I'm sorry, I can't help with that.")


def test_missing_schedule_array_raises():
    with pytest.raises(ValueError, match="no 'schedule' array"):
        _parse_schedule_json(json.dumps({"overview": "nope"}))


def test_empty_schedule_array_raises():
    with pytest.raises(ValueError, match="no 'schedule' array"):
        _parse_schedule_json(json.dumps({"schedule": []}))


def test_days_with_no_blocks_raise():
    payload = {"schedule": [{"date": "2026-07-27", "blocks": []}]}
    with pytest.raises(ValueError, match="no blocks"):
        _parse_schedule_json(json.dumps(payload))


def test_json_array_at_top_level_raises():
    with pytest.raises(ValueError, match="expected object"):
        _parse_schedule_json("[1, 2, 3]")


@pytest.mark.parametrize("bad", ["", None, "   "])
def test_blank_responses_raise(bad):
    with pytest.raises(json.JSONDecodeError):
        _parse_schedule_json(bad)
