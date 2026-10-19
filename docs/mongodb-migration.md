# MongoDB migration acceptance record

Production remains on Supabase until every cutover requirement below passes.
The Atlas connector is connected to the existing free Cluster0 in US_WEST_2.
On 2026-10-19 the native driver passed 36 isolated Atlas runtime checks; this
does not establish full application or production readiness.

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
| Transaction boundary | Driver-managed transaction callback implemented; rollback/concurrency checks passed on real local MongoDB 8.0.34 and Atlas |
| ID allocation | Atomic counters and bulk range reservation implemented and locally tested |
| Large text | Lossless bounded compression/decompression; every snapshot document round-tripped exactly |
| Web sessions | MongoDB interface implemented and locally tested; not wired into App.py |
| Flashcards | Native CRUD, imports, queues, review history, forecast, counts, deletion, and owner scoping implemented and locally tested |
| Account API | Explicit SQL/Mongo repositories and plain native UserMixin records; registration, login, bearer lookup, password/status API tested without importing App.py |
| Browser accounts | Registration and policy acknowledgements, password sign-in lookups/lockouts, user loaders, security events, password resets and Google identity resolution use the repository boundary; downstream feature hooks still need porting |
| Password resets | Native and SQL repositories atomically consume links, replace credentials and advance session epoch; web reset route and user loader now use the repository boundary |
| Policy acknowledgements | Owner-scoped reads and transactional version updates use SQL/Mongo repositories; existing signup and guest behavior retained |
| Desktop sign-in handoff | Native and SQL ticket issuance and atomic single-use redemption; PKCE burn, expiry and issuance epoch preserved; legacy tickets treated as epoch zero |
| Mobile browser handoff | SQL/native single-use code issuance/replacement/redemption, expiry and credential epochs; browser clears prior data, rotates SID and stamps immutable proof; first-party bearer required |
| Native API credentials | API user reads use account repository; digest/status/scope reads and atomic usage counters implemented; native applications, approval/denial, rotation and revocation implemented; admin/mail callbacks require startup wiring |
| Extension credentials | Native and SQL issue/reuse/lookup/revoke operations; existing opaque tokens retained; stale verified password cannot issue new credentials |
| Student identity | Explicit native/SQL profile reads and partial saves; onboarding form, settings identity API, onboarding chat and assistant-name lookup use the boundary |
| Personalization/phone preferences | Explicit native/SQL partial updates; existing web normalization and bounds retained; removing a phone atomically clears SMS opt-in |
| Focus/premium/LMS preferences | Focus enforcement, alarm-file metadata and premium model preferences use account partial writes; LMS source JSON updates merge under the parent lock/write with exact unknown numbers preserved |
| Role/attribution/tutor allowance | Role changes recheck stored age under lock; first-touch attribution is single-write; monthly tutor reset, cap and increment serialize on the authoritative account with paid bypass |
| Age and parental approval | Immutable first age write and atomic single-use approval in SQL/native repositories; native denial conditionally erases only an account still awaiting that token |
| Account erasure | Explicit native ownership policy, transactional deletion and shared-reference anonymization; upload quarantine/rollback and authenticated-session cleanup tested; archive restore still pending |
| Linked LMS profiles | SQL/native encrypted credential reads, create/activate, switch, rename and delete; owner-scoped profile management and grade/policy readers use the boundary |
| Canvas OAuth | SQL/native token storage, refresh rotation, status and disconnect; grant and linked-profile writes are transactional; stale refresh/disconnect cannot overwrite reconnection |
| Google Calendar and Drive | Native account selection/linking, refresh and disconnect implemented; owner/grant races and exact restored token metadata passed local and Atlas checks |
| Notion | Native manual/OAuth connections, selected database, rotating token pair and disconnect implemented; route/policy suites and local concurrency checks passed |
| Classroom, Blackboard and Moodle | Native connection/status/disconnect, assignment credential readers and conditional OAuth rotation implemented; local real concurrency and native/legacy tests passed |
| Adaptive tutor learner model | Six native collections implemented: profiles, mastery, mistakes, learner memory, imports and summaries; shared validation and response formats; owner checks and transactional mutations |
| Tutor conversations/profile memory | Native owner-scoped conversation CRUD, compressed transcript reads/writes and atomic profile-turn updates; user/guest HTTP history routes work without a SQL engine |
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

