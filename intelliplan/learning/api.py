"""Owned course/study APIs and institution-scoped administration."""
from datetime import datetime
from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from intelliplan.learning import courses, policy

bp = Blueprint('learning_hub', __name__)


def body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError('Send a JSON object.')
    return value


def owned(model, identity):
    row = policy.models()[model].query.filter_by(id=identity, user_id=current_user.id).first()
    if not row:
        abort(404)
    return row


def gate(feature):
    if not current_user.is_authenticated:
        abort(401)
    allowed, reason = policy.decision(current_user, feature)
    if not allowed:
        return jsonify(error=reason, feature=feature, message='This capability is unavailable under your current policy.'), 403


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(error='invalid_request', message=str(error)), 400


@bp.errorhandler(IntegrityError)
def conflict(error):
    from App import db
    db.session.rollback()
    return jsonify(error='conflict', message='This operation has already been recorded. Reload and try again.'), 409


@bp.route('/learning')
def hub():
    return render_template('learning_hub.html', active_page='study_hub')


@bp.route('/tools')
def tools_page():
    from intelliplan.learning.tool_catalog import groups_for
    return render_template('study_tools.html', active_page='study_tools',
                           tool_groups=groups_for(current_user),
                           can_manage_school=policy.is_learning_admin(current_user))


@bp.route('/school-admin')
def admin_page():
    if not policy.is_learning_admin(current_user):
        abort(403)
    return render_template('school_admin.html', active_page='school_admin')


@bp.route('/api/learning/capabilities')
def capabilities():
    return jsonify(features={key: dict(allowed=policy.decision(current_user, key)[0],
        reason=policy.decision(current_user, key)[1], maturity=f.maturity, limitation=f.limitation,
        future_entitlement=f.future_entitlement) for key, f in policy.FEATURES.items()})


@bp.route('/api/school-admin/organizations', methods=['GET', 'POST'])
def organizations():
    from App import db, is_admin
    M = policy.models()
    if not current_user.is_authenticated:
        abort(401)
    if request.method == 'POST':
        if not is_admin(current_user):
            abort(403)
        data = body()
        kind = data.get('kind')
        if kind not in {'school', 'district'}:
            raise ValueError('Organization kind must be school or district.')
        parent_id = data.get('parent_id')
        if parent_id:
            parent = db.session.get(M['Organization'], parent_id)
            if kind != 'school' or not parent or parent.kind != 'district':
                raise ValueError('Only a school may have a district parent.')
        row = M['Organization'](name=courses.text(data.get('name'), 160), kind=kind,
                                parent_id=parent_id, policy=policy.validate_policy(data.get('policy', {})))
        db.session.add(row)
        db.session.flush()
        db.session.add(M['Audit'](organization_id=row.id, actor_id=current_user.id,
                                 action='organization_created', changes=row.policy))
        db.session.commit()
        return jsonify(id=row.id), 201
    rows = M['Organization'].query.order_by(M['Organization'].id).limit(200).all()
    return jsonify(organizations=[dict(id=o.id, name=o.name, kind=o.kind, parent_id=o.parent_id,
        policy=o.policy, version=o.version) for o in rows if policy.administers(current_user, o.id)])


@bp.route('/api/school-admin/organizations/<int:oid>/policy', methods=['PUT'])
def organization_policy(oid):
    from App import db
    M = policy.models()
    if not policy.administers(current_user, oid):
        abort(403)
    data = body()
    settings = policy.validate_policy(data.get('policy'))
    version = data.get('version')
    if type(version) is not int:
        raise ValueError('Provide the policy version shown in the dashboard.')
    changed = M['Organization'].query.filter_by(id=oid, version=version).update(
        dict(policy=settings, version=version + 1), synchronize_session=False)
    if not changed:
        db.session.rollback()
        return jsonify(error='version_conflict', message='The policy changed. Reload before saving.'), 409
    db.session.add(M['Audit'](organization_id=oid, actor_id=current_user.id,
                             action='policy_updated', changes=settings))
    db.session.commit()
    return jsonify(version=version + 1)


