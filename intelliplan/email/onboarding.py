"""The onboarding sequence: three nudges that stop when they stop applying.

The welcome email (``campaigns.sweep_welcome``) goes out on day zero and
tells a new student everything at once. Almost nobody acts on all of it in
one sitting, so this module follows up — but only about the things that
student has not already done.

That last part is the whole design. A drip that sends "connect your school"
on day two regardless of whether the school was connected on day one is
worse than no drip at all: it proves the app is not paying attention, and
it teaches the reader to ignore the next one. So every step names a *goal*,
the sweep reads the account's real state before it sends, and a step whose
goal is already met is never sent at all — not deferred, not queued, just
dropped.

The ladder, and why it is ordered:

* Day 2 — ``connect``: link a school account, so assignments arrive on their
  own instead of being typed in.
* Day 4 — ``plan``: turn those assignments into a schedule.
* Day 7 — ``study``: actually sit down for one focused session.

Each rung depends on the one below it being useful, so the sweep sends the
*earliest* unmet, unsent step rather than the most advanced one. Someone who
never connected a school should be asked about that before they are asked to
run a study session.

Everything goes through ``sender.send_lifecycle_email``, so the eligibility
gate, the deduplication ledger and the unsubscribe headers apply here
exactly as they do to the welcome mail. Like the welcome, these use the
transactional gate: they explain how to set up a service the person signed
up for days ago, and they carry an unsubscribe link regardless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .sender import send_lifecycle_email

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    """One rung of the sequence."""

    #: Ledger key. Versioned so the copy can be rewritten later and resent
    #: to people who already had v1 without a migration.
    key: str
    #: Template basename — ``emails/<template>.html`` and ``text/<template>.txt``.
    template: str
    subject: str
    preheader: str
    #: Days after signup this step becomes due.
    day: int
    #: Key into :func:`onboarding_progress`. When it reads True the step is
    #: dropped rather than sent.
    goal: str


STEPS: tuple[Step, ...] = (
    Step(
        key="onboarding_connect_v1",
        template="onboarding_connect",
        subject="Connect your school — IntelliPlan does the rest",
        preheader="One connection and your assignments show up on their own.",
        day=2,
        goal="connected",
    ),
    Step(
        key="onboarding_plan_v1",
        template="onboarding_plan",
        subject="Turn your assignments into a plan",
        preheader="IntelliPlan splits your work into sessions and spaces them out for you.",
        day=4,
        goal="planned",
    ),
    Step(
        key="onboarding_study_v1",
        template="onboarding_study",
        subject="One focused session is all it takes to start",
        preheader="Pick a block, start the timer, and let IntelliPlan learn how you work.",
        day=7,
        goal="studied",
    ),
)

#: How long past its due day a step stays sendable. A cron that misses a run
#: — or a day — must catch up rather than silently skip a cohort. Beyond
#: this the step ages out: a "connect your school" email three weeks after
#: signup is not onboarding, it is a reminder that we were not paying
#: attention.
GRACE_DAYS = 4

#: Oldest account the sweep will look at, derived rather than hardcoded so
#: adding a fourth step does not need this constant edited too.
MAX_AGE_DAYS = max(step.day for step in STEPS) + GRACE_DAYS
MIN_AGE_DAYS = min(step.day for step in STEPS)


# ── What has this student actually done? ────────────────────────────


def onboarding_progress(user_id: int) -> dict[str, bool]:
    """Read the three goals off the database.

    EXISTS probes, not counts: the question is "has this happened at all",
    and a student with four hundred completed sessions should not cost more
    to check than one with a single session.

    Every probe is wrapped, because a table that does not exist on this
    deployment must not take the whole sweep down. It fails *closed* — an
    unreadable table reads as "goal not met", which at worst sends a nudge
    about something already done. The alternative, defaulting to True,
    would silently disable the sequence the first time a model was renamed.
    """
    return {
        "connected": _has_connected_account(user_id),
        "planned": _exists("SavedSchedule", user_id),
        "studied": _has_completed_session(user_id),
    }


def _exists(model_name: str, user_id: int) -> bool:
    """True when at least one row of ``model_name`` belongs to this user."""
    from App import db
    import App

    model = getattr(App, model_name, None)
    if model is None:
        logger.warning("onboarding: model %s is not exported from App", model_name)
        return False
    try:
        return (
            db.session.query(model.id).filter(model.user_id == user_id).first()
            is not None
        )
    except Exception:
        logger.exception("onboarding: probe of %s failed for user %s", model_name, user_id)
        return False


#: Every table that means "this student linked something to IntelliPlan".
#: All of them, not just the common two: telling a student who connected
#: Moodle that they have not connected anything is precisely the failure
#: this module exists to avoid.
_ACCOUNT_MODELS = (
    "LinkedAccount",
    "CanvasIntegration",
    "GoogleIntegration",
    "ClassroomIntegration",
    "BlackboardIntegration",
    "MoodleIntegration",
    "NotionIntegration",
)


def _has_connected_account(user_id: int) -> bool:
    return any(_exists(name, user_id) for name in _ACCOUNT_MODELS)


def _has_completed_session(user_id: int) -> bool:
    """A session the student actually finished.

    ``abandoned`` deliberately does not count. Someone who started a timer
    and walked away has not had the experience this step is selling, and is
    a better candidate for the nudge than for being skipped.
    """
    from App import ActiveSession, db

    try:
        return (
            db.session.query(ActiveSession.id)
            .filter(ActiveSession.user_id == user_id, ActiveSession.state == "completed")
            .first()
            is not None
        )
    except Exception:
        logger.exception("onboarding: session probe failed for user %s", user_id)
        return False


# ── Choosing the step ───────────────────────────────────────────────


def due_step(age_days: float, sent_keys: set[str], progress: dict[str, bool]) -> Step | None:
    """The one email this student should get right now, or ``None``.

    Pure, so the sequencing rules are testable without a database.

    "Earliest unmet rung" rather than "furthest due step": the ladder is
    ordered, and asking someone to run a study session when they have not
    connected a school skips the part that makes the session worth running.
    """
    for step in STEPS:
        if step.key in sent_keys:
            continue
        if progress.get(step.goal):
            # Already done it. Not deferred — dropped.
            continue
        if age_days < step.day:
            # Not due yet; nothing later is due either, since STEPS is
            # ordered by day.
            return None
        if age_days > step.day + GRACE_DAYS:
            # Aged out. Move on and let a later rung apply if it still fits.
            continue
        return step
    return None


# ── The sweep ───────────────────────────────────────────────────────


def _blank_summary() -> dict:
    return {
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "reasons": {},
        "by_step": {},
        "no_step_due": 0,
    }


def sweep_onboarding(now: datetime | None = None, limit: int = 500) -> dict:
    """Send at most one onboarding email to each student who needs one.

    Deliberately user-centric rather than step-centric. A step-per-sweep
    loop would mail the same person twice in one run after the cron had been
    down for a few days, which is the exact moment they least deserve it.
    """
    from App import EmailSend, User, db

    now = now or datetime.utcnow()
    youngest = now - timedelta(days=MIN_AGE_DAYS)
    oldest = now - timedelta(days=MAX_AGE_DAYS)
    summary = _blank_summary()

    candidates = (
        User.query.filter(User.created_at <= youngest, User.created_at >= oldest)
        .limit(limit)
        .all()
    )
    if not candidates:
        logger.info("onboarding sweep: no accounts in the window")
        return summary

    # One query for the whole cohort's ledger rows rather than one per user.
    # `failed` rows are excluded from "already sent" so a provider outage
    # gets retried, matching sender._claim.
    step_keys = [step.key for step in STEPS]
    ids = [u.id for u in candidates]
    sent_by_user: dict[int, set[str]] = {}
    rows = (
        db.session.query(EmailSend.user_id, EmailSend.email_key)
        .filter(
            EmailSend.user_id.in_(ids),
            EmailSend.email_key.in_(step_keys),
            EmailSend.status != "failed",
        )
        .all()
    )
    for user_id, email_key in rows:
        sent_by_user.setdefault(user_id, set()).add(email_key)

    for user in candidates:
        created = user.created_at or now
        age_days = (now - created).total_seconds() / 86400.0
        sent_keys = sent_by_user.get(user.id, set())

        # Cheap check first: if every remaining step is already sent there is
        # nothing to look up in the database.
        if all(step.key in sent_keys for step in STEPS):
            summary["no_step_due"] += 1
            continue

        progress = onboarding_progress(user.id)
        step = due_step(age_days, sent_keys, progress)
        if step is None:
            summary["no_step_due"] += 1
            continue

        result = send_lifecycle_email(
            user=user,
            email_key=step.key,
            template_name=step.template,
            subject=step.subject,
            preheader=step.preheader,
            # Transactional, for the same reason the welcome mail is: this
            # explains how to set up a service the student signed up for.
            marketing=False,
            context_extra={"progress": progress, "step_key": step.key},
        )

        bucket = summary["by_step"].setdefault(
            step.key, {"sent": 0, "skipped": 0, "failed": 0}
        )
        if result.sent:
            summary["sent"] += 1
            bucket["sent"] += 1
        elif result.reason in {"provider_failed", "render_failed"}:
            summary["failed"] += 1
            bucket["failed"] += 1
        else:
            summary["skipped"] += 1
            bucket["skipped"] += 1
        summary["reasons"][result.reason] = summary["reasons"].get(result.reason, 0) + 1

    if len(candidates) >= limit:
        logger.warning("onboarding sweep hit its limit of %s; remainder deferred", limit)
        summary["truncated"] = True

    logger.info("onboarding sweep: %s", summary)
    return summary