All twenty-four checks in `mongodb-runtime-local-20261018.json` passed against an
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

## Focus, premium and LMS preferences continuation

Focus settings validate the whole submitted patch before saving. Alarm uploads
and removal persist their filename through the account repository. This retains
the existing filesystem workflow; broader upload lifecycle coordination remains
a separate cutover requirement. Premium answer preferences use partial writes.
LMS source reads include native owner-scoped integration-presence checks; writes
merge partial fields in a parent transaction rather than replacing a stale request
snapshot. Unknown plain/compressed JSON values retain exact fractions, exponents
and large integers. Empty/no-op patches preserve original text byte-for-byte.

The affected account/preferences/erasure/profile/premium/focus suite passed 188
tests. Native web tests exercise persistence, validation, alarm upload/removal,
and owner-scoped LMS source reads. Sixteen real local MongoDB checks passed,
including concurrent LMS preference patches over compressed precise JSON.

## Atomic account transitions continuation

Role changes validate the saved birth year under the same account lock/write.
Existing student/teacher/parent behavior is retained. First-touch attribution
keeps the first accepted value through a conditional write, including when
requests race. The existing consent gate continues to control when it is called.
The free tutor counter now resets and increments under the account lock/write;
paid status comes from that authoritative row, and paid users bypass the counter.

The account/tutor-counter/insight/premium/policy suite passed 206 tests, including
native request-boundary checks. Nineteen real MongoDB checks passed: twenty tutor
attempts against a limit of seven accepted exactly seven, the next month reset
worked, eight first-touch attempts accepted exactly one, and saved child age
prevented a parent-role change. Full tutor, billing and insight domain storage,
native application startup, Atlas access and cutover remain incomplete.

## Tutor conversation and profile-memory continuation

Tutor memory and conversation functions select an explicit native repository
before touching SQL. User and guest records remain separate; authenticated
mutations coordinate with account removal. Compressed legacy transcript fields,
unknown fields, precise profile JSON numbers and original IDs remain readable.
Profile-turn updates recompute against the current stored profile inside the
transaction, preventing concurrent requests from losing counts. Conversation
saves merge distinct branches without duplicating their common prefix.
Account and identity repository factories also honor an explicitly configured
native store, so these tutor helper paths do not initialize SQL.

The tutor-history/assignment/premium/cloud-documents/counter suite passed 126
tests. Six native history/helper/HTTP tests passed after the last helper test was
added. Independent review passed five native/HTTP tests with no scoped blocker.
Three cloud OAuth fixture failures reproduced against the committed baseline;
fixtures now provide verified Google email and an explicit test callback host.
Twenty-one real local MongoDB checks passed, including concurrent account/guest
memory creation, twelve profile-turn writes, eight concurrent conversation
branches, and account erasure that retains guest memory.

A read-only Atlas rehearsal preflight found zero duplicated tutor-memory user
keys, zero duplicated guest keys, and zero ambiguous user/guest owners on
2026-10-18. Repeat against the final snapshot before building native unique indexes.
Full tutor request dependencies, remaining domains, native startup and cutover
are still incomplete.

## Mobile browser handoff continuation

Mobile browser codes now issue, replace previous unspent codes and redeem through
SQL/native repositories. Redemption is atomic, validates the stored destination,
expires after ninety seconds and carries the issuance credential epoch. Existing
SQL tickets gain an additive epoch column under the deployment schema lock;
legacy tickets default to epoch zero. Browser login clears prior session data,
rotates the server session ID and stamps the immutable redemption epoch.

Scoped developer API keys cannot mint full browser sessions. The handoff accepts
first-party bearer identity only, including rejection when an API key accompanies
a JWT. Native API key authentication reads MongoDB status/scopes and increments
usage atomically; revoked keys fail immediately and duplicate active hashes fail
closed. Developer key management, broader REST endpoint storage and native app
startup remain to be ported.

The combined account/mobile/browser/API-key/additive-migration suite passed 167
tests. Independent review found and verified the scoped-key escalation fix.
Twenty-three real MongoDB checks passed: concurrent mobile redemption accepted
one claimant, concurrent issuance left one unspent code, twelve API usage touches
retained every increment, and account erasure raced safely with ticket issuance.
Atlas native-driver access and final deployment/cutover remain pending.

