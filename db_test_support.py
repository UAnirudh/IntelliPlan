"""Shared cleanup helpers for tests that delete accounts.

The suite runs against one in-memory SQLite database, and SQLite hands a
deleted account's id to the next user inserted. A cleanup that removes users
but leaves their ``user_identities`` rows behind makes the next account on
that id fail with ``UNIQUE constraint failed: user_identities.user_id``, and
which test fails then depends on module order. Cleanups therefore delete the
identity rows of the users they remove, through this helper.
"""

import App


def delete_identities_for(user_query):
    """Delete the identity rows of every user ``user_query`` matches."""
    ids = [user.id for user in user_query.all()]
    if ids:
        App.UserIdentity.query.filter(
            App.UserIdentity.user_id.in_(ids)
        ).delete(synchronize_session=False)
