"""Glue between App.py and intelliplan/premium.

Imported by App.py after model setup; every App import is lazy, matching the
other ``*_glue.py`` modules. Owns:

* **The budget.** Premium plans include a monthly AI budget in dollars. Every
  platform-paid model call is priced from the provider's own usage report
  and written to ``ai_spend_events``; the budget is the plan's allowance plus
  any top-ups, minus that spend.
* **The Premium tutor turn.** Route the question, ask for clarification if
  it is too vague, then answer on the student's linked key or on Claude.
* **Linked AI accounts.** Store, verify and remove a student's own API keys.
* **The hooks into ai_provider.** Claude calls made by any other feature are
  metered against the same budget, stop leading the model chain once it is
  spent, and linked keys are tried first.
* **Billing.** Premium checkout, top-ups and the webhook events for them.

Off switches
------------
Premium is sold only when ``BILLING_ENABLED`` is on and its Stripe Prices are
set. Linking AI accounts is off until ``BYOK_ENABLED=1``, because the Privacy
Policy has to describe sending prompts to the provider a student chooses
before any student can choose one.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from typing import Any

from flask import Blueprint, g, has_request_context, jsonify, redirect, render_template, request
from flask_login import current_user

from time_utils import utcnow

from intelliplan.growth import plans as growth_plans
from intelliplan.premium import byok, catalog, claude_tutor, pricing, router

logger = logging.getLogger(__name__)

premium_bp = Blueprint("premium", __name__)

FREE = "free"
PRO = catalog.PRO

#: Where the router's own calls are recorded. Never drawn from a budget:
#: deciding which model to use is IntelliPlan's overhead, not the student's.
SOURCE_PLATFORM = "platform"
SOURCE_BYOK = "byok"
SOURCE_ROUTER = "router"


def byok_enabled() -> bool:
    return os.getenv("BYOK_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _platform_claude_key() -> str | None:
    import ai_provider

    return ai_provider.anthropic_api_key()


# ── Plan ────────────────────────────────────────────────────────────────


def current_tier(user: Any = None, now: datetime | None = None) -> str:
    """free | pro | premium | premium_byok, for a signed-in user."""
    user = user if user is not None else (current_user if has_request_context() else None)
    if user is None or not getattr(user, "is_authenticated", False):
        return FREE
    now = now or utcnow()
    if growth_plans.plan_for(getattr(user, "paid_until", None), now) != growth_plans.PAID:
        return FREE
    tier = getattr(user, "plan_tier", None) or PRO
    return tier if catalog.is_premium(tier) else PRO


# ── Budget ──────────────────────────────────────────────────────────────


def _period(now: datetime | None = None) -> str:
    return growth_plans.period_key(now or utcnow())


def spent_micro(user_id: int, period: str, source: str = SOURCE_PLATFORM) -> int:
    from sqlalchemy import func

    from App import AISpendEvent, db

    total = db.session.query(func.coalesce(func.sum(AISpendEvent.cost_micro), 0)).filter(
        AISpendEvent.user_id == user_id, AISpendEvent.period == period,
        AISpendEvent.source == source).scalar()
    return int(total or 0)


def granted_micro(user_id: int, period: str) -> int:
    from sqlalchemy import func

    from App import AICreditGrant, db

    total = db.session.query(func.coalesce(func.sum(AICreditGrant.amount_micro), 0)).filter(
        AICreditGrant.user_id == user_id, AICreditGrant.period == period).scalar()
    return int(total or 0)


def budget_state(user: Any, now: datetime | None = None) -> dict:
    """Everything the meter shows. Money as integer micro-dollars and as USD."""
    now = now or utcnow()
    period = _period(now)
    tier = current_tier(user, now)
    plan = catalog.get(tier)
    allowance = plan.ai_budget_micro if plan else 0
    extra = granted_micro(user.id, period)
    total = allowance + extra
    spent = spent_micro(user.id, period)
    remaining = max(0, total - spent)
    return {
        "tier": tier,
        "period": period,
        "metered": plan is not None,
        "budget_micro": total,
        "spent_micro": spent,
        "remaining_micro": remaining,
        # What the calls on the student's own key would have cost on ours.
        "byok_value_usd": pricing.usd(spent_micro(user.id, period, SOURCE_BYOK)),
        "budget_usd": pricing.usd(total),
        "spent_usd": pricing.usd(spent),
        "remaining_usd": pricing.usd(remaining),
        "topups_usd": pricing.usd(extra),
        "low": plan is not None and total > 0 and remaining <= total * catalog.LOW_BUDGET_SHARE,
        "exhausted": plan is not None and remaining <= 0,
    }


def record_spend(user_id: int, *, provider: str, model: str, usage: pricing.Usage,
                 source: str, purpose: str, tier: str | None = None,
                 now: datetime | None = None) -> int:
    """Write one priced call to the ledger. Returns its cost in micro-dollars.

    Its own connection and transaction, like the AI allowance counter, so
    recording spend never commits or rolls back what the request has pending.
    """
    from App import AISpendEvent, db

    now = now or utcnow()
    cost = pricing.cost_micro(model, usage) if source != SOURCE_BYOK else 0
    if source == SOURCE_BYOK:
        # What it would have cost on our key: shown to the student as savings.
        cost_shadow = pricing.cost_micro(model, usage) if pricing.known(model) else 0
    else:
        cost_shadow = cost
    try:
        with db.engine.begin() as conn:
            conn.execute(AISpendEvent.__table__.insert().values(
                user_id=user_id, period=_period(now), created_at=now,
                provider=provider[:16], model=model[:64], source=source, purpose=purpose[:16],
                tier=(tier or None), input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens, cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens, cost_micro=cost_shadow,
            ))
    except Exception as exc:
        # The answer was already delivered. Losing one ledger row is better
        # than failing the request after the money is spent.
        logger.error("could not record AI spend for user %s: %s", user_id, exc)
    return cost


def grant_credit(user_id: int, amount_micro: int, *, reason: str = "topup",
                 stripe_ref: str | None = None, now: datetime | None = None) -> bool:
    """Add budget to the current month. Idempotent on ``stripe_ref``."""
    from App import AICreditGrant, db

    if amount_micro <= 0:
        return False
    if stripe_ref and AICreditGrant.query.filter_by(stripe_ref=stripe_ref).first():
        return False
    db.session.add(AICreditGrant(user_id=user_id, period=_period(now), amount_micro=amount_micro,
                                 reason=reason, stripe_ref=stripe_ref))
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("credit grant failed (duplicate webhook?): %s", exc)
        return False
    return True


def _maybe_warn_low_budget(user: Any, state: dict) -> None:
    """Email once a month when the budget drops below the warning line."""
    if not state.get("low") or getattr(user, "ai_low_budget_notified", None) == state["period"]:
        return
    from App import db

    try:
        user.ai_low_budget_notified = state["period"]
        db.session.commit()
    except Exception:
        db.session.rollback()
        return
    uid = user.id

    def _send():
        try:
            from App import User, _send_email, app, APP_BASE_URL
            from intelliplan.email.eligibility import is_transactional_eligible

            with app.app_context():
                row = db.session.get(User, uid)
                ok, _reason = is_transactional_eligible(row)
                if not ok:
                    return
                body = (
                    f"You've used most of this month's AI budget on IntelliPlan "
                    f"(${state['remaining_usd']:.2f} of ${state['budget_usd']:.2f} left).\n\n"
                    "Plani will keep answering with the free models once it runs out. "
                    "You can add more, or link your own AI account, here:\n"
                    f"{(APP_BASE_URL or '').rstrip('/')}/settings/ai\n\n— IntelliPlan"
                )
                _send_email(row.email, "Your IntelliPlan AI budget is running low", body)
        except Exception as exc:
            logger.warning("low budget email failed for user %s: %s", uid, exc)

    threading.Thread(target=_send, name=f"ip-budget-{uid}", daemon=True).start()


# ── Linked accounts ─────────────────────────────────────────────────────


def _is_adult(user: Any, now: datetime | None = None) -> bool:
    year = getattr(user, "birth_year", None)
    if not year:
        return False
    return (now or utcnow()).year - int(year) - 1 >= 18


def linked_accounts(user_id: int, *, active_only: bool = False) -> list[Any]:
    from App import LinkedAIAccount

    rows = LinkedAIAccount.query.filter_by(user_id=user_id).all()
    if active_only:
        rows = [r for r in rows if r.status == "active" and r.api_key]
    order = {p: i for i, p in enumerate(byok.PREFERENCE)}
    return sorted(rows, key=lambda r: order.get(r.provider, 99))


def _account_view(row: Any) -> dict:
    meta = byok.PROVIDERS.get(row.provider, {})
    return {
        "provider": row.provider,
        "name": meta.get("name", row.provider),
        "key_hint": row.key_hint,
        "status": row.status,
        "last_error": row.last_error,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _mark_key(row: Any, *, ok: bool, error: str | None = None) -> None:
    from App import db

    try:
        if ok:
            row.status, row.last_error, row.last_used_at = "active", None, utcnow()
        else:
            row.status, row.last_error = "invalid", (error or "rejected")[:200]
        db.session.commit()
    except Exception:
        db.session.rollback()


# ── Routing ─────────────────────────────────────────────────────────────


def _classifier(user_id: int | None):
    """The cheap model that reads the question: Groq, else Gemini, else none."""
    import ai_provider

    if not (ai_provider.groq_api_key() or ai_provider.gemini_available()):
        return None

    def run(messages: list[dict]) -> str:
        if ai_provider.groq_api_key():
            model = ai_provider.GROQ_FAST
            resp = ai_provider._groq_client().chat.completions.create(
                model=model, messages=messages, temperature=0, max_tokens=400,
                response_format={"type": "json_object"})
            if user_id and getattr(resp, "usage", None) is not None:
                record_spend(user_id, provider="groq", model=model,
                             usage=pricing.Usage.from_openai_style(resp.usage),
                             source=SOURCE_ROUTER, purpose="route")
            return resp.choices[0].message.content or ""
        return ai_provider._gemini_chat(messages, "fast", 0.0, 400, {"type": "json_object"},
                                        thinking_budget=0)

    return run


class BudgetExhausted(RuntimeError):
    pass


def wants_premium_tutor(user: Any) -> bool:
    """Whether the tutor should take the Premium path for this student."""
    if not getattr(user, "is_authenticated", False):
        return False
    tier = current_tier(user)
    if catalog.is_premium(tier):
        return True
    return byok_enabled() and bool(linked_accounts(user.id, active_only=True))


_JUST_ANSWER = {"just answer", "just answer it", "just answer please", "skip", "skip questions",
                "no just answer", "answer anyway"}


_TAG_PREFIX = re.compile(r"^\s*(\[[^\]\n]{0,400}\]\s*\n?)+")


def _plain(text: str) -> str:
    """The student's words without the page's "[Subject: …]" style prefixes."""
    return _TAG_PREFIX.sub("", str(text or "")).strip()


def tutor_turn(user: Any, *, stable_system: str, dynamic_system: list[str],
               messages: list[dict], force_answer: bool = False) -> dict | None:
    """One Premium tutor turn. Returns None when the normal path should answer.

    The result is ``{"kind": "clarify" | "answer", "reply": str, ...}``.
    Raises BudgetExhausted when a Premium student has spent the month's
    budget and has no linked key that works.
    """
    question = _plain(next((m.get("content", "") for m in reversed(messages)
                            if m.get("role") == "user"), ""))
    context = messages[:-1] if messages and messages[-1].get("role") == "user" else messages
    if question.strip().lower().rstrip(".!") in _JUST_ANSWER:
        force_answer = True

    tier_name = current_tier(user)
    plan = catalog.get(tier_name)
    linked = linked_accounts(user.id, active_only=True) if byok_enabled() else []
    if plan is None and not linked:
        return None

    classification = router.classify(question, context, classifier=_classifier(user.id))
    clarify_ok = bool(getattr(user, "premium_clarify", True)) and not force_answer \
        and not router.last_turn_was_clarification(context)
    if clarify_ok and classification.needs_clarification:
        return {
            "kind": "clarify",
            "reply": router.clarification_text(classification.clarifying_questions),
            "questions": classification.clarifying_questions,
            "route": {"intent": classification.intent, "subject": classification.subject,
                      "source": classification.source},
        }

    preference = getattr(user, "premium_model_pref", None) or router.AUTO
    input_tokens = pricing.estimate_tokens(
        stable_system + "".join(dynamic_system) + "".join(str(m.get("content", "")) for m in messages))

    # 1. The student's own key, if they linked one. Unmetered.
    for row in linked:
        route = router.decide(classification, preference=preference, remaining_micro=None,
                              input_tokens=input_tokens)
        try:
            result = byok.chat(row.provider, row.api_key, tier=route.tier, messages=messages,
                               stable_system=stable_system, dynamic_system=dynamic_system,
                               max_tokens=route.max_tokens, effort=route.effort,
                               claude_model=route.model)
        except claude_tutor.TutorError as exc:
            logger.info("linked %s key failed for user %s: %s", row.provider, user.id, exc)
            if exc.key_invalid:
                _mark_key(row, ok=False, error=str(exc))
            continue
        _mark_key(row, ok=True)
        record_spend(user.id, provider=row.provider, model=result.model, usage=result.usage,
                     source=SOURCE_BYOK, purpose="tutor", tier=route.tier)
        return {"kind": "answer", "reply": result.text, "model": result.model,
                "source": SOURCE_BYOK, "provider": row.provider, "route": route.as_dict(),
                "cost_usd": 0.0}

    # 2. Claude on IntelliPlan's key, within the budget.
    key = _platform_claude_key()
    if plan is None or not key:
        return None
    state = budget_state(user)
    route = router.decide(classification, preference=preference,
                          remaining_micro=state["remaining_micro"], input_tokens=input_tokens)
    if route is None:
        raise BudgetExhausted("This month's AI budget is used up.")
    result = claude_tutor.answer(api_key=key, model=route.model, effort=route.effort,
                                 max_tokens=route.max_tokens, messages=messages,
                                 dynamic_system=dynamic_system, stable_system=stable_system)
    cost = record_spend(user.id, provider="anthropic", model=result.model, usage=result.usage,
                        source=SOURCE_PLATFORM, purpose="tutor", tier=route.tier)
    after = budget_state(user)
    _maybe_warn_low_budget(user, after)
    return {"kind": "answer", "reply": result.text, "model": result.model,
            "source": SOURCE_PLATFORM, "route": route.as_dict(), "refused": result.refused,
            "cost_usd": pricing.usd(cost),
            "budget": {k: after[k] for k in ("remaining_usd", "budget_usd", "low", "exhausted")}}


# ── ai_provider hooks ───────────────────────────────────────────────────


def _claude_gate() -> bool:
    """May this request's other AI features use IntelliPlan's Claude key?

    Pro keeps its original behaviour. A Premium student may while budget
    remains; once it is spent their requests fall back to the free models
    instead of failing.
    """
    if not has_request_context() or not getattr(current_user, "is_authenticated", False):
        return True
    tier = current_tier()
    if not catalog.is_premium(tier):
        return True
    try:
        return not budget_state(current_user)["exhausted"]
    except Exception as exc:
        logger.warning("budget check failed, allowing Claude: %s", exc)
        return True


def _spend_recorder(provider: str, model: str, usage: Any, source: str) -> None:
    """Meter Claude calls made by features other than the tutor."""
    if not has_request_context() or not getattr(current_user, "is_authenticated", False):
        return
    if getattr(g, "_premium_tutor_metered", False):
        return
    record_spend(int(current_user.id), provider=provider, model=model,
                 usage=pricing.Usage.from_anthropic(usage), source=source, purpose="feature")


def _byok_steps(tier: str) -> list[tuple[str, str, str]]:
    """Linked keys to try first for this request's other AI features."""
    if not byok_enabled() or not has_request_context() or \
            not getattr(current_user, "is_authenticated", False):
        return []
    steps: list[tuple[str, str, str]] = []
    mapped = {"fast": router.QUICK, "standard": router.BALANCED, "vision": router.BALANCED}
    for row in linked_accounts(int(current_user.id), active_only=True):
        if row.provider == byok.ANTHROPIC:
            model = router.tier_spec(mapped.get(tier, router.BALANCED)).model
        else:
            model = byok._model(row.provider, mapped.get(tier, router.BALANCED))
        if model:
            steps.append((row.provider, model, row.api_key))
    return steps