## Developer API credential lifecycle continuation

Native developer applications recheck the saved owner's age and serialize the
five-open-slot limit with the parent account. Read-only applications may
auto-approve under the existing rules; write scopes wait for administrator
review. Roll/revoke operations are owner scoped. Pending-only decisions accept
one approval and one minted secret, even with competing reviewers. Views omit
secret hashes; unknown/compressed fields survive partial updates.

Native admin routes require an injected checker and fail closed if it is absent.
Native notifications use an injected mail sender after the transaction; approval
reports `emailed_secret: false` when that sender is missing or fails. Actual mail
provider delivery and full application startup remain unverified.

Five native HTTP lifecycle tests and the existing SQL key tests passed in a
32-test run; the final key/mobile-browser suite passed 34 tests. Independent review passed five native HTTP tests and Ruff with no
scoped blocker. Twenty-four real MongoDB checks passed, including eight competing
applications accepting exactly five, rotation/revocation invalidating previous
secrets, and eight admin decisions accepting exactly one approval.


### AI spending and credits: local native verification (2026-10-18)

Native spend recording, integer totals, user/month/source isolation, top-up credits, refund/dispute revocations, payment reference deduplication and the usage breakdown now dispatch to MongoDB. Child writes coordinate with account deletion. Credit failures propagate for payment-event retry; duplicate success is checked against the owner. Index creation is a required startup prerequisite.

Validation: 57 native and existing premium tests passed; independent review approved the scoped change. The official local MongoDB run passed 25 checks, including eight concurrent payment retries yielding one credit and concurrent amounts above floating-point precision remaining exact. This does not establish a strict concurrent spending cap. Linked provider keys, email warnings, full billing routes/startup and Atlas driver validation remain pending. Application readiness remains false; no production changes.


### Linked AI-provider keys: local native verification (2026-10-18)

Native provider-key listing/linking/unlinking and status updates now use owner-scoped MongoDB operations. New credentials require working encryption, current adult eligibility is checked inside the transaction, and changes coordinate with account deletion. Replacement preserves unknown fields. Status completion checks the stored credential version and key, preventing a stale failed request from disabling replacement or relinked credentials. Error records and public views omit secrets; legacy plaintext remains readable for migration compatibility, with encryption on replacement.

Validation: 61 native/legacy premium tests passed. Independent scoped review and Ruff passed. Official local MongoDB verification passed 26 checks, including concurrent linking, encrypted storage and stale status rejection. No real provider calls or email delivery were tested. School-policy checks, email warnings, full billing and native app startup are still pending, as is Atlas driver validation. Production remains unchanged.


### Email suppression and budget notices: local native verification (2026-10-18)

Address suppression now uses MongoDB before any legacy import, normalizes addresses and survives account deletion/recreation. Suppression read errors deny sending. Native low-budget notices require an injected sender, atomically claim one attempt for a month and reject stale earlier-month claims, then reload current account eligibility and suppression before sending. As in the prior behavior, a claim records an attempt, not confirmed inbox delivery.

Validation: 83 native/legacy eligibility and premium tests passed; six final focused tests and independent review passed after the monotonic-period adjustment. Official local MongoDB verification passed 27 checks, including concurrent suppressions and notice claims. Actual sender delivery, lifecycle send history/campaigns, full native app startup and Atlas runtime validation remain pending. No production changes.


### Institution policy reads: local native verification (2026-10-18)

Native decisions now read institution memberships and ancestor restrictions, feature flags and beta enrollment/consent without importing App/SQL. Restrictions retain intersection semantics and account removal, missing organizations/ancestors, cycles and malformed settings deny access. Global administrator checks require the injected checker. Migrated raw/compressed JSON policy text is decoded before validation; malformed authorization booleans and consent timestamps fail closed. Premium permission checks reload the account and invalidate request policy caches. Governance writes remain unported.

Validation: 78 affected native/legacy policy, premium and email tests passed before review adjustments; 17 final focused policy cases and independent re-review passed, including restored compressed JSON and malformed authorization regressions. Official local MongoDB passed 28 checks, including source-format hierarchy reads and live cache invalidation. Native startup, governance writes, broader learning workflows and Atlas runtime verification remain pending. No production changes.


