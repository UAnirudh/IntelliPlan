"""CLI lifecycle, queue recovery and privacy without external generation."""
import json
import os
import signal
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

import App
from intelliplan.learning import courses, policy, worker


@pytest.fixture
def queued_course(monkeypatch):
    with App.app.app_context():
        App.db.create_all()
        flag = App.FeatureFlag.query.filter_by(key='school_course_generator').first()
        created_flag = flag is None
        previous_enabled = flag.enabled if flag else False
        if flag is None:
            flag = App.FeatureFlag(key='school_course_generator')
            App.db.session.add(flag)
        flag.enabled = True
        owner = App.User(email=f'worker-{uuid4().hex}@example.test', password_hash='x')
        App.db.session.add(owner)
        App.db.session.flush()
        uid, email = owner.id, owner.email
        course = policy.models()['Course'](user_id=uid, mode='curated', state='generating',
            goal='Python foundations', blueprint=courses.create_blueprint('Python foundations', 'curated'))
        App.db.session.add(course)
        App.db.session.flush()
        cid = course.id
        App.db.session.add(policy.models()['Job'](course_id=cid))
        App.db.session.commit()
    monkeypatch.setattr(App, 'ADMIN_EMAILS', App.ADMIN_EMAILS | {email})
    yield cid
    with App.app.app_context():
        for name in ('Lesson', 'Job'):
            policy.models()[name].query.filter_by(course_id=cid).delete()
        policy.models()['Course'].query.filter_by(id=cid).delete()
        App.User.query.filter_by(id=uid).delete()
        flag = App.FeatureFlag.query.filter_by(key='school_course_generator').one()
        if created_flag:
            App.db.session.delete(flag)
        else:
            flag.enabled = previous_enabled
        App.db.session.commit()


def test_batch_cli_drains_real_curated_queue_and_keeps_review_required(queued_course):
    result = App.app.test_cli_runner().invoke(args=['learning-worker', '--steps', '25'])
    assert result.exit_code == 0, result.output
    assert 'Processed 7 generation steps.' in result.output
    with App.app.app_context():
        M = policy.models()
        assert M['Lesson'].query.filter_by(course_id=queued_course, reviewed=False).count() == 6
        assert App.db.session.get(M['Course'], queued_course).state == 'needs_review'
        job = M['Job'].query.filter_by(course_id=queued_course).one()
        assert job.state == 'completed' and job.lease is None
        assert job.attempts == 0 and job.reserved_tokens == 0
    empty = App.app.test_cli_runner().invoke(args=['learning-worker'])
    assert empty.exit_code == 0 and 'Processed 0 generation steps.' in empty.output


