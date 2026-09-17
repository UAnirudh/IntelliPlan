"""Glue between App.py and intelliplan/growth.

Imported by App.py after model setup; every App import is lazy, matching
the other ``*_glue.py`` modules. Owns:

* **Measurement.** Reading our own tables into ``retention.UserFacts`` and
  serving the activation funnel and weekly cohorts to the admin.
* **The plan.** Resolving free vs paid per request and charging the AI
  allowance, via the hooks ``ai_provider`` exposes.
* **Referral rewards.** Free months of the paid plan, both sides, instead
  of gift cards.
* **Checkout.** Stripe Checkout, including a link a student can hand to
  whoever actually holds the card.

Billing is off unless ``BILLING_ENABLED=1``. The public pricing, FAQ and
legal pages promise a free product with no paid tier; metering students
before those pages change would break a published promise. With the flag
off nothing is metered and nothing is sold, but plans, referral months and
the measurement all still run -- so the day it turns on, months students
already earned are real.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from flask import Blueprint, g, has_request_context, jsonify, redirect, render_template, request
from flask_login import current_user

from time_utils import utcnow

from intelliplan.growth import plans, retention

logger = logging.getLogger(__name__)

growth_bp = Blueprint("growth", __name__)

#: How long a parent-pays link stays valid.
PAY_LINK_MAX_AGE = 14 * 24 * 3600
PAY_LINK_SALT = "growth-pay-link"
#: A referred account counts as activated if it did one of these within
#: this many days of signing up -- the definition the ambassador page
#: already published.
ACTIVATION_DAYS = 7
PAYWALL_HEADER = "X-IntelliPlan-Paywall"


def billing_enabled() -> bool:
    return os.getenv("BILLING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


# ── Measurement ─────────────────────────────────────────────────────────


def _date_of(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _user_ids_with_rows(models: Iterable[Any], user_ids: set[int] | None = None) -> set[int]:
    """Every user id that owns at least one row in any of ``models``."""
    from App import db

    found: set[int] = set()
    for model in models:
        column = getattr(model, "user_id", None)
        if column is None:
            continue
        try:
            query = db.session.query(column).filter(column.isnot(None))
            if user_ids is not None and len(user_ids) <= 500:
                query = query.filter(column.in_(list(user_ids)))
            query = query.distinct()
            found.update(int(uid) for (uid,) in query.all())
        except Exception as exc:
            db.session.rollback()
            logger.warning("growth: reading %s failed: %s", getattr(model, "__name__", model), exc)
    return found if user_ids is None else found & user_ids


def _connection_models() -> list[Any]:
    import App

    names = (
        "LinkedAccount", "GoogleIntegration", "CanvasIntegration", "ClassroomIntegration",
        "BlackboardIntegration", "MoodleIntegration", "LMSToken",
    )
    return [getattr(App, n) for n in names if hasattr(App, n)]


def load_user_facts(since: datetime | None = None) -> list[retention.UserFacts]:
    """One ``UserFacts`` per account created since ``since``.

    Bulk reads, one per table, rather than per-user queries: this runs over
    every signup in the window and N+1 here is thousands of round trips.
    """
    from App import ActiveSession, SavedSchedule, StudyPoints, User, UserStreak, db

    query = db.session.query(User.id, User.created_at)
    if since is not None:
        query = query.filter(User.created_at >= since)
    signups = {int(uid): created for uid, created in query.all() if created is not None}
    if not signups:
        return []
    ids = set(signups)

    connected = _user_ids_with_rows(_connection_models(), ids)
    active: dict[int, set[date]] = {uid: set() for uid in ids}
    planned: set[int] = set()

    def mark(uid: Any, when: Any) -> None:
        day = _date_of(when)
        if day is not None and uid in active:
            active[uid].add(day)

    try:
        for uid, raw in db.session.query(UserStreak.user_id, UserStreak.qualified_dates_json).all():
            for d in _json_list(raw):
                mark(uid, d)
        for uid, sessions, history in db.session.query(
            StudyPoints.user_id, StudyPoints.session_history, StudyPoints.streak_history
        ).filter(StudyPoints.user_id.isnot(None)).all():
            for rec in _json_list(sessions):
                mark(uid, rec.get("date") if isinstance(rec, dict) else None)
            for d in _json_list(history):
                mark(uid, d if isinstance(d, str) else None)
        for uid, created in db.session.query(SavedSchedule.user_id, SavedSchedule.created_at).filter(
            SavedSchedule.user_id.isnot(None)
        ).all():
            if uid in active:
                planned.add(uid)
                mark(uid, created)
        for uid, started in db.session.query(ActiveSession.user_id, ActiveSession.started_at).filter(
            ActiveSession.user_id.isnot(None)
        ).all():
            if uid in active:
                planned.add(uid)
                mark(uid, started)
    except Exception as exc:
        db.session.rollback()
        logger.warning("growth: activity read failed: %s", exc)

    return [
        retention.UserFacts(
            user_id=uid,
            signed_up=created.date(),
            connected=uid in connected,
            planned=uid in planned,
            active_dates=frozenset(active[uid]),
        )
        for uid, created in signups.items()
    ]


def growth_report(weeks: int = 26, now: datetime | None = None) -> dict:
    now = now or utcnow()
    facts = load_user_facts(since=now - timedelta(weeks=max(1, weeks)))
    funnel = retention.activation_funnel(facts)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_weeks": weeks,
        "signups": len(facts),
        "funnel": funnel,
        "biggest_leak": retention.biggest_leak(funnel),
        "cohorts": retention.cohort_retention(facts, now.date()),
    }


@growth_bp.route("/admin/growth")
def admin_growth():
    from App import is_admin

    if not is_admin(current_user):
        return jsonify({"status": "error", "message": "not found"}), 404
    weeks = _clamp_int(request.args.get("weeks"), 26, 1, 104)
    report = growth_report(weeks)
    if request.args.get("format") == "json" or request.accept_mimetypes.best == "application/json":
        return jsonify({"status": "ok", **report})
    return render_template("admin_growth.html", report=report, active_page="admin")


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# ── The plan and the AI allowance ───────────────────────────────────────


def current_plan(user: Any = None, now: datetime | None = None) -> str:
    user = user if user is not None else current_user
    if not getattr(user, "is_authenticated", False):
        return plans.FREE
    return plans.plan_for(getattr(user, "paid_until", None), now or utcnow())


def usage_for(user_id: int, now: datetime | None = None) -> int:
    from App import AIUsage

    row = AIUsage.query.filter_by(user_id=user_id, period=plans.period_key(now or utcnow())).first()
    return int(row.count or 0) if row else 0


def _charge_one(user_id: int, limit: int, now: datetime) -> tuple[bool, int]:
    """Count one generation if the allowance has room. Returns (ok, used).

    Its own connection and transaction, so charging the allowance never
    commits -- or rolls back -- whatever the request has pending on the
    ORM session.
    """
    from sqlalchemy import text

    from App import db

    period = plans.period_key(now)
    with db.engine.begin() as conn:
        used = conn.execute(
            text("SELECT count FROM ai_usage WHERE user_id = :u AND period = :p"),
            {"u": user_id, "p": period},
        ).scalar()
        if used is None:
            conn.execute(
                text("INSERT INTO ai_usage (user_id, period, count, updated_at) "
                     "VALUES (:u, :p, 0, :t)"),
                {"u": user_id, "p": period, "t": now},
            )
            used = 0
        if used >= limit:
            return False, int(used)
        conn.execute(
            text("UPDATE ai_usage SET count = count + 1, updated_at = :t "
                 "WHERE user_id = :u AND period = :p"),
            {"u": user_id, "p": period, "t": now},
        )
        return True, int(used) + 1


def _plan_resolver() -> str:
    if not has_request_context():
        return plans.FREE
    return current_plan()


def _usage_gate(plan: str) -> None:
    """Charge one generation per request to a signed-in free account."""
    from ai_provider import AIAllowanceExceeded

    if not billing_enabled() or not has_request_context():
        return
    if plan == plans.PAID or not getattr(current_user, "is_authenticated", False):
        return
    if getattr(g, "_ai_generation_charged", False):
        return
    limit = plans.free_monthly_allowance()
    ok, used = _charge_one(int(current_user.id), limit, utcnow())
    if not ok:
        g._ai_paywall = True
        raise AIAllowanceExceeded(
            f"You've used all {limit} free AI generations this month. "
            "Upgrade for unlimited, or they reset on the 1st.",
            limit=limit,
            used=used,
        )
    g._ai_generation_charged = True


def _mark_paywall(response):
    if getattr(g, "_ai_paywall", False):
        response.headers[PAYWALL_HEADER] = "ai_allowance"
    return response


@growth_bp.route("/api/plan", methods=["GET"])
def api_plan():
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401
    settle_referral_rewards(current_user)
    plan = current_plan()
    state = plans.allowance_state(usage_for(current_user.id), plan)
    paid_until = getattr(current_user, "paid_until", None)
    return jsonify({
        "status": "ok",
        "billing_enabled": billing_enabled(),
        "metered": billing_enabled() and plan == plans.FREE,
        "paid_until": paid_until.isoformat() if paid_until else None,
        **state,
    })


# ── Referral rewards ────────────────────────────────────────────────────


def grant_signup_reward(new_user: Any, now: datetime | None = None) -> bool:
    """The referred student's month. Given at signup, once."""
    if not getattr(new_user, "referred_by_id", None):
        return False
    now = now or utcnow()
    new_user.paid_until = plans.extend_paid_until(
        new_user.paid_until, now, plans.REFERRAL_REWARD_DAYS
    )
    return True


