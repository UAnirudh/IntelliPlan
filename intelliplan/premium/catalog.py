"""The paid plans, what each costs, and how much AI each includes.

Pure data and rules. Every number a page shows or a gate enforces comes from
here, and each can be changed by an environment variable without a deploy,
because the right price is something to learn from real students, not
something to guess once in code.

Plans
-----
``pro``           The existing plan. Unchanged by this module.
``premium``       Claude as the tutor, with a monthly AI budget in dollars.
``premium_byok``  Premium for a student who links their own AI account.
                  Answers run on their key, so the plan costs less and
                  includes only a small budget for when their key fails.

A plan's ``ai_budget_usd`` is what a student may spend on platform-paid
model calls in one calendar month (UTC). It is the student's allowance,
not our cost ceiling: margin, card fees and hosting come out of the price.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from intelliplan.premium.pricing import micro_from_usd

PRO = "pro"
PREMIUM = "premium"
PREMIUM_BYOK = "premium_byok"

MONTHLY = "month"
YEARLY = "year"

#: Plan ids a checkout may sell. ``pro`` keeps its own checkout in growth_glue.
SELLABLE = (PREMIUM, PREMIUM_BYOK)


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return max(0.0, value)


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_usd: dict[str, float]
    ai_budget_usd: float
    byok: bool
    stripe_price_env: dict[str, str]
    features: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ai_budget_micro(self) -> int:
        return micro_from_usd(self.ai_budget_usd)

    def stripe_price_id(self, interval: str) -> str:
        return os.getenv(self.stripe_price_env.get(interval, ""), "").strip()


def premium() -> Plan:
    return Plan(
        id=PREMIUM,
        name="Premium",
        price_usd={
            MONTHLY: _env_float("PREMIUM_PRICE_USD", 20.0),
            YEARLY: _env_float("PREMIUM_PRICE_USD_YEARLY", 192.0),
        },
        ai_budget_usd=_env_float("PREMIUM_AI_BUDGET_USD", 20.0),
        byok=False,
        stripe_price_env={MONTHLY: "STRIPE_PRICE_ID_PREMIUM",
                          YEARLY: "STRIPE_PRICE_ID_PREMIUM_YEARLY"},
        features=(
            "Claude as your tutor, with the right model picked for each question",
            "Clarifying questions before long answers, so you get the one you need",
            "A monthly AI budget with a live meter, and top-ups if you run out",
            "Choose Fast, Balanced or Deep answers, or let IntelliPlan decide",
            "Link your own AI account to stretch your budget",
            "Everything in the free plan",
        ),
    )


def premium_byok() -> Plan:
    return Plan(
        id=PREMIUM_BYOK,
        name="Premium (your own AI account)",
        price_usd={
            MONTHLY: _env_float("PREMIUM_BYOK_PRICE_USD", 8.0),
            YEARLY: _env_float("PREMIUM_BYOK_PRICE_USD_YEARLY", 76.0),
        },
        ai_budget_usd=_env_float("PREMIUM_BYOK_AI_BUDGET_USD", 1.0),
        byok=True,
        stripe_price_env={MONTHLY: "STRIPE_PRICE_ID_PREMIUM_BYOK",
                          YEARLY: "STRIPE_PRICE_ID_PREMIUM_BYOK_YEARLY"},
        features=(
            "Everything in Premium",
            "Answers run on the AI account you link, so the plan costs less",
            "A small included budget for when your account is unavailable",
        ),
    )


def plans() -> dict[str, Plan]:
    return {p.id: p for p in (premium(), premium_byok())}


def get(plan_id: str | None) -> Plan | None:
    return plans().get(str(plan_id or ""))


def is_premium(plan_id: str | None) -> bool:
    return str(plan_id or "") in (PREMIUM, PREMIUM_BYOK)


def plan_for_price(price_id: str) -> tuple[str, str] | None:
    """Which (plan, interval) a Stripe Price belongs to, for the webhook."""
    if not price_id:
        return None
    for plan in plans().values():
        for interval in (MONTHLY, YEARLY):
            if plan.stripe_price_id(interval) == price_id:
                return plan.id, interval
    return None


# ── Top-ups ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TopUp:
    price_usd: float
    credit_usd: float

    @property
    def credit_micro(self) -> int:
        return micro_from_usd(self.credit_usd)

    @staticmethod
    def stripe_price_id() -> str:
        return os.getenv("STRIPE_PRICE_ID_TOPUP", "").strip()


def topup() -> TopUp:
    """A one-time purchase that adds AI budget to the current month.

    Credit is less than the price on purpose: the card fee is charged on
    the price, and a top-up that credits every cent loses money.
    """
    return TopUp(
        price_usd=_env_float("PREMIUM_TOPUP_PRICE_USD", 5.0),
        credit_usd=_env_float("PREMIUM_TOPUP_CREDIT_USD", 4.0),
    )


#: Share of the monthly budget at which the student is warned.
LOW_BUDGET_SHARE = 0.2