@bp.route('/api/school-admin/organizations/<int:oid>/members', methods=['GET', 'POST'])
def organization_members(oid):
    from App import db, User, is_admin
    M = policy.models()
    if not policy.administers(current_user, oid):
        abort(403)
    if request.method == 'GET':
        return jsonify(members=[dict(user_id=r.user_id, role=r.role) for r in
            M['Membership'].query.filter_by(organization_id=oid).limit(200).all()])
    data = body()
    role, uid = data.get('role', 'student'), data.get('user_id')
    if role not in {'student', 'teacher', 'admin'} or type(uid) is not int:
        raise ValueError('Provide a user ID and a valid role.')
    # Delegation of administrative authority is a platform-owner action.
    if role == 'admin' and not is_admin(current_user):
        abort(403)
    if not db.session.get(User, uid):
        abort(404)
    row = M['Membership'].query.filter_by(user_id=uid, organization_id=oid).first()
    if row and row.role == 'admin' and not is_admin(current_user):
        abort(403)
    if not row:
        row = M['Membership'](user_id=uid, organization_id=oid, role=role)
        db.session.add(row)
    row.role = role
    db.session.add(M['Audit'](organization_id=oid, actor_id=current_user.id,
                             action='membership_updated', changes=dict(user_id=uid, role=role)))
    db.session.commit()
    return jsonify(status='ok')


@bp.route('/api/school-admin/organizations/<int:oid>/audit')
def organization_audit(oid):
    if not policy.administers(current_user, oid):
        abort(403)
    M = policy.models()
    rows = M['Audit'].query.filter_by(organization_id=oid).order_by(M['Audit'].id.desc()).limit(100).all()
    return jsonify(events=[dict(id=r.id, action=r.action, actor_id=r.actor_id,
        changes=r.changes, created_at=r.created_at.isoformat()) for r in rows])


@bp.route('/api/learning/courses', methods=['GET', 'POST'])
def course_list():
    denied = gate('course_generator')
    if denied:
        return denied
    from App import db
    M = policy.models()
    if request.method == 'GET':
        rows = M['Course'].query.filter_by(user_id=current_user.id).order_by(M['Course'].id.desc()).limit(100).all()
        return jsonify(courses=[dict(id=r.id, title=r.blueprint['title'], state=r.state, mode=r.mode,
                                     revision=r.revision, revision_source=r.blueprint.get('revision_source')) for r in rows])
    data = body()
    mode = data.get('mode')
    if mode == 'generated':
        denied = gate('generated_content')
        if denied:
            return denied
        if not policy.permitted_providers(current_user):
            return jsonify(error='third_party_processing_disabled'), 403
        if data.get('confirm_provider_use') is not True:
            raise ValueError('Confirm provider use before generating the blueprint.')
    goal = courses.text(data.get('goal'), 2000)
    # At most one blueprint per request; protect against repeated expensive calls.
    if M['Course'].query.filter_by(user_id=current_user.id, goal=goal, mode=mode).first():
        return jsonify(error='duplicate_course', message='This course already exists in your library.'), 409
    try:
        if mode == 'generated':
            from ai_provider import model_chain
            from intelliplan.learning.governance import reserve_tokens
            if not model_chain():
                return jsonify(error='provider_unavailable', message='An approved provider must be configured before generation.'), 503
            reserve_tokens(current_user, len(goal.encode('utf-8')) + 1000 + 4000)
            db.session.commit()
        outline = courses.create_blueprint(goal, mode)
    except ValueError:
        db.session.rollback()
        raise
    except Exception:
        return jsonify(error='provider_unavailable', message='The blueprint could not be generated. Try again later.'), 503
    row = M['Course'](user_id=current_user.id, mode=mode, goal=goal, blueprint=outline)
    db.session.add(row)
    db.session.commit()
    return jsonify(id=row.id, blueprint=row.blueprint, state=row.state), 201