### Lifecycle email send history: local native verification (2026-10-18)

Native lifecycle claims now serialize with account erasure, deduplicate each user/campaign, retry definite failures with fresh claim tokens and reject stale outcomes. A raised provider exception leaves pending status because acceptance is unknown; the injected sender contract must reserve falsy responses for definite nonacceptance. Current account eligibility, suppression, age, address and school notification policy are rechecked after rendering. Native unsubscribe signing/URLs and template base URLs avoid the SQL application. A missing sender consumes no claim.

Validation: 78 affected native/legacy email and eligibility tests passed, including nine native real-template/headers/retry/consent cases. Independent scoped review and final Ruff passed. Official local MongoDB passed 29 checks, including concurrent lifecycle claims and retry-token protection. No email was sent to real recipients. Actual provider delivery, campaign sweeps/unsubscribe endpoint, full native startup and Atlas runtime remain pending. Atlas test was retried after user confirmation but the saved driver URI was still empty, so no cloud test writes occurred. Production unchanged.


### Native signed unsubscribe endpoint (2026-10-18)

Logged-out GET and one-click POST unsubscribe now dispatch natively when MongoDB is configured. The transaction persists the normalized address suppression and switches off marketing on exact matching addresses, retaining reminder consent, dated consent evidence and unknown fields. Malformed signatures/addresses return 400; storage failures return 503 without claiming success. Suppression remains independent of deleted accounts.

Validation: 47 native/legacy email tests passed; independent review and scoped Ruff passed. Official local MongoDB passed 30 checks, including eight concurrent unsubscribes and a forced abort proving suppression and visible preferences roll back together. No real email sent. Full app startup, campaign sweeps and Atlas driver verification remain pending; production unchanged.


### Native onboarding email sweep (2026-10-18)

Onboarding now reads native connection, saved-plan and completed-session goals per owner, native cohort dates and existing send history. Restored ISO-offset dates and BSON dates normalize to UTC; pending/accepted messages remain deduplicated while definite failures retry within the normal age window. Candidate results honor the requested limit; timestamp compatibility currently requires streaming user records to select the cohort. Actual cron execution/sending was not performed.

Validation: 32 native/legacy onboarding and lifecycle tests passed, independent review and Ruff passed. Official local MongoDB passed 31 checks, including restored timestamp cohort selection, owner progress isolation and accepted-send history. Campaign newsletters/welcome selection, full native startup and Atlas verification remain pending. No production changes.


### Native lifecycle campaign selection (2026-10-18)

Welcome/feedback cohorts, active-use probes, newsletter consent audiences, thank-you accepted history, explicit administrator test-recipient selection and integration-count reads now dispatch to MongoDB. Existing scheduling/dry-run defaults and full sender eligibility remain in place. Candidate timestamp handling supports restored ISO offsets/BSON dates; empty admin configuration selects nobody. Queries stream user records to preserve restored date compatibility, so scan work can exceed the result limit. No real campaigns were run.

Validation: 82 affected native/legacy mail, weekly and onboarding tests passed; independent scoped review and Ruff passed. Official local MongoDB passed 32 checks, including accepted-send exclusion and exact synthetic test-recipient scope. Actual email delivery, admin route persistence, calendar/provider and other application domains, full native app startup, Atlas runtime and final cutover remain pending. Production unchanged.


### Outlook calendar grants: local native verification (2026-10-18)

Outlook calendar callbacks, status, owner-token retrieval/refresh, disconnect and strict scheduler connection-presence checks now use an explicit SQL/native repository. Token refresh compares a frozen grant snapshot inside the owner-coordinated transaction, rejecting stale completion after replacement/disconnect; unknown restored fields and existing encrypted/plaintext compatibility are preserved. Explicit-owner school calendar policy reads use the native account repository. Encryption retains existing configured-key behavior.

Validation: 65 native/legacy Outlook, calendar completeness, busy-time and owner-repair tests passed. Independent isolated grant checks and scoped Ruff passed. Official local MongoDB passed 33 checks, including eight concurrent connections leaving one row, one accepted refresh per frozen grant, encrypted configured storage and no post-disconnect resurrection. Real Microsoft OAuth/provider reads, Google/OneDrive persistence, full scheduler/app startup, Atlas validation and cutover remain pending. No production changes.


