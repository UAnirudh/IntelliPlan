"""Lifecycle email: welcome, onboarding, unfinished-work nudges, newsletter.

Six modules, one job each:

* ``eligibility`` — may we mail this person at all? Every send goes through it.
* ``templates``   — render an HTML + plain-text pair from a context dict.
* ``sender``      — ``send_lifecycle_email``, the single public entry point,
                    which owns the deduplication ledger.
* ``campaigns``   — welcome, the feedback ask, and the newsletter.
* ``onboarding``  — the day 2/4/7 setup sequence, where each step is dropped
                    rather than sent once the student has done the thing.
* ``drafts``      — one weekly nudge listing what the student left
                    unfinished, and nothing at all when that list is empty.

Nothing here imports ``App`` at module scope. ``App`` imports this package
during boot, and a top-level import back into it would be circular; the
functions take what they need as arguments or import lazily inside the call.

The package is named ``email``, which shadows the standard library's
``email`` only for code *inside* this package that writes ``import email``.
Python 3's absolute imports mean ``from email.message import EmailMessage``
in ``App.py`` still resolves to the stdlib, which is what the SMTP fallback
depends on.
"""
