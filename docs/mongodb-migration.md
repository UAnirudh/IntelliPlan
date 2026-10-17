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
| Accounts, LMS, scheduling, tutor, Family, learning, notifications, groups, billing and other features | Still use SQLAlchemy/PostgreSQL; application-wide port remains outstanding |
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
