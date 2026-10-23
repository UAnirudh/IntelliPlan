"""Prepare a private PC database; never connects to or changes production.

Secrets and archives live outside the checkout. Backup uses one exported
read-only snapshot for both pg_dump and table fingerprints. restore-check
restores only in a new, network-disabled temporary container, never the live DB.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'deployment/self-hosted-postgres/compose.json'
PROJECT = 'intelliplan-pc'
IMAGE = 'postgres:17.11-bookworm'
DATABASE = 'intelliplan'
SETTINGS = "SET TimeZone='UTC'; SET DateStyle='ISO, MDY'; SET IntervalStyle='postgres'; SET extra_float_digits=1;"
TABLES = "SELECT schemaname, tablename FROM pg_tables WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema' ORDER BY 1,2"
_docker_endpoint: str | None = None


def default_state() -> Path:
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share')))
    return base / 'IntelliPlan' / 'PostgreSQL'


def safe_state(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() or any(c in str(candidate) for c in '\n\r,'):
        raise ValueError('State directory must be an absolute physical path without commas or newlines.')
    resolved = candidate.resolve()
    if candidate.absolute() != resolved:
        raise ValueError('State directory may not traverse a symbolic link or junction.')
    if resolved == Path(resolved.anchor) or resolved == Path.home() or resolved in ROOT.parents or resolved == ROOT or ROOT in resolved.parents:
        raise ValueError('State must be a dedicated directory outside the repository, home root, and drive root.')
    return resolved


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'nt':
        identity = os.environ.get('USERDOMAIN', '') + '\\' + getpass.getuser()
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        identity + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    else:
        path.chmod(0o700)


def state_child(state: Path, relative: str) -> Path:
    candidate = state / relative
    resolved = candidate.resolve()
    if resolved != candidate.absolute() or not resolved.is_relative_to(state):
        raise ValueError('State contents may not traverse symbolic links or junctions.')
    return candidate


def exclusive_write(path: Path, content: str) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def initialize(state: Path) -> None:
    private_directory(state)
    secret_dir = state_child(state, 'secrets')
    private_directory(secret_dir)
    expected = [state_child(state, 'secrets/' + name) for name in ('postgres_password', 'app_password', 'session_key')]
    marker = state_child(state, 'settings.json')
    state_child(state, 'app.env')
    if marker.exists():
        validate_state(state)
        print('Setup already exists; credentials and persistent data were preserved.')
        return
    if any(path.exists() for path in expected) or (state / 'app.env').exists():
        raise ValueError('Partial setup found. Recover its saved files; refusing to replace any credential.')
    for path in expected:
        exclusive_write(path, secrets.token_urlsafe(48))
    exclusive_write(state / 'app.env', '# Local validation only. Set your HTTPS canonical URL before a public cutover.\nAPP_BASE_URL=http://localhost:8080\n')
    exclusive_write(marker, json.dumps({'version': 1, 'project': PROJECT, 'image': IMAGE}, indent=2) + '\n')
    print(f'Private configuration prepared at {state}. No database has been started or copied.')


def validate_state(state: Path) -> None:
    marker = json.loads(state_child(state, 'settings.json').read_text(encoding='utf-8'))
    if marker != {'version': 1, 'project': PROJECT, 'image': IMAGE}:
        raise ValueError('Setup version/image differs. Review the upgrade before starting.')
    for name in ('postgres_password', 'app_password', 'session_key'):
        path = state_child(state, 'secrets/' + name)
        if path.is_symlink() or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', path.read_text().strip()):
            raise ValueError('Missing or invalid secret; existing database passwords are never regenerated automatically.')
    if not state_child(state, 'app.env').is_file():
        raise ValueError('Optional app configuration is missing.')


def run(args: list[str], *, env=None, output=None, input_data=None, timeout=600) -> bytes:
    # Context inspection reads local CLI metadata and never contacts a daemon.
    # Every daemon command is pinned, so a context/env change cannot redirect
    # a database archive or a service operation to someone else's Docker host.
    if args[0] == 'docker' and args[1:3] != ['context', 'inspect']:
        if _docker_endpoint is None:
            raise ValueError('A local Docker endpoint must be verified before running a Docker command.')
        args = ['docker', '--host', _docker_endpoint, *args[1:]]
        env = dict(os.environ if env is None else env)
        for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            env.pop(key, None)
    result = subprocess.run(args, env=env, input=input_data,
                            stdout=output if output is not None else subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=timeout)
    if result.returncode:
        # A provider/tool error can contain configuration; do not echo it.
        raise RuntimeError('A Docker/PostgreSQL command failed. Check Docker Desktop and the selected local service; credentials were not printed.')
    return result.stdout or b''


def validate_docker_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if any(character in endpoint for character in '\n\r\x00'):
        raise ValueError('Docker endpoint contains invalid characters.')
    # Named pipes must use the local-machine dot, never a remote UNC host.
    if re.fullmatch(r'npipe:/{4}\./pipe/[A-Za-z0-9_.-]+', endpoint):
        return endpoint
    parsed = urlsplit(endpoint)
    if (parsed.scheme == 'unix' and not parsed.netloc and parsed.path.startswith('/')
            and not parsed.query and not parsed.fragment and '%' not in parsed.path):
        return endpoint
    raise ValueError('Self-hosting requires a local Docker named pipe or Unix socket. TCP, SSH and remote contexts are refused.')


def select_local_docker() -> str:
    global _docker_endpoint
    _docker_endpoint = None
    override = os.environ.get('DOCKER_HOST', '').strip()
    if override:
        validate_docker_endpoint(override)
    context = os.environ.get('DOCKER_CONTEXT', '').strip()
    if context and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', context):
        raise ValueError('Docker context name is invalid.')
    if context or not override:
        arguments = ['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}']
        if context:
            arguments.append(context)
        endpoint = run(arguments, timeout=20).decode().strip()
    else:
        endpoint = override
    _docker_endpoint = validate_docker_endpoint(endpoint)
    return _docker_endpoint


def compose(state: Path, *args: str, output=None, timeout=600) -> bytes:
    env = dict(os.environ, INTELLIPLAN_PG_STATE=str(state))
    return run(['docker', 'compose', '--project-name', PROJECT, '--file', str(COMPOSE), *args], env=env, output=output, timeout=timeout)


def docker_ready() -> None:
    select_local_docker()
    if run(['docker', 'info', '--format', '{{.OSType}}'], timeout=20).strip() != b'linux':
        raise ValueError('Docker Desktop must be running in Linux-container mode (WSL2).')
    version = run(['docker', 'version', '--format', '{{.Server.Version}}'], timeout=20).decode().strip()
    if not version.split('.')[0].isdigit() or int(version.split('.')[0]) < 28:
        raise ValueError('Docker Engine 28 or later is required for correct localhost-port isolation. Update Docker Desktop.')
    run(['docker', 'compose', 'version'])


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def fingerprint_query(schema: str, table: str) -> str:
    qualified = identifier(schema) + '.' + identifier(table)
    return ("SELECT json_build_object('rows',count(*),'digest',coalesce(encode(sha256(convert_to("
            "string_agg(h,'' ORDER BY h),'UTF8')),'hex'),'')) FROM (SELECT "
            "encode(sha256(convert_to(t::text,'UTF8')),'hex') AS h FROM " + qualified + ' t) rows')


def live_snapshot(state: Path):
    import psycopg2
    connection = psycopg2.connect(host='127.0.0.1', port=5432, dbname=DATABASE, user='postgres',
                                  password=(state / 'secrets/postgres_password').read_text().strip(), connect_timeout=10)
    connection.set_session(readonly=True, isolation_level='REPEATABLE READ', autocommit=False)
    return connection


def backup(state: Path) -> Path:
    backup_dir = state_child(state, 'backups')
    private_directory(backup_dir)
    filename = 'intelliplan-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4)
    partial, archive = backup_dir / (filename + '.partial'), backup_dir / (filename + '.dump')
    connection = live_snapshot(state)
    try:
        with connection.cursor() as cursor:
            cursor.execute(SETTINGS)
            cursor.execute('SELECT pg_export_snapshot()')
            snapshot = cursor.fetchone()[0]
            cursor.execute(TABLES)
            names = cursor.fetchall()
            tables = []
            for schema, table in names:
                cursor.execute(fingerprint_query(schema, table))
                tables.append({'schema': schema, 'table': table, **cursor.fetchone()[0]})
        with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            compose(state, 'exec', '-T', '--user', 'postgres', 'db', 'pg_dump', '--format=custom',
                    '--no-owner', '--no-privileges', '--snapshot=' + snapshot, '--dbname=' + DATABASE, output=stream, timeout=3600)
            stream.flush()
            os.fsync(stream.fileno())
        with partial.open('rb') as stream:
            header = stream.read(5)
        if header != b'PGDMP':
            raise ValueError('Archive is not a PostgreSQL custom dump; partial file preserved for inspection.')
        partial.rename(archive)
        metadata = {'version': 1, 'image': IMAGE, 'database': DATABASE, 'sha256': file_hash(archive), 'tables': tables}
        exclusive_write(archive.with_suffix('.json'), json.dumps(metadata, indent=2) + '\n')
    finally:
        connection.rollback()
        connection.close()
    print(f'Consistent archive and fingerprints saved: {archive}. Run restore-check and copy both files off this PC.')
    return archive


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def restore_check(archive: Path) -> dict:
    archive = archive.resolve(strict=True)
    if archive.is_relative_to(ROOT):
        raise ValueError('Use a dedicated private backup directory outside the checkout.')
    metadata = json.loads(archive.with_suffix('.json').read_text(encoding='utf-8'))
    if metadata.get('version') != 1 or metadata.get('image') != IMAGE or metadata.get('sha256') != file_hash(archive):
        raise ValueError('Archive metadata or checksum mismatch; nothing was restored.')
    expected = metadata.get('tables')
    if not isinstance(expected, list) or len(expected) > 10000 or any(
        not isinstance(row, dict) or not isinstance(row.get('schema'), str) or not isinstance(row.get('table'), str)
        or not isinstance(row.get('rows'), int) or row['rows'] < 0
        or not isinstance(row.get('digest'), str) or not re.fullmatch(r'(?:[0-9a-f]{64})?', row['digest'])
        for row in expected
    ):
        raise ValueError('Archive fingerprint metadata is invalid; nothing was restored.')
    # No host ports, network, secrets or production volume. Its only data mount
    # is disposable RAM; trust authentication is confined to this container.
    container = run(['docker', 'run', '--detach', '--rm', '--network', 'none', '--memory', '6g',
                     '--shm-size', '256m', '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,size=4294967296',
                     '--label', 'intelliplan.restore-check=true', '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
                     '--env', 'POSTGRES_DB=intelliplan_restore', IMAGE, 'postgres', '-c', 'shared_buffers=128MB']).decode().strip()
    if not re.fullmatch(r'[0-9a-f]{64}', container):
        raise ValueError('Docker did not return a valid isolated container ID.')
    try:
        for attempt in range(60):
            try:
                run(['docker', 'exec', '--user', 'postgres', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'intelliplan_restore'])
                break
            except RuntimeError:
                if attempt == 59:
                    raise
                time.sleep(1)
        run(['docker', 'cp', str(archive), container + ':/tmp/verify.dump'])
        run(['docker', 'exec', container, 'chown', 'postgres:postgres', '/tmp/verify.dump'])
        run(['docker', 'exec', '--user', 'postgres', container, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
             '-d', 'intelliplan_restore', '-c', 'CREATE ROLE intelliplan NOLOGIN'])
        run(['docker', 'exec', '--user', 'postgres', container, 'pg_restore', '--exit-on-error', '--single-transaction',
             '--no-owner', '--no-privileges', '--dbname=intelliplan_restore', '/tmp/verify.dump'], timeout=3600)
        def query(statement):
            raw = run(['docker', 'exec', '--user', 'postgres', container, 'psql', '-X', '-q', '-A', '-t',
                       '-v', 'ON_ERROR_STOP=1', '-d', 'intelliplan_restore', '-c', SETTINGS + statement])
            return json.loads(raw.decode().strip())
        names = query("SELECT coalesce(json_agg(t),'[]') FROM (" + TABLES + ') t')
        if {(row['schemaname'], row['tablename']) for row in names} != {(row['schema'], row['table']) for row in expected}:
            raise ValueError('Restored table inventory differs from the backup snapshot.')
        for row in expected:
            actual = query(fingerprint_query(row['schema'], row['table']))
            if actual != {'rows': row['rows'], 'digest': row['digest']}:
                raise ValueError('Restored table fingerprint mismatch; the backup is not verified.')
        result = {'verified': True, 'tables': len(expected), 'rows': sum(row['rows'] for row in expected),
                  'archive_sha256': metadata['sha256'], 'checked_at': datetime.now(timezone.utc).isoformat()}
        exclusive_write(archive.with_name(archive.stem + '-verified-' + secrets.token_hex(4) + '.json'), json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        return result
    finally:
        # Only the exact new --rm container above is stopped. No named volume,
        # source database, existing service or backup is removed.
        try:
            run(['docker', 'stop', container])
        except Exception:
            raise RuntimeError('Stop the isolated restore-check container in Docker Desktop: ' + container + '. The real database was not changed.') from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=['init', 'start', 'start-app', 'status', 'client', 'backup', 'restore-check'])
    parser.add_argument('--state-dir', default=str(default_state()))
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args(argv)
    try:
        state = safe_state(args.state_dir)
        if args.command == 'init':
            # Local context metadata is checked before writing any state.
            select_local_docker()
            initialize(state)
            return 0
        validate_state(state)
        docker_ready()
        if args.command == 'start':
            compose(state, 'up', '--detach', '--wait', 'db')
            print('Private PostgreSQL is ready at 127.0.0.1:5432. No production setting was changed.')
        elif args.command == 'start-app':
            compose(state, '--profile', 'local-app', 'up', '--detach', '--build', '--wait', 'web', timeout=3600)
            print('Optional same-PC app is bound to http://localhost:8080; configure HTTPS before any public cutover.')
        elif args.command == 'status':
            print(compose(state, 'ps').decode())
        elif args.command == 'client':
            print('Use Docker Desktop Exec as postgres, or psql with 127.0.0.1:5432/intelliplan; passwords stay in the protected secrets directory.')
        elif args.command == 'backup':
            backup(state)
        elif args.command == 'restore-check':
            if not args.archive:
                raise ValueError('--archive is required for restore-check.')
            restore_check(args.archive)
        return 0
    except Exception as error:
        print(str(error) if isinstance(error, (ValueError, RuntimeError)) else f'Local setup failed ({type(error).__name__}); credentials were not printed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
