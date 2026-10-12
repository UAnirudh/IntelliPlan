# Retention, account deletion and PostgreSQL verification — 2026-10-12

This continuation closes specific deletion/retention races and adds disposable PostgreSQL proof. It does not establish all-store retention, arbitrary text anonymization, production readiness or completion of the master brief.

## Implemented behavior

- Audit cleanup removes explicit integer/canonical-string account references under `user_id`, `target_user_id`, `actor_user_id`, `user_ids` and `target_user_ids`, including nested objects/arrays. Other users, unrelated numeric policy values, unknown fields and free text remain unchanged. Booleans are not treated as numeric account IDs. Input objects are not mutated.
- Audit records are scanned in200-row pages inside account deletion's existing transaction. Each update compares the raw stored JSON snapshot before writing. A conflicting update is reloaded and merged, with at most four attempts. Persistent conflicts fail the operation so its transaction rolls back; they cannot silently restore another removed reference.
- Account deletion locks the target User before child cleanup and audit scanning. PostgreSQL foreign-key insertion checks block new target memberships until deletion commits or rolls back. This prevents a membership/audit reference being inserted after the cleanup scan.
- Retention validates bounded candidate IDs and table names, locks applicable users, memberships, institution policies, sessions and evidence/feedback, then intersects the original snapshot with fresh eligibility. Extended/disabled policies, unfinished sessions and recent records are retained. Scope/ownership drift aborts before deleting. Malformed foreign-owner session children prevent parent pruning.
- Retention reports actual deleted counts, including evidence removed with expired completed sessions. Dry-run eligibility counts remain separate. Accounts, courses and open sessions remain outside this pruning command. The explicit `--apply --confirm-retention-policy` requirement remains; no automatic destructive scheduler is installed.
- Central policy scope reads refresh already-loaded membership rows, in addition to organization/flag refresh. A committed transfer cannot retain an old institution through the identity map.

PostgreSQL row locks are held through the apply/delete transaction. SQLite ignores `FOR UPDATE`, so its fixtures do not establish concurrent safety. Arbitrary email/name references, unstructured text, unknown JSON identity schemas and other data stores remain outside this cleanup. Large-tenant performance, deadlock/serialization retry strategy for other workflows, provider deletion propagation and production operation still require verification.

## Evidence

The final full disposable SQLite regression passes **4223 tests,31 skipped,10284 warnings in267.61s**, with exit0. Seven of those skips are the explicitly gated PostgreSQL cases, which were executed separately below. [Full result](evidence/pytest-retention-full.txt).

The scoped SQLite learning/account-deletion/worker/Plani run passes **283 tests,2352 warnings in13.72s** before the final account User-lock addition; the full result above includes that addition. Tests include205 nested audit records crossing page boundaries, preservation of another account, actual conflicting SQL writes and bounded rollback, stale retention policies/session state, malformed foreign ownership, current scope refresh and actual deletion counts. [Focused result](evidence/pytest-retention-targeted.txt).

PostgreSQL17.9 was found under the existing local installation. A new disposable cluster was initialized in the ignored verification directory and listened only on127.0.0.1:55543. No existing cluster, production database, student record, vendor credential or live provider was used. Unrelated application timers were disabled.

The fresh PostgreSQL run passes **290 tests,2401 warnings in57.50s**. Its seven independent-connection cases exercise:

1. Two concurrent audit cleanups preserve both removed account references.
2. A different-school membership/audit insert attempted after the deletion scan blocks, then fails its User FK after deletion; no new audit reference remains.
3. A policy change waits until retention commits under the locked prior policy.
4. Both account-deletion/retention orderings complete safely. The final tests explicitly observe the trailing User-lock SQL and its blocked operation before releasing the leader.
5. Two simultaneous worker claims produce one curated lesson checkpoint.
6. Two600-token reservations against one1000-token school allowance admit one reservation and retain600 tokens.

The parameterized ordering case supplies two of the seven tests. An initial PG run exposed a test-only sequence collision after deliberately inserting explicit primary keys. That ownership-regression fixture now advances its synthetic PostgreSQL sequences; no application sequence-repair behavior was added. [PostgreSQL result](evidence/pytest-postgres-targeted.txt).

A native `pg_dump` custom backup was restored into a second fresh fixture database. The independent migration verifier compares **127 tables,692 synthetic rows and124 sequences**, with identical row counts/content fingerprints and caught-up sequence positions. Comparison occurred before later fixture mutations. [Comparison](evidence/postgres-restore-comparison.txt).

The restored database then passes **22 worker/concurrency tests,71 warnings in17.26s**, including the strengthened lock-order proof. This validates a restored synthetic schema and those workflows, not restored production data. [Restored results](evidence/pytest-postgres-restored.txt). The temporary server was stopped and port55543 verified closed; the ignored synthetic backup remains available locally.

The fresh browser harness exits0 with16 HTTP/storage workflows,27 responsive checks,27 scoped axe checks and no JavaScript errors. Provider output remains declared fixtures; raw manual accessibility cases remain recorded. Changed Python Ruff/compilation and whitespace checks pass. Independent Python/source and scoped spec reviews completed after fixes for lost audit updates, retention policy races, late membership references and the lock-test overlap gap. [Browser results](evidence/browser-results.json).

## Reproduction

Use a disposable localhost PostgreSQL database named with the `intelliplan_fixture` prefix. The concurrency fixture also requires explicit opt-in and refuses other hosts/database names:

```powershell
$env:DATABASE_URL='postgresql://fixture_role@127.0.0.1:fixture_port/intelliplan_fixture'
$env:APP_BASE_URL='https://intelliplan.tech'
$env:FOLLOWTHROUGH_INPROCESS_CRON='0'
$env:NOTIFICATIONS_INPROCESS_CRON='0'
$env:INTELLIPLAN_POSTGRES_VERIFICATION='1'
python -m pytest tests/test_learning_hub.py tests/test_account_deletion.py tests/test_learning_worker.py tests/test_plani_policy.py tests/test_learning_postgres.py -q
```

Replace the fixture role/port with that disposable cluster's settings. The tests create synthetic scoped records; ordinary SQLite runs skip the seven concurrency cases. Production backups, restored-data comparison, rollout supervision, approved provider generation and authenticated real integration/device paths remain separate acceptance work.
