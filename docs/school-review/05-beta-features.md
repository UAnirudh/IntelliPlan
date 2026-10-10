# Beta feature report

The new capability registry centralizes request/worker eligibility for the following capability groups. This is not an exhaustive independent maturity audit of every legacy feature or every old feature flag.

| Registry maturity | Capability | Reason and actual control |
|---|---|---|
| Admin-Only Beta | `course_generator` | New curated/original course workspace; human review and operator evaluation required; explicit flag + admin + institution beta permission |
| Admin-Only Beta | `generated_content` | Original lesson generation; same eligibility plus allowed provider processing; live provider blocked locally |
| Internal Alpha | `video_generation` | No rendering adapter; denied even if an operator enables its flag |
| Opt-In Beta | None added | A general per-user versioned beta opt-in, preview eligibility and feedback lifecycle is not implemented |
| Stable (legacy operational designation) | `tutor`, `study`, `review`, `focus`, `scheduler`, `canvas`, `studentvue`, `calendar`, `uploads`, `notifications` | Already shipped capability groups retained without breaking existing access; this designation does **not** certify live reliability, school authorization, instructional effectiveness or all contained experiments |

Limits are returned alongside maturity metadata. StudentVUE explicitly requires separate district authorization/live reliability verification. Focus explicitly depends on the optional extension for website restriction and retains emergency exit. AI and practice data explicitly carry accuracy/mastery limits. No new marketing claim should turn these legacy registry values into independently earned Stable quality badges.

Missing experimental flags deny. `allow_beta=false` defeats admin course eligibility. Subscription status never overrides the admin-only gate. Video stays unavailable. Institution policy restrictions are enforced at mapped routes and shared provider calls, rather than relying on hiding buttons.

Promotion/demotion requirements are still process work: a complete legacy feature inventory, measured reliability/quality thresholds, beta opt-in records, release-owner decisions, feedback/incident reporting and a tested kill-switch playbook. Until then, demonstrate only the specific verified flows and keep the new generator disabled for normal accounts.
