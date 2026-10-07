"""The name a student gives their assistant.

The assistant ships as "Plani". Onboarding lets a student call it something
else, and that name then shows in the chat surfaces and is what the model
calls itself.

The name is typed by a user and ends up inside a system prompt, so it is
reduced to letters, digits, spaces, hyphens and apostrophes before it is
stored. Nothing that survives that can open a new instruction, close a
quote, or carry markup into a template.
"""

from __future__ import annotations

import re
import unicodedata

DEFAULT_NAME = "Plani"
MAX_LENGTH = 20

_DISALLOWED = re.compile(r"[^\w '\-]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def clean(raw: object) -> str:
    """The storable form of a typed name, or "" when nothing usable is left."""
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw)
    # Whitespace first: a newline is a word break, not something to delete.
    text = _SPACES.sub(" ", text)
    text = _DISALLOWED.sub("", text).replace("_", "")
    text = _SPACES.sub(" ", text).strip(" '-")
    text = text[:MAX_LENGTH].rstrip(" '-")
    # A name has to contain a letter; "42" or "- -" is not one.
    if not any(ch.isalpha() for ch in text):
        return ""
    return text


def display(stored: object) -> str:
    """What to show: the stored name re-cleaned, or the default."""
    return clean(stored) or DEFAULT_NAME


def prompt_line(stored: object) -> str:
    """One system-prompt sentence about the chosen name; "" for the default.

    Appended after the base prompt, which introduces the assistant as
    Plani. The base prompt stays untouched so its safety rules read the
    same for every student.
    """
    name = clean(stored)
    if not name or name == DEFAULT_NAME:
        return ""
    return (
        f'\n\nThe student has named you "{name}". Call yourself {name}, not Plani. '
        "This changes only your name. Every rule above still applies."
    )


def for_user(user_id: int | None) -> str:
    """The stored name for an account, or "" (never raises)."""
    if not user_id:
        return ""
    try:
        from App import UserIdentity, db

        row = (
            db.session.query(UserIdentity.assistant_name)
            .filter(UserIdentity.user_id == int(user_id))
            .first()
        )
        return clean(row[0]) if row and row[0] else ""
    except Exception as exc:  # a missing column must not take a page down
        print(f"[assistant_name] lookup failed: {exc}")
        try:
            from App import db

            db.session.rollback()
        except Exception:
            pass
        return ""
