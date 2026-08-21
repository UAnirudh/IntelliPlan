"""ScheduleAuditRepository against a real SQLite schema.

Uses its own in-memory app rather than the package ``models`` fixture,
because the audit tables need a ``users`` table and nothing else — pulling
in the Command Center models would couple two unrelated schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from intelliplan.repositories.schedule_audit import ScheduleAuditRepository
from time_utils import utcnow


@pytest.fixture()
def audit():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db = SQLAlchemy(app)

    class User(db.Model):
        __tablename__ = "users"
        id = db.Column(db.Integer, primary_key=True)

    from intelliplan.models import scheduler_decisions

    ScheduleVersion, ScheduleDecision = scheduler_decisions.register(db)

    ctx = app.app_context()
    ctx.push()
    db.create_all()
    db.session.add(User(id=1))
    db.session.add(User(id=2))
    db.session.commit()

    yield ScheduleAuditRepository(ScheduleVersion, ScheduleDecision, db.session), db
    ctx.pop()


# ── fake decisions ───────────────────────────────────────────────────


@dataclass(frozen=True)
class FakeKind:
    value: str


@dataclass(frozen=True)
class FakeCandidate:
    kind: FakeKind
    task_id: str
    course: str


@dataclass(frozen=True)
class FakeAction:
    candidate: FakeCandidate
    score: float
    confidence: float
    completion_probability: float
    reason_codes: tuple[str, ...]


def an_action(task_id="t1", course="Physics", codes=("due_today",)):
    return FakeAction(
        candidate=FakeCandidate(FakeKind("start_task"), task_id, course),
        score=0.81,
        confidence=0.7,
        completion_probability=0.66,
        reason_codes=codes,
    )


# ── versions ─────────────────────────────────────────────────────────


def test_versions_start_at_one_and_increment(audit):
    repo, _ = audit
    assert repo.record_version(1).version == 1
    assert repo.record_version(1).version == 2
    assert repo.record_version(1).version == 3


def test_version_numbering_is_per_student(audit):
    repo, _ = audit
    repo.record_version(1)
    repo.record_version(1)
    assert repo.record_version(2).version == 1


def test_a_version_records_why_it_exists(audit):
    repo, _ = audit
    repo.record_version(
        1,
        trigger="session_missed",
        objective_key="safety_first",
        candidate_count=4,
        selected_score=0.83,
        deadline_risk=0.08,
        stress=0.61,
        completion_odds=0.72,
        feasible=False,
        violation_keys=["overlap", "overlap", "after_deadline"],
        optimisation_ms=142,
        scheduled_minutes=510,
        deferred_minutes=45,
        changes=[{"task_id": "t1", "kind": "moved", "from": "2026-08-21"}],
    )
    row = repo.versions_for(1)[0]
    assert row["trigger"] == "session_missed"
    assert row["objective"] == "safety_first"
    assert row["candidates"] == 4
    assert row["feasible"] is False
    assert sorted(row["violations"]) == ["after_deadline", "overlap"]
    assert row["optimisation_ms"] == 142
    assert row["changes"][0]["task_id"] == "t1"


def test_history_reads_newest_first(audit):
    repo, _ = audit
    for _ in range(3):
        repo.record_version(1)
    assert [r["version"] for r in repo.versions_for(1)] == [3, 2, 1]


def test_the_history_limit_is_bounded(audit):
    repo, _ = audit
    for _ in range(5):
        repo.record_version(1)
    assert len(repo.versions_for(1, limit=2)) == 2
    assert len(repo.versions_for(1, limit=9999)) == 5


def test_a_student_never_sees_another_students_versions(audit):
    repo, _ = audit
    repo.record_version(1, trigger="generated")
    repo.record_version(2, trigger="override")
    assert [r["trigger"] for r in repo.versions_for(1)] == ["generated"]


def test_an_over_long_violation_list_does_not_fail_the_insert(audit):
    repo, _ = audit
    row = repo.record_version(1, violation_keys=[f"violation_{i}" for i in range(200)])
    assert row is not None
    assert len(row.violation_keys) <= 255


# ── decisions ────────────────────────────────────────────────────────


def test_the_whole_ranked_list_is_written_with_its_ranks(audit):
    repo, _ = audit
    written = repo.record_decisions(1, [an_action("t1"), an_action("t2"), an_action("t3")])
    assert [r.rank for r in written] == [0, 1, 2]
    assert [r.task_id for r in written] == ["t1", "t2", "t3"]


def test_a_freshly_shown_recommendation_is_unanswered_not_rejected(audit):
    """``None`` and ``False`` mean different things, and folding them
    together would teach the model that every unread card was disliked."""
    repo, _ = audit
    written = repo.record_decisions(1, [an_action()])
    assert written[0].accepted is None
    assert repo.acceptance_rate(1) is None


def test_accepting_resolves_the_open_recommendation(audit):
    repo, db = audit
    repo.record_decisions(1, [an_action("t1")])
    assert repo.resolve(1, "t1", True) is True
    assert repo.acceptance_rate(1) == 1.0


def test_rejecting_is_recorded_as_a_rejection(audit):
    repo, _ = audit
    repo.record_decisions(1, [an_action("t1")])
    repo.resolve(1, "t1", False)
    assert repo.acceptance_rate(1) == 0.0


def test_resolving_something_that_was_never_recommended_is_a_no_op(audit):
    repo, _ = audit
    assert repo.resolve(1, "ghost", True) is False


def test_an_old_recommendation_is_not_retroactively_accepted(audit):
    """An action taken on Thursday must not resolve Monday's card."""
    repo, db = audit
    written = repo.record_decisions(1, [an_action("t1")])
    written[0].created_at = utcnow() - timedelta(days=3)
    db.session.commit()
    assert repo.resolve(1, "t1", True, within_hours=12) is False


def test_reason_codes_are_stored_as_stable_keys_not_sentences(audit):
    repo, _ = audit
    written = repo.record_decisions(
        1, [an_action(codes=("due_today", "low_mastery"))]
    )
    assert written[0].reason_codes == "due_today,low_mastery"


def test_an_override_is_recorded_as_already_answered(audit):
    repo, _ = audit
    row = repo.record_override(1, action_kind="move", task_id="t1", course="Physics")
    assert row.accepted is True
    assert row.resolved_at is not None
    assert row.decision_type == "override"


def test_overrides_do_not_pollute_the_recommendation_acceptance_rate(audit):
    """Acceptance rate answers "did they take our advice?". An override is
    the student doing their own thing, which is a different question."""
    repo, _ = audit
    repo.record_override(1, action_kind="move", task_id="t1")
    assert repo.acceptance_rate(1) is None


def test_acceptance_rate_is_scoped_to_the_student(audit):
    repo, _ = audit
    repo.record_decisions(1, [an_action("t1")])
    repo.resolve(1, "t1", True)
    assert repo.acceptance_rate(2) is None


def test_long_fields_are_truncated_rather_than_failing_the_insert(audit):
    repo, _ = audit
    long_action = FakeAction(
        candidate=FakeCandidate(FakeKind("start_task"), "x" * 400, "c" * 400),
        score=0.5, confidence=0.5, completion_probability=0.5,
        reason_codes=tuple(f"code_{i}" for i in range(200)),
    )
    written = repo.record_decisions(1, [long_action])
    assert len(written[0].task_id) <= 128
    assert len(written[0].course) <= 160
    assert len(written[0].reason_codes) <= 255
