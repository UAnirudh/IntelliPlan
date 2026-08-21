"""Counterfactual scheduling — candidate generation, measurement, choice."""

from __future__ import annotations

from datetime import date, timedelta

from intelliplan.intelligence.counterfactual import (
    PROFILES,
    Candidate,
    ObjectiveWeights,
    choose,
    generate_candidates,
    measure,
)
from intelliplan.intelligence.planner import DayCapacity, PlannerTask, build_plan

TODAY = date(2026, 8, 21)


def task(tid, *, days=3, minutes=120, priority=70, points=50, kind="homework",
         course="Physics"):
    return PlannerTask(
        id=tid, title=f"Task {tid}", course=course, kind=kind,
        due_date=TODAY + timedelta(days=days), est_minutes=minutes,
        priority=priority, points_possible=points,
    )


def caps(days=7, minutes=180):
    return [DayCapacity(day=TODAY + timedelta(days=i), minutes=minutes)
            for i in range(days)]


# ── generation ───────────────────────────────────────────────────────


def test_every_profile_produces_a_candidate():
    candidates = generate_candidates([task("a"), task("b", days=6)], caps(),
                                     today=TODAY)
    assert len(candidates) == len(PROFILES)
    assert {c.profile.key for c in candidates} == {p.key for p in PROFILES}


def test_candidates_come_back_best_first():
    candidates = generate_candidates([task("a"), task("b", days=6)], caps(),
                                     today=TODAY)
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_no_tasks_means_no_candidates_rather_than_empty_plans():
    assert generate_candidates([], caps(), today=TODAY) == []


def test_profiles_actually_produce_different_plans():
    """If every profile returned the same plan the whole exercise would be
    theatre. Deadline-safe and retention-first must disagree."""
    candidates = {c.profile.key: c for c in generate_candidates(
        [task("a", days=5, minutes=240), task("b", days=9, minutes=180)],
        caps(days=10), today=TODAY,
    )}
    safe = candidates["safety_first"].metrics
    spread = candidates["retention"].metrics
    assert safe.deadline_risk <= spread.deadline_risk


# ── measurement ──────────────────────────────────────────────────────


def test_a_plan_that_covers_everything_scores_full_academic_benefit():
    tasks = [task("a"), task("b", days=6)]
    plan = build_plan(tasks, caps(), today=TODAY)
    assert measure(plan, tasks, TODAY).academic_benefit == 1.0


def test_deferred_work_shows_up_as_deadline_risk():
    """A week that cannot hold the work must not measure as risk-free."""
    tasks = [task(str(i), days=2, minutes=300) for i in range(6)]
    plan = build_plan(tasks, caps(days=3, minutes=60), today=TODAY)
    metrics = measure(plan, tasks, TODAY)
    assert plan.deferred
    assert metrics.deadline_risk > 0.0
    assert metrics.academic_benefit < 1.0


def test_a_packed_week_measures_more_stressful_than_a_light_one():
    tasks = [task("a", minutes=400, days=5)]
    tight = measure(build_plan(tasks, caps(days=6, minutes=120), today=TODAY),
                    tasks, TODAY)
    roomy = measure(build_plan(tasks, caps(days=6, minutes=400), today=TODAY),
                    tasks, TODAY)
    assert tight.stress > roomy.stress


def test_a_day_touching_several_subjects_costs_context_switching():
    tasks = [
        task("a", course="Physics", minutes=60, days=1),
        task("b", course="History", minutes=60, days=1),
        task("c", course="Biology", minutes=60, days=1),
    ]
    plan = build_plan(tasks, caps(days=2, minutes=300), today=TODAY)
    assert measure(plan, tasks, TODAY).context_switching > 0.0


def test_completion_odds_use_the_injected_model_when_there_is_one():
    tasks = [task("a", course="Chemistry")]
    plan = build_plan(tasks, caps(), today=TODAY)

    def never_finishes(course, minutes, day):
        return 0.1

    m = measure(plan, tasks, TODAY, completion_probability=never_finishes)
    assert m.completion_odds < 0.2


def test_a_broken_probability_provider_does_not_break_measurement():
    tasks = [task("a")]
    plan = build_plan(tasks, caps(), today=TODAY)

    def boom(course, minutes, day):
        raise RuntimeError("model unavailable")

    assert 0.0 <= measure(plan, tasks, TODAY, completion_probability=boom).completion_odds <= 1.0


def test_spread_revision_measures_as_better_learning_than_one_long_sitting():
    exam = PlannerTask(id="e", title="Physics exam", course="Physics",
                       kind="exam", due_date=TODAY + timedelta(days=6),
                       est_minutes=240, priority=90, points_possible=100)
    spread = measure(build_plan([exam], caps(days=7, minutes=90), today=TODAY),
                     [exam], TODAY)
    crammed = measure(build_plan([exam], caps(days=1, minutes=600), today=TODAY),
                      [exam], TODAY)
    assert spread.learning_benefit > crammed.learning_benefit


# ── choice ───────────────────────────────────────────────────────────


def test_the_winner_is_the_top_candidate_when_nothing_is_vetoed():
    candidates = generate_candidates([task("a")], caps(), today=TODAY)
    assert choose(candidates) is candidates[0]


def test_an_infeasible_favourite_is_skipped_for_the_next_best():
    candidates = generate_candidates([task("a")], caps(), today=TODAY)
    top = candidates[0]
    winner = choose(candidates, require_feasible=lambda c: c is not top)
    assert winner is not None
    assert winner is candidates[1]


def test_all_candidates_infeasible_returns_nothing_rather_than_a_broken_plan():
    candidates = generate_candidates([task("a")], caps(), today=TODAY)
    assert choose(candidates, require_feasible=lambda c: False) is None


def test_choosing_from_nothing_is_not_an_error():
    assert choose([]) is None


# ── objective weights ────────────────────────────────────────────────


def test_the_objective_is_configurable_and_changes_the_winner():
    tasks = [task("a", days=5, minutes=240), task("b", days=9, minutes=180)]
    safety = ObjectiveWeights(deadline_risk=6.0, learning=0.0, stress=0.0)
    calm = ObjectiveWeights(deadline_risk=0.0, stress=5.0, learning=0.0)
    safe_pick = choose(generate_candidates(tasks, caps(days=10), today=TODAY,
                                           objective=safety))
    calm_pick = choose(generate_candidates(tasks, caps(days=10), today=TODAY,
                                           objective=calm))
    assert safe_pick.metrics.deadline_risk <= calm_pick.metrics.deadline_risk
    assert calm_pick.metrics.stress <= safe_pick.metrics.stress


def test_the_displayed_score_stays_inside_zero_to_one():
    for c in generate_candidates([task("a"), task("b", days=1, minutes=600)],
                                 caps(days=2, minutes=60), today=TODAY):
        assert 0.0 <= c.normalised_score <= 1.0


def test_a_candidate_serialises_for_the_api():
    candidate: Candidate = generate_candidates([task("a")], caps(), today=TODAY)[0]
    payload = candidate.to_dict()
    assert payload["key"] in {p.key for p in PROFILES}
    assert "deadline_risk" in payload["metrics"]
    assert payload["description"]