# ── Routes ──────────────────────────────────────────────────────────────


def _login_required():
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401
    return None


@premium_bp.route("/settings/ai")
def premium_settings_page():
    if not current_user.is_authenticated:
        return redirect("/login?next=/settings/ai")
    from growth_glue import billing_enabled

    plan_rows = list(catalog.plans().values())
    return render_template(
        "premium_settings.html",
        active_page="settings",
        state=budget_state(current_user),
        plans=plan_rows,
        topup=catalog.topup(),
        providers=byok.PROVIDERS,
        accounts=[_account_view(r) for r in linked_accounts(current_user.id)],
        byok_enabled=byok_enabled(),
        can_link=_is_adult(current_user),
        billing_enabled=billing_enabled(),
        model_pref=getattr(current_user, "premium_model_pref", None) or router.AUTO,
        clarify=bool(getattr(current_user, "premium_clarify", True)),
        paid=request.args.get("paid") == "1",
    )


@premium_bp.route("/api/premium/status", methods=["GET"])
def api_premium_status():
    denied = _login_required()
    if denied:
        return denied
    state = budget_state(current_user)
    return jsonify({
        "status": "ok",
        **state,
        "model_pref": getattr(current_user, "premium_model_pref", None) or router.AUTO,
        "clarify": bool(getattr(current_user, "premium_clarify", True)),
        "byok_enabled": byok_enabled(),
        "accounts": [_account_view(r) for r in linked_accounts(current_user.id)],
        "plans": [{"id": p.id, "name": p.name, "price_usd": p.price_usd,
                   "ai_budget_usd": p.ai_budget_usd, "byok": p.byok, "features": list(p.features)}
                  for p in catalog.plans().values()],
    })


