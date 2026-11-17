# Windows verification

Verified on the ARM64 Windows development machine on November 16, 2026.

## Runtime checks

Windows Code Integrity previously rejected NumPy's `_umath_linalg` extension.
Fresh processes now import both `numpy.linalg` and `psycopg2` successfully,
and all 11 retrieval tests pass. No Application Control policy, Defender
setting, signing requirement, or PowerShell execution policy was relaxed.
A future signing rejection should be diagnosed from
`Microsoft-Windows-CodeIntegrity/Operational`; it is not a reason to disable
the machine's security controls.

The development machine also lacked npm. The official Node.js 24.22.3 ARM64
archive was checked against its published SHA-256 checksum, its executable's
OpenJS Foundation Authenticode signature validated, and its directory added
to the user's PATH. Open a new terminal to pick up that PATH change. Use
`npm.cmd` in PowerShell so package commands do not depend on executing the
`npm.ps1` shim.

## Reproduce the Python checks

Run from the repository in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-mongodb.txt pytest playwright
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -c "import numpy.linalg, psycopg2; print('Native imports passed')"
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:SECRET_KEY = 'local-disposable-verification-only'
# Point this at an installed PostgreSQL bin directory containing initdb,
# pg_ctl, pg_dump and pg_restore to enable the migration integration tests.
$env:MIGRATION_TEST_PG_BIN = 'C:\path\to\postgresql\bin'
$scratch = Join-Path $PWD ('.verification-tests\' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest -q -rs --basetemp=$scratch
```

The migration fixture starts and stops its own loopback-only PostgreSQL
server. Its native backup test generates synthetic data and a native dump
when no external dump is provided. It no longer requires a production
backup or a fixed historical table count.

`tests/test_learning_postgres.py` runs separately with
`INTELLIPLAN_POSTGRES_VERIFICATION=1` and `DATABASE_URL` pointing to an
explicitly disposable localhost database whose name begins with
`intelliplan_fixture`. All nine concurrency checks passed against such a
scratch database. Do not run that fixture against a normal development or
production database.

Accessibility fixtures now finish onboarding and fail on unexpected route
redirects instead of skipping the page. Focus tests create their own
synthetic users and keep uploaded sounds inside pytest's temporary folder.
Browser scraper checks use the installed Playwright Chromium build.

The SQL variants of two native MongoDB regression tests intentionally skip:
their SQL behavior is covered by separate route and child-privacy tests.
GitHub Actions installs Chromium before the main unit suite and has a
dedicated PostgreSQL 16 job for the concurrency and native backup checks.

## Source and local handoffs

The original privacy working edits are preserved on
`codex/preserve-local-privacy-edits`. Current main retains the newer integrated
protections. The unsent school email drafts are included as source documents;
no email was sent. Historical `release-artifacts/` bundles and publishing
instructions remain local and are ignored because their source release is
already in Git history.

## Remaining OneDrive metadata lock

The original OneDrive checkout's `.git/ORIG_HEAD` recall timed out while Git
was updating its undo reference. Windows Restart Manager identifies the Git
process that attempted the recall, but Windows reports no running instance
when asked to terminate it; the undo reference and its lock remain held.
The checkout was safely fast-forwarded using a checked index/worktree update
and a compare-and-set branch update, without deleting the held metadata.
Both working trees are clean and match the published commit. Future merges
in the OneDrive checkout may still wait on that undo-reference lock until
Windows releases it, likely after a restart. No automatic reboot, OneDrive
shutdown, forced unlock, or security-policy change was performed.
