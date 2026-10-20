# Fluid workspace

The shared web shell uses nested radii, a floating navigation surface, quieter
borders, and a consistent spacing scale. The same controls load through
`base.html`, including on public pages. App cards and full-screen conversations
receive additional layout styling in `static/css/ip-fluid.css`.

## Components and references

The app already has vanilla implementations of Watermelon components in
`ip-ui.css` and `ip-ui.js`. Keep those components and the Jinja stack. Reference
patterns were checked against these primary sources during this update:

- [Watermelon UI](https://ui.watermelon.sh/free/react-components): free MIT
  components; retain the existing framework-compatible ports.
- [shadcn sidebar](https://ui.shadcn.com/docs/components/radix/sidebar): collapsed
  navigation, a visible trigger, independent panel state, keyboard access.
- [Basecoat select](https://basecoatui.com/components/select/): restrained picker
  surfaces and clear selection states. The implementation here keeps real native
  selects, with `appearance: base-select` enhancement on supporting desktop
  browsers. Phones and older browsers retain native pickers.
- [Uiverse](https://uiverse.io/): MIT UI examples for rounded controls and quiet
  interaction states.

New code is implemented locally against the existing tokens. No React runtime,
Tailwind build, remote executable components, or new packages are introduced.

## Layout behavior

- The main sidebar retains Auto / Expanded / Collapsed, hover preview, the
  visible handle, and Ctrl+Backslash. The existing device preference is retained.
- More tools / Fewer tools minimizes secondary navigation. The active tool stays
  visible, and saved ordering and custom visibility continue to apply. Phone Menu
  retains every permitted destination regardless of desktop disclosure state.
- Chat history, dashboard overview, and the Learning Hub study guide can be
  hidden independently. Preferences contain layout state only. The tutor's
  existing mobile drawer takes over when the viewport becomes narrow.
- Controls keep visible focus indicators and native form/keyboard semantics.
  Reduced motion preferences disable the new motion.

## Institution tools

`policy.FEATURES` and `enforcement.SURFACES` add independent controls for writing,
math, extraction, text dissection, grade modeling, meetings, study groups,
memories, pets, balance, and Command Center. Existing tutor/study/source
restrictions still apply. A school cannot override a district denial.

`/school-admin` shows searchable tool controls, inherited denials, saved policy
versions, and reload. Editing is locked during save, and saving refreshes the
organization list so child restrictions reflect parent changes. Stale writes
remain rejected. Administrators can only change organizations they administer.
The browser hides restricted navigation and disables tool links using the same
server route map; direct HTTP requests are independently enforced. Disabling
Command Center sends its page navigation to the classic dashboard, preserving
the post-login destination while denying the assistant endpoint.

No existing district policy is changed by installing this update; an authorized
administrator must save their intended choices.

## Verification

Observed results: 439 targeted tests passed. The page matrix completed all 39
page/viewport combinations without horizontal overflow or JavaScript page errors.
The final focused interaction run passed sidebar/panel persistence, mobile
history, keyboard selection, save/reload, parent-policy refresh, direct API
denial, and reduced motion. It also refreshed the light/dark previews.

- `python -m pytest tests/test_learning_hub.py tests/test_mongo_learning_policy.py
  tests/test_app_shell_contract.py tests/test_plani_policy.py
  tests/test_learning_legacy_tools.py -q --disable-warnings`
- `node --check` for changed JavaScript files; `git diff --check`.
- `python scripts/verify_fluid_workspace_browser.py` exercises real Flask pages
  and APIs with an isolated in-memory account and blocks external browser
  requests. It covers public and signed-in pages at 1440, 1024, and 390 pixels,
  light/dark screenshots, native select keyboard operation, independent panel
  persistence, sidebar disclosure, mobile history, district saving/reloading,
  inherited restrictions, direct API denial, and reduced motion.

Screenshots and the JSON report are written to
`output/playwright/fluid-workspace/`. They use fixture data, not student data.
This change is local until committed and deployed through the normal release
process.
