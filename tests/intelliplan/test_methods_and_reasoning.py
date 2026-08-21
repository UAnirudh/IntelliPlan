"""Study-method selection, and the LLM reasoning layer's guardrails."""

from __future__ import annotations

from datetime import date

import pytest

from intelliplan.domain.student import ActionCandidate, ActionKind, NextAction
from intelliplan.intelligence import methods, reasoning

TODAY = date(2026, 8, 21)


# ── methods ──────────────────────────────────────────────────────────


def test_low_mastery_builds_the_concept_before_drilling_it():
    plan = methods.recommend(mastery=0.25, confidence=0.8, minutes=45, kind="test")
    assert plan.band == "low"
    assert [s.key for s in plan.steps] == ["learn", "guided", "independent"]
    assert "low_mastery" in plan.reason_codes


def test_moderate_mastery_leads_with_retrieval():
    plan = methods.recommend(mastery=0.6, confidence=0.8, minutes=45, kind="test")
    assert plan.band == "moderate"
    assert plan.steps[0].key == "retrieval"
    assert plan.steps[-1].key == "errors"


def test_high_mastery_with_an_exam_close_trains_timed_performance():
    plan = methods.recommend(mastery=0.9, confidence=0.9, minutes=60, kind="test",
                             assessment_days_away=2)
    assert plan.band == "high"
    assert [s.key for s in plan.steps] == ["mixed", "timed", "errors"]
    assert "assessment_near" in plan.reason_codes


def test_high_mastery_with_no_exam_is_maintenance_not_re_teaching():
    plan = methods.recommend(mastery=0.9, confidence=0.9, minutes=60, kind="test")
    assert [s.key for s in plan.steps] == ["spaced", "interleave"]


def test_a_shaky_mastery_estimate_refuses_the_aggressive_ladder():
    """Recommending timed exam practice to someone we only *think* has the
    material is the expensive direction to be wrong in."""
    plan = methods.recommend(mastery=0.92, confidence=0.2, minutes=45, kind="test",
                             assessment_days_away=1)
    assert plan.band == "moderate"
    assert "low_confidence_estimate" in plan.reason_codes


def test_never_measured_material_starts_with_a_diagnostic():
    plan = methods.recommend(mastery=None, confidence=0.0, minutes=45, kind="test")
    assert plan.steps[0].key == "diagnose"
    assert "no_mastery_evidence" in plan.reason_codes


def test_routine_homework_is_not_dressed_up_as_a_study_ladder():
    plan = methods.recommend(mastery=None, minutes=45, kind="homework")
    assert plan.band == "routine"
    assert [s.key for s in plan.steps] == ["work", "check"]


def test_an_essay_gets_a_production_shape_not_a_revision_one():
    plan = methods.recommend(mastery=0.5, confidence=0.8, minutes=90, kind="essay")
    assert plan.band == "production"
    assert [s.key for s in plan.steps] == ["plan", "draft", "review"]


def test_admin_work_gets_no_method_at_all():
    plan = methods.recommend(mastery=None, minutes=15, kind="administrative")
    assert plan.band == "n/a"
    assert len(plan.steps) == 1


def test_a_short_sitting_is_not_split_into_a_three_step_ladder():
    plan = methods.recommend(mastery=0.5, confidence=0.8, minutes=15, kind="test")
    assert len(plan.steps) == 1
    assert "short_session" in plan.reason_codes


def test_the_steps_always_add_up_to_the_session():
    for minutes in (10, 25, 45, 90, 137):
        for mastery in (None, 0.2, 0.6, 0.95):
            plan = methods.recommend(mastery=mastery, confidence=0.8,
                                     minutes=minutes, kind="test")
            assert sum(s.minutes for s in plan.steps) == minutes


def test_a_method_plan_serialises_with_its_rationale():
    payload = methods.recommend(mastery=0.6, confidence=0.8, minutes=45,
                                kind="test").to_dict()
    assert payload["rationale"]
    assert payload["summary"].count("→") == 2


# ── reasoning layer ──────────────────────────────────────────────────


def an_action(*, reasons=("due_tomorrow", "high_academic_value"), confidence=0.8):
    return NextAction(
        candidate=ActionCandidate(
            kind=ActionKind.START_TASK,
            title="Physics problem set",
            course="Physics",
            minutes=45,
            task_id="t1",
            due_date=date(2026, 8, 22),
        ),
        score=0.8,
        confidence=confidence,
        reason_codes=reasons,
        components=(("deadline", 1.2),),
        completion_probability=0.74,
        method="Recall it closed-book first",
        explanation=(),
    )