@bp.route('/api/learning/courses/<int:cid>', methods=['GET', 'PUT'])
def course_detail(cid):
    denied = gate('course_generator')
    if denied:
        return denied
    from App import db
    row = owned('Course', cid)
    M = policy.models()
    if request.method == 'PUT':
        if row.state != 'draft' or M['Job'].query.filter_by(course_id=row.id).first():
            return jsonify(error='generation_started', message='A generated blueprint cannot be edited in place.'), 409
        data = body()
        revision = data.get('revision', row.revision)
        if type(revision) is not int:
            raise ValueError('Provide a valid outline revision.')
        blueprint = courses.validate_blueprint(data.get('blueprint'))
        changed = M['Course'].query.filter_by(id=row.id, state='draft', revision=revision).update(
            dict(blueprint=blueprint, revision=revision + 1), synchronize_session=False)
        if not changed:
            db.session.rollback()
            return jsonify(error='outline_changed', message='The outline changed or generation started. Your edits have been kept; reload before saving again.'), 409
        db.session.commit()
    lessons = M['Lesson'].query.filter_by(course_id=row.id).order_by(M['Lesson'].id).all()
    positions = {spec['key']: index for index, spec in enumerate(row.blueprint['lessons'])}
    lessons.sort(key=lambda lesson: positions[lesson.key])
    evidence = M['Evidence'].query.filter_by(user_id=current_user.id).all()
    attempts = {e.lesson_id: e for e in evidence if e.lesson_id}
    public_lessons = []
    for lesson in lessons:
        item = courses.public_lesson(lesson)
        attempt = attempts.get(lesson.id)
        if attempt:
            item['attempt'] = dict(correct=attempt.correct, assisted=attempt.assisted,
                                   explanation=lesson.content.get('quiz', {}).get('explanation', ''))
        public_lessons.append(item)
    job = M['Job'].query.filter_by(course_id=row.id).first()
    return jsonify(id=row.id, blueprint=row.blueprint, state=row.state, mode=row.mode, revision=row.revision,
        lessons=public_lessons, next=courses.next_activity(row, evidence),
        job=dict(state=job.state, error=job.error, reserved_tokens=job.reserved_tokens,
                 token_budget=job.token_budget, attempts=job.attempts) if job else None)


@bp.route('/api/learning/courses/<int:cid>/generation', methods=['POST'])
def generation(cid):
    denied = gate('course_generator')
    if denied:
        return denied
    from App import db
    row = owned('Course', cid)
    data = body()
    action = data.get('action', 'start')
    M = policy.models()
    job = M['Job'].query.filter_by(course_id=cid).first()
    if action == 'cancel':
        if not job:
            abort(404)
        job.state, job.lease, job.lease_until = 'cancelled', None, None
    elif action in {'start', 'retry'}:
        if M['Lesson'].query.filter_by(course_id=cid).count() == len(row.blueprint['lessons']):
            return jsonify(error='lessons_complete', message='All lessons are saved. Review them or create a lesson revision.'), 409
        if row.mode == 'generated':
            denied = gate('generated_content')
            if denied:
                return denied
            if data.get('confirm_provider_use') is not True:
                raise ValueError('Confirm provider use and the token ceiling before generation.')
        if job and job.state not in {'failed', 'cancelled'}:
            return jsonify(error='already_started'), 409
        if not job:
            job = M['Job'](course_id=cid, token_budget=policy.effective(current_user)['max_generation_tokens'])
            db.session.add(job)
        job.state, job.error, job.lease, job.lease_until = 'queued', None, None, None
        row.state = 'generating'
    else:
        raise ValueError('Choose start, retry, or cancel.')
    db.session.commit()
    return jsonify(state=job.state, message='Generation is processed by the learning-worker command.'), 202


