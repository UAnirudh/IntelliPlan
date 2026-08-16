"""Breaking large work into stages that actually differ from each other.

The planner already splits a long task into sittings that respect focus
length and space repetition across days. What it produces is arithmetic:
"part 3 of 5", five interchangeable slices of one number. For a problem set
that is exactly right — problems 11 to 20 are the same activity as problems 1
to 10, and naming them would be noise.

For a research paper it is wrong in a way that costs the student the grade.
"Part 3 of 5" of an essay does not tell them what to do when they sit down,
and worse, it implies the parts are interchangeable when they are strictly
ordered: you cannot outline before you have read the sources, and revising a
draft that does not exist is not a thing a person can do at 7pm on Tuesday.

So this module answers two questions:

* **Is this task worth decomposing?** Most are not. A task earns stages by
  being big enough that the student would otherwise stare at it, *and* by
  being the kind of work whose phases are genuinely different activities.
* **What are the stages, and what order do they go in?** Named steps with
  shares of the total effort and real prerequisite edges, which
  ``planner._apply_dependencies`` already knows how to honour — it has simply
  never been given any.

Why templates and not a language model
--------------------------------------
The stages of a research paper are not a creative question. They are the same
for every research paper, they are what every writing curriculum teaches, and
a student who sees them once learns the shape of the work. Asking a model to
regenerate them per assignment buys nothing, costs a round trip, and
introduces the failure mode where the plan says "brainstorm synergies" because
the temperature was high that day.

The one thing a model *would* be good at — reading a specific prompt and
noticing that this paper needs an annotated bibliography — is a refinement on
top of a correct default, not a replacement for having one.

Purity
------
No Flask, no ORM, no clock, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

__all__ = [
    "Stage",
    "StageTemplate",
    "Decomposition",
    "decompose",
    "should_decompose",
    "MIN_DECOMPOSE_MINUTES",
]


#: Below this, stages are overhead. A ninety-minute essay is one sitting of
#: writing, and telling someone to spend twelve minutes "proofreading" a
#: paragraph they wrote forty minutes ago is the kind of advice that makes a
#: planner feel like paperwork.
MIN_DECOMPOSE_MINUTES = 150

#: A stage shorter than this is folded into its neighbour. Same reasoning as
#: ``planner.MIN_SESSION_MINUTES`` — nobody opens a laptop for eight minutes.
MIN_STAGE_MINUTES = 20


@dataclass(frozen=True, slots=True)
class Stage:
    """One named phase of a larger piece of work."""

    key: str
    #: What the student sees, e.g. "Research and gather sources".
    label: str
    #: Fraction of the task's total effort, 0..1.
    share: float
    minutes: int = 0
    #: Stage keys that must be finished before this one starts.
    depends_on: tuple[str, ...] = ()
    #: Why this stage exists, for the "how this was planned" panel.
    note: str = ""
    #: True when the stage is mostly one uninterrupted push and splitting it
    #: across days costs more than it saves.
    wants_one_sitting: bool = False


@dataclass(frozen=True, slots=True)
class StageTemplate:
    """The shape of one kind of work."""

    kind: str
    stages: tuple[Stage, ...]
    #: Detected from the title/description; the first match wins.
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Decomposition:
    """The result: either stages, or a reasoned refusal."""

    stages: tuple[Stage, ...] = ()
    template: str = ""
    reason: str = ""

    @property
    def applied(self) -> bool:
        return bool(self.stages)


# ── Templates ─────────────────────────────────────────────────────────
#
# Shares are effort fractions, and the interesting claim in each one is where
# the time actually goes. Students consistently under-budget the parts that
# are not "writing" — the reading before it and the revision after — which is
# precisely why a template is worth more than an even split.

_RESEARCH_PAPER = StageTemplate(
    kind="research_paper",
    triggers=("research paper", "research essay", "term paper", "dissertation",
              "thesis paper", "annotated bibliography", "cite", "citations",
              "sources", "mla", "apa"),
    stages=(
        Stage("understand", "Read the prompt and rubric closely", 0.05,
              note="Ten minutes here is the cheapest insurance against writing "
                   "the wrong essay."),
        Stage("research", "Research and gather sources", 0.25, depends_on=("understand",),
              note="Budgeted as a quarter of the work because it reliably is."),
        Stage("thesis", "Settle on a thesis", 0.07, depends_on=("research",),
              wants_one_sitting=True,
              note="Deciding this before drafting prevents mid-paragraph drift."),
        Stage("outline", "Outline the argument", 0.10, depends_on=("thesis",),
              wants_one_sitting=True),
        Stage("draft", "Write the draft", 0.32, depends_on=("outline",),
              note="The largest single block, and the one that needs the outline "
                   "already done."),
        Stage("revise", "Revise for argument and structure", 0.15, depends_on=("draft",),
              note="Deliberately a separate day from drafting — you cannot see "
                   "your own draft clearly an hour after writing it."),
        Stage("proofread", "Proofread and check citations", 0.06, depends_on=("revise",),
              wants_one_sitting=True),
    ),
)

_ESSAY = StageTemplate(
    kind="essay",
    triggers=("essay", "paper", "write", "writing", "reflection", "narrative",
              "commentary", "review", "report", "article"),
    stages=(
        Stage("understand", "Read the prompt and rubric closely", 0.06),
        Stage("plan", "Plan the argument and outline it", 0.18, depends_on=("understand",),
              wants_one_sitting=True),
        Stage("draft", "Write the draft", 0.45, depends_on=("plan",)),
        Stage("revise", "Revise and tighten", 0.21, depends_on=("draft",),
              note="A separate sitting from drafting, on purpose."),
        Stage("proofread", "Proofread", 0.10, depends_on=("revise",),
              wants_one_sitting=True),
    ),
)

_EXAM = StageTemplate(
    kind="exam",
    triggers=("exam", "final", "midterm", "test", "unit assessment"),
    stages=(
        Stage("inventory", "List what's covered and what's shaky", 0.08,
              wants_one_sitting=True,
              note="Studying without this is studying what you already know."),
        Stage("learn", "Work through the weak topics", 0.34, depends_on=("inventory",)),
        Stage("practice", "Practice problems under exam conditions", 0.34,
              depends_on=("learn",),
              note="Recall practice beats re-reading by a wide margin, and it is "
                   "the part students skip."),
        Stage("mistakes", "Review what you got wrong", 0.16, depends_on=("practice",),
              note="The single highest-value hour in exam prep."),
        Stage("final_pass", "Light final pass", 0.08, depends_on=("mistakes",),
              wants_one_sitting=True,
              note="Light on purpose — cramming the night before costs more in "
                   "sleep than it returns in recall."),
    ),
)

_PRESENTATION = StageTemplate(
    kind="presentation",
    triggers=("presentation", "present", "slides", "slide deck", "powerpoint",
              "keynote", "pitch", "poster"),
    stages=(
        Stage("understand", "Confirm the brief and time limit", 0.05),
        Stage("research", "Gather content", 0.22, depends_on=("understand",)),
        Stage("structure", "Structure the talk", 0.13, depends_on=("research",),
              wants_one_sitting=True),
        Stage("build", "Build the slides", 0.35, depends_on=("structure",)),
        Stage("rehearse", "Rehearse out loud, timed", 0.25, depends_on=("build",),
              note="Rehearsal is where a presentation is actually made, and it is "
                   "the stage that gets dropped when the plan runs late."),
    ),
)

_LAB = StageTemplate(
    kind="lab",
    triggers=("lab report", "lab write-up", "practical report", "experiment"),
    stages=(
        Stage("prepare", "Review the method and your data", 0.12),
        Stage("analyse", "Work through the calculations and graphs", 0.33,
              depends_on=("prepare",)),
        Stage("write", "Write results and discussion", 0.38, depends_on=("analyse",)),
        Stage("check", "Check units, errors, and the conclusion", 0.17,
              depends_on=("write",), wants_one_sitting=True),
    ),
)

_PROJECT = StageTemplate(
    kind="project",
    triggers=("project", "build", "design", "portfolio", "model", "prototype"),
    stages=(
        Stage("scope", "Work out what's actually required", 0.10,
              wants_one_sitting=True),
        Stage("plan", "Break it down and gather what you need", 0.15,
              depends_on=("scope",)),
        Stage("build", "Do the main work", 0.50, depends_on=("plan",)),
        Stage("review", "Check it against the rubric", 0.15, depends_on=("build",)),
        Stage("finish", "Finish and submit", 0.10, depends_on=("review",),
              wants_one_sitting=True),
    ),
)

_READING = StageTemplate(
    kind="reading",
    triggers=("read chapter", "reading assignment", "read pages", "novel",
              "read the text"),
    stages=(
        Stage("read", "Read it through", 0.62),
        Stage("notes", "Take notes on what matters", 0.24, depends_on=("read",)),
        Stage("questions", "Answer the questions", 0.14, depends_on=("notes",)),
    ),
)

#: Order matters. The most specific template must be tried first, or every
#: research paper matches the generic "essay" trigger on the word "paper".
TEMPLATES: tuple[StageTemplate, ...] = (
    _RESEARCH_PAPER,
    _LAB,
    _PRESENTATION,
    _EXAM,
    _ESSAY,
    _PROJECT,
    _READING,
)

#: Which planner ``kind`` values fall back to which template when the text
#: gives no clearer signal.
_KIND_FALLBACK: Mapping[str, StageTemplate] = {
    "exam": _EXAM,
    "test": _EXAM,
    "project": _PROJECT,
    "lab": _LAB,
}

#: Work that is repetitive by nature. Naming stages for it is theatre: the
#: fifth set of ten problems is the same activity as the first.
_NEVER_DECOMPOSE = ("problem set", "worksheet", "homework set", "practice set",
                    "drill", "vocabulary", "flashcard", "quiz")


# ── Detection ─────────────────────────────────────────────────────────


def _match_template(title: str, kind: str, description: str) -> StageTemplate | None:
    haystack = f"{title} {description[:600]}".lower()
    for template in TEMPLATES:
        for trigger in template.triggers:
            if re.search(rf"\b{re.escape(trigger)}", haystack):
                return template
    return _KIND_FALLBACK.get((kind or "").strip().lower())


def should_decompose(
    minutes: int,
    title: str = "",
    kind: str = "",
    description: str = "",
    problem_count: int = 0,
) -> tuple[bool, str]:
    """Whether stages would help, and why not when they would not.

    The reason string is not decoration — it is what stops this from becoming
    a feature that fires on everything because nobody could remember what it
    was supposed to skip.
    """
    text = f"{title} {description[:400]}".lower()

    if problem_count >= 5:
        return False, "Repetitive work — the parts are interchangeable."
    for word in _NEVER_DECOMPOSE:
        if word in text:
            return False, "Repetitive work — the parts are interchangeable."
    if minutes < MIN_DECOMPOSE_MINUTES:
        return False, f"Under {MIN_DECOMPOSE_MINUTES} minutes — stages would be overhead."
    if _match_template(title, kind, description) is None:
        return False, "No recognisable shape to break this into."
    return True, ""


# ── Decomposition ─────────────────────────────────────────────────────


def decompose(
    minutes: int,
    title: str = "",
    kind: str = "",
    description: str = "",
    problem_count: int = 0,
) -> Decomposition:
    """Break ``minutes`` of work into ordered, named stages.

    Returns an empty :class:`Decomposition` carrying a ``reason`` when the task
    should be left alone, which is the common case and not a failure.
    """
    ok, reason = should_decompose(minutes, title, kind, description, problem_count)
    if not ok:
        return Decomposition(reason=reason)

    template = _match_template(title, kind, description)
    assert template is not None  # should_decompose already checked
    return Decomposition(
        stages=_allocate(template.stages, minutes),
        template=template.kind,
    )


def _allocate(stages: Sequence[Stage], minutes: int) -> tuple[Stage, ...]:
    """Turn effort shares into whole minutes that sum to the total.

    Two properties the caller depends on: the parts add up to exactly the
    whole (a plan whose stages sum to 4h55 for a 5h task invites the student
    to hunt for the missing five minutes), and no stage is too small to be
    worth sitting down for.
    """
    total = max(0, int(minutes))
    if total <= 0 or not stages:
        return ()

    raw = [(s, s.share * total) for s in stages]
    allocated = [(s, int(value)) for s, value in raw]

    # Hand out the rounding remainder to the stages with the largest fractional
    # parts, so the biggest stages are not systematically shortchanged.
    remainder = total - sum(m for _, m in allocated)
    if remainder > 0:
        order = sorted(
            range(len(raw)), key=lambda i: raw[i][1] - int(raw[i][1]), reverse=True
        )
        for i in order[:remainder]:
            stage, m = allocated[i]
            allocated[i] = (stage, m + 1)

    merged = _fold_small(allocated)
    return tuple(
        Stage(
            key=s.key,
            label=s.label,
            share=s.share,
            minutes=m,
            depends_on=s.depends_on,
            note=s.note,
            wants_one_sitting=s.wants_one_sitting,
        )
        for s, m in merged
    )


def _fold_small(
    allocated: Sequence[tuple[Stage, int]]
) -> list[tuple[Stage, int]]:
    """Absorb undersized stages into the next stage that depends on them.

    Folding *forward* rather than backward keeps the dependency chain intact:
    "settle on a thesis" merged into "outline the argument" is still after the
    research, whereas merging it backwards into research would license
    starting it before the reading is done.
    """
    kept: list[tuple[Stage, int]] = []
    carry = 0
    for stage, mins in allocated:
        mins += carry
        carry = 0
        if mins < MIN_STAGE_MINUTES and stage is not allocated[-1][0]:
            carry = mins
            continue
        kept.append((stage, mins))

    if carry and kept:
        stage, mins = kept[-1]
        kept[-1] = (stage, mins + carry)
    elif carry:
        stage, _ = allocated[-1]
        kept.append((stage, carry))

    # The last stage has no forward neighbour to fold into, so it is the one
    # case that folds backwards: at the smallest size we decompose at all,
    # "proofread" comes out at nine minutes, and nine minutes is not a
    # sitting. Merging it into the revision it follows keeps every dependency
    # valid, because the absorbing stage is strictly earlier.
    while len(kept) > 1 and kept[-1][1] < MIN_STAGE_MINUTES:
        _, orphan_minutes = kept.pop()
        stage, mins = kept[-1]
        kept[-1] = (stage, mins + orphan_minutes)

    # Dependencies naming a folded-away stage are rewritten to the surviving
    # stage that absorbed it, so nothing depends on a stage the plan no longer
    # contains.
    surviving = {s.key for s, _ in kept}
    out: list[tuple[Stage, int]] = []
    for index, (stage, mins) in enumerate(kept):
        deps = tuple(d for d in stage.depends_on if d in surviving)
        if stage.depends_on and not deps and index > 0:
            deps = (kept[index - 1][0].key,)
        out.append(
            (
                Stage(
                    key=stage.key,
                    label=stage.label,
                    share=stage.share,
                    minutes=stage.minutes,
                    depends_on=deps,
                    note=stage.note,
                    wants_one_sitting=stage.wants_one_sitting,
                ),
                mins,
            )
        )
    return out
