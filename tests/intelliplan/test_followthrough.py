"""The Follow-Through model — does a planned sitting actually happen?

The model is only worth having if three things are true, and each has tests:

* it learns real effects from a student's history and ignores noise;
* it is honest when it knows nothing (no fabricated insights, the
  population's answer rather than a guess dressed up as the student's);
* its training data is clean — no label leaks, no superseded plans counted
  as abandoned ones, no assignment titles in the outcome log.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

import pytest

from intelliplan.intelligence.followthrough import (
    DEFAULT_PRIOR,
    FollowThroughObservation,
    Prior,
    evaluate_holdout,
    fit_followthrough,
    fit_population_prior,
    harvest_plan_outcomes,
    observations_from_outcomes,
    observations_from_sessions,
    slot_mix_for_windows,
)

NOW = datetime(2026, 3, 30, 12, 0)
MONDAY = date(2026, 3, 23)


def synthetic(n=400, seed=7, morning=1.0, friday=-0.9, length=-0.8, load=-0.3):
    """A student with known habits, drawn from a known logistic model."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        day = NOW.date() - timedelta(days=rng.randint(1, 60))
        slot = rng.choice(["morning", "afternoon", "evening"])
        minutes = rng.choice([25, 45, 60, 90])
        prior_load = rng.choice([0, 0, 45, 90, 150])
        z = (0.4 + (morning if slot == "morning" else 0.0)
             + (friday if day.weekday() == 4 else 0.0)
             + length * math.log(minutes / 45) + load * prior_load / 60)
        done = rng.random() < 1 / (1 + math.exp(-z))
        out.append(FollowThroughObservation(
            done=done, minutes=minutes, day=day, slot_mix={slot: 1.0},
            prior_load_minutes=prior_load, course=rng.choice(["chem", "math"]),
            at=datetime.combine(day, datetime.min.time()),
        ))
    return out


# ── learning ─────────────────────────────────────────────────────────


def test_it_learns_the_direction_of_every_planted_habit():
    model = fit_followthrough(synthetic(), now=NOW, stamina_minutes=45)
    assert model.coef("slot_morning") > 0.4
    assert model.coef("dow_fri") < -0.2
    assert model.coef("length") < -0.4
    assert model.coef("load") < -0.05


def test_mornings_beat_evenings_for_a_morning_person():
    model = fit_followthrough(synthetic(), now=NOW, stamina_minutes=45)
    wednesday = MONDAY + timedelta(days=2)
    morning = model.probability(day=wednesday, slot_mix="morning", minutes=45)
    evening = model.probability(day=wednesday, slot_mix="evening", minutes=45)
    assert morning > evening + 0.08


def test_out_of_time_accuracy_beats_predicting_the_average():
    evaluation = evaluate_holdout(synthetic(), now=NOW, stamina_minutes=45)
    assert evaluation is not None
    assert evaluation.beats_baseline
    assert evaluation.lift > 0.03
    assert evaluation.n_test >= 8


def test_holdout_refuses_to_score_thin_history():
    assert evaluate_holdout(synthetic(n=12), now=NOW) is None


def test_the_fit_converges_quickly():
    model = fit_followthrough(synthetic(), now=NOW)
    assert 1 <= model.iterations <= 10


# ── honesty ──────────────────────────────────────────────────────────


def test_with_no_history_it_is_exactly_the_prior():
    model = fit_followthrough([], now=NOW)
    assert model.baseline_probability == pytest.approx(0.60, abs=1e-6)
    assert not model.has_signal
    assert model.insights() == []


def test_one_bad_evening_nudges_rather_than_declares():
    one = [FollowThroughObservation(done=False, minutes=45, day=MONDAY, slot_mix={"evening": 1.0})]
    model = fit_followthrough(one, now=NOW)
    assert 0.45 < model.baseline_probability < 0.60


def test_insights_are_only_claimed_with_evidence_behind_them():
    # Pure noise: 60 coin flips with no structure.
    rng = random.Random(3)
    noise = [
        FollowThroughObservation(
            done=rng.random() < 0.6, minutes=45,
            day=NOW.date() - timedelta(days=rng.randint(1, 40)),
            slot_mix={rng.choice(["morning", "evening"]): 1.0},
        )
        for _ in range(60)
    ]
    model = fit_followthrough(noise, now=NOW)
    slot_claims = [i for i in model.insights() if i.key.startswith("slot_")]
    assert slot_claims == []


def test_a_real_habit_becomes_a_sentence_the_student_can_read():
    model = fit_followthrough(synthetic(n=600), now=NOW, stamina_minutes=45)
    texts = " ".join(i.text for i in model.insights())
    assert "morning" in texts
    for insight in model.insights():
        assert insight.strength >= 1.5


# ── training data ────────────────────────────────────────────────────