def _activated(user: Any) -> bool:
    """Connected a school tool or planned something within the first week."""
    from App import SavedSchedule

    created = getattr(user, "created_at", None) or utcnow()
    deadline = created + timedelta(days=ACTIVATION_DAYS)
    if _user_ids_with_rows(_connection_models(), {int(user.id)}):
        return True
    try:
        return (
            SavedSchedule.query.filter(
                SavedSchedule.user_id == user.id,
                SavedSchedule.created_at <= deadline,
            ).first()
            is not None
        )
    except Exception:
        return False


def settle_referral_rewards(inviter: Any, now: datetime | None = None) -> int:
    """Pay the inviter a month for each referral that has activated.

    The inviter is paid on activation, not signup: paying for a signup pays
    for a throwaway account, and paying for a student who connected their
    school pays for the thing we want. Returns the months granted now.
    """
    from App import User, db

    now = now or utcnow()
    try:
        already = User.query.filter(
            User.referred_by_id == inviter.id, User.referral_rewarded_at.isnot(None)
        ).count()
        pending = (
            User.query.filter(
                User.referred_by_id == inviter.id, User.referral_rewarded_at.is_(None)
            )
            .order_by(User.created_at.asc())
            .limit(50)
            .all()
        )
    except Exception as exc:
        db.session.rollback()
        logger.warning("referral settle query failed: %s", exc)
        return 0

    granted = 0
    for referee in pending:
        if not _activated(referee):
            continue
        # Recorded even past the cap, so the dashboard count stays true and a
        # referral cannot be "saved up" to pay out after the cap is raised.
        referee.referral_rewarded_at = now
        if already + granted < plans.REFERRAL_MAX_REWARDS:
            inviter.paid_until = plans.extend_paid_until(
                inviter.paid_until, now, plans.REFERRAL_REWARD_DAYS
            )
            granted += 1
    if pending:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.warning("referral settle commit failed: %s", exc)
            return 0
    return granted