@bp.route('/api/learning/courses/<int:cid>/lessons/<int:lid>/revision', methods=['POST'])
def lesson_revision(cid, lid):
    denied = gate('course_generator')
    if denied:
        return denied
    from App import db
    course = owned('Course', cid)
    M = policy.models()
    lesson = M['Lesson'].query.filter_by(id=lid, course_id=cid).first()
    if not lesson:
        abort(404)
    data = body()
    action = data.get('action')
    if action not in {'edit', 'regenerate', 'new_check'}:
        raise ValueError('Choose edit, regenerate, or new_check.')
    if course.mode != 'generated':
        return jsonify(error='original_course_required', message='Revision beta supports original courses only. Curated sources stay linked to their authors.'), 409
    job = M['Job'].query.filter_by(course_id=cid).first()
    if (job and job.state in {'queued', 'running'} or
            M['Lesson'].query.filter_by(course_id=cid).count() != len(course.blueprint['lessons'])):
        return jsonify(error='generation_in_progress', message='Finish or cancel generation and save every lesson before making a revision.'), 409
    replacement = courses.validate_lesson(data.get('content')) if action == 'edit' else None
    if action in {'regenerate', 'new_check'}:
        denied = gate('generated_content')
        if denied:
            return denied
        if not policy.permitted_providers(current_user):
            return jsonify(error='third_party_processing_disabled'), 403
        if data.get('confirm_provider_use') is not True:
            raise ValueError('Confirm provider use and the token ceiling before generating a revision.')
        from ai_provider import model_chain
        if not model_chain():
            return jsonify(error='provider_unavailable', message='An approved provider must be configured before regeneration.'), 503
    revised = courses.fork_lesson(course, lesson, replacement, assessment_only=action == 'new_check')
    db.session.commit()
    return jsonify(id=revised.id, state=revised.state, revision=revised.revision,
        message='A revised course was saved. The original course and its evidence are preserved; review the revised course before scoring.'), 201


@bp.route('/api/learning/courses/<int:cid>/outline-revision', methods=['POST'])
def outline_revision(cid):
    denied = gate('course_generator')
    if denied:
        return denied
    from App import db
    course = owned('Course', cid)
    M = policy.models()
    job = M['Job'].query.filter_by(course_id=cid).first()
    if (job and job.state in {'queued', 'running'} or
            M['Lesson'].query.filter_by(course_id=cid).count() != len(course.blueprint['lessons'])):
        return jsonify(error='incomplete_course', message='Save every lesson and finish generation before revising the course organization.'), 409
    data = body()
    revision = data.get('revision')
    if type(revision) is not int:
        raise ValueError('Provide the displayed course revision.')
    if revision != course.revision:
        return jsonify(error='outline_changed', message='Reload the course before saving its organization.'), 409
    revised = courses.fork_outline(course, data.get('blueprint'))
    db.session.commit()
    return jsonify(id=revised.id, state=revised.state, revision=revised.revision,
        message='Course organization saved in a new revision. Check prerequisite sequencing and accuracy before reviewing it.'), 201


@bp.route('/api/learning/courses/<int:cid>/review', methods=['GET', 'POST'])
def review_course(cid):
    denied = gate('course_generator')
    if denied:
        return denied
    from App import db
    row = owned('Course', cid)
    M = policy.models()
    if request.method == 'GET':
        positions = {spec['key']: index for index, spec in enumerate(row.blueprint['lessons'])}
        return jsonify(lessons=[dict(id=lesson.id, key=lesson.key, content=lesson.content) for lesson in
                               sorted(M['Lesson'].query.filter_by(course_id=cid).all(), key=lambda lesson: positions[lesson.key])])
    data = body()
    if data.get('confirm_accuracy') is not True:
        raise ValueError('Confirm that lesson accuracy and every answer key have been checked.')
    lessons = M['Lesson'].query.filter_by(course_id=cid).all()
    if len(lessons) != len(row.blueprint['lessons']):
        return jsonify(error='incomplete_course'), 409
    for lesson in lessons:
        lesson.reviewed = True
    row.state = 'reviewed'
    db.session.commit()
    return jsonify(state=row.state)