@premium_bp.route("/api/premium/preferences", methods=["POST"])
def api_premium_preferences():
    denied = _login_required()
    if denied:
        return denied
    from App import db

    data = request.get_json(silent=True) or {}
    if "model_pref" in data:
        pref = str(data.get("model_pref") or "").strip().lower()
        if pref not in router.PREFERENCES:
            return jsonify({"status": "error", "message": "Unknown answer depth."}), 400
        current_user.premium_model_pref = pref
    if "clarify" in data:
        current_user.premium_clarify = bool(data.get("clarify"))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"status": "error", "message": "Could not save that."}), 500
    return jsonify({"status": "ok", "model_pref": current_user.premium_model_pref,
                    "clarify": bool(current_user.premium_clarify)})


@premium_bp.route("/api/premium/usage", methods=["GET"])
def api_premium_usage():
    """This month's spend by day and by model, for the meter's breakdown."""
    denied = _login_required()
    if denied:
        return denied
    from App import AISpendEvent

    period = _period()
    rows = AISpendEvent.query.filter(AISpendEvent.user_id == current_user.id,
                                     AISpendEvent.period == period,
                                     AISpendEvent.source != SOURCE_ROUTER).all()
    by_day: dict[str, int] = {}
    by_model: dict[str, dict] = {}
    for r in rows:
        if r.source == SOURCE_PLATFORM:
            day = r.created_at.strftime("%Y-%m-%d") if r.created_at else period
            by_day[day] = by_day.get(day, 0) + int(r.cost_micro or 0)
        entry = by_model.setdefault(f"{r.source}:{r.model}", {
            "model": r.model, "source": r.source, "calls": 0, "cost_micro": 0})
        entry["calls"] += 1
        entry["cost_micro"] += int(r.cost_micro or 0)
    return jsonify({
        "status": "ok",
        "period": period,
        "by_day": [{"day": d, "cost_usd": pricing.usd(c)} for d, c in sorted(by_day.items())],
        "by_model": [{**v, "cost_usd": pricing.usd(v.pop("cost_micro"))}
                     for v in sorted(by_model.values(), key=lambda v: -v["cost_micro"])],
    })


