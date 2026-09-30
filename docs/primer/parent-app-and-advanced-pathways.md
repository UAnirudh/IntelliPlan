# Foundations and Family app: product and delivery plan

## Product thesis

IntelliPlan already knows the work a student must do and can offer practice in the same account. The opportunity is to connect **planning, evidence of learning, and a supportive adult** without making either practice or parenting feel like surveillance. The learner owns the work and controls linked-account access. The parent sees what the system actually observed, what remains uncertain, and one useful next step.

## Learner pathways

| Stage | Initial scope | Adaptation |
| --- | --- | --- |
| K–8 | Existing reading, writing, and arithmetic story activities | Grade placement, prerequisite gates, missed-skill repair, hint-aware review |
| Grades 9–12 | Argument reading, precise writing, algebra through quantitative reasoning | Same evidence engine, age-appropriate case narratives, grade-specific skill and item bank |
| College | Research and quantitative literacy foundation | A separate college placement rather than pretending year in college implies mastery; scaffolding revisits grade 12 when needed |

The question bank uses versioned, deterministic item IDs. It stores answers and explanations on the server and attempt evidence per learner. A correct answer with a hint is distinguished from independent success. The first expansion adds 30,000 reconstructable items across 30 high-school/college skills to the existing K-8 bank. The items vary numbers, setting, and source evidence; their number alone is not a claim of 30,000 independently reviewed lessons. This covers foundational competencies, not a complete course catalog or an unvalidated AI assessment of essays.

## Why this belongs in IntelliPlan

The student's plan already contains real deadlines and classes. Foundations turns a weak or due skill into a small practice step; Family turns observed progress and imminent work into a useful conversation starter. The loop is **plan → practice → evidence → next step → support**. This is more distinctive than a separate worksheet library because each surface can improve the next action without exposing private tutor dialogue to an adult.

The product standard is retained learning and reduced planning friction. Question count, app opens, and streak length are diagnostics, not the outcome. Teacher and parent views should help the student make the next decision, while the student can see and revoke every accepted link.

## Family app: parent jobs

1. **Connect safely.** Invite a student, show pending state, and require the student's explicit acceptance. The student chooses whether to share assignments, recorded study, and Foundations practice, and can change that selection later. College sharing always requires explicit student opt-in; either party can revoke a link.
2. **Know what happened.** Show completed and upcoming work plus Foundations practice, with timestamps and explicit source/limits. Never infer time spent from page visits.
3. **Know what to do next.** Surface the nearest due item, overdue work, and a calm prompt for a conversation; do not turn an absence of data into a failure label.
4. **Support without nagging.** Send one bounded, in-app encouragement at a time, allow withdrawal, and show whether the student acknowledged it. Avoid SMS/email pressure by default.
5. **Protect autonomy.** Parent access is scoped to accepted links, limited to a deliberately small overview, and visible to the student. Personal tutor conversations and raw responses remain private.

## Architecture

- Serve a distinct Family application shell on `parent.intelliplan.tech` from the existing Flask deployment, with its own hostname and host-only session cookie. The public student hostname remains canonical for student pages and OAuth callbacks. The same database and account system permit accepted links without credential duplication.
- Expose a host-aware Family root, dedicated CSS/JS, and narrow same-origin JSON APIs. Reuse `StudentLink` for consent, add a small durable table for bounded encouragement, and derive workload from the real assignment repository. Keep account and learner IDs in every authorization query.
- Keep Foundations content generation deterministic and versioned. Extend grade validation to high school and one college foundation track, while retaining existing K–8 attempt history and IDs.
- Require TLS and a DNS/custom-domain binding for the parent hostname in deployment. A code release alone cannot create a working public hostname; verify actual DNS, certificate, routing, login, and API responses after binding.

### Data and algorithms