def test_without_the_opt_in_nothing_is_sent_and_the_text_is_still_grounded():
    calls: list = []

    def ai(messages, **kwargs):
        calls.append(messages)
        return {"why": "should never be used"}

    result = reasoning.explain(an_action(), minutes_available=60,
                               personalization_enabled=False, ai_chat_json=ai)
    assert calls == []
    assert result.generated_by == "fallback-template"
    assert result.why


def test_a_failing_provider_falls_back_rather_than_erroring():
    def boom(messages, **kwargs):
        raise RuntimeError("provider down")

    result = reasoning.explain(an_action(), minutes_available=60,
                               personalization_enabled=True, ai_chat_json=boom)
    assert result.generated_by == "fallback-template"
    assert result.why


def test_malformed_json_falls_back():
    result = reasoning.explain(an_action(), minutes_available=60,
                               personalization_enabled=True,
                               ai_chat_json=lambda m, **k: "not an object")
    assert result.generated_by == "fallback-template"


def test_an_empty_answer_falls_back():
    result = reasoning.explain(an_action(), minutes_available=60,
                               personalization_enabled=True,
                               ai_chat_json=lambda m, **k: {"why": "   "})
    assert result.generated_by == "fallback-template"


def test_a_rambling_answer_is_rejected_rather_than_truncated():
    """A model that ignored the length rule has probably ignored the others."""
    result = reasoning.explain(
        an_action(), minutes_available=60, personalization_enabled=True,
        ai_chat_json=lambda m, **k: {"why": "x" * 900},
    )
    assert result.generated_by == "fallback-template"


def test_hallucinated_reason_codes_are_dropped():
    result = reasoning.explain(
        an_action(), minutes_available=60, personalization_enabled=True,
        ai_chat_json=lambda m, **k: {
            "why": "Your problem set is due tomorrow.",
            "primary_reasons": ["mercury_is_in_retrograde", "due_tomorrow"],
        },
    )
    assert result.generated_by == "ai"
    assert result.primary_reasons == ("due_tomorrow",)


def test_a_fully_invented_reason_list_falls_back_to_the_engines_own():
    result = reasoning.explain(
        an_action(), minutes_available=60, personalization_enabled=True,
        ai_chat_json=lambda m, **k: {
            "why": "Start here.", "primary_reasons": ["made_up", "also_made_up"],
        },
    )
    assert result.primary_reasons == ("due_tomorrow", "high_academic_value")


def test_a_valid_answer_is_accepted():
    result = reasoning.explain(
        an_action(), minutes_available=60, personalization_enabled=True,
        ai_chat_json=lambda m, **k: {
            "why": "Your problem set is due tomorrow and it carries real weight.",
            "primary_reasons": ["due_tomorrow"],
            "uncertainty": "",
        },
    )
    assert result.is_ai
    assert result.primary_reasons == ("due_tomorrow",)


# ── the data-minimisation contract ───────────────────────────────────


def test_the_model_is_told_the_reason_codes_and_nothing_private():
    state = reasoning.build_state(an_action(), minutes_available=60, today=TODAY)
    assert state["action"]["due_in_days"] == 1
    assert state["decision"]["reason_codes"] == ["due_tomorrow", "high_academic_value"]

    # The payload is an allow-list, not a filter. Asserting on the exact key
    # set is what makes a future field added to the decision object fail here
    # instead of quietly shipping to a third party.
    assert set(state) == {
        "student_first_name", "action", "decision",
        "reason_code_meanings", "context",
    }
    assert set(state["action"]) == {
        "kind", "title", "course", "minutes", "due_in_days", "method",
    }
    assert set(state["decision"]) == {
        "reason_codes", "confidence", "confident", "completion_probability",
    }
    assert set(state["context"]) == {
        "minutes_available", "best_time_of_day", "alternatives_considered",
    }
    # Reason-code meanings are the fixed sentences from REASON_LABELS, never
    # per-student values.
    from intelliplan.intelligence.nba import REASON_LABELS

    assert all(v == REASON_LABELS[k] for k, v in state["reason_code_meanings"].items())


def test_a_shaky_decision_does_not_hand_the_model_a_precise_probability():
    """A number the engine is unsure of must not be given to something that
    will confidently repeat it."""
    state = reasoning.build_state(an_action(confidence=0.3), minutes_available=60)
    assert state["decision"]["completion_probability"] is None
    assert state["decision"]["confident"] is False


def test_the_state_is_reproducible_without_reading_the_clock():
    a = reasoning.build_state(an_action(), minutes_available=60, today=TODAY)
    b = reasoning.build_state(an_action(), minutes_available=60, today=TODAY)
    assert a == b


def test_a_low_confidence_fallback_says_what_is_uncertain():
    result = reasoning.fallback_explain(an_action(confidence=0.2))
    assert result.uncertainty


def test_the_fallback_never_returns_an_empty_sentence():
    assert reasoning.fallback_explain(an_action(reasons=())).why
