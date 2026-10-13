# Native dependency security patches

The mobile lockfile updates three transitive packages within their existing
parent dependency ranges: `shell-quote` 1.10.0 to 1.12.0, `source-map-js` 1.2.1
to 1.2.2, and `brace-expansion` 5.0.9 to 5.0.12. No Expo/React Native downgrade,
dependency override or app source change is introduced.

The npm lockfile audit reports 22 affected mobile packages before the update
(one critical and 21 high), and 19 high with zero critical after. These counts
include affected parent packages, rather than 19 distinct root vulnerabilities.
The remaining root packages are `braces` and `node-forge`; their published
versions are still affected. Desktop's unchanged lockfile reports eight
moderate findings propagated from `sprintf-js`. An audit's suggested major
framework downgrade is not a verified safe remediation.

The Security workflow now audits both native lockfiles at the critical
threshold on pull requests, main pushes and its existing weekly schedule. It
also installs mobile dependencies from the lockfile with scripts disabled and
runs the existing mobile typecheck. Both local critical-threshold audit commands
exit successfully while displaying the unresolved high/moderate findings.
This threshold is explicit and does not mean the dependency graph is clean.

Local clean installation succeeds (625 packages), mobile TypeScript checks
pass, and the installed dependency tree resolves the three patched versions.
Independent source reviews confirm the scoped parent-range compatibility and
workflow behavior. This checkpoint does not establish native packaging,
signing, device behavior, exploitability of every inherited advisory or
completion of the original security requirements.

Primary package advisories:

- [shell-quote command injection](https://github.com/ljharb/shell-quote/security/advisories/GHSA-pqg4-j6r4-53mv)
- [brace-expansion CPU denial of service](https://github.com/juliangruber/brace-expansion/security/advisories/GHSA-q2hr-2g5m-vwhr)
