"""SQL administration persistence with account-bound grants and atomic audit."""
from flask import current_app
from time_utils import utcnow


def admin_access():
    return SQLAdminAccess(current_app.intelliplan_db, current_app.intelliplan_user_model,
        current_app.extensions['account_admin_grant_model'], current_app.extensions['account_admin_audit_model'])


class SQLAdminAccess:
    def __init__(self, db, users, grants, audit):
        self.db, self.users, self.grants, self.audit = db, users, grants, audit

    def get(self, user_id):
        return self.db.session.get(self.users, user_id)

    def by_email(self, email):
        return self.users.query.filter_by(email=email).first()

    def has_grant(self, user_id):
        return self.grants.query.populate_existing().filter_by(user_id=user_id, revoked_at=None).first() is not None

    def list(self):
        return self.users.query.join(self.grants, self.grants.user_id == self.users.id).filter(
            self.grants.revoked_at.is_(None)).all()

    def granted_ids(self, user_ids):
        """Read one fresh grant snapshot for an account listing."""
        if not user_ids:
            return set()
        return {user_id for (user_id,) in self.db.session.query(self.grants.user_id).filter(
            self.grants.user_id.in_(user_ids), self.grants.revoked_at.is_(None)).all()}

    def change(self, actor_id, user_id, grant, owner_checker):
        actor, user = self.get(actor_id), self.get(user_id)
        if actor is None or not owner_checker(actor):
            raise PermissionError('forbidden')
        if user is None:
            raise LookupError('account_not_found')
        if owner_checker(user):
            raise ValueError('owner_access_is_configured_on_server')
        row = self.db.session.get(self.grants, user_id)
        if grant:
            if row is None:
                row = self.grants(user_id=user_id)
                self.db.session.add(row)
            row.granted_by_id, row.granted_at, row.revoked_at = actor_id, utcnow(), None
        elif row is not None:
            row.revoked_at = utcnow()
        self.db.session.add(self.audit(actor_id=actor_id, target_id=user_id, action='grant' if grant else 'revoke'))
        self.db.session.commit()