### OneDrive grants and refresh revocation: local native verification (2026-10-19)

OneDrive callback, token getter/refresh, disconnect, connection metadata and document-client refresh persistence now use the Microsoft grant repository with a separate OneDrive collection and ID sequence. Restored high-water marks are respected independently of Outlook. Owner-coordinated compare-and-set rejects stale refresh after reconnection/disconnect. A document client whose persistence callback reports revocation clears its token and remains invalidated for subsequent requests, refreshes and header access. Ordinary persistence errors retain prior provider-client behavior.

Validation: 100 affected native/legacy Microsoft, cloud-document and calendar-owner tests passed; scoped Ruff passed. Official local MongoDB passed 34 checks, including concurrent OneDrive connections, independent restored sequence high-water checks and stale refresh rejection. Review identified the sequence bug and reusable-client revocation bypass; both were corrected with regressions, with final re-review requested. No live Microsoft OAuth or provider reads occurred.

Production is not ready: Google calendar/Drive and other application domains, full native startup/sessions integration, real Atlas driver checks, final maintenance snapshot/comparison and Railway deployment validation remain pending. The private driver URI was checked on 2026-10-19 and remains empty. Supabase and production remain unchanged.


### Atlas driver connection attempt (2026-10-19)

The private driver URI is now populated and targets intelliplan_runtime_test, but still contains the literal password placeholder. The native driver ping timed out before any test writes. Read-only Atlas inspection confirmed the existing free Cluster0 is available, with one /32 access entry that does not match the current workstation IPv4 address. Attempting to add only the current workstation address through the Atlas connector was rejected as not confirmed; no network access entry was created. Manual password substitution and current-IP allowlisting are required before retry. No credentials or network addresses are recorded here. Supabase and production unchanged.

Independent final review of the OneDrive counter and permanent client invalidation fixes passed, including an isolated repeated-request/header/refresh check with zero provider requests after revocation.


### Native Atlas runtime verified (2026-10-19)

The user completed the private database password and current workstation network-access setup. Native PyMongo verification against intelliplan_runtime_test on the existing free Cluster0 passed all 34 checks. This confirms the connection, permissions, native index creation and ported-domain behavior on Atlas, rather than only the connector/local server. Sanitized evidence: mongodb-runtime-atlas-20261019.json.

Coverage includes transaction rollback, ID allocation and independent restored OneDrive counters, concurrent account/preferences/LMS/Canvas/tutor updates, authentication handoffs, developer keys, integer AI spending/credits, linked provider credentials, email suppression/claims/unsubscribe, institution policy reads, Outlook/OneDrive grant races, flashcard reviews, learner mastery, sessions and account erasure including rollback/child-creation races. Test data is synthetic and isolated. No Supabase reads/writes or production cutover occurred in this run.

Full application readiness remains false: remaining SQL-dependent application domains and bootstrap/session wiring, complete browser/mobile workflows, live provider integrations and final maintenance snapshot/restore/comparison plus Railway deployment verification are not complete. The successful driver run is not permission or evidence to switch production. Existing source and rehearsal copies are retained.

## Google Calendar and Drive native grant port (2026-10-19)

Saved Google Calendar accounts, active selection, removal/promotion, OAuth linking,
Drive grants and refreshes now use explicit SQL/native repositories. Native writes
serialize on the owner and preserve source IDs, unknown fields and exact token JSON
numbers. Refresh proof prevents overwriting reconnects or recreating disconnected
grants; a revoked Drive client stops subsequent provider requests. Restored duplicate
Drive emails retain the newest-grant lookup behavior. Authenticated users cannot
reuse cookie tokens after disconnect. Calendar provider failures retain saved grants.

Validation: 120 existing Google/cloud/calendar tests passed; all 11 dedicated native
Google tests passed, and the earlier joint native Google/Microsoft run passed 19
tests before adding the calendar-export regression. Scoped Ruff and diff checks
passed. Read-only review approved the selector fix and transaction boundaries.
Real local MongoDB 8.0.34 passed 36 runtime checks, including concurrent multi-account
connection/activation, stale refresh rejection, active-account promotion, Drive token
metadata preservation and encrypted credentials. Evidence: `mongodb-runtime-local-google-20261019.json`.
Atlas verification for these two new checks is pending. All data used for runtime
checks is synthetic in the dedicated test database. No live Google OAuth/provider
acceptance or whole-app native startup is claimed. Production remains on Supabase.