def remember_referral_from_query() -> None:
    """``?ref=<code>`` on any shared link counts as a referral click.

    The sharing loop -- a group invite dropped in a class chat, a live
    session link -- is where new students actually arrive from, and before
    this only ``/ref/<code>`` could attribute them.
    """
    code = (request.args.get("ref") or "").strip().lower()[:16]
    if not code or request.method != "GET" or getattr(current_user, "is_authenticated", False):
        return
    from flask import session

    from App import User

    if session.get("pending_referral"):
        return  # first click wins
    try:
        inviter = User.query.filter_by(referral_code=code).first()
    except Exception:
        return
    if inviter is not None:
        session["pending_referral"] = inviter.id
        session.modified = True


def referral_code_for(user: Any) -> str | None:
    from App import _ensure_referral_code

    try:
        return _ensure_referral_code(user)
    except Exception:
        return None


# ── Streak emails: their own one-click off switch ───────────────────────


@growth_bp.route("/email/streak-off/<token>", methods=["GET", "POST"])
def streak_email_off(token):
    from markupsafe import escape

    from App import User, _mini_page, db
    from intelliplan.email.sender import parse_unsubscribe_token

    payload = parse_unsubscribe_token(token or "")
    if not payload or payload.get("scope") != "streak":
        return _mini_page("Link not recognised", "<h1>We couldn't read that link</h1>"), 400
    address = (payload.get("email") or "").strip().lower()
    try:
        for user in User.query.filter(db.func.lower(User.email) == address).all():
            user.streak_emails_opt_in = False
        db.session.commit()
    except Exception:
        db.session.rollback()
    if request.method == "POST":
        return jsonify({"status": "ok", "unsubscribed": True})
    return _mini_page(
        "Streak emails off",
        "<h1>Streak emails are off</h1>"
        f"<p>We won't email <strong>{escape(address)}</strong> about streaks again. "
        'Other reminders are unchanged — manage them in <a href="/settings">Settings</a>.</p>',
    )


