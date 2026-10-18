# MongoDB migration acceptance record

Production remains on Supabase until every cutover requirement below passes.
The Atlas connector is connected to the existing free Cluster0 in US_WEST_2.

## Required outcomes

- [x] Independent consistent snapshot of every application table, with schema,
  constraints, indexes, original IDs, binary fields, and precise timestamps.
- [x] Restore the snapshot to an isolated Atlas database; compare every row and
  field and reject missing, extra, or changed documents.
- [ ] Replace SQLAlchemy queries and raw SQL in the application with MongoDB
  operations, including account authorization and account erasure.
- [ ] Replace SQL-backed web sessions and test login, logout, OAuth callbacks,
  expiration, and session regeneration across processes.
- [ ] Replace database locks, sequence allocation, relational uniqueness,
  foreign keys, cascades, atomic operations, and migration boot coordination.
- [ ] Exercise scheduling, LMS synchronization, flashcards, tutor, Family,
  learning, notifications, group tasks, mobile APIs, and newsletter paths.
- [ ] Build application indexes and verify concurrency behavior on real Atlas.
- [ ] Take a final snapshot under maintenance, validate against Atlas, and leave
  the source retained for rollback. A rehearsal copy is not a final copy.
- [ ] Supply exact Railway variables and verify the deployed app after the user
  applies them. Do not change the current production URL before these pass.

## Snapshot tooling

Install `requirements-mongodb.txt`, then run `scripts/migrate_to_mongodb.py
--help`. A private env file can supply `SOURCE_DATABASE_URL` and `MONGODB_URI`.
Never put credentials or snapshots in this repository. Use a private directory
outside the checkout, restrict its OS permissions, and retain an independent
copy before cutover.

The snapshot command opens one read-only, repeatable-read PostgreSQL transaction.
It exports every public table, including empty tables. Its manifest preserves
column types, defaults, primary keys, indexes, and constraint definitions. It
does not claim those PostgreSQL constraints are automatically enforced in MongoDB.
Dates and timestamps use ISO strings to retain microseconds. Binary values use
BSON Binary and exact decimals use Decimal128. Unsupported values fail closed.

The restore command refuses a database containing any collections. It validates
all file hashes before writing, compares every restored document, and marks the
copy `application_ready: false`. Interrupted copies must be retained for diagnosis;
retry into a new database. No tool command deletes a source or destination.

## Current architecture findings

Live inventory on 2026-10-17: 132 public tables and 131 foreign keys. Supabase
Auth and Storage are empty; application accounts and sessions are custom.
The app initializes Flask-SQLAlchemy in App.py and uses SQLAlchemy ORM/Core in
accounts, integrations, scheduling, tutor, flashcards, Family, and learning.
db_boot.py depends on PostgreSQL advisory locks. These require a real port;
changing DATABASE_URL to a MongoDB URI cannot run the existing application.

## Verified rehearsal on 2026-10-17

Atlas database: `intelliplan_rehearsal_20261017`, on the existing free Cluster0.
Snapshot time: `2026-10-17T20:07:14.588416+00:00`.

- All **132 application collections** exist, including **56 empty collections**.
- All **21,420 rows** were copied. Each transfer batch was checked against the
  actual stored BSON documents, and every collection's final row count matched.
- Original business IDs, password hashes, encrypted integration tokens, binary
  session data, JSON text, and microsecond timestamps were retained.
- **130 ID counters** were seeded so future allocations exceed source sequence
  reservations and restored IDs. This does not implement relational constraints
  or application locks.
- MongoDB `_migration_status` records `status: verified_snapshot`,
  `application_ready: false`, and `final_cutover_copy: false`.
- The native PostgreSQL backup's **public application schema and all 132 tables**
  were restored into an isolated local PostgreSQL server, then exported and
  compared through the MongoDB snapshot format. This is not a claim that all
  Supabase platform schemas can be restored to a generic PostgreSQL server.
- Source writes continued during the rehearsal. A new final snapshot is required
  under maintenance before any application switch.

Private backups and `atlas-verification.json` are outside the repository in
`%USERPROFILE%/intelliplan-db-backups/mongodb-20261017/`, with restricted Windows
ACLs. `rehearsal-02` is the validated snapshot. The earlier `rehearsal-01` is
explicitly marked superseded and cannot pass snapshot validation.

## Application port status