@premium_bp.route("/api/ai-accounts", methods=["GET"])
def api_ai_accounts():
    denied = _login_required()
    if denied:
        return denied
    return jsonify({"status": "ok", "enabled": byok_enabled(), "can_link": _is_adult(current_user),
                    "providers": [{"id": k, **v} for k, v in byok.PROVIDERS.items()],
                    "accounts": [_account_view(r) for r in linked_accounts(current_user.id)]})


@premium_bp.route("/api/ai-accounts", methods=["POST"])
def api_ai_accounts_link():
    denied = _login_required()
    if denied:
        return denied
    import secret_box

    from App import LinkedAIAccount, db

    if not byok_enabled():
        return jsonify({"status": "error", "message": "Linking AI accounts isn't available yet."}), 503
    if not _is_adult(current_user):
        # Provider API accounts are for adults, and a key is a payment
        # method: a student under 18 cannot hold one in their own name.
        return jsonify({"status": "error",
                        "message": "You need to be 18 or older to link an AI account."}), 403
    if not secret_box.is_enabled():
        # A third-party key is spendable money. It is never stored in plain text.
        logger.error("BYOK link refused: DATA_ENCRYPTION_KEY is not configured")
        return jsonify({"status": "error",
                        "message": "Linking is unavailable right now. This is on our side."}), 503
    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider") or "").strip().lower()
    key = str(data.get("api_key") or "").strip()
    check = byok.verify(provider, key)
    if not check.ok:
        return jsonify({"status": "error", "message": check.message}), 400
    row = LinkedAIAccount.query.filter_by(user_id=current_user.id, provider=provider).first()
    if row is None:
        row = LinkedAIAccount(user_id=current_user.id, provider=provider)
        db.session.add(row)
    row.api_key = key
    row.key_hint = byok.hint(key)
    row.status, row.last_error, row.verified_at = "active", None, utcnow()
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"status": "error", "message": "Could not save that. Try again."}), 500
    logger.info("user %s linked a %s key", current_user.id, provider)
    return jsonify({"status": "ok", "message": check.message, "account": _account_view(row)})