# ── Checkout ────────────────────────────────────────────────────────────


def _pay_serializer():
    from itsdangerous import URLSafeTimedSerializer

    from App import app

    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt=PAY_LINK_SALT)


def make_pay_token(user_id: int) -> str:
    return _pay_serializer().dumps({"uid": int(user_id)})


def read_pay_token(token: str) -> int | None:
    from itsdangerous import BadSignature, SignatureExpired

    try:
        payload = _pay_serializer().loads(token, max_age=PAY_LINK_MAX_AGE)
    except (BadSignature, SignatureExpired, Exception):
        return None
    try:
        return int(payload.get("uid"))
    except (AttributeError, TypeError, ValueError):
        return None


def _stripe():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    price = os.getenv("STRIPE_PRICE_ID", "")
    if not (billing_enabled() and key and price):
        return None, None
    try:
        import stripe
    except ImportError:
        logger.error("BILLING_ENABLED is set but the stripe package is not installed")
        return None, None

    stripe.api_key = key
    return stripe, price


def _base_url() -> str:
    from App import APP_BASE_URL

    return (APP_BASE_URL or request.host_url or "").rstrip("/")


def _checkout_for(student: Any, *, payer_is_student: bool) -> Any:
    stripe, price = _stripe()
    if stripe is None:
        return None
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price, "quantity": 1}],
        # The student, whoever pays. The webhook trusts this id only because
        # Stripe signs the event that carries it back.
        "client_reference_id": str(student.id),
        "metadata": {"user_id": str(student.id), "payer": "student" if payer_is_student else "other"},
        "subscription_data": {"metadata": {"user_id": str(student.id)}},
        "success_url": f"{_base_url()}/upgrade?paid=1",
        "cancel_url": f"{_base_url()}/upgrade",
        "allow_promotion_codes": True,
    }
    if payer_is_student and student.email:
        params["customer_email"] = student.email
    return stripe.checkout.Session.create(**params)


def _is_minor(user: Any, now: datetime) -> bool:
    year = getattr(user, "birth_year", None)
    return bool(year) and now.year - int(year) < 18


@growth_bp.route("/upgrade")
def upgrade_page():
    if not current_user.is_authenticated:
        return redirect("/login?next=/upgrade")
    settle_referral_rewards(current_user)
    now = utcnow()
    plan = current_plan(now=now)
    return render_template(
        "upgrade.html",
        active_page="pricing",
        billing_enabled=billing_enabled(),
        checkout_ready=_stripe()[0] is not None,
        plan=plan,
        allowance=plans.allowance_state(usage_for(current_user.id, now), plan),
        paid_until=getattr(current_user, "paid_until", None),
        is_minor=_is_minor(current_user, now),
        referral_code=referral_code_for(current_user),
        reward_days=plans.REFERRAL_REWARD_DAYS,
        paid=request.args.get("paid") == "1",
    )


@growth_bp.route("/api/billing/checkout", methods=["POST"])
def api_checkout():
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401
    if _is_minor(current_user, utcnow()):
        # A card in a minor's name is a chargeback waiting to happen. The
        # parent link below is the checkout for them.
        return jsonify({"status": "error", "message": "use_parent_link"}), 403
    try:
        checkout = _checkout_for(current_user, payer_is_student=True)
    except Exception as exc:
        logger.exception("stripe checkout failed: %s", exc)
        return jsonify({"status": "error", "message": "Checkout is unavailable right now."}), 502
    if checkout is None:
        return jsonify({"status": "error", "message": "Checkout is not open yet."}), 503
    return jsonify({"status": "ok", "url": checkout.url})


