"""Institution budgets, opt-in preview governance, and explicit retention tools."""

from datetime import datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from intelliplan.learning import policy


def reserve_tokens(user, tokens):
    """Reserve all applicable organization budgets in the caller's transaction.

    Atomic conditional increments prevent concurrent overspend. Reservations
    are conservative, include failures, and are not currency invoices.
    """
    from App import db

    M = policy.models()
    month = datetime.utcnow().strftime("%Y-%m")
    scopes = [
        (
            org.id,
            None,
            org.policy.get("monthly_generation_tokens", 100000),
        )
        for org in sorted(policy.organizations(user), key=lambda o: o.id)
    ]
    if not scopes:
        scopes = [(None, user.id, policy.effective(user)["monthly_generation_tokens"])]
    for oid, uid, limit in scopes:
        query = M["Budget"].query.filter_by(
            organization_id=oid, user_id=uid, month=month
        )
        if not query.first():
            try:
                with db.session.begin_nested():
                    db.session.add(
                        M["Budget"](organization_id=oid, user_id=uid, month=month)
                    )
                    db.session.flush()
            except IntegrityError:
                pass  # Another transaction created this month's counter.
        changed = query.filter(M["Budget"].reserved_tokens <= limit - tokens).update(
            {M["Budget"].reserved_tokens: M["Budget"].reserved_tokens + tokens},
            synchronize_session=False,
        )
        if not changed:
            raise ValueError("Monthly institution generation token budget exhausted.")


def anonymize_audit_targets(conn, user_id):
    """Anonymize membership/preview references inside the account-delete transaction."""
    table = policy.models()["Audit"].__table__
    rows = conn.execute(
        select(table.c.id, table.c.changes).where(
            table.c.changes["user_id"].as_integer() == user_id
        )
    ).all()
    for identity, changes in rows:
        value = dict(changes)
        value.pop("user_id", None)
        value["deleted_account"] = True
        conn.execute(update(table).where(table.c.id == identity).values(changes=value))


def retention_candidates(now=None, limit=500):
    """Only new learning evidence, completed sessions and feedback; never accounts.

    This deliberately preserves course content and unfinished sessions. Zero
    retention days means no automatic pruning. The shortest parent policy wins.
    """
    from App import User

    M = policy.models()
    now = now or datetime.utcnow()
    result = {name: [] for name in ("Evidence", "Sitting", "Feedback")}
    for name in result:
        model = M[name]
        query = model.query.order_by(model.id)
        if name == "Sitting":
            query = model.query.filter_by(state="completed").order_by(model.id)
        if name == "Evidence":
            open_sessions = (
                M["Sitting"]
                .query.with_entities(M["Sitting"].id)
                .filter(M["Sitting"].state.in_(["active", "paused"]))
            )
            query = query.filter(
                (model.session_id.is_(None)) | (~model.session_id.in_(open_sessions))
            )
            # Completion evidence is the session's retention clock. Only the
            # session deletion may remove it, even across limited batches.
            query = query.filter(
                (model.kind != "complete") | model.session_id.is_(None)
            )
        # Limit eligible records, not the first rows: accounts with retention
        # disabled must not permanently starve later eligible accounts.
        for row in query.yield_per(100):
            owner = User.query.get(row.user_id)
            if not owner:
                continue
            days = policy.effective(owner)["retention_days"]
            timestamp = row.created_at
            if name == "Sitting":
                completion = (
                    M["Evidence"]
                    .query.filter_by(session_id=row.id, kind="complete")
                    .order_by(M["Evidence"].created_at.desc())
                    .first()
                )
                if not completion:
                    continue  # Historical sessions have no reliable completion timestamp.
                timestamp = completion.created_at
            if days and timestamp < now - timedelta(days=days):
                result[name].append(row.id)
                if len(result[name]) >= limit:
                    break
    return result


def apply_retention(candidates):
    from App import db

    M = policy.models()
    # Children before parents. This command only runs on operator-approved
    # policies; no scheduled destructive command is installed automatically.
    for name in ("Evidence", "Feedback", "Sitting"):
        ids = candidates[name]
        if ids:
            if name == "Sitting":
                M["Evidence"].query.filter(M["Evidence"].session_id.in_(ids)).delete(
                    synchronize_session=False
                )
            M[name].query.filter(M[name].id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