@premium_bp.route("/api/ai-accounts/<provider>", methods=["DELETE"])
def api_ai_accounts_unlink(provider):
    denied = _login_required()
    if denied:
        return denied
    from App import LinkedAIAccount, db

    LinkedAIAccount.query.filter_by(user_id=current_user.id,
                                    provider=str(provider).lower()).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"status": "ok"})


# ── Billing ─────────────────────────────────────────────────────────────


def sellable_plan(raw: Any) -> str:
    """The plan a checkout request asked for. Anything unknown is Pro."""
    value = str(raw or "").strip().lower()
    return value if value in catalog.SELLABLE else PRO


@premium_bp.route("/api/premium/topup", methods=["POST"])
def api_premium_topup():
    denied = _login_required()
    if denied:
        return denied
    from growth_glue import _base_url, _is_minor, billing_enabled

    if not catalog.is_premium(current_tier()):
        return jsonify({"status": "error", "message": "Top-ups are for Premium plans."}), 403
    if _is_minor(current_user, utcnow()):
        return jsonify({"status": "error", "message": "use_parent_link"}), 403
    price = catalog.TopUp.stripe_price_id()
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not (billing_enabled() and price and key):
        return jsonify({"status": "error", "message": "Top-ups aren't open yet."}), 503
    try:
        import stripe

        stripe.api_key = key
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": price, "quantity": 1}],
            client_reference_id=str(current_user.id),
            metadata={"user_id": str(current_user.id), "kind": "ai_topup"},
            customer=getattr(current_user, "stripe_customer_id", None) or None,
            success_url=f"{_base_url()}/settings/ai?paid=1",
            cancel_url=f"{_base_url()}/settings/ai",
        )
    except Exception as exc:
        logger.exception("stripe top-up checkout failed: %s", exc)
        return jsonify({"status": "error", "message": "Checkout is unavailable right now."}), 502
    return jsonify({"status": "ok", "url": session.url})


