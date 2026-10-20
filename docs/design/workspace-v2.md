# Workspace v2 delivery and acceptance

This local update follows the supplied IntelliPlan and ChatGPT/Codex screenshots:
one continuous workspace, readable conversation width, quiet controls, and depth
on navigation and inputs. Soft inset/raised controls combine with restrained glass
on chrome. Content and interactive diagrams do not pay for backdrop blur.

## Implemented behavior

| Request | Implementation | Evidence |
| --- | --- | --- |
| Roomier, fluid UI throughout the site | Shared tokens, responsive surfaces, typography, native enhanced dropdowns, persistent sidebar and panel controls; tutor header, unboxed replies and two-row composer | `verify_workspace_v2_browser.py`, `verify_fluid_workspace_browser.py` |
| Smooth transitions | Same-origin browser view transitions, panel entrances and native appearance-dialog entrance; OS and user reduced motion settle current animations and prevent new ones | Actual Chromium + vendored GSAP verification, frontend review |
| Customization | Appearance dialog: spacing, reading width, surface, accent, motion and character; stored on this device, phone menu and keyboard focus restoration | Workspace browser script |
| Correct AI formatting | Shared Marked renderer plus DOMPurify; JSON reply wrappers, six heading levels, lists, tables, code; executable markup removed | Workspace browser script, independent frontend review |
| Responsive visual exercises | SVG nodes retained; input events update descriptions immediately and coalesce drawing into one animation frame; global motion observers ignore diagram redraws | 300-input browser exercise with retained-node and mutation assertions |
| Audio | Per-reply read-aloud, shared status, manual play after autoplay denial, timeout/provider/decode browser fallback, stop/new-reply cancellation, URL cleanup; 401/403 never bypassed | 10 browser-runtime unit tests; dedicated speech-provider tests |
| Useful tool catalog | `/study-tools` wired to a previously unreachable catalog, full-card native links, search/categories, visible policy restrictions | Foundations/tools browser script |
| Useful Foundations | 3/6/9-question sessions, independent versus assisted feedback, saved progress, next skills, grade selection; stale grade/learner responses ignored | 27 Python tests; 13 browser flows including delayed responses and learner switching |
| Faster retrieval | Today explanations reuse the existing 90-second owner/policy-stamped payload, avoiding a second connector/AI context build; mutations and policy changes invalidate it | Command Center and scheduling service tests |
| Better scheduling | Real calendar feasibility, pinned-duration preservation, urgency ordering, fast all-fit days and fewer repeated candidate simulations | Scheduler tests and independent feasibility oracles |
| District tool switches | Independent inherited policy decisions, direct-route/API enforcement, saved/reloaded controls | Policy integration tests and fluid workspace browser script |
| Grant admin access | Owner-only exact-account lookup, user-ID-bound grant/revoke, audit records, active-session revocation; delegated admins cannot grant admins | Adult access tests and browser script |
| Teacher and parent entry | Role-specific adult signup/sign-in, teacher setup, existing parent portal, explicit student-approved sharing, tasks and optional tool-policy view, revocation | Adult/teacher tests and browser script |

Adult dashboards only show explicitly linked students and granted scopes. Adding
the optional tools scope does not expand existing shares. The new account-admin
grant model and adult routes target the current SQL-backed application; SQL-free
Mongo native parity is outside this update and is not claimed.

## Library use

Existing local Watermelon ports remain in use. The new appearance dialog,
disclosures, pill controls and collapsible navigation adapt free component patterns
to Jinja and native browser semantics:

- [Watermelon components](https://ui.watermelon.sh/components)
- [shadcn sidebar](https://ui.shadcn.com/docs/components/radix/sidebar)
- [Basecoat components](https://basecoatui.com/components/)

AI prose uses pinned same-origin Marked 18.1.1 and DOMPurify 3.4.17 under
`static/js/vendor/rich-text/`, with their license notices retained. Marked requires
sanitization; DOMPurify runs on every rendered AI response. See the primary
[Marked documentation](https://marked.js.org/) and
[DOMPurify project](https://github.com/cure53/DOMPurify).

## Performance and verification limits

Seven-run local benchmark medians against HEAD: 20-item fitting-day sitting work
4.969 to 0.422 ms; normalization of 60 deadlines 1.950 to 1.180 ms; exact-clock
20-item pipeline 23.970 to 14.401 ms. Some larger pipelines cost more than HEAD
because they now perform actual calendar-feasibility checks (60-item example:
142.675 versus 125.756 ms). These are local CPU benchmarks, not live connector or
end-to-end latency claims. Reproduce with `scripts/benchmark_scheduler_pipeline.py`.

The broad integration run passed 920 tests; separate speech and adult/auth suites
and independent frontend/backend reviews also passed. The shared workspace matrix
passed 54 page/viewport layouts and four interaction flows, plus the district and
sidebar interaction rerun. Foundations passed 13 browser flows and six layouts;
adult/admin acceptance passed 20 checks including 16 screenshots at desktop and
phone widths. The final phone composer check asserts that scrollable content ends
above the composer. A 300-input exercise completed in 6.2 ms on the local machine
with zero SVG child-list mutations (timing is illustrative, not a service SLA).
Browser scripts use isolated
SQLite accounts and block external browser traffic. Screenshots/reports are under
`output/playwright/workspace-v2/`, `foundations-tools/`, and `adult-access/`.

No Fish or Groq speech key is configured in the checked local environment. Browser
fallback paths are tested, but real provider synthesis and physical speaker output
are not verified. Groq Orpheus permits only 200 input characters; longer replies use
the browser fallback when Groq is the configured provider. The official limit and
voice list are documented at [Groq Orpheus](https://console.groq.com/docs/text-to-speech/orpheus).

Changes are local. No production deployment, schema cutover, commit or push has
been performed by this update.