def test_an_abandoned_sittings_short_timer_is_never_its_length():
    rows = [{
        "started_at": datetime(2026, 3, 20, 19), "planned_minutes": 0,
        "actual": 8, "completed": False,
    }]
    # No planned length and not finished: its duration is a symptom of the
    # label, so the row is dropped rather than taught as "8-minute sittings fail".
    assert observations_from_sessions(rows) == []


def test_fatigue_is_rebuilt_from_earlier_sittings_the_same_day():
    rows = [
        {"started_at": datetime(2026, 3, 20, 16), "planned_minutes": 45, "actual": 40, "completed": True},
        {"started_at": datetime(2026, 3, 20, 18), "planned_minutes": 45, "actual": 45, "completed": True},
        {"started_at": datetime(2026, 3, 21, 18), "planned_minutes": 45, "actual": 45, "completed": False},
    ]
    obs = observations_from_sessions(rows)
    assert [o.prior_load_minutes for o in obs] == [0, 40, 0]
    assert all(o.source == "session" for o in obs)
    assert obs[0].slot_mix == {"afternoon": 1.0}


def plan(start, days=3, minutes=(45, 30)):
    return {"schedule": [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "blocks": [
                {"block_id": f"d{i}-b{j}", "task_id": f"t{j}", "assignment": f"Secret title {j}",
                 "course": "Chem", "duration_minutes": m, "start_iso": f"{(start + timedelta(days=i)).isoformat()}T17:00:00"}
                for j, m in enumerate(minutes)
            ],
        }
        for i in range(days)
    ]}


def test_harvest_labels_past_blocks_by_their_checkboxes():
    start = date(2026, 3, 20)
    rows = harvest_plan_outcomes(plan(start), {"d0-b0": {"done": True}}, today=date(2026, 3, 22))
    assert len(rows) == 4                     # two past days × two blocks
    assert sum(r["done"] for r in rows) == 1
    assert rows[1]["prior_load"] == 45        # the second block follows 45 minutes of work
    assert rows[0]["slot"] == "evening"      # 5 PM, by the app's own slot rules


def test_harvest_never_stores_an_assignment_title():
    rows = harvest_plan_outcomes(plan(date(2026, 3, 20)), {}, today=date(2026, 3, 23))
    assert rows
    assert all("Secret" not in str(v) for r in rows for v in r.values())


def test_a_superseded_plan_is_not_counted_as_abandoned():
    start = date(2026, 3, 20)
    rows = harvest_plan_outcomes(
        plan(start), {}, today=date(2026, 3, 25), valid_until=date(2026, 3, 21),
    )
    assert {r["date"] for r in rows} == {"2026-03-20"}


def test_the_same_outcome_counts_once():
    rows = harvest_plan_outcomes(plan(date(2026, 3, 20)), {}, today=date(2026, 3, 22))
    assert len(observations_from_outcomes(rows + rows)) == len(rows)


# ── the population prior ─────────────────────────────────────────────


def test_a_population_of_two_is_not_a_population():
    assert fit_population_prior({1: synthetic(n=50), 2: synthetic(n=50, seed=8)}, now=NOW) is DEFAULT_PRIOR


def test_the_population_prior_is_learned_from_everyone():
    by_user = {u: synthetic(n=120, seed=u, morning=1.2) for u in range(8)}
    prior = fit_population_prior(by_user, now=NOW)
    assert prior.users == 8
    assert prior.sample_size > 0
    assert prior.mean("slot_morning") > 0.5
    # Courses are school-specific and never pooled.
    assert not any(k.startswith("course:") for k in prior.means)


def test_a_new_student_starts_from_the_population():
    prior = Prior(means={**DEFAULT_PRIOR.means, "slot_morning": 1.0}, variances=DEFAULT_PRIOR.variances)
    model = fit_followthrough([], now=NOW, prior=prior)
    assert model.coef("slot_morning") == pytest.approx(1.0)


def test_a_stored_prior_round_trips_and_a_corrupt_one_falls_back():
    prior = fit_population_prior({u: synthetic(n=80, seed=u) for u in range(6)}, now=NOW)
    again = Prior.from_dict(prior.to_dict())
    assert again.mean("bias") == pytest.approx(prior.mean("bias"), abs=1e-5)
    assert Prior.from_dict({"means": {"bias": "nan-ish"}}) is DEFAULT_PRIOR
    assert Prior.from_dict(None) is DEFAULT_PRIOR


def test_slot_mix_follows_the_clock():
    class W:
        def __init__(self, s, e):
            self.start = datetime(2026, 3, 23, *s)
            self.end = datetime(2026, 3, 23, *e)

    mix = slot_mix_for_windows([W((10, 0), (12, 0)), W((18, 0), (20, 0))])
    assert mix == {"morning": 0.5, "evening": 0.5}
    assert slot_mix_for_windows([]) == {"evening": 1.0}