def apply_billing_event(user: Any, event: Any) -> str:
    """Premium's part of a verified Stripe event. Returns what it did.

    Called by growth_glue's webhook handler after it has found the user.
    ``"topup"`` means the event was a top-up and must not touch the plan
    window; ``"plan"`` means ``plan_tier`` was set; ``""`` means nothing.
    """
    kind = event["type"]
    obj = event["data"]["object"]
    meta = obj.get("metadata") or {}
    if kind == "checkout.session.completed" and meta.get("kind") == "ai_topup":
        grant_credit(user.id, catalog.topup().credit_micro, reason="topup",
                     stripe_ref=f"cs:{obj.get('id')}")
        return "topup"
    plan_id = meta.get("plan")
    if kind == "invoice.paid":
        lines = ((obj.get("lines") or {}).get("data") or [{}])
        meta = (obj.get("subscription_details") or {}).get("metadata") or lines[0].get("metadata") or {}
        plan_id = meta.get("plan")
        if not plan_id:
            price = ((lines[0].get("price") or {}).get("id")) or \
                (((lines[0].get("pricing") or {}).get("price_details") or {}).get("price"))
            found = catalog.plan_for_price(price or "")
            plan_id = found[0] if found else None
    if plan_id in catalog.SELLABLE or plan_id == PRO:
        user.plan_tier = plan_id
        return "plan"
    return ""


# ── Install ─────────────────────────────────────────────────────────────


def install(app: Any) -> None:
    import ai_provider

    app.register_blueprint(premium_bp)
    ai_provider.set_premium_hooks(claude_gate=_claude_gate, spend_recorder=_spend_recorder,
                                  byok_steps=_byok_steps)
