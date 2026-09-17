"""The questions we ask students, and when we are allowed to ask.

Asking is the only way to learn *why*: the event log can show that someone
left the connect screen, never that they left because their school uses a
portal we do not support. But a product that interrupts studying to run a
survey has made itself the thing in the way, so the scheduling rules here
are deliberately strict:

* one prompt at a time, and never two in the same day;
* nothing until the account is old enough for the question to be fair;
* a dismissal is an answer -- that question does not come back;
* under-13 and parent-pending accounts are never prompted.

Pure: the caller passes the state, this returns which question is due.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

__all__ = ["Prompt", "PROMPTS", "prompt_for", "prompt_by_key", "is_valid_answer"]


@dataclass(frozen=True, slots=True)
class Prompt:
    key: str
    question: str
    #: (value, label). Empty for a free-text question.
    options: tuple[tuple[str, str], ...] = ()
    #: Invite a sentence after the choice. Always optional for the student.
    detail_label: str = ""
    #: Days since signup before this is fair to ask.
    min_age_days: int = 0
    #: Requires the student to have built a plan.
    needs_plan: bool = False


PROMPTS: tuple[Prompt, ...] = (
    # First, while they can still remember. Its answer is the only honest
    # source for "which channel is working" -- referrers are missing for
    # every link opened from a messaging app, which is most of them.
    Prompt(
        key="heard_from",
        question="How did you hear about IntelliPlan?",
        options=(
            ("friend", "A friend or classmate"),
            ("teacher", "A teacher or school"),
            ("tiktok", "TikTok"),
            ("instagram", "Instagram"),
            ("youtube", "YouTube"),
            ("reddit", "Reddit"),
            ("search", "Google or another search"),
            ("ai", "ChatGPT or another AI"),
            ("other", "Somewhere else"),
        ),
        detail_label="Where exactly? (optional)",
    ),
    # What they came for, once they have seen enough to answer.
    Prompt(
        key="main_need",
        question="What do you most want IntelliPlan to do for you?",
        options=(
            ("track", "Keep track of what's due"),
            ("plan", "Plan when to actually do it"),
            ("understand", "Help me understand the material"),
            ("grades", "Keep my grades where I want them"),
            ("focus", "Help me focus and stop procrastinating"),
            ("other", "Something else"),
        ),
        detail_label="Anything else? (optional)",
        min_age_days=2,
    ),
    # The share prompt, at the moment it is true: they have a plan.
    Prompt(
        key="invite",
        question="Know someone drowning in assignments?",
        options=(("copied", "Copy my invite link"), ("later", "Not right now")),
        min_age_days=1,
        needs_plan=True,
    ),
    # Last, and open-ended, because by now they know what is missing.
    Prompt(
        key="missing",
        question="What's the one thing IntelliPlan still doesn't do for you?",
        detail_label="Tell us in a sentence",
        min_age_days=10,
    ),
)

_BY_KEY = {p.key: p for p in PROMPTS}


def prompt_by_key(key: Any) -> Prompt | None:
    return _BY_KEY.get(key) if isinstance(key, str) else None


def prompt_for(
    *,
    account_age_days: float,
    has_plan: bool,
    settled: Sequence[str] | set[str],
    hours_since_last_prompt: float | None,
    is_child: bool = False,
) -> Prompt | None:
    """The next question due, or None -- which is the common answer.

    ``settled`` is every key already answered or dismissed.
    ``hours_since_last_prompt`` is None when none has ever been shown.
    """
    if is_child:
        return None
    if hours_since_last_prompt is not None and hours_since_last_prompt < 24:
        return None
    done = set(settled)
    for prompt in PROMPTS:
        if prompt.key in done:
            continue
        if account_age_days < prompt.min_age_days:
            continue
        if prompt.needs_plan and not has_plan:
            continue
        return prompt
    return None


def is_valid_answer(prompt: Prompt, answer: Any) -> bool:
    """A choice from this prompt's own list, or any answer to a free-text one."""
    if not prompt.options:
        return answer is None or isinstance(answer, str)
    return isinstance(answer, str) and answer in {value for value, _ in prompt.options}
