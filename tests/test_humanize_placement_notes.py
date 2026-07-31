"""Tests for the placement notes humanize_schedule() attaches to a plan.

A schedule that quietly changes shape is worse than one that changes shape and
says so. When the engine splits a long assignment into sittings, or pushes work
to another day, the student has to be told which assignment moved and why —
otherwise the plan looks arbitrary and they stop following it.
"""

from datetime import date, timedelta

import App
from scheduler_engine import StudyDNA

# A Monday far enough ahead that windows_for_date() never trims to "now".
MONDAY = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
AVAILABILITY = {"Mon": ["morning", "evening"]}


def _plan(*blocks):
    return {
        "schedule": [
            {
                "date": MONDAY.strftime("%Y-%m-%d"),
                "day_name": "Monday",
                "blocks": [dict(b) for b in blocks],
            }
        ]
    }


def _block(title, minutes, course="History"):
    return {
        "assignment": title,
        "course": course,
        "duration_minutes": minutes,
        "is_break": False,
    }


def _humanize(plan, stamina=30):
    dna = StudyDNA(sample_size=10, best_slot="morning", stamina_minutes=stamina)
    return App.humanize_schedule(
        plan, "morning", 3, availability=AVAILABILITY, commitments="", dna=dna
    )


def _work(plan):
    return [
        b
        for day in plan["schedule"]
        for b in day.get("blocks", [])
        if not b.get("is_break")
    ]


def test_a_split_assignment_is_explained_once_not_once_per_sitting():
    plan = _humanize(_plan(_block("Research paper", 180)))
    notes = plan.get("placement_notes") or []
    split_notes = [n for n in notes if "Research paper" in n]
    assert len(split_notes) == 1
    assert "4" in split_notes[0], "the note should say how many sittings"


def test_the_split_note_names_the_assignment_not_the_numbered_sitting():
    # The blocks are titled "Research paper (part 2 of 4)". A note repeating
    # that would read as if a sitting had itself been split again.
    plan = _humanize(_plan(_block("Research paper", 180)))
    note = next(n for n in plan["placement_notes"] if "Research paper" in n)
    assert "part 2 of 4" not in note
    assert '"Research paper"' in note


def test_no_split_note_when_nothing_was_split():
    plan = _humanize(_plan(_block("Short reading", 30)))
    notes = plan.get("placement_notes") or []
    assert not [n for n in notes if "sitting" in n]


def test_every_minute_survives_the_split_that_the_note_describes():
    plan = _humanize(_plan(_block("Research paper", 180)))
    assert sum(b["duration_minutes"] for b in _work(plan)) == 180


def test_each_sitting_keeps_the_parent_title_for_grouping():
    plan = _humanize(_plan(_block("Research paper", 180)))
    parts = [b for b in _work(plan) if b.get("part_total")]
    assert parts, "a 180-minute block should have been split"
    assert {b["parent_title"] for b in parts} == {"Research paper"}
