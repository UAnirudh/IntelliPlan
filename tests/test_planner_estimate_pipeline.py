"""Regressions at the seam between App.py and the deterministic planner.

Both of these were live bugs that quietly degraded every plan: the estimate
correction was applied twice, and enrichment could not recognise a split
sitting so it discarded the planner's own output for it.
"""

from datetime import date, timedelta

import App


TOMORROW = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")


def _assignment(**over):
    base = {
        "title": "Problem set 4",
        "course": "Math",
        "due_date": TOMORROW,
        "estimated_time": 60,
        "priority": "High",
        "difficulty": "Hard",
        "points_possible": 40,
    }
    base.update(over)
    return base


# ── Estimates are corrected once, by the planner's own model ──────────


def test_planner_rows_carry_the_raw_estimate():
    """``_planner_task_rows`` must hand the planner the *uncorrected* number.

    The planner runs its own EstimationModel over est_minutes. If App.py has
    already multiplied by the student's measured ratio, the two corrections
    compound — 1.5x becomes 2.25x — and the student gets a plan a third longer
    than the week they actually have.
    """
    rows = App._planner_task_rows([_assignment(estimated_time=60)], [])
    assert rows[0]["est_minutes"] == 60


def test_the_ai_path_still_gets_a_corrected_estimate():
    """The prompt says the estimates it receives are already calibrated, so
    the correction has to survive on that path — it just cannot happen before
    the planner branch."""
    from scheduler_engine import StudyDNA

    dna = StudyDNA(sample_size=20, estimation_ratio=1.5)
    assert dna.adjust_estimate(60, "Math") == 90


# ── Enrichment must recognise a split sitting ─────────────────────────


def _plan_with_split():
    return {
        "schedule": [
            {
                "date": TOMORROW,
                "day_name": "Wednesday",
                "blocks": [
                    {
                        "id": "b1",
                        "assignment": "Problem set 4 (part 2 of 3)",
                        "parent_title": "Problem set 4",
                        "duration_minutes": 40,
                        "priority": 88,
                        "is_break": False,
                    }
                ],
            }
        ]
    }


def test_a_split_sitting_keeps_its_priority_and_deadline():
    data = App.enrich_schedule_data(
        _plan_with_split(), [_assignment()], "evening", 2
    )
    block = data["schedule"][0]["blocks"][0]
    assert block["priority"] == "High"
    assert block["difficulty"] == "Hard"
    assert block["due_date"] == TOMORROW


def test_the_planners_numeric_priority_is_not_lost():
    data = App.enrich_schedule_data(
        _plan_with_split(), [_assignment()], "evening", 2
    )
    assert data["schedule"][0]["blocks"][0]["priority_score"] == 88


def test_an_unsplit_block_still_matches_by_its_own_title():
    plan = _plan_with_split()
    plan["schedule"][0]["blocks"][0].update(
        {"assignment": "Problem set 4", "parent_title": ""}
    )
    data = App.enrich_schedule_data(plan, [_assignment()], "evening", 2)
    assert data["schedule"][0]["blocks"][0]["priority"] == "High"


# ── Metadata sizing reaches the planner ───────────────────────────────


def test_metadata_beats_the_points_heuristic():
    """Two 50-point assignments, one an essay and one seven problems, must not
    arrive at the planner with the same duration."""
    essay = _assignment(
        title="Research paper",
        kind="project",
        estimated_time=75,
        description="Write a 2,000-word researched essay with citations.",
    )
    homework = _assignment(
        title="Section 3.4", estimated_time=75, description="Complete problems 12-18."
    )
    rows = {r["title"]: r for r in App._planner_task_rows([essay, homework], [])}
    assert rows["Research paper"]["est_minutes"] > 400
    assert rows["Section 3.4"]["est_minutes"] < 45


def test_a_student_supplied_duration_is_never_overruled():
    """Asking how long something takes and then ignoring the answer is worse
    than never asking."""
    row = App._planner_task_rows(
        [
            _assignment(
                estimated_time=30,
                estimate_source="student",
                description="Write a 2,000-word essay.",
            )
        ],
        [],
    )[0]
    assert row["est_minutes"] == 30


def test_an_empty_description_keeps_the_upstream_estimate():
    row = App._planner_task_rows([_assignment(estimated_time=75, description="")], [])[0]
    assert row["est_minutes"] == 75


def test_problem_counts_become_split_boundaries():
    row = App._planner_task_rows(
        [_assignment(description="Complete problems 1-20.")], []
    )[0]
    assert row["subtask_count"] == 20


def test_saved_descriptions_are_used_when_the_lms_has_none():
    """Students paste real instructions into IntelliPlan for assignments whose
    LMS description is blank. That text is the best sizing signal we have."""
    row = App._planner_task_rows(
        [_assignment(title="Essay", kind="project", estimated_time=60, description="")],
        [],
        descriptions={"Essay": "Write a 2,000-word essay."},
    )[0]
    assert row["est_minutes"] > 400


def test_the_lms_row_sizer_falls_back_to_points_without_metadata():
    minutes, description = App._lms_row_sizing({"name": "Worksheet"}, 40)
    assert minutes == 60
    assert description == ""


def test_the_lms_row_sizer_reads_html_descriptions():
    minutes, description = App._lms_row_sizing(
        {"name": "Reading", "description": "<p>Read pages 100-130.</p>"}, 20
    )
    assert 90 <= minutes <= 120
    assert "<p>" not in description


def test_the_lms_row_sizer_never_raises():
    minutes, _ = App._lms_row_sizing(None, "not a number")
    assert minutes > 0


# ── One priority engine ───────────────────────────────────────────────


def test_planner_rows_use_the_real_priority_engine():
    """A three-bucket label lookup cannot tell an overdue midterm from a
    worksheet due in a fortnight. The engine can, and the scheduler is the
    surface that acts on the answer."""
    far = (date.today() + timedelta(days=13)).strftime("%Y-%m-%d")
    overdue = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    rows = {
        r["title"]: r
        for r in App._planner_task_rows(
            [
                _assignment(title="Midterm exam", kind="exam", priority="Medium",
                            points_possible=100, due_date=overdue),
                _assignment(title="Vocab worksheet", priority="Medium",
                            points_possible=5, due_date=far),
            ],
            [],
        )
    }
    assert rows["Midterm exam"]["priority"] > rows["Vocab worksheet"]["priority"]


def test_priority_carries_its_reasons_for_the_ui():
    row = App._planner_task_rows(
        [_assignment(due_date=date.today().strftime("%Y-%m-%d"))], []
    )[0]
    assert row["priority_reasons"]


def test_a_student_marked_high_priority_is_respected():
    high = App._planner_task_rows([_assignment(priority="High")], [])[0]["priority"]
    low = App._planner_task_rows([_assignment(priority="Low")], [])[0]["priority"]
    assert high > low
