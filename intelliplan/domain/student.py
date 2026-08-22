"""Student-model domain types.

The adaptive scheduler needs to say things like "we think you finish 62% of
45-minute Physics sittings started in the evening, and we are not very sure
of that". Every type here therefore carries its **evidence** alongside its
value: a point estimate with no sample count attached is indistinguishable
from a guess, and the product's rule is that we never present a guess as a
measurement.

Pure data. No ORM, no Flask, no clock.
"""

from __future__ import annotations

import hashlib

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

__all__ = [
    "identity_key",
    "Evidence",
    "EvidenceSource",
    "SlotStat",
    "BehaviorProfile",
    "MasteryEstimate",
    "ActionKind",
    "ActionCandidate",
    "NextAction",
    "Consequence",
    "ConsequenceReport",
]


def identity_key(title: str, course: str = "") -> str:
    """A stable id for "the same piece of work", across ingest paths.

    The plan block and the assignment row for one Physics set carry ids
    minted by different systems ("t-physics", "manual:412"), so an id is not
    an identity. Title-and-course is: two pieces of work a student cannot
    tell apart are, for scheduling purposes, the same thing.

    Hashed rather than stored as text on purpose. The audit tables must not
    become a second copy of a student's academic record, and a digest lets
    rows be *matched* without any of them holding a readable assignment
    title. Truncated to 32 hex characters — collisions at that width are far
    less likely than the title collisions the function is already choosing
    to treat as identity.
    """
    normalised = f"{(title or '').strip().lower()}|{(course or '').strip().lower()}"
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]


class EvidenceSource(str, Enum):
    """Where a number came from. Rendered as "based on…" in the UI and
    used to decide whether a claim may be stated as fact."""

    MEASURED = "measured"        # this student's own history, enough samples
    PARTIAL = "partial"          # this student's history, below the noise floor
    POPULATION = "population"    # default prior — we know nothing about them yet
    STATED = "stated"            # the student told us directly


@dataclass(frozen=True, slots=True)
class Evidence:
    """Provenance and strength behind a single estimate.

    ``samples`` is the effective (decay-weighted) observation count, which is
    why it is a float rather than an int: an observation from 90 days ago is
    worth a fraction of one from yesterday.
    """

    source: EvidenceSource
    samples: float
    confidence: float  # 0..1

    @staticmethod
    def population(confidence: float = 0.25) -> "Evidence":
        return Evidence(EvidenceSource.POPULATION, 0.0, confidence)

    @property
    def is_measured(self) -> bool:
        return self.source is EvidenceSource.MEASURED


@dataclass(frozen=True, slots=True)
class SlotStat:
    """How a student behaves in one part of the day."""

    slot: str                    # "morning" | "afternoon" | "evening"
    completion_rate: float       # 0..1
    sessions: float              # decay-weighted count
    median_minutes: int


@dataclass(frozen=True, slots=True)
class BehaviorProfile:
    """The behavioural half of the student model.

    Everything here is *learned*. Nothing in this dataclass may be
    hand-tuned per student — the only hardcoded numbers in the module that
    builds it are the population priors used when a student has no history.
    """

    completion_rate: float                  # overall, 0..1
    completion_evidence: Evidence
    abandon_rate: float                     # started-but-not-finished, 0..1
    slots: tuple[SlotStat, ...]
    best_slot: str | None
    stamina_minutes: int                    # typical sitting they finish
    workload_tolerance_minutes: int         # daily minutes they sustain
    reschedule_rate: float                  # 0..1, how often they move work
    observations: float                     # decay-weighted total

    def slot_stat(self, slot: str) -> SlotStat | None:
        for s in self.slots:
            if s.slot == slot:
                return s
        return None


@dataclass(frozen=True, slots=True)
class MasteryEstimate:
    """An *estimate* of what a student knows — never asserted as a fact.

    ``mastery`` is the decayed point estimate; ``confidence`` is how much we
    trust it; ``sources`` names what fed it so the UI can say "from 3 quiz
    results and 2 tutor sessions" instead of producing a bare number.
    """

    subject: str
    topic: str
    concept: str
    mastery: float               # 0..1
    confidence: float            # 0..1
    days_since_review: int | None
    assessment_days_away: int | None
    risk: float                  # 0..1 — academic risk this concept carries
    sources: tuple[str, ...] = ()

    @property
    def is_weak(self) -> bool:
        return self.mastery < 0.6


class ActionKind(str, Enum):
    """The heterogeneous things a student can be told to do next.

    Deliberately wider than "work on an assignment": a scheduler that can
    only ever recommend more work cannot recommend the right thing when the
    right thing is a break or a retrieval drill.
    """

    START_TASK = "start_task"
    CONTINUE_TASK = "continue_task"
    STUDY_CONCEPT = "study_concept"
    REVIEW_CONCEPT = "review_concept"
    PRACTICE = "practice"
    EXAM_PREP = "exam_prep"
    ADMIN = "admin"
    BREAK = "break"
    DEFER = "defer"


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    """One thing the student could do in the next block of free time.

    Candidates are generated cheaply and scored deterministically. The score
    and its components live on :class:`NextAction`, not here, so that
    generation and evaluation stay separable and separately testable.
    """

    kind: ActionKind
    title: str
    subject: str = ""
    course: str = ""
    minutes: int = 30
    task_id: str = ""
    concept: str = ""
    due_date: date | None = None
    # 0..1 inputs the scorer reads. Generators fill what they know and leave
    # the rest at their neutral defaults rather than inventing values.
    academic_value: float = 0.5
    learning_gain: float = 0.5
    mastery_gap: float = 0.0
    deadline_pressure: float = 0.0
    difficulty: float = 0.5
    assessment_days_away: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NextAction:
    """A scored recommendation, with the arithmetic behind it exposed."""

    candidate: ActionCandidate
    score: float                          # 0..1
    confidence: float                     # 0..1
    reason_codes: tuple[str, ...]
    components: tuple[tuple[str, float], ...]   # (component key, contribution)
    completion_probability: float
    method: str = ""                      # recommended study method, if any
    explanation: tuple[str, ...] = ()     # human-readable "why now" lines


@dataclass(frozen=True, slots=True)
class Consequence:
    """One measurable effect of an override, signed so the UI can render
    it as a gain or a cost without re-deriving the sign."""

    key: str
    label: str
    delta: float
    better: bool


@dataclass(frozen=True, slots=True)
class ConsequenceReport:
    """What happens if the student overrides the plan.

    The student is never blocked from overriding. They are told the price.
    """

    accepted_possible: bool
    gains: tuple[Consequence, ...]
    costs: tuple[Consequence, ...]
    summary: str
    infeasible_reason: str = ""
