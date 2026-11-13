# COPPA readiness and school approval — November 13, 2026

## Release decision

**Not certified compliant; do not launch under-13 use on the strength of this report.** The local changes close specific access and consent defects. They are not deployed, and do not establish provider configuration, an executed agreement, guardian verification, all-store deletion, or institutional approval.

The live browser review was attempted but the browser origin-approval gate denied access to intelliplan.tech. No alternative route was used to bypass it. This review concerns repository code and local synthetic tests, not observed production behavior.

## Verification evidence

Final focused local run: **306 passed**, 607 warnings, in 37.81 seconds. Scope: child privacy, policy compliance controls, API registration, email eligibility, school pilot, school outreach/policy, privacy notices, mobile API, API keys, welcome delivery, token encryption, account deletion, AI fallback and speech policy tests. Tests use disposable SQLite and synthetic records/mocked providers; no real student records or live email/provider calls were used. JavaScript syntax, changed Python syntax and diff whitespace checks passed. The three changed mobile TypeScript files passed syntax validation with TypeScript 6.0.3. Full mobile type checking/native builds, the full repository suite, live Canvas authentication, production provider settings and deployed-browser behavior were not verified.

## Implemented controls

- New known-under-13 independent web signups are rejected before account storage and before parent emails are sent. The form no longer asks for a guardian's email for this unavailable signup path.
- API, mobile and extension registration requires a valid birth year before storing an account or issuing a credential. The youngest possible age is used; a birth year alone cannot establish that this year's thirteenth birthday has occurred. Mobile and current extension forms now send the year.
- Existing OAuth accounts identified as under 13 are restricted without collecting a new guardian email or sending an activation link. Existing minimal account data is not silently deleted.
- Possession of a parent email link no longer activates an account, records verified approval, or sends a welcome message. Pending-signup removal remains available. Legacy email-approved known-under-13 accounts remain held on the tested cookie, bearer, API-key, extension, provider and lifecycle-email paths.
- Unknown-age native users may authenticate to reach minimal status and age setup; coursework stays blocked. Extension credential access requires known age. This is not an assertion that every legacy cookie, desktop, child-profile or anonymous route has undergone a complete age/consent audit.
- The public contact section includes the user-authorized phone number, 425-286-3759, and existing email, uanirudh0811@gmail.com. The service brand is IntelliPlan. A mailing address and confirmed legal operator identity remain missing.
- Privacy notice version 7 describes the under-13 restriction. Account deletion wording no longer promises that deleting a pending row immediately erases every log and backup. Pending-removal logs no longer print the child's email address.
- The separate [course 4716 pilot](35-lakeside-course-4716-pilot.md) remains available for isolated synthetic testing, with external AI off by default. It must not share the public production database or be enabled deployment-wide on the public service.

## Requirements still needing evidence

The [FTC compliance plan](https://www.ftc.gov/business-guidance/resources/childrens-online-privacy-protection-rule-six-step-compliance-plan-your-business) covers coverage assessment, policy/direct notice, consent, parental rights, security and retention. The [amended rule](https://www.govinfo.gov/content/pkg/FR-2025-04-22/html/2025-05904.htm) generally required compliance by April 22, 2026. The operational work below must be completed against the current rule; a policy document alone does not satisfy it.

| Requirement | Required completion evidence | Owner/action |
| --- | --- | --- |
| Operator and contact notice | Actual legal operator and public mailing address; complete operator/service-provider notice information; monitored parent request channel | Operator supplies facts; school/legal review |
| Coverage and age design | Assess child-directed and mixed-audience features, age screens before unnecessary data collection, OAuth, anonymous use, desktop and child profiles | Privacy/legal assessment plus route/network audit |
| Verifiable authorization | Implement and validate an appropriate parental verification method or a specific lawful school arrangement; record notice version, scope, authority, verification and withdrawal | Select and contract with a reviewed verification service or approve a documented supported process; no self-attested email override |
| Direct notice and disclosures | Accurate purposes, information categories, relevant operators/providers, choices and consent method; separate consent where required for nonintegral third-party disclosures | Review actual feature/provider data flows |
| Parent rights | Verify authority, give access/correction/deletion and stop future collection promptly; avoid disclosing another person's data | Document and test an end-to-end request workflow |
| Retention and deletion | Written purpose-based schedule covering pending signups, account/LMS data, chat, uploads, vector indexes, analytics, security records, emails, caches, logs and backups; automated enforcement and deletion evidence | Infrastructure/operator implementation; existing learning-retention command covers only a subset |
| Security program | Named responsible person, risk assessment, least-privilege access, encryption/key handling, authentication, patching, incident response, recovery tests and periodic review | Operator plus infrastructure owners; written program must match actual practice |
| Vendors and AI | Contracts and data-use restrictions for every processor; actual Groq organization ZDR proof, approved models/features, no unapproved fallback, minor-use terms and deletion commitments | Operator, provider and school procurement/legal |
| School approval | Exact review form/questionnaire, required evidence, agreement/DPA, FERPA basis where applicable, acceptable data, SSO/accessibility/security expectations and sign-off | Ask Jaime Britto and designated school reviewers |

The [FTC COPPA FAQ](https://www.ftc.gov/business-guidance/resources/complying-coppa-frequently-asked-questions) explains that school authorization has limited educational scope; the operator retains responsibility. Ordinary email acknowledgment is not implemented here as a verified authorization method. No claim is made that every AI provider is a disclosure under COPPA in every arrangement; review the actual contractual role and use.

## Security and retention working procedure — draft for approval

1. Assign the actual responsible operator and incident contact. Inventory every data store and provider, including browser assets and hosting logs. Record necessity and authorized purpose for each collected field.
2. For each store, approve the shortest necessary period, lawful exceptions, deletion trigger, executable job, backup expiration and verification evidence. Do not represent proposed periods as implemented settings. Existing pending signups need a reviewed cleanup decision; none were deleted during this task.
3. Review access and secrets with least privilege. Require token encryption on the pilot; verify encryption and key access on production separately. Use separate test identities, synthetic content, isolated infrastructure and scoped read-only Canvas access.
4. Receive rights requests at the listed privacy contact, stop disallowed further collection promptly, verify the requester's authority, search all stores, fulfill access/deletion and preserve only the minimum legally necessary records. Test with synthetic data, including backups and vendor propagation.
5. Review risks, changes and vendors regularly and after any incident. Keep an incident runbook with containment, credential revocation, evidence preservation, investigation and applicable school/legal notice obligations. Do not invent a universal breach-notice deadline.
6. Before any under-13 reopening, review family/Foundations profiles, third-party browser assets, anonymous tools, OAuth pre-age collection, background jobs and other credentials. A parent's adult account and a grade selection do not themselves prove verified authority for every child-facing use.

## School pilot acceptance

Have Lombardi/Canvas administration confirm whether course 4716 is a clean sandbox. Request a dedicated ordinary test student enrolled only in that course. If it contains real student records, use an approved clean sandbox and update the boundary before testing. Request reviewers' own IntelliPlan test account on the private instance; share its credentials through an approved channel, not an email attachment or personal school password.

Ask Jaime Britto for the school's exact approval requirements before claiming readiness. The accompanying email drafts mention conversations with Whitney and Hans de Grys as requested, without claiming their endorsement. No email was sent.
