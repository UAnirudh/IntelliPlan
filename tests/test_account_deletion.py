"""Account deletion has to actually delete the account.

Google Play requires a working in-app deletion path, and GDPR/CCPA require
the data to genuinely go. Both are broken by the same quiet failure: a table
carrying a ``users.id`` foreign key that nobody added to the deletion plan.

On SQLite that only leaks orphan rows, so it looks fine locally. On the
Postgres production runs, the unlisted reference makes the final
``DELETE FROM users`` raise and the endpoint returns a 500 — which is how
this shipped with roughly half the tables missing, plus ``day_archive`` for
a table actually named ``day_archives``.

The first test below is the one that matters: it walks the live model
registry rather than a hand-maintained list, so a model added next year
fails here instead of in production.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime

import pytest

import App as app_module


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        yield


def _deletion_plan_source() -> str:
    """The literal body of ``_account_delete_impl``.

    Read as text on purpose. The plan is a local variable built at call time,
    and importing it would mean running a deletion; the point here is to
    check the source names every table, which the text answers directly.
    """
    import inspect

    return inspect.getsource(app_module._account_delete_impl)


def _tables_with_user_fk() -> dict[str, str]:
    """Every mapped table holding a foreign key into ``users.id``.

    Straight off the SQLAlchemy metadata, so it sees models registered from
    ``intelliplan/models/`` as well as the ones declared in App.py.
    """
    found: dict[str, str] = {}
    for table in app_module.db.metadata.tables.values():
        for column in table.columns:
            for fk in column.foreign_keys:
                if fk.target_fullname == "users.id":
                    found.setdefault(table.name, column.name)
    return found


def test_every_table_referencing_users_is_in_the_deletion_plan(ctx):
    """The regression guard. Add a model with a user_id FK and this fails
    until the deletion plan learns about it."""
    source = _deletion_plan_source()
    missing = []
    for table, column in sorted(_tables_with_user_fk().items()):
        if table == "users":
            # Self-reference (referred_by_id), handled by nulling out rather
            # than deleting so a referrer's departure cannot cascade into
            # someone else's account.
            assert "referred_by_id = NULL" in source
            continue
        # The table has to be named, and the column it is filtered on too —
        # `student_links` carries two, and deleting on only one of them
        # leaves a row that still blocks the user delete.
        if not re.search(rf"\b{re.escape(table)}\b", source):
            missing.append(f"{table} (no mention)")
        elif not re.search(rf"\b{re.escape(column)}\b", source):
            missing.append(f"{table}.{column} (table named, column not filtered)")
    assert not missing, "tables referencing users.id but absent from the deletion plan: " + ", ".join(missing)


def test_the_plan_only_names_tables_that_exist(ctx):
    """A typo like `day_archive` for `day_archives` is swallowed by the
    per-statement except and silently deletes nothing."""
    source = _deletion_plan_source()
    real = set(app_module.db.metadata.tables)
    named = set(re.findall(r"DELETE FROM (\w+)", source)) | set(
        re.findall(r"UPDATE (\w+) SET", source)
    )
    unknown = sorted(t for t in named if t not in real)
    assert not unknown, f"deletion plan names tables that do not exist: {unknown}"


def test_the_plan_deletes_children_before_their_parents(ctx):
    """Order is load-bearing under enforced foreign keys."""
    source = _deletion_plan_source()

    def position(needle: str) -> int:
        index = source.find(needle)
        assert index != -1, needle
        return index

    # Focus samples reference active_sessions.
    assert position("active_focus_samples") < position("DELETE FROM active_sessions")
    # Votes reference feature_requests.
    assert position("DELETE FROM feature_request_votes") < position(
        "DELETE FROM feature_requests"
    )
    # The user row goes last of all.
    assert position("DELETE FROM users WHERE id") > position("DELETE FROM manual_tasks")


def test_the_lifecycle_email_ledger_is_cleared_but_suppression_is_not(ctx):
    """Unsubscribing is keyed by address precisely so it outlives the
    account. Clearing it on delete would let a re-registered address be
    mailed again."""
    source = _deletion_plan_source()
    assert "DELETE FROM email_sends" in source
    assert "email_suppression" not in source


def test_a_study_group_survives_its_owner_leaving(ctx):
    """Other students' shared workspace is not this user's to delete."""
    source = _deletion_plan_source()
    assert "UPDATE study_groups SET owner_id = NULL" in source
    assert "DELETE FROM study_groups" not in source


def test_deleting_an_account_removes_the_user_and_their_rows(ctx):
    """End to end against the real database."""
    user = app_module.User(
        email=f"del-{uuid.uuid4().hex[:10]}@example.test",
        name="Sam",
        birth_year=2000,
        password_hash="x",
        created_at=datetime.utcnow(),
    )
    app_module.db.session.add(user)
    app_module.db.session.commit()
    user_id = user.id

    app_module.db.session.add_all(
        [
            app_module.ManualTask(user_id=user_id, title="A task"),
            app_module.SavedSchedule(user_id=user_id, name="Week", schedule_data="{}"),
            app_module.DayArchive(
                user_id=user_id,
                archive_date=date(2026, 8, 1),
                item_type="schedule",
                payload="{}",
            ),
            app_module.EmailSend(user_id=user_id, email_key="welcome", status="sent"),
            app_module.ActiveSession(user_id=user_id, title="Session", state="paused"),
        ]
    )
    app_module.db.session.commit()

    with app_module.app.test_request_context():
        from flask_login import login_user

        login_user(user)
        response = app_module._account_delete_impl()

    payload = response[0] if isinstance(response, tuple) else response
    assert payload.get_json()["status"] == "ok", payload.get_json()

    app_module.db.session.expire_all()
    assert app_module.User.query.filter_by(id=user_id).first() is None
    for model in (
        app_module.ManualTask,
        app_module.SavedSchedule,
        app_module.DayArchive,
        app_module.EmailSend,
        app_module.ActiveSession,
    ):
        assert model.query.filter_by(user_id=user_id).count() == 0, model.__name__