def test_watch_stops_after_checkpoint_and_next_process_resumes(queued_course, monkeypatch):
    stop = Event()
    monkeypatch.setattr(worker, 'Event', lambda: stop)
    real_step = courses.run_one_job
    count = 0
    def step():
        nonlocal count
        result = real_step()
        count += 1
        if count == 2:
            stop.set()
        return result
    monkeypatch.setattr(courses, 'run_one_job', step)
    result = App.app.test_cli_runner().invoke(args=['learning-worker', '--watch', '--steps', '25'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output.splitlines()[-1]) == {'event': 'worker_stopped', 'total_steps': 2}
    with App.app.app_context():
        M = policy.models()
        first_ids = {row.id for row in M['Lesson'].query.filter_by(course_id=queued_course)}
        assert len(first_ids) == 2
        assert M['Job'].query.filter_by(course_id=queued_course).one().state == 'queued'
    monkeypatch.setattr(courses, 'run_one_job', real_step)
    stop.clear()
    resumed = App.app.test_cli_runner().invoke(args=['learning-worker', '--steps', '25'])
    assert resumed.exit_code == 0 and 'Processed 5 generation steps.' in resumed.output
    with App.app.app_context():
        rows = policy.models()['Lesson'].query.filter_by(course_id=queued_course).all()
        assert len(rows) == 6 and first_ids <= {row.id for row in rows}


def test_watch_waits_on_empty_queue_then_processes_new_work(monkeypatch):
    class Stop:
        requested = False
        waits = []
        def is_set(self):
            return self.requested
        def set(self):
            self.requested = True
        def wait(self, seconds):
            self.waits.append(seconds)
    stop = Stop()
    calls = 0
    def step():
        nonlocal calls
        calls += 1
        if calls == 3:
            stop.set()
        return calls > 1
    monkeypatch.setattr(worker, 'Event', lambda: stop)
    monkeypatch.setattr(courses, 'run_one_job', step)
    result = App.app.test_cli_runner().invoke(args=['learning-worker', '--watch', '--poll-seconds', '0.2'])
    assert result.exit_code == 0, result.output
    assert stop.waits == [0.2]
    assert calls == 3
    events = [json.loads(line) for line in result.output.splitlines()]
    assert events[-1] == {'event': 'worker_stopped', 'total_steps': 2}


@pytest.mark.parametrize('raised', [RuntimeError('PRIVATE TOKEN'), ValueError('PRIVATE PROMPT')])
def test_unexpected_failure_exits_nonzero_without_logging_sensitive_exception(monkeypatch, raised):
    monkeypatch.setattr(courses, 'run_one_job', Mock(side_effect=raised))
    remove = Mock()
    monkeypatch.setattr(App.db.session, 'remove', remove)
    result = App.app.test_cli_runner().invoke(args=['learning-worker', '--watch'])
    assert result.exit_code == 1
    assert 'PRIVATE' not in result.output
    assert 'worker_failed' in result.output
    remove.assert_called()


@pytest.mark.parametrize('signum', [signal.SIGINT, signal.SIGTERM])
def test_shutdown_signal_requests_stop_and_restores_previous_handlers(signum):
    previous = {value: signal.getsignal(value) for value in (signal.SIGINT, signal.SIGTERM)}
    stop = Event()
    with worker.shutdown_signals(stop):
        signal.getsignal(signum)(signum, None)
        assert stop.is_set()
    assert {value: signal.getsignal(value) for value in previous} == previous


def test_signal_setup_failure_restores_handlers_already_installed(monkeypatch):
    installed = []
    originals = {signal.SIGINT: object(), signal.SIGTERM: object()}
    def set_signal(signum, handler):
        if signum == signal.SIGTERM:
            raise ValueError('not in main thread')
        installed.append((signum, handler))
    monkeypatch.setattr(signal, 'getsignal', originals.get)
    monkeypatch.setattr(signal, 'signal', set_signal)
    with pytest.raises(Exception, match='main process thread'):
        with worker.shutdown_signals(Event()):
            pytest.fail('setup must fail')
    assert installed[-1] == (signal.SIGINT, originals[signal.SIGINT])


@pytest.mark.parametrize('arguments', [
    ['--steps', '0'], ['--steps', '1001'], ['--poll-seconds', '0'], ['--poll-seconds', '61'],
    ['--poll-seconds', 'nan'], ['--poll-seconds', 'inf']])
def test_invalid_cli_bounds_do_not_start_work(monkeypatch, arguments):
    step = Mock()
    monkeypatch.setattr(courses, 'run_one_job', step)
    result = App.app.test_cli_runner().invoke(args=['learning-worker', *arguments])
    assert result.exit_code == 2
    step.assert_not_called()


def test_dedicated_entry_disables_unrelated_timers_before_loading_flask(monkeypatch):
    monkeypatch.setenv('FOLLOWTHROUGH_INPROCESS_CRON', '1')
    monkeypatch.setenv('NOTIFICATIONS_INPROCESS_CRON', '1')
    monkeypatch.setattr(worker.sys, 'argv', ['worker', '--watch', '--steps', '25'])
    def cli(*, args):
        assert args == ['--app', 'App', 'learning-worker', '--watch', '--steps', '25']
        assert os.environ['FOLLOWTHROUGH_INPROCESS_CRON'] == '0'
        assert os.environ['NOTIFICATIONS_INPROCESS_CRON'] == '0'
    fake_cli = Mock(side_effect=cli)
    monkeypatch.setattr('flask.cli.cli.main', fake_cli)
    worker.main()
    fake_cli.assert_called_once()
