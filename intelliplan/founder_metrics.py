"""Small, privacy-conscious aggregations for the founder dashboard.

Inputs are already consented first-party route events. This module returns
counts only; it never returns identifiers, page content, or raw event rows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable


FEATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Today and command center", ("/dashboard", "/command-center")),
    ("Calendar and planning", ("/scheduler",)),
    ("Active study", ("/active",)),
    ("Tutor and Plani", ("/tutor",)),
    ("Study and flashcards", ("/study-and-learn", "/study-tools", "/flashcards")),
    ("Courses and classes", ("/courses", "/my-courses", "/classes")),
    ("Grades", ("/gradebook", "/grades")),
)

MINIMUM_REPORTABLE_ACCOUNTS = 5
MINIMUM_ACTIVE_ACCOUNTS_FOR_ACTIONS = 20


def feature_adoption(
    rows: Iterable[tuple[str, str]],
    *, minimum_accounts: int = MINIMUM_REPORTABLE_ACCOUNTS,
    total_accounts: int | None = None,
) -> list[dict]:
    """Aggregate ``(route_rule, actor)`` rows into feature adoption counts.

    Either tail below the disclosure floor is grouped as ``suppressed``.
    The public status does not reveal whether usage or non-usage is the small
    group. Low counts also are not enough evidence to label a feature unused.
    """
    actors_by_rule: dict[str, set[str]] = defaultdict(set)
    for rule, actor in rows:
        if isinstance(rule, str) and isinstance(actor, str) and actor:
            actors_by_rule[rule].add(actor)

    output = []
    for name, rules in FEATURES:
        accounts: set[str] = set()
        for rule in rules:
            accounts.update(actors_by_rule.get(rule, ()))
        count = len(accounts)
        if (count < minimum_accounts or
                total_accounts is not None and total_accounts - count < minimum_accounts):
            # Keep one public status for either small tail. Distinguishing low
            # use from near-universal use would disclose the complementary
            # suppressed cell through the founder API.
            accounts, status = None, "suppressed"
        else:
            accounts, status = count, "measured"
        output.append({"feature": name, "accounts": accounts, "status": status})
    return output


def feature_optimization_actions(
    features: Iterable[dict],
    *, active_accounts: int,
    minimum_active_accounts: int = MINIMUM_ACTIVE_ACCOUNTS_FOR_ACTIONS,
    low_adoption_share: float = 0.2,
) -> dict:
    """Suggest aggregate feature discovery experiments without exposing users.

    Recommendations are withheld until the monthly active denominator is large
    enough to make a feature comparison useful. Feature counts remain subject
    to the same small-cell floor as the adoption table.
    """
    if active_accounts < minimum_active_accounts:
        return {"status": "insufficient_data", "items": []}

    items = []
    for feature in features:
        if feature.get("status") != "measured":
            continue
        count = feature.get("accounts")
        if isinstance(count, int) and count / active_accounts < low_adoption_share:
            items.append({
                "feature": feature["feature"],
                "signal": "low_share_of_active_accounts",
                "action": "Test a clearer entry point or onboarding example, then compare adoption next month.",
            })
    return {
        "status": "measured",
        "minimum_active_accounts": minimum_active_accounts,
        "items": items,
    }