- **Identity and consent:** one account table; `StudentLink` records the adult, student, relationship, pending/accepted state, and the student's selected sharing scopes. Every Family read joins an accepted parent link to the exact learner account and applies those scopes before loading each evidence source. College access follows this same explicit approval path. Revocation removes the link and associated notes. Links accepted before scoped sharing retain their prior summary access until the student changes it.
- **Practice evidence:** `primer_learner`, profile, skill state, hint, and attempt tables keep versioned evidence without storing raw answer text. Grade placement selects an initial curriculum level. After two misses without independent success, a domain can step back one grade for a prerequisite activity, then return when evidence improves.
- **Next question:** choose an unlocked skill due for review, prioritize weak evidence, and reconstruct a stable item ID on demand. A missed answer is due immediately; successful attempts return after one, three, or seven days according to streak and hint use. The smoothed estimate `(correct + 1) / (attempts + 2)` is a ranking signal, not a calibrated probability of mastery.
- **Family workload:** read active assignments from the same repository as Command Center, remove LMS work the student marked complete, and combine it with recorded manual completions. Work, recorded study, and Foundations are fetched independently so one failed source does not blank the others; unshared or unavailable metrics are never displayed as zero. Sessions come from student-app records; their count does not prove off-app study time. Individual LMS adapters can still return an empty list on upstream failure, so a zero task count is not proof that every source synced successfully.
- **Support:** send only reviewed in-app message templates, with one pending note and a 24-hour interval. The student can acknowledge a note or revoke the link. No automatic pressure campaign runs from sparse activity data.

### Runtime and operations

The current release uses the existing Flask service and SQL database, a separate host-aware Family shell, server-side item reconstruction, and same-origin JSON APIs. It does not need a separate queue or model call for every exercise. Store only small practice events and bounded note records. Monitor API error rates, item reconstruction failures, consent/revocation events, and slow assignment fetches. If an LMS is unavailable, Family should mark the summary unavailable rather than display a reassuring zero. When traffic warrants it, move LMS fetching and aggregate reporting to a background job with explicit freshness timestamps; the authorization rule remains on the read path.

### Production hostname status

Railway production now has a custom-domain binding for `parent.intelliplan.tech` on the existing web service. The DNS provider must add a CNAME record for `parent.intelliplan.tech` pointing to `oabt5ta0.up.railway.app`. Railway reported certificate ownership validation pending on 2026-09-27. The hostname is not considered live until the record resolves, TLS is issued, and a real browser request succeeds.

On 2026-09-28, public DNS still returned NXDOMAIN for the parent hostname. The authoritative nameservers are Namecheap's registrar-servers.com. This workspace has no Namecheap API access. Add the parent CNAME and any ownership-verification TXT record currently displayed in Railway's domain settings. The complete Family shell also runs at `https://intelliplan.tech/parent`: its sign-in and adult registration preserve that destination, with the same student-consent APIs as the subdomain. The subdomain keeps a host-only session; the path fallback uses the main site's existing account session.

## Verification and iteration gates

1. Unit tests for advanced item reconstruction, keys, placement, and ownership.
2. API tests for pending/accepted/revoked links, role boundaries, college opt-in, and nudge throttling.
3. Browser checks for desktop/mobile layout, sign-in to Family, accepted-link overview, and a learner seeing a note.
4. Full repository test suite, diff review, branch push, and live-host verification when deployment access exists.

## Longer horizon

1. Add teacher-authored course maps and diagnostics that identify misconceptions, with sampled human review of content and age-appropriateness.
2. Evaluate learning gain with delayed checks, not only immediate correctness; calibrate skill estimates against those checks before calling them mastery.
3. Let a tutor explain alternate strategies and discuss evidence, while keeping objective grading separate from generative feedback. Evaluate open writing with a reviewed rubric and an appeal path before presenting scores as reliable.
4. Offer teachers cohort-level intervention signals and parents optional conversation guides. For adults in college, maintain student-controlled sharing and granular scope choices.
5. Expand to longer narrative arcs and student-created projects when they measurably improve persistence and transfer. Measure retained learning and useful parent conversations, not raw question volume or time on site.
