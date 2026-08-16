"""Render an email into its HTML and plain-text halves.

The HTML lives in ``Main_Project/templates/emails/`` and goes through Flask's
own Jinja environment, so it picks up the app's filters and autoescaping for
free. The plain-text half lives beside this module in ``text/`` and is
hand-written — an auto-stripped conversion of a table-based HTML email reads
like a ransom note, and the text part is what a screen reader and a text-only
client actually get.

Autoescaping is on for the HTML and deliberately off for the text: escaping
``&`` into ``&amp;`` inside a plain-text body is a visible bug.
"""

from __future__ import annotations

import pathlib
from typing import Any, NamedTuple

#: Where the hand-written plain-text bodies live.
_TEXT_DIR = pathlib.Path(__file__).parent / "text"

#: The fallback when a user has no name. Many accounts arrive through Google
#: OAuth with nothing in the name field, so this is the common case, not the
#: edge case.
DEFAULT_NAME_FALLBACK = None  # templates spell the fallback themselves


class RenderedEmail(NamedTuple):
    """The two halves of a message, plus the subject line."""

    subject: str
    text: str
    html: str


def _app_url() -> str:
    from App import APP_BASE_URL

    return (APP_BASE_URL or "https://intelliplan.tech").rstrip("/")


def postal_address() -> str:
    """The physical mailing address CAN-SPAM requires in commercial email.

    Returns an empty string when unset. Callers that send marketing must
    treat that as a refusal to send rather than rendering a blank footer —
    see ``sender.send_lifecycle_email``.
    """
    import os

    return (os.getenv("MARKETING_POSTAL_ADDRESS") or "").strip()


def reply_to() -> str:
    """Where a reply to a lifecycle email should land.

    Every one of the three templates invites a reply, but the From address
    is a no-reply sender, so without this the answer goes into a mailbox
    nobody reads. Overridable because the address that should receive
    student replies is not necessarily the one that sends.
    """
    import os

    return (os.getenv("MARKETING_REPLY_TO") or "founder@intelliplan.tech").strip()


def build_context(
    user: Any = None,
    unsubscribe_url: str = "",
    preheader: str = "",
    **extra: Any,
) -> dict:
    """Assemble the variables every template expects.

    ``user_name`` is left as ``None`` when the user has no name rather than
    being defaulted here — the templates spell their own fallback, because
    "Hey there," and "Your AI study planner" need different grammar from
    "Hey Sam," and "Sam, your AI study planner".
    """
    app_url = _app_url()
    name = (getattr(user, "name", None) or "").strip() if user is not None else ""
    context = {
        "user_name": name or None,
        "app_url": app_url,
        "app_domain": app_url.split("//", 1)[-1].rstrip("/"),
        "unsubscribe_url": unsubscribe_url,
        "postal_address": postal_address(),
        "preheader": preheader,
    }
    context.update(extra)
    return context


def render(template_name: str, subject: str, context: dict) -> RenderedEmail:
    """Render ``<template_name>.html`` and ``text/<template_name>.txt``.

    Raises whatever Jinja raises on a broken template. A lifecycle email that
    cannot render is a bug to fix, not a send to skip quietly — the caller
    catches it per-recipient so one bad row cannot abort a whole sweep.
    """
    from flask import render_template
    from jinja2 import Template

    html = render_template(f"emails/{template_name}.html", **context)

    source = (_TEXT_DIR / f"{template_name}.txt").read_text(encoding="utf-8")
    # A bare Jinja Template, not the app environment: autoescape must stay off
    # here or every ampersand in the text body becomes "&amp;".
    text = Template(source, autoescape=False).render(**context)
    # Hand-written bodies plus {% for %} blocks leave ragged blank runs. Collapse
    # three-or-more newlines to two so the text part looks written, not generated.
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    return RenderedEmail(subject=subject, text=text.strip() + "\n", html=html)
