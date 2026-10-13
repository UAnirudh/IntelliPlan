"""Institution budgets, opt-in preview governance, and explicit retention tools."""

from datetime import datetime, timedelta
from sqlalchemy import Text, cast, or_, select, update
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


def anonymize_audit_value(value, user_id):
    """Remove explicit user references, preserving other users and policy values.

    Unknown keys and free text cannot be safely attributed to an account.
    This is structured-reference removal, not arbitrary text anonymization.
    """
    def matches(identity):
        return (type(identity) is int and identity == user_id or
                isinstance(identity, str) and identity == str(user_id))
    if isinstance(value, list):
        return [anonymize_audit_value(item, user_id) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned, removed = {}, False
    for key, item in value.items():
        if key in {'user_id', 'target_user_id', 'actor_user_id'} and matches(item):
            removed = True
            continue
        if key in {'user_ids', 'target_user_ids'} and isinstance(item, list):
            kept = [identity for identity in item if not matches(identity)]
            removed |= len(kept) != len(item)
            item = kept
        cleaned[key] = anonymize_audit_value(item, user_id)
    if removed:
        cleaned['deleted_account'] = True
    return cleaned


def anonymize_audit_targets(conn, user_id):
    """Scrub structured references in the account-delete transaction, in pages."""
    table = policy.models()["Audit"].__table__
    cursor = 0
    while True:
        rows = conn.execute(select(table.c.id, table.c.changes, cast(table.c.changes, Text)).where(
            table.c.id > cursor).order_by(table.c.id).limit(200)).all()
        if not rows:
            break
        for identity, changes, raw in rows:
            for attempt in range(4):
                cleaned = anonymize_audit_value(changes, user_id)
                if cleaned == changes:
                    break
                result = conn.execute(update(table).where(
                    table.c.id == identity, cast(table.c.changes, Text) == raw).values(changes=cleaned))
                if result.rowcount == 1:
                    break
                latest = conn.execute(select(table.c.changes, cast(table.c.changes, Text)).where(
                    table.c.id == identity)).first()
                if latest is None:
                    break
                changes, raw = latest
            else:
                raise RuntimeError('Audit references changed during deletion. Retry the account operation.')
        cursor = rows[-1][0]


def retention_candidates(now=None, limit=500):
    """Only new learning evidence, completed sessions and feedback; never accounts.

    This deliberately preserves course content and unfinished sessions. Zero
    retention days means no automatic pruning. The shortest parent policy wins.
    """
    from App import User

    M = policy.models()
    policy.invalidate_cache()
    now = now or datetime.utcnow()
    result = {name: [] for name in ("Evidence", "Sitting", "Feedback")}
    for name in result:
        model = M[name]
        query = model.query.populate_existing().order_by(model.id)
        if name == "Sitting":
            query = query.filter_by(state="completed")
            # A malformed cross-owner child must never be removed by pruning
            # another account's parent session.
            query = query.filter(~select(M['Evidence'].id).where(
                M['Evidence'].session_id == model.id,
                M['Evidence'].user_id != model.user_id).exists())
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
                    .query.populate_existing().filter_by(session_id=row.id, kind="complete")
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


def lock_retention_state(candidates):
    """Hold PostgreSQL owner/scope/content locks until the apply transaction ends.

    SQLite ignores FOR UPDATE; local fixture checks do not prove concurrency.
    User locks also prevent new membership inserts through their foreign key.
    """
    from App import User
    M = policy.models()
    owners, session_ids = set(), set(candidates['Sitting'])
    for name, identities in candidates.items():
        rows = M[name].query.populate_existing().filter(M[name].id.in_(identities)).all()
        owners.update(row.user_id for row in rows)
        if name == 'Evidence':
            session_ids.update(row.session_id for row in rows if row.session_id)
    users = User.query.filter(User.id.in_(owners)).order_by(User.id).with_for_update().all()
    M['Membership'].query.populate_existing().filter(M['Membership'].user_id.in_(owners)).order_by(
        M['Membership'].id).with_for_update().all()
    scopes = {org.id for user in users for org in policy.organizations(user)}
    M['Organization'].query.populate_existing().filter(M['Organization'].id.in_(scopes)).order_by(
        M['Organization'].id).with_for_update().all()
    if any(org.id not in scopes for user in users for org in policy.organizations(user)):
        raise RuntimeError('Institution scope changed. Rerun the retention preview.')
    M['Sitting'].query.populate_existing().filter(M['Sitting'].id.in_(session_ids)).order_by(
        M['Sitting'].id).with_for_update().all()
    M['Evidence'].query.populate_existing().filter(or_(M['Evidence'].id.in_(candidates['Evidence']),
        M['Evidence'].session_id.in_(session_ids))).order_by(M['Evidence'].id).with_for_update().all()
    M['Feedback'].query.populate_existing().filter(M['Feedback'].id.in_(candidates['Feedback'])).order_by(
        M['Feedback'].id).with_for_update().all()
    for name, identities in candidates.items():
        if any(row.user_id not in owners for row in M[name].query.populate_existing().filter(
                M[name].id.in_(identities))):
            raise RuntimeError('Record ownership changed. Rerun the retention preview.')


def apply_retention(candidates):
    from App import db

    M = policy.models()
    names = {'Evidence', 'Feedback', 'Sitting'}
    if (not isinstance(candidates, dict) or set(candidates) != names or any(
            not isinstance(ids, list) or len(ids) > 5000 or
            any(type(identity) is not int or identity <= 0 for identity in ids)
            for ids in candidates.values())):
        raise ValueError('Provide bounded learning-retention candidate IDs.')
    # A dry-run snapshot does not authorize deletion after policy or session
    # state changes. Recheck current eligibility and only intersect that set.
    lock_retention_state(candidates)
    current = retention_candidates(limit=5000)
    selected = {name: set(candidates[name]) & set(current[name]) for name in names}
    removed = {name: 0 for name in names}
    # Children before parents. This command only runs on operator-approved
    # policies; no scheduled destructive command is installed automatically.
    for name in ("Evidence", "Feedback", "Sitting"):
        ids = selected[name]
        if ids:
            if name == "Sitting":
                removed['Evidence'] += M["Evidence"].query.filter(M["Evidence"].session_id.in_(ids)).delete(
                    synchronize_session=False
                )
            removed[name] += M[name].query.filter(M[name].id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return removed
