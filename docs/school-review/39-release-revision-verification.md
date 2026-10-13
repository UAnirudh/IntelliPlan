# Verify the revision served before live release tests

The main-push browser-test wait previously accepted any HTTP 200 from
`/api/live`, including the previous deployment while Railway built the new
one. That endpoint actually lists the owner's live rooms; it is not a
deployment readiness check.

Public `/health` now reports a validated full commit SHA from Railway's
[`RAILWAY_GIT_COMMIT_SHA`](https://docs.railway.com/variables/reference)
and sends `Cache-Control: no-store`. A missing or malformed revision is null.
No secret or student record is added to the response.

The main-push workflow waits up to420 seconds for this exact GitHub commit,
HTTP200, status `ok`, and true migration/schema flags before starting live
browser checks. The polling script bounds each network timeout and sleep by
the remaining window and retries network failures, invalid JSON and truncated
HTTP bodies. An old or unknown revision cannot pass. This proves the checked
revision was ready at the probe moment, rather than all migrations or every
subsequent browser request's revision.

Focused tests cover old/missing commit identifiers, failed schema checks,
network/JSON/truncated-body recovery, finite timeout validation and uncached
public health metadata. All18 focused tests, scoped Ruff and the repository's
configured Bandit scan pass. Independent Python and spec review findings were
resolved. Hosted full checks and production verification remain separate.

## Preceding checkpoints delivered

PR88 merged as `715d0150c6ec77b7f61f548bd6b032e8fbc7ba6f` and deployed in Railway
production as `37007a27-637c-4851-ba07-432c8e29cd24` (SUCCESS). Pre-merge and
post-merge hosted checks passed4501 unit tests with16 skipped and130 public
browser tests with1 skipped. Live health/schema/read checks passed; four web
workers started and the filtered runtime error scan was empty.

PR89 merged as `c7bae703d46f5200725d8fecbfbb00fe92f73b96` and deployed as
`a76cd0df-b80f-4fe3-8bca-2dc08b09b5b1` (SUCCESS). Pre-merge hosted tests passed
4501 unit tests with16 skipped and130 public browser tests with1 skipped.
All security jobs passed, including the new native critical-dependency/mobile
typecheck job. Live health/schema checks passed and the filtered runtime error
scan was empty. Native packaging/device tests and the remaining high/moderate
advisories are still open.

These public checks do not establish real authenticated student/vendor/provider
flows. The original master brief, country/language support and the complete
UI simplification remain incomplete.