| Component | Status |
| --- | --- |
| Connection lifecycle | Native MongoStore implemented; lazy clients, fork handling, majority writes |
| Transaction boundary | Driver-managed transaction callback implemented; real Atlas rollback/concurrency tests pending |
| ID allocation | Atomic counters and bulk range reservation implemented and locally tested |
| Large text | Lossless bounded compression/decompression; every snapshot document round-tripped exactly |
| Web sessions | MongoDB interface implemented and locally tested; not wired into App.py |
| Flashcards | Native CRUD, imports, queues, review history, forecast, counts, deletion, and owner scoping implemented and locally tested |
| Account API | Explicit SQL/Mongo repositories and plain native UserMixin records; registration, login, bearer lookup, password/status API tested without importing App.py |
| Password resets | Native and SQL repositories atomically consume links, replace credentials and advance session epoch; web reset route and user loader now use the repository boundary |
| Adaptive tutor learner model | Six native collections implemented: profiles, mastery, mistakes, learner memory, imports and summaries; shared validation and response formats; owner checks and transactional mutations |
| Remaining accounts and domains | Browser registration/login/OAuth, account erasure, security logging, LMS, scheduling, tutor chat/checks/education plans, Primer, Family, learning, notifications, groups and billing still need porting; application-wide startup remains SQLAlchemy |
| Railway | Production variables and deployment unchanged; no MongoDB cutover |

The native flashcard unit tests exercise an 800-card import, seeded IDs,
cross-account denial, compressed legacy text edits, daily new-card allowance,
review history and deletion. Additional tests cover binary sessions, expiry,
logout and session regeneration across app instances. PostgreSQL integration
tests cover exact JSON numbers, literal BSON-looking JSON keys, RLS failure,
binary data, composite keys, sequences, empty tables, native backup restoration,
and rejection of unsupported JSON arrays.

Focused evidence: 25 native migration/persistence/flashcard tests, 23 database
boot tests, and 38 PostgreSQL/original-flashcard tests passed locally. Required
real Atlas transaction/concurrency, browser, mobile, deployment, and final
maintenance-window checks have not passed yet. Review approval covers migration
tooling and the isolated flashcard port only.

Do not change Railway DATABASE_URL to a MongoDB URI. The current App.py still
initializes SQLAlchemy and database-backed sessions. Keep the existing SECRET_KEY
and integration-encryption keys during the eventual cutover. The remaining port
must be completed before generating final Railway connection instructions.

## Rewrite continuation: accounts and learner model

The account API selects an explicitly injected `accounts_repository` and
`account_passwords` provider. The legacy app continues using SQL; no production
feature creates Mongo-only users while other features reference PostgreSQL IDs.
Native accounts preserve all restored fields and precise date values. New
accounts retain the existing defaults for consent, privacy, preferences and plan.
Nullable unique Google/referral identifiers use partial indexes.

Password changes compare the hash actually verified by the caller and return
the exact epoch from that atomic write. A later reset cannot cause a stale
browser to acquire the newer epoch. Reset completion burns outstanding links,
changes the credential, clears lockout and advances the epoch in one transaction.
The web reset route rejects invalid tokens before expensive password hashing,
then rechecks validity inside the transaction. Email delivery remains outside it.

The native tutor is selected only through an explicitly registered `mongo_store`.
Its tests forbid SQL access. Learner reads/writes check current user/guest
ownership, and child mutations touch the parent profile in the same transaction.
Account removal must still coordinate new profile creation before cutover.
Stronger unique indexes were preflighted against the Atlas rehearsal: no duplicate
profile user/guest keys, active mistake keys or conversation-summary keys were
found. Repeat this preflight against the final snapshot.

Validation during this continuation: 57 account/API/web-password tests passed,
70 shared/native tutor tests passed, and the added zero-limit native tutor
regression passed in a focused run. Review covered these boundaries and the
runtime verification script; it does not certify the full application.

`scripts/check_mongodb_runtime.py --env-file <private file>` checks real driver
connectivity, transaction rollback, concurrent ID allocation, single-use reset
consumption and concurrent mastery updates. It refuses every database except
`intelliplan_runtime_test`, retains synthetic evidence, and sanitizes errors.
It currently reports `configuration_required`: a native `MONGODB_URI` has not
been supplied. These real-driver checks have not passed yet.

Atlas administration service-account credentials configure MCP management
access; they do not replace the application's driver connection URI. The user
has entered the service-account fields directly in the local Codex configuration.
The Windows MCP launcher uses Node directly to preserve the npm version-range
argument. Configuration parsing passed; live MCP authentication verification is
pending. Codex must restart to expose the newly configured tools to the agent.
