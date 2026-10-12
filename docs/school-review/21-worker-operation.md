# Durable course-worker operation

The previous CLI exited when its bounded batch was empty. New courses waited for the operator's next invocation. This continuation adds explicit continuous operation while preserving the existing one-lesson leases, checkpoint storage, policy/provider gates, cancellation and conservative token reservations.

## Operation

The existing bounded command remains supported:

```powershell
python -m flask --app App learning-worker --steps 25
```

A dedicated process can now drain the queue, wait when idle and notice later jobs:

```powershell
python -m intelliplan.learning.worker --watch --steps 25 --poll-seconds 5
```

The dedicated module entry point disables `FOLLOWTHROUGH_INPROCESS_CRON` and `NOTIFICATIONS_INPROCESS_CRON` before importing App, so it cannot start unrelated planning/notification timers. With the legacy Flask command above, explicitly set both variables to0 before app startup for the same isolation. Existing web-service timer settings remain unchanged.

Watch mode installs SIGINT/SIGTERM handlers that request shutdown after the current durable step. It restores the previous handlers on exit and releases the ORM session after every step, including failures. Idle waiting is interruptible. Batch size is1–1000; polling must be finite and0.1–60 seconds. Neither web requests nor imports start a course worker.

Structured events report process start, processed step counts, stop and unexpected error classes. Counts include completion/recovery steps, not just newly generated lessons. Prompts, goals, credentials, identities and exception messages are omitted. Unexpected infrastructure failures exit nonzero for the supervisor; per-job failures retain the existing inspectable failed state and require explicit retry. Events do not prove provider accuracy or a production process's continued health.

## Optional supervisor configuration

[The opt-in Compose service](../../deployment/learning-worker.compose.yml) reuses the existing Dockerfile with the isolated module entry point, explicitly disabled unrelated timers, restart policy and120-second shutdown grace period. Configure the same approved PostgreSQL session connection, application secret, encryption/provider settings and administrator/feature-flag state as the web app. The existing `.env` is excluded from the image. Do not use a container-private SQLite file for a production worker: it would process a separate queue. The file exposes no HTTP port, contains no credentials and does not activate unless the profile or service is explicitly selected. Profile, command, restart and shutdown settings follow the [Docker Compose service reference](https://docs.docker.com/reference/compose-file/services/).

From the repository root, after reviewed configuration and restored-database validation:

```powershell
docker compose --env-file .env -f deployment/learning-worker.compose.yml --profile learning-worker up --build -d
docker compose --env-file .env -f deployment/learning-worker.compose.yml --profile learning-worker logs --follow
docker compose --env-file .env -f deployment/learning-worker.compose.yml --profile learning-worker stop
```

No container, supervisor or production worker has been started by this checkpoint. Docker is unavailable locally, so only YAML parsing/configuration review is performed. Railway can use the same explicit CLI command in a separately configured service; no Railway changes are made here.

Graceful shutdown is bounded by the platform's termination deadline and the provider's response. A forced stop may leave a running lease; the existing ten-minute expiry permits recovery. Do not shorten grace periods or run competing workers in production before validating lease/concurrency behavior on a restored PostgreSQL database.

## Verification and remaining proof

The targeted learning/Plani/worker suite passes **247 tests,2042 warnings in32.08s**. It executes an actual six-lesson curated queue in disposable SQLite, verifies review remains required, stops after two stored lessons, resumes in a new CLI invocation without replacing those IDs, and completes the remaining lessons. Polling, handler setup/restore, invalid numeric bounds, failure exit and sensitive-message suppression use declared stubs or directly invoked registered handlers. [Focused results](evidence/pytest-worker-targeted.txt).

Cold `python -m flask --app App learning-worker --help` succeeds with explicit test-only secret and in-memory database settings. The first help invocation correctly refused a missing SECRET_KEY; no production fallback was introduced. Changed Python Ruff and compilation pass. Independent Python and scoped spec reviews report no actionable findings. The preceding LMS full regression passed4183 tests /24 skipped before this CLI addition; it is not represented as a full run of the worker continuation.

Actual operating-system signal delivery, abrupt provider termination, a built/running supervisor, PostgreSQL concurrency and restored-schema validation, approved live provider output and production health alerts remain unverified. The full master brief and school-launch gates remain incomplete.
