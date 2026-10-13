# Study-session HTTP concurrency proof — 2026-10-12

Two additional controlled interleavings now exercise the existing study-session safeguards through signed-in HTTP requests against the restored disposable PostgreSQL fixture. No application change was needed.

- Two starts both pass the initial no-open-session read. One request is held immediately before insert, the other commits, and the first then collides with the existing partial unique index. The actual blueprint error handler returns409, the other request returns201, and the database retains exactly one active/paused session.
- A completion updates a paused session while holding its transaction open. An in-flight resume reads the old revision and reaches its update, where PostgreSQL blocks it behind completion. After completion commits, the revision comparison rejects resume with409. Saved state remains completed, elapsed time stays120 seconds, resumed time is null, and exactly one completion record has nullable correctness. Completion still makes no mastery claim.

The first direct-function reproduction bypassed the blueprint error handler and incorrectly treated the protected uniqueness collision as an application failure. The final tests use the complete HTTP dispatch and verify both response and storage behavior. A separate restored-fixture run exposed a test setup that always inserted an existing unique feature flag. That test now reuses/enables the flag when present; its approval, consent, withdrawal and policy assertions remain.

## Evidence and limits

The restored PostgreSQL scope passes **199 tests,1659 warnings in20.49s**, exit0: nine explicit-local concurrency tests plus learning, worker, fixed-bank and account-deletion checks. [Result](evidence/pytest-postgres-study.txt). This runs against the synthetic database restored in the [previous continuation](22-retention-postgres.md); the earlier127-table/692-row/124-sequence fingerprint comparison occurred before subsequent test mutations. It is not a production-data restore.

The separate SQLite learning/concurrency scope passes **145 tests,9 skipped,1541 warnings in11.00s**, exit0; the nine guarded PG cases correctly skip outside the explicitly chosen fixture. [SQLite result](evidence/pytest-study-sqlite.txt). Changed tests pass Ruff and whitespace checks.

Independent Python and scoped spec reviewers found no actionable test/evidence defects. All forced waits/futures have deadlines, SQL listeners are removed and release signals run in finally blocks. The local-host/database-prefix/explicit-opt-in guard remains. The fixture server was stopped and port55543 verified closed after execution.

These are two selected SQL interleavings, not arbitrary-load, every-session-race, deployed supervision or production reliability proof. No external provider, real student data or production system was used. The preceding full SQLite regression4245/31 and17-workflow browser run belong to the application-identical curated-check checkpoint; these extra tests and fixture correction were verified separately rather than claiming a new complete-suite count.
