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


def feature_adoption(rows: Iterable[tuple[str, str]], *, minimum_accounts: int = 5) -> list[dict]:
    """Aggregate ``(route_rule, actor)`` rows into feature adoption counts.

    Values below the disclosure floor are deliberately grouped as
    ``suppressed``. An absent/low count is not enough evidence to label a
    feature unused, especially for a product with school-age users.
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
        output.append({
            "feature": name,
            "accounts": count if count >= minimum_accounts else None,
            "status": "measured" if count >= minimum_accounts else "below_threshold",
        })
    return output
