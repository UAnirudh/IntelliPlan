"""Private setup and recovery orchestration; Docker is never invoked by these tests."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('self_hosted_postgres', ROOT / 'scripts/self_hosted_postgres.py')
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)


def test_compose_keeps_database_private_and_app_unprivileged():
    config = json.loads(pg.COMPOSE.read_text())
    database, app = config['services']['db'], config['services']['web']
    assert database['image'] == pg.IMAGE == 'postgres:17.11-bookworm'
    assert database['ports'] == ['127.0.0.1:5432:5432']
    assert config['networks']['database']['internal'] is True
    assert database['environment']['POSTGRES_PASSWORD_FILE'] == '/run/secrets/postgres_password'
    assert 'postgres_data:/var/lib/postgresql/data' in database['volumes']
    assert app['profiles'] == ['local-app']
    assert app['ports'] == ['127.0.0.1:8080:8080']
    assert 'postgres_password' not in app['secrets']
    assert all('INTELLIPLAN_PG_STATE' in secret['file'] for secret in config['secrets'].values())


@pytest.mark.parametrize('path', [ROOT, ROOT / 'secrets', ROOT.parent, Path.home(), Path(ROOT.anchor), Path('relative')])
def test_state_rejects_repository_roots_and_relative_paths(path):
    with pytest.raises(ValueError):
        pg.safe_state(path)


def test_initialization_preserves_credentials_and_never_prints_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pg, 'private_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    state = pg.safe_state(tmp_path / 'private-state')
    pg.initialize(state)
    original = {name: (state / 'secrets' / name).read_text() for name in ('postgres_password', 'app_password', 'session_key')}
    assert len(set(original.values())) == 3 and all(len(secret) >= 48 for secret in original.values())
    pg.initialize(state)
    assert original == {name: (state / 'secrets' / name).read_text() for name in original}
    output = capsys.readouterr().out
    assert all(secret not in output for secret in original.values())
    pg.validate_state(state)


def test_partial_setup_does_not_replace_a_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, 'private_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    state = tmp_path / 'private-state'
    (state / 'secrets').mkdir(parents=True)
    saved = state / 'secrets/app_password'
    saved.write_text('user-saved-do-not-replace')
    with pytest.raises(ValueError, match='Partial setup'):
        pg.initialize(state)
    assert saved.read_text() == 'user-saved-do-not-replace'


def test_state_rejects_relocated_child_before_reading(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    candidate = state / 'secrets'
    original = Path.resolve
    monkeypatch.setattr(Path, 'resolve', lambda path, *a, **k: tmp_path / 'elsewhere' if path == candidate else original(path, *a, **k))
    with pytest.raises(ValueError, match='symbolic'):
        pg.state_child(state, 'secrets')


def archive_fixture(tmp_path):
    archive = tmp_path / 'backup.dump'
    archive.write_bytes(b'PGDMP-fixture-only-not-a-real-backup')
    metadata = {'version': 1, 'image': pg.IMAGE, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'tables': [{'schema': 'public', 'table': 'students', 'rows': 2, 'digest': 'a' * 64}]}
    archive.with_suffix('.json').write_text(json.dumps(metadata))
    return archive, metadata


def test_bad_archive_checksum_is_rejected_before_docker(tmp_path, monkeypatch):
    archive, _ = archive_fixture(tmp_path)
    archive.write_bytes(b'changed')
    monkeypatch.setattr(pg, 'run', lambda *_a, **_k: pytest.fail('Docker must not run'))
    with pytest.raises(ValueError, match='checksum'):
        pg.restore_check(archive)


def test_restore_runs_only_in_new_isolated_container_and_compares_content(tmp_path, monkeypatch):
    archive, metadata = archive_fixture(tmp_path)
    commands, container = [], 'b' * 64

    def fake_run(arguments, **_kwargs):
        commands.append(arguments)
        if arguments[:2] == ['docker', 'run']:
            return container.encode()
        if 'psql' in arguments:
            if 'CREATE ROLE' in arguments[-1]:
                return b''
            if 'json_agg' in arguments[-1]:
                return b'[{"schemaname":"public","tablename":"students"}]'
            return json.dumps({'rows': 2, 'digest': metadata['tables'][0]['digest']}).encode()
        return b''

    monkeypatch.setattr(pg, 'run', fake_run)
    result = pg.restore_check(archive)
    assert result['verified'] is True and result['rows'] == 2
    start = commands[0]
    assert start[start.index('--network') + 1] == 'none'
    assert '--publish' not in start and '--mount' not in start and '--volume' not in start
    assert '--rm' in start and '--tmpfs' in start
    assert all('compose' not in command for command in commands)
    restore = next(command for command in commands if 'pg_restore' in command)
    assert container in restore and '--single-transaction' in restore and '--exit-on-error' in restore
    assert commands[-1] == ['docker', 'stop', container]
    assert archive.exists() and archive.with_suffix('.json').exists()


def test_restore_failure_stops_only_new_container_and_keeps_archive(tmp_path, monkeypatch):
    archive, _ = archive_fixture(tmp_path)
    commands, container = [], 'c' * 64

    def fake_run(arguments, **_kwargs):
        commands.append(arguments)
        if arguments[:2] == ['docker', 'run']:
            return container.encode()
        if 'pg_restore' in arguments:
            raise RuntimeError('restore failed')
        return b''

    monkeypatch.setattr(pg, 'run', fake_run)
    with pytest.raises(RuntimeError, match='restore failed'):
        pg.restore_check(archive)
    assert commands[-1] == ['docker', 'stop', container]
    assert archive.exists() and not list(tmp_path.glob('*-verified-*.json'))


def test_backup_uses_same_exported_readonly_snapshot_for_dump_and_fingerprints(tmp_path, monkeypatch):
    commands = []

    class Cursor:
        statement = ''

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            self.statement = statement

        def fetchone(self):
            return ('snapshot-fixture',) if 'pg_export_snapshot' in self.statement else ({'rows': 1, 'digest': 'd' * 64},)

        def fetchall(self):
            return [('public', 'students')]

    class Connection:
        closed = False
        rolled_back = False

        def cursor(self):
            return Cursor()

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    connection = Connection()

    def fake_compose(state, *arguments, output=None, **_kwargs):
        commands.append(arguments)
        output.write(b'PGDMP-fixture-snapshot')

    monkeypatch.setattr(pg, 'private_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(pg, 'live_snapshot', lambda _state: connection)
    monkeypatch.setattr(pg, 'compose', fake_compose)
    archive = pg.backup(tmp_path)
    assert '--snapshot=snapshot-fixture' in commands[0]
    metadata = json.loads(archive.with_suffix('.json').read_text())
    assert metadata['tables'][0]['rows'] == 1
    assert metadata['sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert connection.closed and connection.rolled_back


def test_newer_engine_required_before_starting_any_services(monkeypatch):
    commands = []

    def fake_run(arguments, **_kwargs):
        commands.append(arguments)
        return b'linux' if 'info' in arguments else b'27.5.0'

    monkeypatch.setattr(pg, 'run', fake_run)
    monkeypatch.setattr(pg, 'select_local_docker', lambda: 'unix:///var/run/docker.sock')
    with pytest.raises(ValueError, match='28 or later'):
        pg.docker_ready()
    assert not any('up' in command for command in commands)


@pytest.mark.parametrize('endpoint', ['tcp://127.0.0.1:2375', 'tcp://remote:2376', 'ssh://user@remote', 'npipe:////server/pipe/docker_engine', 'unix://remote/docker.sock'])
def test_remote_endpoint_formats_are_refused(endpoint):
    with pytest.raises(ValueError, match='local Docker'):
        pg.validate_docker_endpoint(endpoint)


@pytest.mark.parametrize('endpoint', ['npipe:////./pipe/docker_engine', 'npipe:////./pipe/dockerDesktopLinuxEngine', 'unix:///var/run/docker.sock', 'unix:///run/user/1000/docker.sock'])
def test_local_endpoint_formats_are_supported(endpoint):
    assert pg.validate_docker_endpoint(endpoint) == endpoint


def test_remote_docker_host_override_refused_before_context_or_daemon(monkeypatch):
    monkeypatch.setenv('DOCKER_HOST', 'ssh://remote')
    monkeypatch.setattr(pg, 'run', lambda *_a, **_k: pytest.fail('No Docker command is allowed for a remote override'))
    with pytest.raises(ValueError, match='local Docker'):
        pg.select_local_docker()
    assert pg._docker_endpoint is None


@pytest.mark.parametrize('explicit_context', [False, True])
def test_remote_active_or_explicit_context_refused_before_daemon(monkeypatch, explicit_context):
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    if explicit_context:
        monkeypatch.setenv('DOCKER_CONTEXT', 'remote-production')
    else:
        monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    commands = []

    def inspect(arguments, **_kwargs):
        commands.append(arguments)
        return b'tcp://remote:2376'

    monkeypatch.setattr(pg, 'run', inspect)
    with pytest.raises(ValueError, match='local Docker'):
        pg.select_local_docker()
    assert len(commands) == 1 and commands[0][1:3] == ['context', 'inspect']
    assert pg._docker_endpoint is None


def test_local_context_selected_and_all_later_operations_are_pinned(monkeypatch):
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.setenv('DOCKER_CONTEXT', 'desktop-linux')
    commands = []

    def process(arguments, **options):
        commands.append((arguments, options))
        return SimpleNamespace(returncode=0, stdout=b'npipe:////./pipe/dockerDesktopLinuxEngine', stderr=b'')

    monkeypatch.setattr(pg.subprocess, 'run', process)
    monkeypatch.setattr(pg, '_docker_endpoint', None)
    assert pg.select_local_docker() == 'npipe:////./pipe/dockerDesktopLinuxEngine'
    # Changing ambient context after validation cannot redirect a transfer.
    monkeypatch.setenv('DOCKER_CONTEXT', 'remote-production')
    monkeypatch.setenv('DOCKER_HOST', 'tcp://remote:2376')
    pg.run(['docker', 'cp', 'private.dump', 'container:/tmp/verify.dump'])
    arguments, options = commands[-1]
    assert arguments[:3] == ['docker', '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine']
    assert 'DOCKER_CONTEXT' not in options['env'] and 'DOCKER_HOST' not in options['env']
    assert 'remote' not in ' '.join(arguments)


def test_app_launcher_supplies_private_url_from_secret_files_without_argv_or_output(monkeypatch, capsys):
    from urllib.parse import urlsplit, unquote

    app_spec = importlib.util.spec_from_file_location('self_hosted_app', ROOT / 'deployment/self-hosted-postgres/start_app.py')
    launcher = importlib.util.module_from_spec(app_spec)
    app_spec.loader.exec_module(launcher)
    password = 'fixture with spaces and symbols +/@' * 2
    session_key = 'session-fixture-only-' * 3
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
    monkeypatch.setenv('SECRET_KEY', 'fixture-before-launch')
    monkeypatch.setattr(Path, 'read_text', lambda path, **_kwargs: password if path.name == 'app_password' else session_key)
    command = []
    monkeypatch.setattr(launcher.os, 'execvp', lambda executable, arguments: command.extend([executable, *arguments]))
    launcher.main()
    target = urlsplit(launcher.os.environ['DATABASE_URL'])
    assert target.hostname == 'db' and target.username == 'intelliplan'
    assert unquote(target.password) == password and launcher.os.environ['SECRET_KEY'] == session_key
    assert password not in str(command) and session_key not in str(command)
    assert not capsys.readouterr().out
