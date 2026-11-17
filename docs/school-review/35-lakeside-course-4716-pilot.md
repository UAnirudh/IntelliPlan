# Lakeside course 4716: privacy review and restricted pilot

Reviewed November 13, 2026. Target: https://lakesideschool.instructure.com/courses/4716.

## Decision and current status

Ready for local technical review of a synthetic-data pilot, not a declaration of legal compliance or school approval. The course URL is a configuration boundary, not evidence that the course contains only synthetic data. No private course content was accessed, credentials created, Groq console settings changed, or deployment performed during this review. Live authenticated testing is pending a school-authorized test identity and isolated deployment.

Do not promise that Groq never stores any data. Its [data policy](https://console.groq.com/docs/your-data) distinguishes prompt/output content from usage metadata. Ordinary inference content is not retained by default, but reliability/abuse logs may retain it for up to 30 days, with legal exceptions. Organization Zero Data Retention (ZDR) disables that logging; persistence features must also be disabled. Metadata remains collected and excludes inputs/outputs. ZDR still involves processing the submitted content.

The [Services Agreement, section 4.2](https://console.groq.com/docs/legal/services-agreement) prohibits model training on inputs/outputs unless the customer explicitly permits or instructs it. Section 3.6 generally excludes beta services from the DPA unless documentation states otherwise. Approve the actual model and feature, not just the provider name. The [Groq DPA](https://console.groq.com/docs/legal/customer-data-processing-addendum) addresses processing on customer instructions and the parties' responsibilities; its existence is not school approval or evidence that every educational use is covered.

## What the school must approve

| Topic | Required evidence or decision | Status |
| --- | --- | --- |
| FERPA | School determines applicability and lawful disclosure basis. If using the school-official exception: legitimate educational purpose, school control over use/maintenance, and use/redisclosure restrictions. Reflect these in the school agreement and subprocessor obligations. | Not established |
| COPPA | Determine ages and applicability. Where school authorization is used, verify actual school authorization and limit use to the approved educational purpose; supply collection/use/disclosure notice and parent access/deletion procedures. Operator retains responsibility. | Not established |
| Security and retention | Written security/retention program, least access, incident response, deletion and backup timelines across all stores, vendor oversight, and auditable operation. | Full operational review pending |
| Provider | Actual Groq organization, all configured keys' membership, ZDR evidence, approved production models/features, applicable agreement/DPA, minor-use eligibility and subprocessor review. | Console/contract verification pending |
| Washington and school policy | Assess applicable Washington school-service/privacy requirements, school procurement terms and student/parent rights. | School/legal review pending |

[DOE school-official guidance](https://studentprivacy.ed.gov/faq/i-want-use-online-tool-or-application-part-my-course-however-i-am-worried-it-violation-ferpa) explains the FERPA conditions. FERPA coverage is funding-dependent; [private K–12 schools are not automatically covered](https://studentprivacy.ed.gov/faq/which-educational-agencies-or-institutions-does-ferpa-apply). Do not assume Lakeside's status from its name or LMS.

The [FTC COPPA FAQ](https://www.ftc.gov/business-guidance/resources/complying-coppa-frequently-asked-questions) describes school authorization and its educational-purpose limits. The [2025 final rule](https://www.govinfo.gov/content/pkg/FR-2025-04-22/html/2025-05904.htm) generally required compliance by April 22, 2026, including enhanced security and retention requirements. Review against the amended rule. [Washington RCW 28A.604](https://app.leg.wa.gov/RCW/default.aspx?cite=28A.604) needs a separate applicability assessment.

## Data flow and limits

Canvas token -> IntelliPlan server -> selected Canvas course/assignments and test-account submissions -> IntelliPlan database/cache/session -> optional AI prompt -> approved provider -> generated output and application storage.

Course descriptions, assignment text, scores, conversations and free text can identify students even after names are removed. Course restriction is not de-identification. No automated comprehensive PII scrubber or all-store deletion guarantee is asserted. The existing [privacy report](08-privacy-security.md) documents legacy, retention and operational gaps that remain. Groq ZDR does not remove IntelliPlan records, browser caches, database backups, host logs or other subprocessors.

## Pilot implementation

Use `deploy/lakeside-pilot.env.example` on a **separate private instance**, with a fresh database, separate secrets and a private access gateway. Do not attach the production database, copy existing student accounts/caches, or enable these deployment-wide restrictions on the public service. Apply the same configuration to every web/worker process that can use pilot credentials; a standalone worker must call `school_pilot.install()` before network work.

The opt-in `school_pilot.py` send guards cover requests and httpx, including current Canvas calls, pagination and shared AI SDKs:

- Course enumeration is replaced **before sending** with `GET /api/v1/courses/4716`; the response is adapted to the existing list callers. Other courses are never enumerated by this path.
- Only exact course metadata, assignment, assignment-group and submission-read paths are permitted. Submission filters are forced to `self`; roster/enrollment, user-wide, file and other course endpoints are blocked.
- Canvas data writes, method overrides, redirects, unexpected query filters and other destinations are blocked. OAuth token exchange/refresh/revocation remains allowed on the selected host. Browser authorization redirects are separate from server data access.
- AI defaults off. Shared provider policy blocks alternatives, including linked-key fallbacks and unauthenticated/background provider calls. Even after approval, only Groq text chat-completions network calls are allowed; audio, vision endpoints, files, batch and fine-tuning are unavailable.
- Server analytics is disabled and Sentry initialization is skipped. Pilot startup requires configured token encryption (`DATA_ENCRYPTION_KEY`). Existing token-encryption behavior outside pilot mode is unchanged.

These are application transport guards, **not a network firewall or browser-content audit**. Direct sockets, other libraries, browser analytics/assets, service workers and alternate deployments need separate control. Restrict outbound networking at the host/proxy to the approved Canvas host (and Groq only when enabled), with narrowly approved database/infrastructure access. Remove unused integration/telemetry keys, disable browser analytics, and review request/body/query logging at every layer. Database connections use a separate transport; isolation and access control are essential.

Some optional features intentionally lose functionality: files, calendar-wide events, roster/enrollment fallbacks, other LMS integrations, media, external resources and external notifications. Do not describe this as full product feature parity.

## Test credentials and course permissions

1. Ask the Canvas administrator to create a **dedicated ordinary student identity**, enrolled only in 4716, with no teacher/admin, observer or other account-level privileges. Prefer synthetic assignments, submissions and grades, and no real student membership/content. If 4716 contains real data, obtain approval or use a clean sandbox first.
2. Have the administrator create a separate OAuth developer key with **Enforce Scopes enabled**. Match the exact read-only scopes in the example configuration, including `url:GET|/api/v1/courses/:id`. Do not use `CANVAS_SCOPES=default` for this pilot because it includes broader user/file endpoints. Set a dedicated pilot callback URI and reconnect to issue fresh grants.
3. Endpoint scopes use placeholders; they do **not** bind a token to course 4716. Canvas permissions from the isolated identity and IntelliPlan's server boundary both matter. See [Canvas developer-key permissions](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth) and [single-course endpoint](https://developerdocs.instructure.com/services/canvas/resources/courses).
4. Authorize through Canvas OAuth using that identity. For an administrator-approved temporary manual-token test, generate an expiring token belonging only to that identity. Never share your normal school password, personal all-course token, teacher/admin token, client secret or Groq key with reviewers.
5. Create a separate IntelliPlan test account on the private pilot instance. Give reviewers its URL, test-account username and a short-lived password through the school's approved password-sharing system. Supply the secret separately from the review document; restrict recipients and revoke access after review. No working credentials have been generated by this change.

## Groq activation, if the school wants AI in the pilot

Keep AI off for the initial Canvas sync demonstration. To enable synthetic AI testing:

1. An organization admin opens [Groq Data Controls](https://console.groq.com/settings/data-controls), enables organization-wide ZDR, disables persistence features, and retains a dated settings record tied to the actual organization.
2. Use only keys from that organization; remove Gemini, Anthropic, Fish Audio, vision/audio-specific and user-supplied provider keys from the isolated instance. Approve the actual model terms and production feature status.
3. Confirm the school agreement, applicable Groq terms/DPA, minor-use conditions, retention/deletion and subprocessor review. Ask Groq in writing to resolve any ambiguity about educational records, children's data, metadata, legal retention and selected feature coverage.
4. Only then set `SCHOOL_PILOT_AI=groq`, `SCHOOL_PILOT_GROQ_ZDR_CONFIRMED=1` and `SCHOOL_PILOT_CONTRACT_APPROVED=1`, with the approved `GROQ_API_KEY`. These flags are operator attestations; they do not configure Groq or prove compliance. They apply deployment-wide, so no unrelated keys/users belong here.
5. Capture a synthetic text request and network evidence that no other provider was contacted. Review host logs without collecting prompt content or credentials. Do not introduce real records until school approval is complete.

## Verification and closeout

Local result on November 13, 2026: **131 passed**, covering `test_school_pilot.py`, `test_ai_provider_fallback.py`, `test_ai_speech_provider.py`, `test_assignment_materials.py`, `test_canvas_gradebook_detail.py` and `test_canvas_course_headline.py`. This includes 32 pilot cases. The configuration-only preflight also passed for 4716 with AI disabled. These are mocked transport/provider checks, not a full application suite, browser walkthrough, authenticated Canvas run, or production deployment verification.

`python -m pytest tests/test_school_pilot.py` exercises the real requests/httpx send boundary with fake transports, including other-course requests, list rewriting, pagination escape, redirects, write denial, query manipulation, unknown destinations, SDK denial, async denial, policy filtering and normal non-pilot behavior. No live credentials or provider requests are used.

For a config-only preflight, set the pilot variables and run `python scripts/check_school_pilot.py`. To check authenticated access, the authorized operator supplies `SCHOOL_PILOT_CANVAS_TEST_TOKEN` privately as an environment secret and runs `python scripts/check_school_pilot.py --live`. Never put the token on the command line or in a tracked file. The script checks HTTP success and response shape for course/assignment access, prints no record content and does not probe an unrelated real course. It does not verify console settings or identity enrollments.

Before sharing credentials, perform a live private-instance walkthrough: connect, inspect course list/assignments, verify only 4716 network calls, exercise expired/revoked token handling and blocked file/other-course requests, check cache/log content, confirm the identity has no other permissions through school-admin inspection, and verify a rejected provider does not receive a request. Document operator/date/results. At closeout revoke reviewer access and Canvas grants; delete the isolated app data and handle backups/logs according to the approved retention schedule.

## Copyable note to the CS department

> I have prepared a restricted IntelliPlan pilot for Canvas course 4716. The proposed setup uses a dedicated test student enrolled only in that course, a separate IntelliPlan instance/database, read-only Canvas scopes, and a server-side course restriction. External AI is disabled initially. Groq supports Zero Data Retention, but I am not claiming its organization settings or legal approval have already been verified. Before enabling AI, we will confirm the actual Groq organization's ZDR settings, approved model/features, applicable contractual terms, and the school's privacy requirements. Initial testing will use synthetic data. I can provide reviewer access through the school's approved credential-sharing method after the isolated instance and test identity are ready.
