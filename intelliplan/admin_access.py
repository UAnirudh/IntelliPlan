"""Account-bound delegated administration; only configured owners manage grants."""
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

bp = Blueprint('admin_access', __name__)


def register_model(db):
    class AdminGrant(db.Model):
        __tablename__ = 'account_admin_grants'
        user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
        granted_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
        granted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        revoked_at = db.Column(db.DateTime)
    class AdminAccessAudit(db.Model):
        __tablename__ = 'account_admin_access_audit'
        id = db.Column(db.Integer, primary_key=True)
        actor_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
        target_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
        action = db.Column(db.String(16), nullable=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    current_app.extensions['account_admin_audit_model'] = AdminAccessAudit
    return AdminGrant


def is_owner(user):
    checker = current_app.extensions.get('admin_owner_checker')
    return bool(callable(checker) and checker(user))


@bp.app_context_processor
def workspace_access_context():
    return {'workspace_owner_admin': is_owner(current_user),
            'workspace_role': getattr(current_user, 'role', 'student')}


def has_grant(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    from intelliplan.persistence.admin_access import admin_access
    return admin_access().has_grant(user.id)


@bp.route('/admin/accounts')
@login_required
def accounts_page():
    if not is_owner(current_user):
        return jsonify(error='forbidden'), 403
    return render_template('admin_accounts.html', active_page='settings', noindex_page=True)


@bp.route('/api/admin/accounts', methods=['GET', 'POST', 'DELETE'])
@login_required
def manage_accounts():
    if not is_owner(current_user):
        return jsonify(error='forbidden'), 403
    from intelliplan.persistence.admin_access import admin_access
    repository = admin_access()
    if request.method == 'GET':
        email = request.args.get('email', '').strip().lower()
        if email:
            user = repository.by_email(email)
            users = [user] if user else []
        else:
            users = repository.list()
        batch_grants = getattr(repository, 'granted_ids', None)
        granted = batch_grants([user.id for user in users]) if callable(batch_grants) else None
        return jsonify(accounts=[dict(id=user.id, email=user.email, name=user.name,
            admin=bool((user.id in granted if granted is not None else repository.has_grant(user.id))
                       or is_owner(user)), owner=is_owner(user)) for user in users])
    body = request.get_json(silent=True)
    user_id = body.get('user_id') if isinstance(body, dict) else None
    if type(user_id) is not int:
        return jsonify(error='invalid_account'), 400
    try:
        repository.change(current_user.id, user_id, request.method == 'POST', is_owner)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 403
    except LookupError as exc:
        return jsonify(error=str(exc)), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, user_id=user_id, admin=request.method == 'POST')