@bp.route('/api/learning/courses/<int:cid>/lessons/<int:lid>/answer', methods=['POST'])
def lesson_answer(cid, lid):
    denied = gate('course_generator')
    if denied:
        return denied
    from App import db
    owned('Course', cid)
    M = policy.models()
    lesson = M['Lesson'].query.filter_by(id=lid, course_id=cid).first()
    if not lesson:
        abort(404)
    if not lesson.reviewed or 'quiz' not in lesson.content:
        return jsonify(error='review_required', message='An administrator must review the answer key first.'), 409
    data = body()
    answer = data.get('answer')
    assisted = data.get('assisted', False)
    if type(answer) is not int or not 0 <= answer < len(lesson.content['quiz']['options']) or type(assisted) is not bool:
        raise ValueError('Choose a valid answer and assistance setting.')
    if M['Evidence'].query.filter_by(user_id=current_user.id, lesson_id=lid).first():
        return jsonify(error='already_scored', message='This check already supplied evidence. Review the explanation.'), 409
    course = owned('Course', cid)
    spec = next(s for s in course.blueprint['lessons'] if s['key'] == lesson.key)
    correct = answer == lesson.content['quiz']['answer']
    # Course builders can inspect every answer key. Their checks are evaluation
    # evidence, never independent evidence, even if they submit assisted=false.
    assisted = True
    db.session.add(M['Evidence'](user_id=current_user.id, lesson_id=lid, objective=spec['objective'],
                               kind='reviewed_curated_check' if course.mode == 'curated' else 'reviewed_generated_check',
                               correct=correct, assisted=assisted))
    db.session.commit()
    return jsonify(correct=correct, explanation=lesson.content['quiz']['explanation'],
                   evidence='Limited assisted course-builder evidence from an administrator-reviewed item.')


@bp.route('/api/learning/progress')
def progress():
    denied = gate('study')
    if denied:
        return denied
    from intelliplan.learning.evidence import due_review, summary
    rows = summary(current_user)
    return jsonify(due_review=due_review(current_user, rows), evidence=rows,
        legacy_progress_url='/api/tutor/adaptive/dashboard',
        review_url='/api/flashcards/study', note='Elapsed time and self-reports do not establish mastery.')


@bp.route('/api/learning/sessions', methods=['GET', 'POST'])
def sittings():
    denied = gate('study')
    if denied:
        return denied
    from App import db
    M = policy.models()
    if request.method == 'GET':
        rows = M['Sitting'].query.filter_by(user_id=current_user.id).order_by(M['Sitting'].id.desc()).limit(50).all()
        return jsonify(sessions=[sitting_payload(row) for row in rows])
    if M['Sitting'].query.filter(M['Sitting'].user_id == current_user.id,
                                M['Sitting'].state.in_(['active', 'paused'])).first():
        return jsonify(error='session_in_progress', message='Resume or finish your existing session first.'), 409
    data = body()
    method = data.get('method', 'retrieval')
    intervention = data.get('intervention', 'reminder')
    minutes = data.get('planned_minutes', 25)
    if method not in {'retrieval', 'practice', 'reading', 'paper'} or intervention not in {'none', 'reminder', 'break', 'pause'}:
        raise ValueError('Choose a supported study method and intervention.')
    if type(minutes) is not int or not 5 <= minutes <= 120:
        raise ValueError('Plan between 5 and 120 minutes.')
    row = M['Sitting'](user_id=current_user.id, objective=courses.text(data.get('objective')),
                       method=method, planned_minutes=minutes, intervention=intervention)
    db.session.add(row)
    db.session.commit()
    return jsonify(sitting_payload(row)), 201


def sitting_payload(row):
    elapsed = row.elapsed_seconds
    if row.state == 'active' and row.resumed_at:
        elapsed += max(0, int((datetime.utcnow() - row.resumed_at).total_seconds()))
    return dict(id=row.id, objective=row.objective, state=row.state,
                planned_minutes=row.planned_minutes, elapsed_seconds=elapsed)


