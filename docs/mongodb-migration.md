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
| Transaction boundary | Driver-managed transaction callback implemented; real local MongoDB 8.0.34 rollback/concurrency checks passed; Atlas checks pending |
| ID allocation | Atomic counters and bulk range reservation implemented and locally tested |
| Large text | Lossless bounded compression/decompression; every snapshot document round-tripped exactly |
| Web sessions | MongoDB interface implemented and locally tested; not wired into App.py |
| Flashcards | Native CRUD, imports, queues, review history, forecast, counts, deletion, and owner scoping implemented and locally tested |
| Account API | Explicit SQL/Mongo repositories and plain native UserMixin records; registration, login, bearer lookup, password/status API tested without importing App.py |
| Browser accounts | Registration and policy acknowledgements, password sign-in lookups/lockouts, user loaders, security events, password resets and Google identity resolution use the repository boundary; downstream feature hooks still need porting |
| Password resets | Native and SQL repositories atomically consume links, replace credentials and advance session epoch; web reset route and user loader now use the repository boundary |
| Policy acknowledgements | Owner-scoped reads and transactional version updates use SQL/Mongo repositories; existing signup and guest behavior retained |
| Desktop sign-in handoff | Native and SQL ticket issuance and atomic single-use redemption; PKCE burn, expiry and issuance epoch preserved; legacy tickets treated as epoch zero |
| Extension credentials | Native and SQL issue/reuse/lookup/revoke operations; existing opaque tokens retained; stale verified password cannot issue new credentials |
| Student identity | Explicit native/SQL profile reads and partial saves; onboarding form, settings identity API, onboarding chat and assistant-name lookup use the boundary |
| Personalization/phone preferences | Explicit native/SQL partial updates; existing web normalization and bounds retained; removing a phone atomically clears SMS opt-in |
| Age and parental approval | Immutable first age write and atomic single-use approval in SQL/native repositories; native denial conditionally erases only an account still awaiting that token |
| Account erasure | Explicit native ownership policy, transactional deletion and shared-reference anonymization; upload quarantine/rollback and authenticated-session cleanup tested; archive restore still pending |
| Linked LMS profiles | SQL/native encrypted credential reads, create/activate, switch, rename and delete; owner-scoped profile management and grade/policy readers use the boundary |
| Canvas OAuth | SQL/native token storage, refresh rotation, status and disconnect; grant and linked-profile writes are transactional; stale refresh/disconnect cannot overwrite reconnection |
| Adaptive tutor learner model | Six native collections implemented: profiles, mastery, mistakes, learner memory, imports and summaries; shared validation and response formats; owner checks and transactional mutations |
| Remaining accounts and domains | Provider credential storage, other account preferences, archive lifecycle, LMS, scheduling, tutor chat/checks/education plans, Primer, Family, learning, notifications, groups and billing still need porting; application-wide startup remains SQLAlchemy |
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
maintenance-window checks have not passed yet. Repository-boundary reviews do
not certify full application readiness.

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
New account-owned profiles touch the user in the creation transaction to coordinate account removal.
Stronger unique indexes were preflighted against the Atlas rehearsal: no duplicate
profile user/guest keys, active mistake keys or conversation-summary keys were
found. Repeat this preflight against the final snapshot.

Validation during this continuation: 57 account/API/web-password tests passed,
70 shared/native tutor tests passed, and the added zero-limit native tutor
regression passed in a focused run. Review covered these boundaries and the
runtime verification script; it does not certify the full application.

`scripts/check_mongodb_runtime.py --env-file <private file>` checks real driver
connectivity, transaction rollback, concurrent ID allocation, single-use reset
and desktop-ticket consumption, and concurrent mastery updates. It refuses every database except
`intelliplan_runtime_test`, retains synthetic evidence, and sanitizes errors.
For Atlas it currently reports `configuration_required`: a native `MONGODB_URI` has not
been supplied. The isolated local real-driver checks below passed.

Atlas administration service-account credentials configure MCP management
access; they do not replace the application's driver connection URI. The user
has entered the service-account fields directly in the local Codex configuration.
The shared npm cache had a missing dependency file. A complete isolated runtime
was installed at `%USERPROFILE%/.codex/runtimes/mongodb-mcp-2.1.2`, and the Windows
MCP launcher now uses Node directly with that runtime. Configuration parsing and
an actual MCP initialize/tools/connect-cluster exchange passed: the service
account connected to Cluster0. The configured MongoDB tools are now exposed to the agent.

