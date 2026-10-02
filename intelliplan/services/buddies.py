"""Study Buddies: who may use it, and the friend-streak arithmetic.

Pure functions only; ``buddies_glue.py`` owns the tables and routes.

What a buddy can see
--------------------
Whether you studied today, your focus minutes today, and the streak the two
of you share. Nothing else -- never a grade, never an assignment or course
name, never an email address. :func:`public_card` is the single place that
shape is built, so a field cannot leak into the response by being added to
a model.

Who may use it
--------------
Showing a child's daily activity to another account is exactly the kind of
disclosure COPPA expects a parent to approve. IntelliPlan has no
parent-approval flow for social features, so the rule is the conservative
one the email gate already uses (``intelliplan.email.eligibility``): an
unknown age counts as a child, the age is the youngest a birth year allows,
and an account still waiting on its parent's consent link is not eligible
for anything social. Off by default for everyone else too; each student
turns it on, and each pairing needs both of them to say yes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from intelliplan.email.eligibility import COPPA_AGE, age_from_birth_year

#: Active buddies per student. Small on purpose: this is a few friends who
#: keep each other honest, not a follower count.
MAX_BUDDIES = 5
#: Pending requests a student can have waiting on them at once. Stops a
#: leaked invite link turning into an inbox nobody can clear.
MAX_PENDING_INCOMING = 10
#: One nudge per buddy per day, and a ceiling across all buddies.
NUDGES_PER_BUDDY_PER_DAY = 1
NUDGES_PER_DAY = 3
#: Focus minutes that make a day count as "studied" by themselves.
MIN_FOCUS_MINUTES = 1


# ── Eligibility ─────────────────────────────────────────────────────────


def eligibility(user: Any, now: datetime | None = None) -> tuple[bool, str]:
    """``(eligible, reason)``. Reason is a stable slug the UI maps to words."""
    if user is None:
        return False, "no_user"
    role = (getattr(user, "role", None) or "student").strip().lower()
    if role != "student":
        return False, "students_only"
    if getattr(user, "parent_email", None) and not getattr(user, "parent_consent_granted", False):
        return False, "awaiting_parent_consent"
    birth_year = getattr(user, "birth_year", None)
    if birth_year is None:
        return False, "unknown_age"
    try:
        age = age_from_birth_year(int(birth_year), now)
    except (TypeError, ValueError):
        return False, "unknown_age"
    if age is None or age < COPPA_AGE:
        return False, "under_13"
    return True, "ok"


def display_name(user: Any) -> str:
    """First name only. Never the email -- that is how you find someone."""
    name = (getattr(user, "name", None) or "").strip()
    if name:
        return name.split()[0][:24]
    return "Study buddy"


# ── Friend streak ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class PairStreak:
    #: Stored run length, ending on ``last_joint``.
    count: int
    longest: int
    #: Last day both studied, or None.
    last_joint: date | None
    #: Both studied ``today``.
    both_today: bool
    #: The run is still unbroken: it ended today or yesterday.
    alive: bool

    @property
    def shown(self) -> int:
        """What the card displays. A broken run reads as zero."""
        return self.count if self.alive else 0

    @property
    def at_risk(self) -> bool:
        """Alive, but it ends tonight unless both of you study."""
        return self.alive and not self.both_today and self.count > 0


def advance_pair_streak(
    *,
    count: int,
    longest: int,
    last_joint: date | None,
    days_a: Iterable[date],
    days_b: Iterable[date],
    today: date,
) -> PairStreak:
    """Fold any new joint study days into a pair's stored streak.

    A joint day is a calendar date both students studied on, each by their
    own local calendar. Days are folded in order; a gap restarts the run at
    one. Days after ``today`` are ignored (a student whose local date is
    ahead of ``today`` counts once ``today`` reaches it), and days on or
    before ``last_joint`` were already counted, so calling this twice with
    the same inputs changes nothing.

    ``today`` is the pair's day: the earlier of the two students' local
    dates, i.e. the day both of them have reached. The run stays alive
    through that whole day if it ended yesterday -- tonight is when it can
    still be saved, and the at-risk nudge depends on that.
    """
    count = max(0, int(count or 0))
    longest = max(int(longest or 0), count)
    joint = sorted({d for d in days_a} & {d for d in days_b})
    for d in joint:
        if d > today or (last_joint is not None and d <= last_joint):
            continue
        if last_joint is not None and d == last_joint + timedelta(days=1) and count > 0:
            count += 1
        else:
            count = 1
        last_joint = d
        longest = max(longest, count)
    alive = last_joint is not None and last_joint >= today - timedelta(days=1)
    return PairStreak(
        count=count,
        longest=longest,
        last_joint=last_joint,
        both_today=last_joint == today,
        alive=alive,
    )


# ── What a buddy sees ───────────────────────────────────────────────────


def public_card(
    *,
    link_id: int,
    name: str,
    sharing: bool,
    studied_today: bool,
    focus_minutes_today: int,
    streak: PairStreak | None,
    can_nudge: bool,
    nudged_you_today: bool = False,
) -> dict[str, Any]:
    """The whole of what one student learns about a buddy.

    ``sharing`` is False when the buddy has turned the feature off or is no
    longer eligible: the link survives, but none of their activity does.
    """
    card: dict[str, Any] = {
        "id": int(link_id),
        "name": name,
        "sharing": bool(sharing),
        "nudged_you_today": bool(nudged_you_today and sharing),
    }
    if not sharing:
        card.update({"studied_today": None, "focus_minutes_today": None,
                     "streak": 0, "streak_both_today": False,
                     "streak_at_risk": False, "can_nudge": False})
        return card
    card.update({
        "studied_today": bool(studied_today),
        "focus_minutes_today": max(0, int(focus_minutes_today or 0)),
        "streak": streak.shown if streak else 0,
        "streak_longest": streak.longest if streak else 0,
        "streak_both_today": bool(streak and streak.both_today),
        "streak_at_risk": bool(streak and streak.at_risk),
        "can_nudge": bool(can_nudge),
    })
    return card
