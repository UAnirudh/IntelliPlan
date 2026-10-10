# School technical review, administrator guide and limited pilot

Current decision: **demonstrable locally, not approved for student deployment**. Lakeside technical reviewers can inspect a reproducible demo and source/tests. No Lakeside or district authorization has been received in this work.

## Demonstration with synthetic accounts

Show a district restriction that the child school cannot loosen; scoped school admin settings and audit; protected concept help and denied direct APIs; a purpose-based paper/retrieval session with pause/reload/resume; curated Python outline and original source attribution; fixture-generated lesson review and server comparison; builder results visibly assisted; course flag/Premium denial; private export and legacy tutor/practice links. Explicitly disclose fixture AI and local-only verification throughout.

## Operator setup

1. Review the patch and reproduce automated checks on disposable SQLite. Rehearse additive schema setup and deletion/rollback against a restored PostgreSQL copy before production.
2. Use an existing authenticated platform-admin session to POST `/api/school-admin/organizations` with name, `kind='district'` or `kind='school'`, optional district parent ID and restrictive policy. No open self-service organization provisioning exists.
3. Grant memberships through `/api/school-admin/organizations/<id>/members` using existing account IDs and roles. Only a platform admin may grant the admin role. No automatic role is inferred from a school email. Organization creation/administrative membership bootstrap currently requires the API, not a provisioning wizard.
4. School/district administrators use `/school-admin`. Save the displayed version with policy updates; a concurrent change returns409 and requires reload. Changes are replacements of that organization's local policy; parent restrictions still apply. The UI shows local checkboxes, not every computed effective child/user restriction.
5. Keep course flags absent/disabled for ordinary use. For a reviewed admin evaluation, an operator may explicitly create/enable `school_course_generator`; eligibility and institution `allow_beta` still apply. No general flag-control UI or beta opt-in registry was added.
6. Configure only institutionally approved provider credentials/contracts. An allowed provider name alone does not enable a provider whose existing contractual gate is closed. Restrict provider lists and third-party processing. Do not place private student details in course goals.
7. Schedule/supervise bounded `python -m flask --app App learning-worker --steps 25` batches. Monitor job failures/reservations, leases and partial lessons. HTTP refresh does not start a worker. No production worker is provisioned in this checkpoint.
8. Review source license terms and every original lesson/answer key. Human review is a claim by the evaluator, not certification of factual accuracy. Never use admin-builder checks as independent student mastery.
9. Enable strict assessment protection during the intended assessment period. It supplies limited reviewed concept assistance and blocks model generation. There is no automatic school calendar/assessment-window scheduler yet.

Website Focus Shield depends on the optional browser extension; web pages cannot stop other applications or manage a school device. Device/MDM restrictions were not implemented. Reading/paper inactivity never creates a diagnosis or a cheating judgment. Emergency exit remains available.

## Gates before a real pilot

Require provider audience/retention/region terms and agreements; school-purpose/authorization review; age/guardian and international privacy review; retention/audit-reference cleanup; SSO/roster correctness; central-policy coverage of background/unmapped paths; worker/backup/monitoring runbook; actual Canvas/StudentVUE/calendar tests; keyboard/screen-reader/contrast testing; learner content-quality evaluation; deployed app verification. General opt-in beta lifecycle and student-facing original course rollout are incomplete.

After those gates, a proposed limited pilot could involve a small volunteer class or administrator review group, school-approved goals and no assessment answers. Establish success thresholds with the school before launch: independently scored understanding/retention, failure/error reporting, permission-bypass checks, user control/accessibility, measured generation costs and deletion requests. Do not invent a learning improvement percentage before collecting reviewed evidence. Keep generator use to approved administrators until separate promotion decisions are complete.