## Browser account continuation

Browser registration now saves the account and policy acknowledgements in one
repository transaction, with explicit allowlists preventing ownership/ID
overrides. Existing parental-consent and marketing gates remain in the web flow.
Google identity lookup/link/create uses a transaction and preserves password
hashes and original account IDs. SQL linking refreshes cached mapped state and
conditionally updates only an unbound/matching identity. The callback rejects
unverified Google email claims. Email auto-linking is limited to Gmail/Google
hosted claims; other existing accounts require password sign-in before linking.

Failed-login counts and lockout activation use atomic database updates. Successful
password authentication captures the exact credential epoch in the conditional
update, then stamps that epoch after login so a concurrent reset cannot upgrade
an old-password session. Browser reset, signup, onboarding and Google identity
regressions passed in a 109-test focused run.

`EpochLoginManager` signs the remember-cookie payload with the issuance epoch
while retaining numeric business/session IDs and Flask-Login's cookie settings.
Legacy remember cookies remain valid only while the account epoch is zero. Actual
pre-reset remember-cookie replay with the session cookie removed is rejected.
Legacy ordinary sessions without an epoch are also treated as epoch zero, so
they cannot adopt a later password-reset epoch. The combined
remember/reset/session-cookie/password suite passed 53 tests.
These guarantees concern browser sessions; API key and stateless bearer-token
revocation policy remains a separate application acceptance item.
The cookie guard explicitly distinguishes request-token authentication; a fresh
mobile bearer token still works after changing the password. Account, policy,
Google callback and password-reset checks passed in a 168-test focused run.

Policy acknowledgement reads and writes now use the account repositories.
Authenticated updates lock/touch the parent account to coordinate lifecycle;
accepted versions never move backwards and guest/account ownership stays separate.

Desktop ticket claims use conditional writes, so concurrent claimants cannot
both consume a code. Tickets record the credential epoch at issuance. Legacy
tickets use zero; a reset invalidates them. Redemption returns an immutable
epoch proof that is stamped after login, preventing a later reset from upgrading
the issued session. The SQL epoch column is added inside the existing schema lock
and preserves legacy rows. Extension credentials preserve existing token values,
owner IDs and revocation behavior. All issuance paths use the repositories.
Cookie-authorized extension handoff also checks the exact authenticated epoch
under the parent lock/write, so a reset during the handoff cannot grant a
persistent credential. Existing extension-token validity is unchanged.
The client-port run passed 136 tests, covering shared SQL/native repositories,
full-app desktop exchange, extension task sync, and Focus Shield. The additive
ticket-column migration passed its preservation/idempotency test. These runs
overlap other counts above and must not be added together. Native unit fixtures
emulate transactions; real Atlas concurrency remains unverified.
The combined account/browser/client run passed 273 tests. After the final
extension-issuance race fix, the affected account/extension suite passed 71
tests, including a full-app reset-between-check-and-issuance regression.

Live native-driver verification is blocked on the Atlas connector's mandatory
confirmation for creating `intelliplan_runtime_driver`, scoped to read/write on
`intelliplan_runtime_test` in Cluster0. The connector explicitly rejected the
unconfirmed `atlas-create-db-user` action; no runtime user was created. The user
approved the scoped action in chat on 2026-10-18, but the retry still returned
"User did not confirm the execution". Connector confirmation remains unresolved;
do not bypass the rejection through another API or credential. The existing production database and verified
rehearsal remain unchanged.

## Student identity continuation

Student identity profiles now have native and SQL repositories with explicit
partial updates. Ownership and business IDs cannot be overwritten through the
patch API. New profiles coordinate with the parent account's lifecycle and
enforce one identity per account. Migrated JSON text, compressed goals, unknown
fields and microsecond timestamps are preserved. SQL saves refresh cached mapped
state under the parent lock, so an explicit patch is not silently dropped when
another connection changed a cached field.

Onboarding form saves, `/identity`, `/api/identity`, onboarding-chat extraction,
settings/profile reads, personalization reads and the chosen-assistant-name
lookup use the boundary. Archive restoration remains part of the unported
archive lifecycle and still uses SQL mapped records. Age/parental-consent and
other notification preferences also remain to be ported separately. The age step
and parental approval were ported in the continuation below.

The account/identity/onboarding/Plani/source-policy run passed 292 tests before
the final stale-cache regression fix. The final affected account/identity/
onboarding suite passed 96 tests, and review independently verified the two
SQL/native stale-cache regressions. The native runtime script now also checks
concurrent profile creation and preservation of unrelated partial updates; those
Atlas-specific checks remain pending the restricted test connection.

