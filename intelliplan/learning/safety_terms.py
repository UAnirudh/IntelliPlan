"""Classroom-scoped clinical vocabulary; never a bypass of content safety.

Only institution-assigned classroom teachers and institution administrators
can choose vocabulary. The classifier still evaluates intent and context.
"""
from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user

from intelliplan.learning import policy


CLINICAL_TERMS = frozenset({
    'penis', 'vagina', 'vulva', 'testicle', 'testicles', 'scrotum', 'uterus',
    'ovary', 'ovaries', 'cervix', 'menstruation', 'breast', 'breasts', 'puberty',
    'semen', 'sperm', 'reproduction', 'contraception', 'pregnancy',
})
POLICY_KEY = 'educational_terms'
bp = Blueprint('teacher_safety', __name__)


def validate_terms(value):
    if not isinstance(value, list) or len(value) > len(CLINICAL_TERMS):
        raise ValueError('Choose terms from the clinical vocabulary list.')
    if any(not isinstance(term, str) or term not in CLINICAL_TERMS for term in value):
        raise ValueError('Only the listed clinical terms can be enabled for lessons.')
    return sorted(set(value))


def _ancestors(organization):
    """Fail closed on missing parents and cycles, as for other policies."""
    from App import db
    Organization = policy.models()['Organization']
    seen = {organization.id}
    parent_id = organization.parent_id
    while parent_id:
        if parent_id in seen or len(seen) > 32:
            raise ValueError('The institution hierarchy could not be checked.')
        seen.add(parent_id)
        parent = db.session.get(Organization, parent_id, populate_existing=True)
        if parent is None:
            raise ValueError('The parent institution policy is unavailable.')
        yield parent
        parent_id = parent.parent_id


def _ceiling(organizations):
    terms = set(CLINICAL_TERMS)
    for organization in organizations:
        data = policy.validate_policy(organization.policy)
        if POLICY_KEY in data:
            terms &= set(data[POLICY_KEY])
    return terms


def _classroom_ceiling(classroom):
    ancestors = list(_ancestors(classroom))
    if not ancestors or ancestors[0].kind != 'school':
        raise ValueError('A classroom must belong to a school before terms can be enabled.')
    return _ceiling(ancestors)


def _manages_classroom(user, organization):
    if not getattr(user, 'is_authenticated', False) or organization.kind != 'class':
        return False
    if policy.administers(user, organization.id):
        return True
    return (getattr(user, 'role', None) == 'teacher'
            and policy.models()['Membership'].query.filter_by(
                user_id=user.id, organization_id=organization.id, role='teacher').first() is not None)


def allowed_terms_for_student(user_id):
    """Return approved clinical terms for the student's assigned classrooms.

    Membership in a teacher's linked-progress list is not classroom authority.
    Non-class institution policy lists act only as ceilings, never grants.
    Callers must continue enforcing sexual/violent intent and output safety.
    """
    from App import User, db
    try:
        if type(user_id) is not int:
            return frozenset()
        user = db.session.get(User, user_id)
        if user is None:
            return frozenset()
        organizations = policy.organizations(user)
        institutional_ceiling = _ceiling(org for org in organizations if org.kind != 'class')
        M = policy.models()
        memberships = M['Membership'].query.populate_existing().filter_by(
            user_id=user_id, role='student').all()
        terms = set()
        for membership in memberships:
            classroom = db.session.get(M['Organization'], membership.organization_id, populate_existing=True)
            if classroom is None or classroom.kind != 'class':
                continue
            data = policy.validate_policy(classroom.policy)
            selected = set(data.get(POLICY_KEY, []))
            terms |= selected & _classroom_ceiling(classroom) & institutional_ceiling
        return frozenset(terms)
    except Exception:
        # A broken policy must never turn the vocabulary list into a bypass.
        return frozenset()


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(error='invalid_request', message=str(error)), 400


@bp.route('/teacher/terminology')
def page():
    if not current_user.is_authenticated:
        abort(401)
    if getattr(current_user, 'role', None) != 'teacher' and not policy.is_learning_admin(current_user):
        abort(403)
    return render_template('teacher_terminology.html', active_page='teacher')


@bp.route('/api/teacher/terminology')
def classrooms():
    from App import is_admin
    from sqlalchemy import or_
    if not current_user.is_authenticated:
        abort(401)
    M = policy.models()
    query = M['Organization'].query.filter_by(kind='class')
    if not is_admin(current_user):
        memberships = M['Membership'].query.filter_by(user_id=current_user.id).all()
        direct = [row.organization_id for row in memberships if row.role in {'teacher', 'admin'}]
        admin = [row.organization_id for row in memberships if row.role == 'admin']
        schools = M['Organization'].query.filter(M['Organization'].kind == 'school',
                                                M['Organization'].parent_id.in_(admin)).all()
        admin.extend(school.id for school in schools)
        query = query.filter(or_(M['Organization'].id.in_(direct), M['Organization'].parent_id.in_(admin)))
    rows = query.order_by(M['Organization'].name).all()
    result = []
    for row in rows:
        if not _manages_classroom(current_user, row):
            continue
        allowed = _classroom_ceiling(row)
        data = policy.validate_policy(row.policy)
        result.append(dict(id=row.id, name=row.name, version=row.version,
                           terms=data.get(POLICY_KEY, []), permitted_terms=sorted(allowed)))
    return jsonify(classrooms=result, vocabulary=sorted(CLINICAL_TERMS))


@bp.route('/api/teacher/terminology/<int:organization_id>', methods=['PUT'])
def update(organization_id):
    from App import db
    M = policy.models()
    row = db.session.get(M['Organization'], organization_id, populate_existing=True)
    if row is None or not _manages_classroom(current_user, row):
        abort(403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'terms', 'version'}:
        raise ValueError('Provide the selected terms and current classroom version.')
    selected = validate_terms(data['terms'])
    if type(data['version']) is not int:
        raise ValueError('Reload the classroom before saving.')
    if not set(selected) <= _classroom_ceiling(row):
        return jsonify(error='institution_restriction', message='Your institution has restricted one or more selected terms.'), 403
    settings = dict(policy.validate_policy(row.policy), **{POLICY_KEY: selected})
    changed = M['Organization'].query.filter_by(id=row.id, version=data['version']).update(
        dict(policy=settings, version=data['version'] + 1), synchronize_session=False)
    if not changed:
        db.session.rollback()
        return jsonify(error='version_conflict', message='The classroom changed. Reload before saving.'), 409
    db.session.add(M['Audit'](organization_id=row.id, actor_id=current_user.id,
                             action='educational_terms_updated', changes={POLICY_KEY: selected}))
    db.session.commit()
    policy.invalidate_cache()
    return jsonify(version=data['version'] + 1, terms=selected)


def install(app):
    app.register_blueprint(bp)
