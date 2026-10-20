# Plani and study-tool restoration — 2026-10-19

## Result

Study & Learn now opens the existing full Plani tutor, sharing conversation
history, declarative interactive exercises, photo input, voice controls, learning
profile and practice paths with `/tutor`. The session/progress workspace remains
at `/learning`. No learning history, tools, data tables or provider restrictions
are removed. The change is based on released `origin/main`, separate from the
unfinished MongoDB migration.

The `/tools` directory restores discovery of 24 existing destinations across
understanding, practice, studying, planning and group work. Search and category
filters progressively enhance server-rendered links; the complete list works
without JavaScript. Restricted cards have an explanation and no destination
link. The existing direct-route/API policy gates remain authoritative.

When a school disables tutoring, the Study & Learn entry shows the toolkit and
its remaining permitted activities. Authorized administrators get an Institution
settings link. Assessment protection remains enforced and is identified in the
tutor header. Admin-only course generation is not promoted to students.

The tutor now has curved, opaque surfaces, a composer in normal layout flow,
expandable practice/assignment controls, and responsive subject choices. Shared
app radius tokens are softened. The phone Plani tab uses the same study entry
and correctly indicates the active page. Both phone menus and command search
include the toolkit.

Saved conversations use native keyboard-operable buttons. Older delayed loads
cannot replace a newly selected conversation or a new chat. Failed deletions
retain the visible conversation and show a recovery message. The history drawer
has a close button, Escape support, focus containment and focus restoration.

## Validation

- 428 focused Python tutor, policy, legacy-tool, assignment and scoring tests pass.
- 47 mobile navigation, shell, layout and UI contract tests pass.
- 51 Node browser-logic checks pass, including nine new history checks.
- Controlled SVG/fraction/quiz browser checks pass at 1280px and 390px, with no
  outbound requests or JavaScript errors.
- The existing disposable HTTP/storage browser suite passes 18 workflows,
  29 responsive checks and 29 scoped axe audits. This includes policy changes,
  scoring, consent, withdrawal, preserved evidence and legacy-tool revocation.
- The expanded Plani verifier exercises rendered templates, interactive sliders,
  voice-policy denial, keyboard history, failed deletion, tool search, school
  restrictions, JavaScript-disabled discovery and non-overlapping composition.
  It supports scoped axe 4.14.0 audits and writes screenshots and reports to
  `output/playwright/plani/`. Final results accompany the delivery checkpoint.
- Changed Python Ruff, JavaScript parsing and whitespace checks pass.

AI replies in browser checks are explicit fixtures. A separate bounded live
provider check used a synthetic adult account and disposable database; it made
zero external requests because no permitted provider was configured locally.
Railway's production service lists Groq key variables, but only names were
available. Neither that configuration nor fixture replies prove current live
provider quality, microphone hardware, camera hardware or spoken playback.

## Meeting walkthrough

1. Open Study & Learn and show the full conversation, a question-led next step,
   and an interactive exercise. Explain that hints and student reasoning remain
   central to tutoring.
2. Open All study tools, search for flashcards, and show that writing, math,
   reading, focus and planning tools remain available.
3. With an authorized demonstration admin account, open Institution settings.
   Show the feature and assessment controls, then use its test student account
   to demonstrate restricted tools and server denial.
4. Open Sessions & progress to distinguish reported work from scored practice.

This report describes local implementation and scoped verification. Production
release and exact served-revision verification must be recorded separately.