## Real local MongoDB verification on 2026-10-18

All fifteen checks in `mongodb-runtime-local-20261018.json` passed against an
isolated, single-member MongoDB 8.0.34 replica set using PyMongo. This was a real
MongoDB server, not a mocked transaction fixture. The official runtime archive's
SHA-256 was checked before running it. The server bound only to localhost,
contained synthetic data, and shut down when the checks finished.

The checks covered driver connectivity, atomic account preferences, deliberate transaction rollback,
concurrent IDs, single-use reset and desktop codes, concurrent partial identity
updates, concurrent extension-token reuse, concurrent flashcard reviews with
complete history, concurrent mastery evidence, and shared web sessions across
two Flask instances including rotation and logout. Account erasure also passed deliberate
rollback, concurrent child creation, pending-denial versus approval, legacy binary
session cleanup, and exact JSON audit anonymization checks. No source or rehearsal rows
were used. Synthetic records are retained outside the repository.

These results validate the implemented native repository operations. They do
not verify Atlas network/credential configuration, all browser/mobile flows,
full MongoDB application startup, remaining domain ports, or production cutover.
`application_ready` and `atlas_verified` remain false in the evidence.

Personalization and phone reminder preferences use an allowlisted partial-update
boundary. The SMS flag is derived from the authoritative phone field in the same
write, so clearing a phone cannot leave SMS opted in or overwrite unrelated
preferences. The repository/onboarding/policy run passed 119 tests, and three
web-route tests verified authorization, normalization, clamping, removal and
personalization toggling. The updated eleven-check real-MongoDB run passed too.

Age collection now records the first valid birth year under the parent account
lock/write. Concurrent submissions preserve the first stored year and consent
link. Child accounts lose marketing opt-in; approval stores notice evidence and
burns the link atomically. The web flow uses the saved account rather than stale
request input. Legacy SQL denial refetches/locks the account before deciding that
it is still pending. Native denial now conditionally erases the pending account in one transaction.
The age/consent/account/policy run passed 156 tests. Independent SQL/native age
contracts passed four tests. All twelve real-MongoDB checks passed, including
eight concurrent initial-age submissions and eight single-use approval attempts.

## Native account-erasure continuation

The explicit ownership policy covers every current foreign key into users,
dependent learner/course rows, sessions, and shared references. Migrated plain
or compressed JSON-text audit payloads are scrubbed without rounding unrelated
numbers. New flashcard decks, learner profiles, security events and authenticated
sessions coordinate writes with the parent account to avoid resurrecting deleted
data. Every future domain writer must retain this lifecycle coordination.

Uploaded files are quarantined before deletion and restored after a known
transaction failure. If MongoDB reports an unknown commit outcome, the files
remain quarantined and the request reports that verification is required; it
does not restore files into an account that may already have been deleted.
Shared lesson filenames and changed upload inventories abort safely.

The focused native-erasure/session/flashcard/tutor/legacy-deletion suite passed
107 tests. Independent review passed 15 erasure/session tests and found no
remaining scoped blocker. All thirteen real-MongoDB checks passed. Full app
startup, remaining domains, Atlas access and cutover remain incomplete.

## LMS profiles and Canvas continuation

All production LinkedAccount and CanvasIntegration query sites now use explicit
repositories. Profile creation and switching serialize with the parent account,
leaving one active profile. Existing encrypted or legacy plaintext credentials,
compressed fields, unknown fields and original profile IDs remain readable.
Profile selectors reject object-valued input and cannot select another owner.

Canvas callbacks save the OAuth grant and linked credentials in one transaction.
Refresh accepts the exact connection proof captured before the network request,
handles rotated refresh tokens and updates only the matching active OAuth profile.
Disconnect removes secrets from inactive profiles too and rejects a proof made
obsolete by reconnection. Selecting an older OAuth school/account requires
reconnection when its grant differs from the stored grant; another school's token
is never paired with that profile's URL. A matching grant uses its authoritative
current token even if the profile's copy is stale.

The combined profile/encryption/LMS/Canvas/policy suite passed 164 tests.
The real local MongoDB run passed fifteen checks, including concurrent profile
creation/switching, competing Canvas refreshes, late-disconnect rejection and a
deliberate failure after the OAuth token write to verify mirrored-write rollback.
Other providers, remaining feature domains, native startup and cutover are pending.
