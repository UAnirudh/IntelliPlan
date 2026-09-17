"""Free vs paid, the AI allowance, and referral rewards.

Pure rules only. The ORM rows are passed in as plain values so the pricing
decisions can be read and tested in one place, away from Flask.

Why the allowance meters *requests*, not model calls
----------------------------------------------------
One student action can walk several models (see ``ai_provider.chat``) or
make several chat calls to build one answer. Charging the allowance per
call would make the same button cost one generation on a good day and four
on a day Gemini is rate-limited — the student would be paying for our
outage. The glue counts at most one generation per HTTP request.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

__all__ = [
    "PAID",
    "FREE",
    "free_monthly_allowance",
    "plan_for",
    "period_key",
    "allowance_state",
    "extend_paid_until",
    "REFERRAL_REWARD_DAYS",
    "REFERRAL_MAX_REWARDS",
]

PAID = "paid"
FREE = "free"

#: Free-plan AI generations per calendar month (UTC). Generous enough that a
#: student can find out the product works before meeting the wall; the wall
#: is meant to appear to someone who is already getting value.
DEFAULT_FREE_MONTHLY = 60

#: What one successful referral is worth, to each side.
REFERRAL_REWARD_DAYS = 30

#: Per inviter. Past this, referrals still record but stop paying — a cap is
#: what stops a folder of throwaway accounts becoming free-forever.
REFERRAL_MAX_REWARDS = 12


def free_monthly_allowance() -> int:
    try:
        value = int(os.getenv("AI_FREE_MONTHLY_GENERATIONS", DEFAULT_FREE_MONTHLY))
    except (TypeError, ValueError):
        return DEFAULT_FREE_MONTHLY
    return max(0, value)


def plan_for(paid_until: datetime | None, now: datetime) -> str:
    """``paid`` while a paid window is open, else ``free``."""
    return PAID if paid_until is not None and paid_until > now else FREE


def period_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def allowance_state(used: int, plan: str, allowance: int | None = None) -> dict:
    """What the student has left this month.

    ``limit`` is ``None`` on the paid plan: unlimited is a different answer
    from a very large number, and the UI should say "unlimited".
    """
    if plan == PAID:
        return {"plan": PAID, "used": used, "limit": None, "remaining": None, "exhausted": False}
    limit = free_monthly_allowance() if allowance is None else allowance
    remaining = max(0, limit - used)
    return {
        "plan": FREE,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "exhausted": used >= limit,
    }


def extend_paid_until(paid_until: datetime | None, now: datetime, days: int) -> datetime:
    """Add ``days`` to the end of any window still open, or start one now.

    Stacking from the current end, not from now: a student with two weeks
    left who earns a referral month should end up with six weeks, not four.
    """
    base = paid_until if paid_until is not None and paid_until > now else now
    return base + timedelta(days=max(0, days))