@growth_bp.route("/api/billing/pay-link", methods=["POST"])
def api_pay_link():
    """A link the student sends to whoever holds the card.

    Deliberately a link the student shares themselves rather than an email
    we send to an address they type: an endpoint that mails arbitrary
    addresses on request is a spam relay with a signup form in front of it.
    """
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401
    if not billing_enabled():
        return jsonify({"status": "error", "message": "Checkout is not open yet."}), 503
    return jsonify({"status": "ok", "url": f"{_base_url()}/pay/{make_pay_token(current_user.id)}"})


@growth_bp.route("/pay/<token>", methods=["GET", "POST"])
def pay_for_student(token):
    from App import User

    uid = read_pay_token(token)
    student = User.query.get(uid) if uid else None
    if student is None or not billing_enabled():
        return render_template(
            "error.html", active_page="error", error_code=404, error_id="PAY-LINK",
            message="That payment link has expired or no longer works. Ask for a new one.",
        ), 404
    if request.method == "POST":
        try:
            checkout = _checkout_for(student, payer_is_student=False)
        except Exception as exc:
            logger.exception("stripe checkout (pay link) failed: %s", exc)
            checkout = None
        if checkout is None:
            return render_template(
                "error.html", active_page="error", error_code=503, error_id="PAY-UNAVAILABLE",
                message="Checkout is unavailable right now. Please try again later.",
            ), 503
        return redirect(checkout.url, code=303)
    # First name only. Whoever opens this link needs to recognise who they
    # are paying for, not read the student's account.
    first = (student.name or "").strip().split(" ")[0] or "a student"
    return render_template(
        "pay_for_student.html", active_page="pricing", student_first_name=first,
        already_paid=current_plan(student) == plans.PAID,
    )


@growth_bp.route("/api/billing/webhook", methods=["POST"])
def stripe_webhook():
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe, _price = _stripe()
    if stripe is None or not secret:
        return jsonify({"status": "error", "message": "billing not configured"}), 503
    try:
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature", ""), secret
        )
    except Exception:
        return jsonify({"status": "error", "message": "bad signature"}), 400
    try:
        apply_billing_event(event)
    except Exception as exc:
        logger.exception("stripe webhook handling failed: %s", exc)
        # 500 so Stripe retries; the handler is idempotent.
        return jsonify({"status": "error"}), 500
    return jsonify({"status": "ok"})


def apply_billing_event(event: Any, now: datetime | None = None) -> bool:
    """Move ``paid_until`` from a verified Stripe event. Idempotent.

    Sets the window to the period Stripe says was paid for rather than
    adding a month per event, so a retried or duplicated webhook cannot
    hand out a second month.
    """
    from App import User, db

    now = now or utcnow()
    kind = event["type"]
    obj = event["data"]["object"]
    if kind == "checkout.session.completed":
        uid = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        period_end = now + timedelta(days=31)
    elif kind == "invoice.paid":
        lines = ((obj.get("lines") or {}).get("data") or [{}])
        meta = (obj.get("subscription_details") or {}).get("metadata") or lines[0].get("metadata") or {}
        uid = meta.get("user_id")
        end = ((lines[0].get("period") or {}).get("end"))
        period_end = datetime.utcfromtimestamp(int(end)) if end else now + timedelta(days=31)
    else:
        return False
    try:
        user = User.query.get(int(uid))
    except (TypeError, ValueError):
        user = None
    if user is None:
        logger.warning("stripe %s for unknown user %r", kind, uid)
        return False
    # Never shorten a window: referral months already earned stay earned.
    if user.paid_until is None or period_end > user.paid_until:
        user.paid_until = period_end
    if obj.get("customer"):
        user.stripe_customer_id = str(obj["customer"])[:64]
    db.session.commit()
    return True


# ── Install ─────────────────────────────────────────────────────────────


def install(app: Any) -> None:
    """Register the blueprint and the per-request hooks."""
    import ai_provider

    app.register_blueprint(growth_bp)
    ai_provider.set_account_hooks(plan_resolver=_plan_resolver, usage_gate=_usage_gate)
    app.before_request(remember_referral_from_query)
    app.after_request(_mark_paywall)
