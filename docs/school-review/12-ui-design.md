# Learning workspace UI pass — 2026-10-10

The engineering checkpoint was saved as `ca0b8b0` before this design pass. This pass improves the implemented Learning Hub, its activities/course workspace, and institution settings. The full master brief and a whole-product redesign remain incomplete; provider, production and educational-quality requirements remain in [the acceptance ledger](11-remaining-work.md).

## References inspected

Live sites were opened and rendered in a fresh, signed-out Chromium context:

| Reference | Visual observation and application |
|---|---|
| [Skydive](https://www.skydive.com/) | Quiet neutral canvas, concise navigation and a distinct main action. Applied a calmer activity bar and one prominent composer action. |
| [Cluely](https://cluely.com/) | Expressive typography and a product surface beneath a clear heading. Applied a serif study-guide heading beside the working composer. |
| [Linear](https://linear.app/) | Restrained surface contrast and clear hierarchy in a dense working product. Applied theme-aware surfaces, course preparation stages and grouped administrative controls. |
| [Notion](https://www.notion.com/) | Broad spacing, readable text and workspace context. Applied a main work column with a supporting guide and links to existing tools. |

These are design interpretations from the pages observed on this date. No reference-site assets, logos, claims or lesson content were copied. The existing font stack, theme palettes, navigation and rendering stack remain in use; this adds no production dependency.

## Implemented behavior

- A smaller introductory heading brings the workspace nearer the top. Native activity buttons retain keyboard behavior and announce selection with `aria-pressed`.
- Question starters populate and focus the composer and select the learning approach. They send no provider request until submission.
- Tutor messages identify their speaker. Failed requests preserve the draft without adding phantom history; successful replies preserve a newer draft typed while waiting. Busy controls expose `aria-busy`.
- The guide separates a question, an attempt and later recall. Foundations, study materials, flashcards and tutor history remain reachable.
- Course preparation stages and lesson progress use persisted state/counts. Native outline disclosure retains editable drafts; later outlines show readable text. Empty libraries explain the next step.
- Institution settings group student activities, connections, beta/AI processing, providers, budgets and retention/assessments. Members, audit, approvals and explicit feedback have distinct sections.
- Progress follows study permission. When no activity is permitted, an access explanation replaces panels without fetching a blocked progress API.
- Surfaces use shared foreground/background/focus tokens in both themes. A flat workspace background makes text contrast measurable. The existing `data-no-motion` opt-out prevents decorative entrances from hiding or shifting workspace content; small control color transitions honor reduced motion.

## Evidence and limits

The disposable-browser verifier passes ten actual HTTP/storage workflows and sixteen UI checks across 320,390,768 and1440px widths, both themes, restricted student access, reviewed courses and consented/scored practice. Geometry checks cover enabled form controls/buttons, using clickable labels for checkboxes; they do not measure every shared navigation or prose link. JavaScript errors:0. External generation remains a declared fixture.

Optional axe-core4.14.0 runs sixteen audits scoped to `.learning-hub` with WCAG2 A/AA,2.1 A/AA and2.2 AA tags. The recorded results contain no automated violations; manual-review cases remain in raw `incomplete` entries. This is not screen-reader validation or WCAG conformance, and excludes unrelated shared navigation/widgets and legacy pages. The focused suite passes77 tests; the engineering checkpoint's full suite passed4020 with24 skipped.

See [browser results](evidence/browser-results.json), [UI regression results](evidence/pytest-ui.txt), [desktop](evidence/learning-hub-desktop.png), [dark mode](evidence/learning-hub-dark.png), [mobile](evidence/learning-hub-mobile.png), [course workspace](evidence/course-workspace-desktop.png), [institution settings](evidence/school-admin-desktop.png) and [preview dark mode](evidence/practice-preview-dark.png). Accounts, courses, approvals and scores shown are disposable fixtures.

Repeat the optional axe checks without adding a production dependency:

```powershell
$verificationPrefix=Join-Path $env:TEMP 'intelliplan-ui-a11y'
npm install --prefix $verificationPrefix --no-audit --no-fund --ignore-scripts axe-core@4.14.0
$env:AXE_CORE_PATH=Join-Path $verificationPrefix 'node_modules/axe-core/axe.min.js'
python scripts/verify_learning_hub.py
```

Without `AXE_CORE_PATH`, workflows/geometry still run but accessibility results are empty. The testing report documents the optional Chromium executable override. The [axe API documentation](https://github.com/dequelabs/axe-core/blob/develop/doc/API.md) defines rendered-content checks; hidden activities are audited after being made visible.
