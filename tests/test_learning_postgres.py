"""Optional concurrency checks restricted to a disposable local PostgreSQL DB.

Set INTELLIPLAN_POSTGRES_VERIFICATION=1 and use a localhost database whose
name starts with intelliplan_fixture. Every account and organization here is
synthetic; cleanup targets only those records. SQLite runs skip these tests.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
from threading import Barrier, Event, get_ident
from uuid import uuid4

import pytest
from flask_login import login_user
from sqlalchemy import event, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Update

import App
from intelliplan.learning import courses, governance, policy


@pytest.fixture
def pg_scope():
    with App.app.app_context():
        engine = App.db.engine
        if (os.getenv('INTELLIPLAN_POSTGRES_VERIFICATION') != '1' or
                engine.dialect.name != 'postgresql' or engine.url.host not in {'127.0.0.1', 'localhost'} or
                not (engine.url.database or '').startswith('intelliplan_fixture')):
            pytest.skip('Requires an explicitly selected disposable local PostgreSQL fixture database.')
        M = policy.models()
        owners = [App.User(email=f'pg-fixture-{uuid4().hex}@example.test', password_hash='x') for _ in range(2)]
        schools = [M['Organization'](name=f'PG fixture {uuid4().hex}', kind='school',
            policy={'retention_days': 7, 'monthly_generation_tokens': 1000}) for _ in range(2)]
        App.db.session.add_all(owners + schools)
        App.db.session.flush()
        uids, oids = [row.id for row in owners], [row.id for row in schools]
        App.db.session.add_all([M['Membership'](user_id=uid, organization_id=oids[0], role='admin') for uid in uids])
        old = datetime.utcnow() - timedelta(days=8)
        evidence = M['Evidence'](user_id=uids[0], objective='Synthetic recall', kind='paper_work', created_at=old)
        audit = M['Audit'](organization_id=oids[0], actor_id=uids[1], action='membership_updated',
            changes={'members': [{'user_id': uid} for uid in uids], 'tutor': False})
        App.db.session.add_all([evidence, audit])
        App.db.session.flush()
        eid, aid = evidence.id, audit.id
        App.db.session.commit()
        App.db.session.remove()
    yield dict(engine=engine, M=M, uids=uids, oids=oids, eid=eid, aid=aid)
    with App.app.app_context():
        for name in ('Evidence', 'Job', 'Lesson'):
            if name == 'Evidence':
                M[name].query.filter(M[name].user_id.in_(uids)).delete(synchronize_session=False)
            else:
                cids = select(M['Course'].id).where(M['Course'].user_id.in_(uids))
                M[name].query.filter(M[name].course_id.in_(cids)).delete(synchronize_session=False)
        for name in ('Sitting', 'Course'):
            M[name].query.filter(M[name].user_id.in_(uids)).delete(synchronize_session=False)
        for name in ('Audit', 'Budget', 'Membership'):
            M[name].query.filter(M[name].organization_id.in_(oids)).delete(synchronize_session=False)
        M['Organization'].query.filter(M['Organization'].id.in_(oids)).delete(synchronize_session=False)
        App.User.query.filter(App.User.id.in_(uids)).delete(synchronize_session=False)
        App.db.session.commit()


def delete_user(uid):
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, uid))
        return App._account_delete_impl().json['status']


def prune(eid):
    with App.app.app_context():
        return governance.apply_retention({'Evidence': [eid], 'Sitting': [], 'Feedback': []})


def test_pg_concurrent_audit_cleanup_merges_two_deleted_account_references(pg_scope):
    engine, M, uids = pg_scope['engine'], pg_scope['M'], pg_scope['uids']
    ready = Barrier(2)
    def scrub(uid):
        with App.app.app_context(), engine.begin() as connection:
            class Synchronized:
                first = True
                def execute(self, statement):
                    if isinstance(statement, Update) and self.first:
                        self.first = False
                        ready.wait(timeout=10)
                    return connection.execute(statement)
            governance.anonymize_audit_targets(Synchronized(), uid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(scrub, uid) for uid in uids]
        for result in results:
            result.result(timeout=15)
    with engine.connect() as connection:
        value = connection.execute(select(M['Audit'].__table__.c.changes).where(
            M['Audit'].__table__.c.id == pg_scope['aid'])).scalar_one()
    assert value == {'members': [{'deleted_account': True}, {'deleted_account': True}], 'tutor': False}


def test_pg_account_delete_blocks_new_membership_and_audit_phantom(pg_scope, monkeypatch):
    reached, release, attempting, finished = Event(), Event(), Event(), Event()
    original = governance.anonymize_audit_targets
    uid = pg_scope['uids'][0]
    def hold_after_scan(connection, user_id):
        original(connection, user_id)
        reached.set()
        assert release.wait(10)
    monkeypatch.setattr(governance, 'anonymize_audit_targets', hold_after_scan)
    def add_reference():
        M = pg_scope['M']
        try:
            with pg_scope['engine'].begin() as connection:
                attempting.set()
                # Different school avoids blocking on the old membership's
                # unique index; the target User FK must provide the protection.
                connection.execute(insert(M['Membership'].__table__).values(
                    user_id=uid, organization_id=pg_scope['oids'][1], role='student'))
                connection.execute(insert(M['Audit'].__table__).values(organization_id=pg_scope['oids'][1],
                    actor_id=pg_scope['uids'][1], action='membership_updated', changes={'user_id': uid}))
            return 'inserted'
        except IntegrityError as exc:
            return exc.orig.pgcode
        finally:
            finished.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(delete_user, uid)
        assert reached.wait(10)
        adding = pool.submit(add_reference)
        try:
            assert attempting.wait(10)
            assert not finished.wait(0.2)
        finally:
            release.set()
        assert deleting.result(timeout=15) == 'ok'
        assert adding.result(timeout=15) == '23503'
    with App.app.app_context():
        assert App.db.session.get(App.User, uid) is None
        assert not pg_scope['M']['Audit'].query.filter_by(organization_id=pg_scope['oids'][1]).count()


def test_pg_retention_holds_policy_until_deletion_commits(pg_scope, monkeypatch):
    selected, release, attempting, finished = Event(), Event(), Event(), Event()
    original = governance.retention_candidates
    def hold_selection(*args, **kwargs):
        value = original(*args, **kwargs)
        selected.set()
        assert release.wait(10)
        return value
    monkeypatch.setattr(governance, 'retention_candidates', hold_selection)
    def extend_policy():
        with pg_scope['engine'].begin() as connection:
            attempting.set()
            connection.execute(update(pg_scope['M']['Organization'].__table__).where(
                pg_scope['M']['Organization'].__table__.c.id == pg_scope['oids'][0]).values(policy={'retention_days': 0}))
        finished.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        applying = pool.submit(prune, pg_scope['eid'])
        assert selected.wait(10)
        changing = pool.submit(extend_policy)
        try:
            assert attempting.wait(10)
            assert not finished.wait(0.2)
        finally:
            release.set()
        assert applying.result(timeout=15)['Evidence'] == 1
        changing.result(timeout=15)
    with App.app.app_context():
        assert pg_scope['M']['Organization'].query.filter_by(id=pg_scope['oids'][0]).one().policy == {'retention_days': 0}


@pytest.mark.parametrize('first', ['delete', 'retention'])
def test_pg_retention_and_account_delete_share_owner_lock_order(pg_scope, monkeypatch, first):
    held, release, attempting, finished = Event(), Event(), Event(), Event()
    if first == 'delete':
        original = governance.anonymize_audit_targets
        def paused(connection, uid):
            original(connection, uid)
            held.set()
            assert release.wait(10)
        monkeypatch.setattr(governance, 'anonymize_audit_targets', paused)
    else:
        original = governance.retention_candidates
        def paused(*args, **kwargs):
            value = original(*args, **kwargs)
            held.set()
            assert release.wait(10)
            return value
        monkeypatch.setattr(governance, 'retention_candidates', paused)
    operations = {'delete': lambda: delete_user(pg_scope['uids'][0]),
                  'retention': lambda: prune(pg_scope['eid'])}
    second = 'retention' if first == 'delete' else 'delete'
    trailing_identity = []
    def observe_lock(conn, cursor, statement, parameters, context, executemany):
        if (trailing_identity and get_ident() == trailing_identity[0] and
                'FROM users' in statement and 'FOR UPDATE' in statement):
            attempting.set()
    def trail():
        trailing_identity.append(get_ident())
        try:
            return operations[second]()
        finally:
            finished.set()
    event.listen(pg_scope['engine'], 'before_cursor_execute', observe_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            leading = pool.submit(operations[first])
            assert held.wait(10)
            trailing = pool.submit(trail)
            try:
                assert attempting.wait(10)
                assert not finished.wait(0.2)
            finally:
                release.set()
            values = {first: leading.result(timeout=15), second: trailing.result(timeout=15)}
    finally:
        event.remove(pg_scope['engine'], 'before_cursor_execute', observe_lock)
    assert values['delete'] == 'ok'
    assert values['retention']['Evidence'] == (0 if first == 'delete' else 1)


def test_pg_two_workers_claim_a_curated_checkpoint_only_once(pg_scope):
    ready = Barrier(2)
    engine, M = pg_scope['engine'], pg_scope['M']
    with App.app.app_context():
        flag = App.FeatureFlag.query.filter_by(key='school_course_generator').first()
        created, prior = flag is None, flag.enabled if flag else False
        if flag is None:
            flag = App.FeatureFlag(key='school_course_generator')
            App.db.session.add(flag)
        flag.enabled = True
        course = M['Course'](user_id=pg_scope['uids'][0], mode='curated', goal='Python foundations',
            state='generating', blueprint=courses.create_blueprint('Python foundations', 'curated'))
        App.db.session.add(course)
        App.db.session.flush()
        cid = course.id
        App.db.session.add(M['Job'](course_id=cid))
        App.db.session.commit()
        App.db.session.remove()
    def before_query(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE learning_generation_jobs ') and parameters.get('state') == 'running':
            ready.wait(timeout=10)
    def step():
        with App.app.app_context():
            return courses.run_one_job()
    event.listen(engine, 'before_cursor_execute', before_query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(step) for _ in range(2)]
            assert sorted(result.result(timeout=15) for result in results) == [False, True]
    finally:
        event.remove(engine, 'before_cursor_execute', before_query)
        with App.app.app_context():
            flag = App.FeatureFlag.query.filter_by(key='school_course_generator').one()
            if created:
                App.db.session.delete(flag)
            else:
                flag.enabled = prior
            App.db.session.commit()
    with App.app.app_context():
        assert M['Lesson'].query.filter_by(course_id=cid).count() == 1
        assert M['Job'].query.filter_by(course_id=cid).one().state == 'queued'


def test_pg_concurrent_shared_budget_reservations_cannot_overspend(pg_scope):
    ready = Barrier(2)
    engine, M = pg_scope['engine'], pg_scope['M']
    def before_query(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO learning_monthly_budgets '):
            ready.wait(timeout=10)
    def reserve(uid):
        with App.app.app_context():
            try:
                governance.reserve_tokens(App.db.session.get(App.User, uid), 600)
                App.db.session.commit()
                return 'reserved'
            except ValueError:
                App.db.session.rollback()
                return 'denied'
    event.listen(engine, 'before_cursor_execute', before_query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(reserve, uid) for uid in pg_scope['uids']]
            assert sorted(result.result(timeout=15) for result in results) == ['denied', 'reserved']
    finally:
        event.remove(engine, 'before_cursor_execute', before_query)
    with App.app.app_context():
        assert M['Budget'].query.filter_by(organization_id=pg_scope['oids'][0]).one().reserved_tokens == 600


def test_pg_concurrent_session_starts_create_only_one_open_session(pg_scope):
    engine, M, uid = pg_scope['engine'], pg_scope['M'], pg_scope['uids'][0]
    leading_insert, release, trailing_query = Event(), Event(), Event()
    threads = {}

    def before_query(conn, cursor, statement, parameters, context, executemany):
        if get_ident() == threads.get('leading') and statement.startswith('INSERT INTO learning_sittings '):
            leading_insert.set()
            assert release.wait(10)
        if get_ident() == threads.get('trailing') and statement.startswith('INSERT INTO learning_sittings '):
            trailing_query.set()

    def start(which):
        with App.app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'], session['_fresh'] = str(uid), True
            threads[which] = get_ident()
            response = client.post('/api/learning/sessions', json={'objective': 'Synthetic concurrent recall'})
            return response.status_code, response.json

    event.listen(engine, 'before_cursor_execute', before_query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            leading = pool.submit(start, 'leading')
            assert leading_insert.wait(10)
            trailing = pool.submit(start, 'trailing')
            try:
                assert trailing_query.wait(10)
                # The trailing request has also passed the open-session read.
                trailing_status, trailing_body = trailing.result(timeout=10)
            finally:
                release.set()
            leading_status, leading_body = leading.result(timeout=15)
            assert (leading_status, trailing_status) == (409, 201)
            assert leading_body['error'] == 'conflict'
            assert trailing_body['state'] == 'active'
    finally:
        release.set()
        event.remove(engine, 'before_cursor_execute', before_query)
    with App.app.app_context():
        assert M['Sitting'].query.filter(M['Sitting'].user_id == uid,
                                       M['Sitting'].state.in_(['active', 'paused'])).count() == 1


def test_pg_completion_cannot_be_resurrected_by_inflight_resume(pg_scope):
    engine, M, uid = pg_scope['engine'], pg_scope['M'], pg_scope['uids'][0]
    with App.app.app_context():
        sitting = M['Sitting'](user_id=uid, objective='Synthetic completed recall', method='retrieval',
            planned_minutes=25, intervention='pause', state='paused', elapsed_seconds=120, resumed_at=None)
        App.db.session.add(sitting)
        App.db.session.commit()
        sid = sitting.id
    updated, release, attempting, finished = Event(), Event(), Event(), Event()
    threads = {}

    def after_query(conn, cursor, statement, parameters, context, executemany):
        if get_ident() == threads.get('complete') and statement.startswith('UPDATE learning_sittings SET '):
            updated.set()
            assert release.wait(10)

    def before_query(conn, cursor, statement, parameters, context, executemany):
        if get_ident() == threads.get('resume') and statement.startswith('UPDATE learning_sittings SET '):
            attempting.set()

    def checkpoint(kind):
        try:
            with App.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'], session['_fresh'] = str(uid), True
                threads[kind] = get_ident()
                response = client.post(f'/api/learning/sessions/{sid}/checkpoint', json={'kind': kind})
                return response.status_code, response.json
        finally:
            if kind == 'resume':
                finished.set()

    event.listen(engine, 'after_cursor_execute', after_query)
    event.listen(engine, 'before_cursor_execute', before_query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            completing = pool.submit(checkpoint, 'complete')
            assert updated.wait(10)
            resuming = pool.submit(checkpoint, 'resume')
            try:
                assert attempting.wait(10)
                assert not finished.wait(0.2)
            finally:
                release.set()
            complete_status, complete_body = completing.result(timeout=15)
            resume_status, resume_body = resuming.result(timeout=15)
            assert complete_status == 200 and complete_body['state'] == 'completed'
            assert complete_body['demonstrated_mastery'] is False
            assert resume_status == 409 and resume_body['error'] == 'session_changed'
    finally:
        release.set()
        event.remove(engine, 'after_cursor_execute', after_query)
        event.remove(engine, 'before_cursor_execute', before_query)
    with App.app.app_context():
        saved = App.db.session.get(M['Sitting'], sid)
        assert saved.state == 'completed' and saved.resumed_at is None
        assert saved.elapsed_seconds == 120
        evidence = M['Evidence'].query.filter_by(user_id=uid, session_id=sid).one()
        assert evidence.kind == 'complete' and evidence.correct is None