@bp.route('/api/learning/sessions/<int:sid>/checkpoint', methods=['POST'])
def checkpoint(sid):
    denied = gate('study')
    if denied:
        return denied
    from App import db
    row = owned('Sitting', sid)
    revision = row.revision
    if row.state == 'completed':
        return jsonify(error='session_completed'), 409
    data = body()
    kind = data.get('kind', 'self_report')
    if kind not in {'self_report', 'paper_work', 'retrieval_attempt', 'inactive', 'complete', 'resume'}:
        raise ValueError('Unsupported checkpoint.')
    intervention = 'none'
    updates = dict(revision=revision + 1)
    if kind == 'inactive':
        if policy.decision(current_user, 'focus')[0]:
            intervention = row.intervention
        if intervention == 'pause':
            updates.update(elapsed_seconds=sitting_payload(row)['elapsed_seconds'], resumed_at=None, state='paused')
        message = 'Reading or thinking away from the keyboard can be useful studying.'
    elif kind == 'resume':
        if row.state == 'paused':
            updates['resumed_at'] = datetime.utcnow()
        updates['state'] = 'active'
        message = 'Session resumed. You control when you are ready to continue.'
    elif kind == 'complete':
        updates.update(elapsed_seconds=sitting_payload(row)['elapsed_seconds'], resumed_at=None, state='completed')
        message = 'Session completed. Time alone is not evidence of understanding.'
    else:
        message = 'Activity recorded as self-reported work, without a mastery claim.'
    changed = policy.models()['Sitting'].query.filter_by(id=sid, revision=revision).update(
        updates, synchronize_session=False)
    if not changed:
        db.session.rollback()
        return jsonify(error='session_changed', message='The session changed in another request. Reload before continuing.'), 409
    if kind in {'self_report', 'paper_work', 'retrieval_attempt', 'complete'}:
        db.session.add(policy.models()['Evidence'](user_id=current_user.id, session_id=sid,
            objective=row.objective, kind=kind, correct=None))
    db.session.commit()
    return jsonify(state=row.state, session=sitting_payload(row), intervention=intervention,
                   message=message, demonstrated_mastery=False)


@bp.route('/api/learning/export')
def export():
    if not current_user.is_authenticated:
        abort(401)
    M = policy.models()
    course_rows = M['Course'].query.filter_by(user_id=current_user.id).all()
    return jsonify(beta_enrollments=[dict(feature=r.feature, approved=r.approved,
        consent_version=r.consent_version, accepted_at=r.accepted_at.isoformat() if r.accepted_at else None)
        for r in M['BetaEnrollment'].query.filter_by(user_id=current_user.id)],
        beta_feedback=[dict(feature=r.feature, category=r.category, message=r.message,
            created_at=r.created_at.isoformat()) for r in
            M['Feedback'].query.filter_by(user_id=current_user.id)],
        courses=[dict(id=r.id, mode=r.mode, goal=r.goal, blueprint=r.blueprint,
                                 lessons=[dict(lesson.content, key=lesson.key, id=lesson.id, reviewed=lesson.reviewed) for lesson in sorted(
                                     M['Lesson'].query.filter_by(course_id=r.id).all(),
                                     key=lambda lesson: next(i for i, spec in enumerate(r.blueprint['lessons']) if spec['key'] == lesson.key))])
                            for r in course_rows],
        sessions=[dict(objective=r.objective, state=r.state, method=r.method) for r in
                  M['Sitting'].query.filter_by(user_id=current_user.id)],
        evidence=[dict(objective=r.objective, kind=r.kind, correct=r.correct, assisted=r.assisted,
                       lesson_id=r.lesson_id, created_at=r.created_at.isoformat()) for r in
                  M['Evidence'].query.filter_by(user_id=current_user.id)])


def install(app):
    from intelliplan.learning.governance_api import install as install_governance
    install_governance(bp, app)
    app.register_blueprint(bp)
    from App import limiter
    endpoint = 'learning_hub.course_list'
    app.view_functions[endpoint] = limiter.limit('10/hour', methods=['POST'])(app.view_functions[endpoint])
    endpoint = 'learning_hub.feedback'
    app.view_functions[endpoint] = limiter.limit('20/hour', methods=['POST'])(app.view_functions[endpoint])
    endpoint = 'learning_hub.lesson_revision'
    app.view_functions[endpoint] = limiter.limit('10/hour', methods=['POST'])(app.view_functions[endpoint])
    endpoint = 'learning_hub.outline_revision'
    app.view_functions[endpoint] = limiter.limit('10/hour', methods=['POST'])(app.view_functions[endpoint])

    from intelliplan.learning.worker import install as install_worker
    install_worker(app)