## Google Atlas acceptance and Notion port (2026-10-19)

Google Calendar/Drive passed all 36 runtime checks on Atlas, run
`58d3509b6e854fb68e7fcfdb42af2e4b`; see `mongodb-runtime-atlas-google-20261019.json`.
This supersedes the pending Atlas status of the Google batch above.

Notion manual/OAuth connections, metadata, selected database, disconnect and token
pair rotation use SQL/native repositories. Parent coordination and frozen grant
proof prevent stale refreshes or remote-create callbacks overwriting reconnects.
Manual reconnect clears old OAuth refresh/expiry while retaining database selection.
Authenticated requests cannot reuse disconnected cookie grants or recreate one by
selecting a database. The LMS policy owner lookup now uses the account repository.

Validation: all 207 tests in the dedicated native Notion, existing Notion OAuth,
owner-calendar and full Plani policy suites passed together. Scoped Ruff, compile
and read-only code review passed. Real local MongoDB passed 37 runtime checks,
including concurrent Notion connect/rotation, encrypted token-pair storage, manual
reconnect and stale selection/disconnect refusal. See
`mongodb-runtime-local-notion-20261019.json`. The first Atlas run of this new
37-check suite returned NetworkTimeout; a retry is pending. No live Notion requests
were sent by tests, no source writes or Railway changes were made, and full native
application startup/production cutover remain incomplete.

## Classroom, Blackboard and Moodle native grants (2026-10-19)

Classroom/Blackboard OAuth callbacks and rotating-token refreshes, Moodle manual
connection, all three status/disconnect paths and assignment credential readers
now use explicit SQL/native repositories. Latest source row selection, restored
duplicates/null owners, unknown fields, precise timestamps and independent IDs
are preserved. Parent writes serialize connects/refreshes with erasure; frozen
record/credential/expiry/origin proof prevents reconnect overwrite and resurrection.
A changed Google email, Blackboard institution or Blackboard user identity clears
omitted refresh credentials and stale identity metadata.

Validation: 63 existing Blackboard tests passed; the joint run with 13 native LMS
cases passed 76 tests. After adding the same-school Blackboard account-switch
regressions, all 15 native LMS cases passed. Ten affected school-policy refresh
cases passed after replacing an ORM object-identity assertion with token, owner and
record-ID assertions; stored-token/revocation checks remain. Scoped Ruff, compilation,
diff checks and read-only review passed. Real local MongoDB passed all 40 runtime
checks; evidence: `mongodb-runtime-local-lms-20261019.json`.

Two Atlas runs of the earlier 37-check suite hit NetworkTimeout. An expanded
40-check run is in progress with opt-in credential-free completion markers to
locate any timeout. The most recent fully completed Atlas evidence remains the
36-check Google run. No source writes, live school-provider calls, paid resource
activation, Railway changes or production readiness are claimed.

## Expanded Atlas run: incomplete final erasure check (2026-10-19)

The progress-enabled expanded Atlas run completed **39 of 40 checks**, including
all native Google Calendar/Drive, Notion, Classroom, Blackboard and Moodle grant
checks. It then returned `NetworkTimeout` during the combined account-erasure and
rollback verification. Its result is **incomplete**, not a full-suite pass.
Evidence: `mongodb-runtime-atlas-lms-partial-20261019.json`.

This supersedes the pending run statuses above. The previous fully completed
Atlas suite still contains 36 checks (Google batch), while local real MongoDB
completed all 40. The final expanded erasure check must pass on Atlas before
its acceptance gate can be closed. No retry changed timeouts, disabled transaction
checks, deleted prior diagnostic data, changed source data or changed production.

Remaining application work includes core task/schedule persistence, unported
learning/Family/notification and related domains, native full-App startup/session
wiring, complete user-flow acceptance and the fresh maintenance-frozen final copy.
Only after those gates pass can Railway receive production MongoDB variables.
