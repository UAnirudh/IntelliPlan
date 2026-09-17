"""Consented product insight: what students do, and what they tell us.

Imported by App.py after model setup; every App import is lazy, matching
the other ``*_glue.py`` modules.

The gate comes first, always
----------------------------
Nothing here records anything unless ``App._analytics_allowed()`` is true
for that request, which means: the visitor was asked, said yes, is not a
child, and is not an account waiting on a parent's consent. Turning
analytics back off does not merely stop collection -- it deletes what was
collected for that visitor and clears the id cookie, because consent you
cannot withdraw is not consent.

What is stored is deliberately thin: a route pattern, an event name from a
fixed list, a handful of allowlisted numeric or enum properties, and a
channel label. No URLs, no query strings, no page content, no free text
except what a student typed into an answer box on purpose.

The questions (``intelliplan/insight/prompts.py``) are separate: a student
answering a question is giving us the answer, so they are asked regardless
of the analytics choice -- but never a child, never twice, and never more
than one a day.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from time_utils import utcnow

from intelliplan.insight import events as ev
from intelliplan.insight import prompts as pr
from intelliplan.insight import reports

logger = logging.getLogger(__name__)

insight_bp = Blueprint("insight", __name__)

VISITOR_COOKIE = "ip_vid"
VISITOR_MAX_AGE = 60 * 60 * 24 * 365
#: Events older than this are deleted. Long enough for a term-over-term
#: comparison, short enough that we are not sitting on a year of behaviour
#: nobody has looked at.
RETENTION_DAYS = int(os.getenv("INSIGHT_RETENTION_DAYS", "180") or 180)
_DETAIL_MAX = 500


# ── Identity ────────────────────────────────────────────────────────────


def _actor() -> tuple[str, int | None, str | None]:
    """(actor, user_id, new_visitor_id_to_set).

    Signed-in students are keyed by account so the funnel survives the
    signup line; before that, by the consented visitor cookie.
    """
    if getattr(current_user, "is_authenticated", False):
        return f"u:{current_user.id}", int(current_user.id), None
    existing = (request.cookies.get(VISITOR_COOKIE) or "").strip()
    if existing and len(existing) <= 32 and existing.isalnum():
        return f"v:{existing}", None, None
    fresh = secrets.token_hex(8)
    return f"v:{fresh}", None, fresh


def _allowed() -> bool:
    from App import _analytics_allowed

    try:
        return bool(_analytics_allowed())
    except Exception:
        return False


# ── Capture ─────────────────────────────────────────────────────────────


def _record(kind: str, rule: str, actor: str, user_id: int | None,
            name: str | None = None, props: dict | None = None,
            channel: str = "") -> bool:
    from App import ProductEvent, db

    try:
        db.session.add(ProductEvent(
            actor=actor[:48], user_id=user_id, kind=kind[:8], rule=rule[:160],
            name=(name or None), props=json.dumps(props or {})[:1000],
            channel=(channel or "")[:40], created_at=utcnow(),
        ))
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        logger.warning("insight: recording %s %s failed: %s", kind, rule, exc)
        return False


def _first_touch(channel: str, rule: str) -> dict:
    return {
        "channel": channel,
        "utm_source": (request.args.get("utm_source") or "")[:40],
        "utm_medium": (request.args.get("utm_medium") or "")[:40],
        "utm_campaign": (request.args.get("utm_campaign") or "")[:60],
        "landing": rule[:160],
        "at": utcnow().isoformat(timespec="seconds"),
    }


def _remember_first_touch(channel: str, rule: str) -> None:
    """Keep the first channel we saw, and attach it once there is an account.

    First touch, not last: the point is to learn which channel *found* the
    student, and a last-touch model credits whichever link they happened to
    click on the way back to a product they already knew.
    """
    from flask import session

    from App import db

    if not session.get("ip_first_touch"):
        session["ip_first_touch"] = _first_touch(channel, rule)
        session.modified = True
    if getattr(current_user, "is_authenticated", False) and not current_user.first_touch_json:
        try:
            current_user.first_touch_json = json.dumps(session["ip_first_touch"])[:1000]
            db.session.commit()
        except Exception:
            db.session.rollback()


def _capture(response):
    """Record at most one event for this request, if the gate allows it."""
    try:
        if not _allowed():
            return response
        if response.status_code >= 400:
            return response  # errors are ip-report's job, not this one
        is_html = response.mimetype == "text/html"
        kind = ev.record_kind(request.method, is_html)
        rule = str(getattr(request.url_rule, "rule", "") or "")
        if kind is None or not ev.should_record_rule(rule):
            return response

        actor, user_id, fresh = _actor()
        channel = ev.classify_channel(
            request.args.get("utm_source"), request.referrer, request.host.split(":")[0]
        )
        if kind == ev.VIEW:
            _remember_first_touch(channel, rule)
        _record(kind, rule, actor, user_id, channel=channel if kind == ev.VIEW else "")
        if fresh:
            response.set_cookie(
                VISITOR_COOKIE, fresh, max_age=VISITOR_MAX_AGE, samesite="Lax",
                httponly=True, secure=request.is_secure,
            )
    except Exception as exc:
        # Telemetry must never be the reason a page fails to render.
        logger.warning("insight capture failed: %s", exc)
    return response


def _handle_consent_change(response):
    """Withdrawing analytics deletes what was collected under it."""
    from App import ProductEvent, db
    import cookie_policy

    if request.endpoint != "api_cookie_consent_save" or request.method != "POST":
        return response
    try:
        granted = (request.get_json(silent=True) or {}).get("granted") or []
        if cookie_policy.ANALYTICS in [str(g) for g in granted]:
            return response
        actor, _uid, _fresh = _actor()
        ProductEvent.query.filter(ProductEvent.actor == actor).delete(synchronize_session=False)
        db.session.commit()
        response.delete_cookie(VISITOR_COOKIE, samesite="Lax")
    except Exception as exc:
        db.session.rollback()
        logger.warning("insight: consent withdrawal cleanup failed: %s", exc)
    return response


@insight_bp.route("/api/insight/event", methods=["POST"])
def record_client_event():
    """Client-reported events: the ones no request can tell us about.

    Answers 204 either way. A client that learns whether it was recorded
    learns the visitor's consent state, and a page has no business
    branching on that.
    """
    if not _allowed():
        return ("", 204)
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not ev.is_allowed_event(name):
        return ("", 204)
    actor, user_id, _fresh = _actor()
    _record("client", str(body.get("rule") or "")[:160] if ev.should_record_rule(body.get("rule")) else "",
            actor, user_id, name=name, props=ev.sanitize_props(body.get("props")))
    return ("", 204)


# ── Questions ───────────────────────────────────────────────────────────


def _is_child(user: Any, now: datetime) -> bool:
    try:
        if user.birth_year and (now.year - int(user.birth_year)) < 13:
            return True
        return bool(user.parent_email and not user.parent_consent_granted)
    except Exception:
        return True  # unreadable age is not permission


def _prompt_state(user: Any, now: datetime) -> tuple[set[str], float | None, bool]:
    from App import InsightAnswer, SavedSchedule

    rows = InsightAnswer.query.filter_by(user_id=user.id).all()
    settled = {r.question for r in rows if r.status in {"answered", "dismissed"}}
    shown = [r.shown_at for r in rows if r.shown_at]
    last = max(shown) if shown else None
    hours = ((now - last).total_seconds() / 3600) if last else None
    has_plan = SavedSchedule.query.filter(SavedSchedule.user_id == user.id).first() is not None
    return settled, hours, has_plan


@insight_bp.route("/api/insight/prompt", methods=["GET"])
def next_prompt():
    if not current_user.is_authenticated:
        return jsonify({"status": "ok", "prompt": None})
    now = utcnow()
    try:
        settled, hours, has_plan = _prompt_state(current_user, now)
    except Exception as exc:
        logger.warning("insight: prompt state failed: %s", exc)
        return jsonify({"status": "ok", "prompt": None})
    created = getattr(current_user, "created_at", None) or now
    prompt = pr.prompt_for(
        account_age_days=(now - created).total_seconds() / 86400,
        has_plan=has_plan,
        settled=settled,
        hours_since_last_prompt=hours,
        is_child=_is_child(current_user, now),
    )
    if prompt is None:
        return jsonify({"status": "ok", "prompt": None})
    _mark_shown(prompt.key, now)
    payload = {
        "key": prompt.key,
        "question": prompt.question,
        "options": [{"value": v, "label": l} for v, l in prompt.options],
        "detail_label": prompt.detail_label,
    }
    if prompt.key == "invite":
        from growth_glue import referral_code_for

        code = referral_code_for(current_user)
        if not code:
            return jsonify({"status": "ok", "prompt": None})
        payload["invite_url"] = f"{request.host_url.rstrip('/')}/ref/{code}"
        payload["reward_days"] = 30
    return jsonify({"status": "ok", "prompt": payload})


def _mark_shown(key: str, now: datetime) -> None:
    from App import InsightAnswer, db

    try:
        row = InsightAnswer.query.filter_by(user_id=current_user.id, question=key).first()
        if row is None:
            row = InsightAnswer(user_id=current_user.id, question=key, status="shown")
            db.session.add(row)
        row.shown_at = now
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("insight: marking %s shown failed: %s", key, exc)


@insight_bp.route("/api/insight/prompt", methods=["POST"])
def answer_prompt():
    from App import InsightAnswer, db

    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401
    body = request.get_json(silent=True) or {}
    prompt = pr.prompt_by_key(body.get("key"))
    if prompt is None:
        return jsonify({"status": "error", "message": "unknown question"}), 400
    dismissed = bool(body.get("dismissed"))
    answer = body.get("answer")
    if not dismissed and not pr.is_valid_answer(prompt, answer):
        return jsonify({"status": "error", "message": "unknown answer"}), 400
    detail = str(body.get("detail") or "").strip()[:_DETAIL_MAX]
    now = utcnow()
    try:
        row = InsightAnswer.query.filter_by(user_id=current_user.id, question=prompt.key).first()
        if row is None:
            row = InsightAnswer(user_id=current_user.id, question=prompt.key, shown_at=now)
            db.session.add(row)
        row.status = "dismissed" if dismissed else "answered"
        row.answer = None if dismissed else (str(answer)[:40] if answer else None)
        row.detail = "" if dismissed else detail
        row.answered_at = now
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("insight: saving answer failed: %s", exc)
        return jsonify({"status": "error", "message": "could not save"}), 500
    return jsonify({"status": "ok"})


# ── Admin ───────────────────────────────────────────────────────────────


def purge_old_events(now: datetime | None = None) -> int:
    from App import ProductEvent, db

    cutoff = (now or utcnow()) - timedelta(days=max(1, RETENTION_DAYS))
    try:
        deleted = ProductEvent.query.filter(ProductEvent.created_at < cutoff).delete(
            synchronize_session=False)
        db.session.commit()
        return int(deleted or 0)
    except Exception as exc:
        db.session.rollback()
        logger.warning("insight: purge failed: %s", exc)
        return 0


def insight_report(days: int = 30, now: datetime | None = None) -> dict:
    from App import InsightAnswer, ProductEvent, User, db

    from growth_glue import load_user_facts

    now = now or utcnow()
    since = now - timedelta(days=max(1, days))

    rows = [
        (kind, rule, actor)
        for kind, rule, actor in db.session.query(
            ProductEvent.kind, ProductEvent.rule, ProductEvent.actor
        ).filter(ProductEvent.created_at >= since).limit(200000).all()
    ]
    client_rows = [
        (name, actor)
        for name, actor in db.session.query(ProductEvent.name, ProductEvent.actor)
        .filter(ProductEvent.created_at >= since, ProductEvent.name.isnot(None))
        .limit(200000).all()
    ]
    visitors = [
        (channel or "direct", actor)
        for channel, actor in db.session.query(ProductEvent.channel, ProductEvent.actor)
        .filter(ProductEvent.created_at >= since, ProductEvent.user_id.is_(None))
        .limit(200000).all()
    ]

    said = {
        uid: answer
        for uid, answer in db.session.query(InsightAnswer.user_id, InsightAnswer.answer)
        .filter(InsightAnswer.question == "heard_from", InsightAnswer.answer.isnot(None)).all()
    }
    facts = {f.user_id: f for f in load_user_facts(since=now - timedelta(days=max(days, 90)))}
    first_touch = {
        uid: raw
        for uid, raw in db.session.query(User.id, User.first_touch_json)
        .filter(User.created_at >= now - timedelta(days=max(days, 90))).all()
    }
    signups: list[tuple[str, bool, bool]] = []
    for uid, fact in facts.items():
        channel = said.get(uid)
        if channel:
            channel = f"said: {channel}"
        else:
            try:
                channel = (json.loads(first_touch.get(uid) or "{}") or {}).get("channel") or "unknown"
            except (TypeError, ValueError):
                channel = "unknown"
        signups.append((channel, fact.connected and fact.planned, fact.came_back()))

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "days": days,
        "events": len(rows),
        "features": reports.feature_usage(rows),
        "client_events": reports.feature_usage([("client", n, a) for n, a in client_rows]),
        "depth": reports.visit_depth(rows),
        "channels": reports.channel_table(signups, visitors),
        "survey": reports.survey_tally([
            (q, a) for q, a in db.session.query(InsightAnswer.question, InsightAnswer.answer)
            .filter(InsightAnswer.status.in_(["answered", "dismissed"])).all()
        ]),
        "notes": [
            {"question": q, "answer": a, "detail": d, "at": t.isoformat(timespec="minutes") if t else ""}
            for q, a, d, t in db.session.query(
                InsightAnswer.question, InsightAnswer.answer, InsightAnswer.detail,
                InsightAnswer.answered_at,
            ).filter(InsightAnswer.detail != "").order_by(InsightAnswer.answered_at.desc()).limit(50).all()
        ],
        "retention_days": RETENTION_DAYS,
    }


@insight_bp.route("/admin/insight")
def admin_insight():
    from App import is_admin

    if not is_admin(current_user):
        return jsonify({"status": "error", "message": "not found"}), 404
    try:
        days = max(1, min(180, int(request.args.get("days", 30))))
    except (TypeError, ValueError):
        days = 30
    purge_old_events()
    report = insight_report(days)
    if request.args.get("format") == "json":
        return jsonify({"status": "ok", **report})
    return render_template("admin_insight.html", report=report, active_page="admin")


# ── Install ─────────────────────────────────────────────────────────────


def install(app: Any) -> None:
    import analytics

    app.register_blueprint(insight_bp)
    # PostHog sends a user id to a third party. It gets the same answer as
    # everything else here: only for a visitor who agreed, never a child.
    analytics.set_consent_gate(_allowed)
    # Flask runs after_request handlers in reverse registration order, so
    # the withdrawal hook is registered *second* to run *first*: a request
    # that turns analytics off must not also be recorded by the capture
    # hook, and must have its cookie cleared rather than re-set.
    app.after_request(_capture)
    app.after_request(_handle_consent_change)
