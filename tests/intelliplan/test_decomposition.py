"""Decomposition tests.

Two properties carry the feature: it fires on work whose phases are genuinely
different activities, and it stays out of the way everywhere else. Most of
these tests are about the second.
"""

from __future__ import annotations

import pytest

from intelliplan.intelligence.decomposition import (
    MIN_DECOMPOSE_MINUTES,
    MIN_STAGE_MINUTES,
    decompose,
    should_decompose,
)


def keys(result):
    return [s.key for s in result.stages]


# ── When it should fire ───────────────────────────────────────────────


def test_a_research_paper_gets_the_research_paper_shape():
    result = decompose(
        600, title="Research paper: the New Deal", kind="project",
        description="Use at least six sources with MLA citations.",
    )
    assert result.template == "research_paper"
    assert keys(result)[:3] == ["understand", "research", "thesis"]
    assert "proofread" in keys(result)


def test_an_exam_is_learn_then_practice_then_mistakes():
    result = decompose(300, title="Physics midterm", kind="exam")
    assert result.template == "exam"
    order = keys(result)
    assert order.index("learn") < order.index("practice") < order.index("mistakes")


def test_a_presentation_reserves_time_to_rehearse():
    result = decompose(240, title="Group presentation on ecosystems", kind="project")
    assert result.template == "presentation"
    rehearse = next(s for s in result.stages if s.key == "rehearse")
    assert rehearse.minutes >= 40


def test_the_kind_carries_it_when_the_title_says_nothing():
    result = decompose(300, title="Unit 4", kind="exam")
    assert result.applied


# ── When it must not fire ─────────────────────────────────────────────


def test_short_work_is_left_alone():
    result = decompose(60, title="Essay on Macbeth", kind="project")
    assert not result.applied
    assert "overhead" in result.reason


@pytest.mark.parametrize(
    "title",
    ["Problem set 7", "Vocabulary worksheet", "Kinematics drill", "Unit 3 quiz"],
)
def test_repetitive_work_is_never_decomposed(title):
    """The fifth set of ten problems is the same activity as the first.
    Naming stages for it is theatre."""
    result = decompose(400, title=title, kind="homework")
    assert not result.applied
    assert "interchangeable" in result.reason


def test_a_long_problem_set_is_repetitive_even_with_a_bland_title():
    result = decompose(400, title="Section 4.2", kind="homework", problem_count=30)
    assert not result.applied


def test_unrecognisable_work_is_left_alone_rather_than_forced():
    result = decompose(400, title="Zzyzx", kind="homework")
    assert not result.applied
    assert "shape" in result.reason


def test_should_decompose_agrees_with_decompose():
    args = dict(title="Research paper", kind="project", description="")
    ok, _ = should_decompose(600, **args)
    assert ok is decompose(600, **args).applied


# ── Allocation ────────────────────────────────────────────────────────


def test_the_stages_add_up_to_exactly_the_whole():
    """A plan whose parts sum to 4h55 for a 5h task invites the student to go
    hunting for the missing five minutes."""
    for total in (150, 233, 300, 601, 1200):
        result = decompose(total, title="Research paper", kind="project")
        assert sum(s.minutes for s in result.stages) == total, total


def test_no_stage_is_too_small_to_sit_down_for():
    result = decompose(MIN_DECOMPOSE_MINUTES, title="Research paper", kind="project")
    assert all(s.minutes >= MIN_STAGE_MINUTES for s in result.stages)


def test_folding_a_small_stage_keeps_the_chain_intact():
    """A folded stage's dependents must not end up depending on nothing —
    that would license starting the draft before the research."""
    result = decompose(MIN_DECOMPOSE_MINUTES, title="Research paper", kind="project")
    present = {s.key for s in result.stages}
    for stage in result.stages[1:]:
        assert stage.depends_on
        assert all(d in present for d in stage.depends_on)


def test_bigger_work_gets_proportionally_bigger_stages():
    small = decompose(300, title="Research paper", kind="project")
    large = decompose(900, title="Research paper", kind="project")
    small_draft = next(s for s in small.stages if s.key == "draft")
    large_draft = next(s for s in large.stages if s.key == "draft")
    assert large_draft.minutes > small_draft.minutes * 2


def test_the_first_stage_depends_on_nothing():
    result = decompose(600, title="Research paper", kind="project")
    assert result.stages[0].depends_on == ()


def test_revision_always_comes_after_drafting():
    """Revising a draft that does not exist is not a thing a person can do at
    7pm on Tuesday."""
    result = decompose(600, title="Essay on Hamlet", kind="project")
    order = keys(result)
    assert order.index("draft") < order.index("revise")
    revise = next(s for s in result.stages if s.key == "revise")
    assert "draft" in revise.depends_on


# ── Robustness ────────────────────────────────────────────────────────


def test_zero_minutes_produces_nothing_rather_than_dividing_by_it():
    assert not decompose(0, title="Research paper", kind="project").applied


def test_negative_minutes_are_survived():
    assert not decompose(-90, title="Research paper", kind="project").applied


def test_a_research_paper_is_not_matched_as_a_generic_essay():
    """Template order matters: "research paper" contains "paper", which is an
    essay trigger, so the specific template has to be tried first."""
    result = decompose(600, title="Research paper", kind="project")
    assert result.template == "research_paper"
