"""Who gets which lifecycle email, and when.

Three campaigns:

* ``sweep_welcome``    — cron, daily. Transactional.
* ``sweep_feedback``   — cron, daily, at the two-week mark, active users only.
* ``send_newsletter``  — admin-triggered, never automatic.

The sweeps are deliberately driven by a cron sweep rather than fired inline
at signup: a Resend timeout must never be able to fail a registration, and a
sweep that misses a day catches up on the next run without anyone noticing.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from .sender import send_lifecycle_email

logger = logging.getLogger(__name__)

WELCOME_KEY = "welcome"
FEEDBACK_KEY = "feedback_v1"

WELCOME_SUBJECT = "Welcome to IntelliPlan — here's how to set it up"
WELCOME_PREHEADER = "Connect your school in under a minute and let the AI plan your week."

FEEDBACK_SUBJECT = "How's IntelliPlan going? (I read every reply)"
FEEDBACK_PREHEADER = "Two weeks in — I'd like to know what's working and what isn't."

#: How far back a welcome sweep looks. Wider than the daily cadence on
#: purpose: a missed cron run must be caught by the next one, not silently
#: skip a day's worth of signups.
WELCOME_WINDOW_HOURS = 36

#: The feedback ask lands at the two-week mark. The window is a day and a
#: half rather than exactly one so a late cron run does not skip a cohort.
FEEDBACK_MIN_DAYS = 14
FEEDBACK_MAX_DAYS = 15.5

#: Seconds between sends in a batch. Small, but enough that a few hundred
#: newsletter recipients arrive as a stream rather than a burst that trips
#: provider rate limits.
BATCH_DELAY_SECONDS = 0.1


def _blank_summary() -> dict:
    return {"sent": 0, "skipped": 0, "failed": 0, "reasons": {}}


def _tally(summary: dict, result) -> None:
    if result.sent:
        summary["sent"] += 1
        return
    if result.reason in {"provider_failed", "render_failed"}:
        summary["failed"] += 1
    else:
        summary["skipped"] += 1
    summary["reasons"][result.reason] = summary["reasons"].get(result.reason, 0) + 1


def has_real_activity(user_id: int) -> bool:
    """Did this person actually use IntelliPlan?

    "At least one completed study session, or at least one connected
    account." Asking for feedback from someone who signed up and never came
    back produces noise and reads as careless.

    ``linked_accounts`` is the cheap proxy for "connected an integration".
    It is not the only integration table — Canvas, Google, Notion, Schoology
    and the rest each have their own — but it is the one row that the common
    school-login path writes, and two EXISTS probes beat seven.
    """
    from App import ActiveSession, LinkedAccount, db

    session_exists = (
        db.session.query(ActiveSession.id)
        .filter(ActiveSession.user_id == user_id, ActiveSession.state == "completed")
        .first()
        is not None
    )
    if session_exists:
        return True
    return (
        db.session.query(LinkedAccount.id)
        .filter(LinkedAccount.user_id == user_id)
        .first()
        is not None
    )


def sweep_welcome(now: datetime | None = None, limit: int = 500) -> dict:
    """Welcome everyone who signed up recently and has not been welcomed."""
    from App import EmailSend, User, db

    now = now or datetime.utcnow()
    cutoff = now - timedelta(hours=WELCOME_WINDOW_HOURS)
    summary = _blank_summary()

    # Exclude only settled rows. A `failed` row is left in the candidate set
    # so a transient provider outage gets retried on the next run rather than
    # permanently costing that user their welcome — see sender._claim.
    already = db.session.query(EmailSend.user_id).filter(
        EmailSend.email_key == WELCOME_KEY, EmailSend.status != "failed"
    )
    candidates = (
        User.query.filter(User.created_at >= cutoff)
        .filter(~User.id.in_(already))
        .limit(limit)
        .all()
    )

    for user in candidates:
        result = send_lifecycle_email(
            user=user,
            email_key=WELCOME_KEY,
            template_name="welcome",
            subject=WELCOME_SUBJECT,
            preheader=WELCOME_PREHEADER,
            # Transactional: it explains a service this person just signed
            # up for. Age and suppression still apply.
            marketing=False,
        )
        _tally(summary, result)

    if len(candidates) >= limit:
        # Never truncate silently. A capped sweep that looks complete is how
        # a backlog goes unnoticed.
        logger.warning(
            "welcome sweep hit its limit of %s; %s users deferred to the next run",
            limit,
            "an unknown number of",
        )
        summary["truncated"] = True

    logger.info("welcome sweep: %s", summary)
    return summary


def sweep_feedback(now: datetime | None = None, limit: int = 500) -> dict:
    """Ask two-week-old accounts how it is going — if they actually used it."""
    from App import EmailSend, User, db

    now = now or datetime.utcnow()
    newest = now - timedelta(days=FEEDBACK_MIN_DAYS)
    oldest = now - timedelta(days=FEEDBACK_MAX_DAYS)
    summary = _blank_summary()
    summary["skipped_inactive"] = 0

    already = db.session.query(EmailSend.user_id).filter(
        EmailSend.email_key == FEEDBACK_KEY, EmailSend.status != "failed"
    )
    candidates = (
        User.query.filter(User.created_at <= newest, User.created_at >= oldest)
        .filter(~User.id.in_(already))
        .limit(limit)
        .all()
    )

    for user in candidates:
        if not has_real_activity(user.id):
            # Counted separately and logged: the number of signups that never
            # came back is a genuinely useful product signal, not just a skip.
            summary["skipped_inactive"] += 1
            summary["skipped"] += 1
            continue
        result = send_lifecycle_email(
            user=user,
            email_key=FEEDBACK_KEY,
            template_name="feedback",
            subject=FEEDBACK_SUBJECT,
            preheader=FEEDBACK_PREHEADER,
            marketing=True,
        )
        _tally(summary, result)

    if len(candidates) >= limit:
        logger.warning("feedback sweep hit its limit of %s; remainder deferred", limit)
        summary["truncated"] = True

    logger.info(
        "feedback sweep: %s (%s skipped as inactive)", summary, summary["skipped_inactive"]
    )
    return summary


# ── Newsletter ──────────────────────────────────────────────────────


def newsletter_key(now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    return f"newsletter_{now.year}_{now.month:02d}"


def normalise_newsletter(payload: dict, now: datetime | None = None) -> dict:
    """Validate and fill in an admin-supplied newsletter payload.

    Content comes from the admin, not from this file — a newsletter whose
    copy lives in the codebase needs a deploy to send.
    """
    now = now or datetime.utcnow()
    payload = payload or {}

    features = []
    for raw in payload.get("features") or []:
        if not isinstance(raw, dict):
            continue
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        features.append(
            {
                "tag": (raw.get("tag") or "New Feature").strip(),
                "title": title,
                "body": (raw.get("body") or "").strip(),
            }
        )

    raw_tip = payload.get("tip") or {}
    tip = {
        "title": (raw_tip.get("title") or "").strip(),
        "body": (raw_tip.get("body") or "").strip(),
        "action": (raw_tip.get("action") or "").strip(),
    }

    from . import content

    stats = payload.get("stats")
    if not (isinstance(stats, list) and len(stats) == 3):
        # The template lays the row out at 33% per cell, so it is three or
        # nothing. A malformed override falls back rather than breaking the
        # layout in an email nobody can recall.
        stats = content.live_stats()

    return {
        "stats": stats,
        "issue_label": (payload.get("issue_label") or f"IntelliPlan Newsletter · {now:%B %Y}").strip(),
        "headline": (payload.get("headline") or "New features, smarter studying.").strip(),
        "intro": (payload.get("intro") or "").strip(),
        "features": features,
        "tip": tip,
        "subject": (payload.get("subject") or "What's new at IntelliPlan").strip(),
        "preheader": (payload.get("preheader") or "").strip(),
        "email_key": (payload.get("email_key") or newsletter_key(now)).strip(),
    }


def newsletter_recipients(limit: int | None = None) -> list:
    """Everyone the marketing gate would currently let through.

    Evaluated properly rather than approximated with a WHERE clause, because
    the gate is the thing under test and a second, looser copy of it in SQL
    is exactly how a child ends up on a marketing list.
    """
    from App import User

    from .eligibility import is_marketing_eligible

    query = User.query.filter(User.marketing_emails_opt_in.is_(True))
    eligible = [u for u in query.all() if is_marketing_eligible(u)[0]]
    return eligible[:limit] if limit else eligible


def send_newsletter(
    payload: dict,
    test: bool = False,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict:
    """Send, test-send, or count the newsletter.

    ``dry_run=True`` (the default) sends nothing and reports who would get
    it. Sending the full list is opt-in and explicit — the default must
    never be the irreversible one.
    """
    from App import ADMIN_EMAILS, User

    from . import templates

    content = normalise_newsletter(payload, now)

    if test:
        recipients = [
            u
            for u in User.query.filter(User.email.isnot(None)).all()
            if (u.email or "").lower() in ADMIN_EMAILS
        ]
    else:
        recipients = newsletter_recipients()

    summary = {
        "email_key": content["email_key"],
        "subject": content["subject"],
        "test": bool(test),
        "dry_run": bool(dry_run),
        "recipients": len(recipients),
        **_blank_summary(),
    }

    if not templates.postal_address():
        summary["error"] = (
            "MARKETING_POSTAL_ADDRESS is unset. CAN-SPAM requires a physical "
            "mailing address in commercial email; refusing to send."
        )
        logger.error(summary["error"])
        return summary

    if dry_run:
        summary["preview_recipients"] = [u.email for u in recipients[:20]]
        return summary

    for user in recipients:
        try:
            result = send_lifecycle_email(
                user=user,
                email_key=content["email_key"],
                template_name="newsletter",
                subject=content["subject"],
                preheader=content["preheader"],
                marketing=True,
                context_extra={
                    "issue_label": content["issue_label"],
                    "headline": content["headline"],
                    "intro": content["intro"],
                    "features": content["features"],
                    "tip": content["tip"],
                    "stats": content["stats"],
                },
                # A test send must be repeatable, so it does not write the
                # ledger — otherwise the first test burns the month's key and
                # the real send silently skips every admin.
                dedupe=not test,
            )
            _tally(summary, result)
        except Exception as exc:
            # One bad recipient must not abort the run.
            logger.exception("newsletter send failed for %s: %s", user.id, exc)
            summary["failed"] += 1
        time.sleep(BATCH_DELAY_SECONDS)

    logger.info("newsletter %s: %s", content["email_key"], summary)
    return summary


# ── Weekly newsletter: generated and sent without human review ──────


def weekly_key(now: datetime | None = None) -> str:
    """One key per ISO week, so a re-fired cron cannot send a second issue."""
    now = now or datetime.utcnow()
    year, week, _ = now.isocalendar()
    return f"newsletter_{year}_w{week:02d}"


def generate_weekly_issue(now: datetime | None = None) -> dict:
    """Assemble this week's issue from the changelog, the tips library, the
    study-science pool and live stats.

    Deterministic for a given week: the rotating sections key off the ISO
    week number, so generating twice produces the same issue. That matters
    when nobody reviews it — a preview must be what actually goes out.
    """
    from . import changelog, content

    now = now or datetime.utcnow()
    since = now - timedelta(days=7)

    features = changelog.recent_changes(since=since, limit=4)
    tip = content.science_for(now)
    feature_tip = content.tip_for(now)

    # The how-to tip rides in as a feature card so every issue teaches
    # something even when the changelog comes back empty.
    features.append(
        {
            "tag": "How to · Tip of the week",
            "title": feature_tip["title"],
            "body": f"{feature_tip['body']} {feature_tip['action']}",
        }
    )

    week = now.isocalendar()[1]
    headline = (
        "New this week, plus a tip worth stealing."
        if len(features) > 1
        else "A tip worth stealing this week."
    )

    return {
        "issue_label": f"IntelliPlan Weekly · {now:%d %B %Y}",
        "headline": headline,
        "intro": (
            "Everything that changed on IntelliPlan this week, one feature you "
            "might not be using yet, and a study technique with evidence behind it."
        ),
        "features": features,
        "tip": tip,
        "stats": content.live_stats(),
        "subject": f"IntelliPlan Weekly — {tip['title'][:60]}",
        "preheader": f"{len(features)} thing(s) new, plus: {tip['title'][:80]}",
        "email_key": weekly_key(now),
        "week": week,
    }


def send_weekly_newsletter(
    now: datetime | None = None, dry_run: bool = False
) -> dict:
    """Generate this week's issue and send it. Called by cron, unattended.

    Sending without review is a deliberate choice by the operator. The
    guards that remain are the ones that do not need a human: every
    recipient still passes the marketing gate, the ISO-week key still makes
    a double cron fire a no-op, and the postal-address check still refuses
    a non-compliant send.

    An issue whose only content is the rotating tip is still sent — that is
    the point of having evergreen sections. An issue with no content at all
    is skipped rather than mailed empty.
    """
    now = now or datetime.utcnow()
    issue = generate_weekly_issue(now)

    if not issue["features"] and not issue["tip"].get("title"):
        logger.warning("weekly newsletter %s has no content; skipping", issue["email_key"])
        return {"email_key": issue["email_key"], "skipped_empty": True, **_blank_summary()}

    return send_newsletter(issue, test=False, dry_run=dry_run, now=now)


# ── Feedback: send to the whole eligible list now ───────────────────


def send_feedback_now(dry_run: bool = True, limit: int | None = None) -> dict:
    """The feedback ask to everyone who passes the marketing gate, ignoring
    the 14-day window and the activity requirement.

    Those two are quality heuristics — they exist so the question lands on
    someone with an opinion worth having. Consent, age and suppression are
    not heuristics and still apply in full: this reaches nobody who
    unsubscribed, nobody under 13 without parental consent, and nobody who
    never opted in.

    Deduplicated on the existing ``feedback_v1`` key, so anyone the cron
    sweep already asked is not asked twice.
    """
    from . import templates

    recipients = newsletter_recipients(limit=limit)
    summary = {
        "email_key": FEEDBACK_KEY,
        "dry_run": bool(dry_run),
        "recipients": len(recipients),
        **_blank_summary(),
    }

    if not templates.postal_address():
        summary["error"] = (
            "MARKETING_POSTAL_ADDRESS is unset. CAN-SPAM requires a physical "
            "mailing address in commercial email; refusing to send."
        )
        logger.error(summary["error"])
        return summary

    if dry_run:
        summary["preview_recipients"] = [u.email for u in recipients[:20]]
        return summary

    for user in recipients:
        try:
            _tally(
                summary,
                send_lifecycle_email(
                    user=user,
                    email_key=FEEDBACK_KEY,
                    template_name="feedback",
                    subject=FEEDBACK_SUBJECT,
                    preheader=FEEDBACK_PREHEADER,
                    marketing=True,
                ),
            )
        except Exception as exc:
            logger.exception("feedback send failed for %s: %s", user.id, exc)
            summary["failed"] += 1
        time.sleep(BATCH_DELAY_SECONDS)

    logger.info("feedback blast: %s", summary)
    return summary
