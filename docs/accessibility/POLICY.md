# IntelliPlan Accessibility Policy

**Target: WCAG 2.2 Level AA.**

IntelliPlan is a study tool used by school and university students. A
meaningful share of that population has a diagnosed disability — ADHD,
dyslexia, low vision, motor impairment — and a much larger share is using the
app tired, on a cracked phone, in bright sunlight, one-handed on a bus. Every
rule below helps both groups. None of them are for a compliance badge.

This policy is enforceable: each rule says how it is checked, and the
automated ones run in `tests/test_accessibility_contract.py`.

---

## 1. Non-negotiables

These block a merge.

### 1.1 Every control has an accessible name

A control a screen reader announces as "button" or "edit text" is a control
that cannot be used. A `placeholder` is **not** a name — it is not exposed as
one, and it vanishes the moment the field has content.

Valid routes: visible `<label for>`, wrapping `<label>`, `aria-label`, or
`aria-labelledby`. An icon-only button must carry `aria-label`.

*Checked:* `test_every_form_control_has_an_accessible_name`,
`test_every_button_has_an_accessible_name`.

### 1.2 Text meets 4.5:1, UI boundaries meet 3:1

Small text (under 18.66px bold / 24px regular) needs **4.5:1** against its
actual composited background — including any `color-mix()` tint sitting over
the card. Large text needs 3:1. Focus indicators, borders that carry meaning,
and icons that are the only signal need 3:1.

**Measure, do not eyeball.** Two failures were shipped in this codebase by
eyeballing, both found only when the ratios were actually computed.

**Never hardcode a text colour on a themed background.** `color: #fff` on
`background: var(--accent)` is legible in light themes and illegible in dark
ones — measured at **1.74:1** on forest-dark, which is invisible. Pair
`--accent` with `--bg-card`: those two sit at opposite ends of every theme's
lightness range, so the pairing holds in all 13 shipped themes (worst case
5.02:1).

*Checked:* `test_accent_and_card_background_stay_legible_in_every_theme`.

### 1.3 Colour is never the only signal

Anything communicated by colour is also communicated in text, shape, or
position. In the next-action card, a cost reads "Costs: 45 min more on
tomorrow" — the word carries it, the red is reinforcement.

*Checked:* by review.

### 1.4 Keyboard reaches everything, and focus is never lost

Every interactive element is reachable and operable by keyboard, in an order
that matches the visual one. No keyboard traps.

Two specific rules, both of which this codebase has broken before:

* **Never hide a node that contains focus** without first moving focus
  somewhere meaningful. Focus falls to `<body>` and the user loses their
  place in the page entirely.
* **When a panel appears in response to an action, move focus into it.** A
  confirmation panel a keyboard user has to go hunting for is one they have
  no way of knowing exists.

*Checked:* by review; `show()` in `next_action.js` implements the first rule.

### 1.5 Live regions announce content, not controls

`role="status"` / `aria-live` regions must contain **text only**. A live
region that also holds buttons gets announced wholesale, so the user hears
button labels read at them as prose, and focus is still nowhere near the
controls.

Split them: the message is the live region, the buttons are siblings outside
it.

*Checked:* `test_live_regions_do_not_contain_interactive_controls`.

### 1.6 Decorative graphics are hidden from assistive tech

A bar chart restating a number already present as text is decoration:
`aria-hidden="true"`. Otherwise a screen reader stops on something with
nothing to say.

*Checked:* by review.

### 1.7 State is exposed programmatically

A disclosure toggle carries `aria-expanded` and keeps it in sync.
`aria-controls` points at an id that exists.

*Checked:* `test_aria_controls_point_at_real_elements`,
`test_toggle_buttons_declare_their_expanded_state`.

### 1.8 Motion respects `prefers-reduced-motion`

Vestibular disorders are real and common. Under the media query, transitions
and transforms reduce to nothing or to opacity. Nothing important is
communicated only by movement.

*Checked:* by review.

### 1.9 Landmarks and a working skip link

One `<main>` per page, a skip link that points at a real landmark, headings
in order without level skips.

*Checked:* `test_the_skip_link_points_at_a_real_landmark`.

---

## 2. Rules specific to an adaptive scheduler

A system that tells students what to do carries obligations a static page
does not.

### 2.1 Uncertainty is stated in words

A recommendation the engine is unsure of must say so in text, not only
through a colour or a smaller number. The next-action card renders "best
guess" rather than a false-precision percentage below 50% confidence.

### 2.2 Explanations are text, not charts

The score breakdown is a labelled list with numeric values. The bars are
`aria-hidden` decoration on top. Someone using a screen reader gets the same
explanation as someone looking at it, not a degraded version.

### 2.3 An override is always available and never punished

The student can always decline a recommendation. The consequence report
states costs plainly and neutrally — no guilt, no dark patterns, no
pre-selected "are you sure you want to fall behind?" framing.

### 2.4 Cognitive load is a first-class constraint

This is an ADHD-heavy user base. The card shows one recommendation, one
reason list, and one primary action. Detail is behind a disclosure. Adding a
second competing call to action to this surface is a regression.

---

## 3. Manual checks before shipping a UI change

Automated tests catch the mechanical failures. These catch the rest:

1. **Tab through it.** Every control reachable, visible focus ring, sensible
   order, nothing trapped.
2. **Zoom to 200%.** No clipping, no horizontal scroll, nothing overlapping.
3. **Both schemes.** Light and dark, and at least one non-default theme.
4. **Screen reader smoke test.** NVDA (Windows) or VoiceOver (macOS): the
   headline, its reasons, and the primary action all announce sensibly.
5. **Reduced motion on.** Nothing moves that should not.

---

## 4. Known open issues

Recorded rather than hidden. Nothing here is acceptable long-term.

| Issue | Severity | Notes |
|---|---|---|
| `color: #fff` hardcoded on `var(--accent)` in `command_center.css` | **Unquantified** | 7 rules pair the two. Most are superseded by a later unscoped block that repaints them `--text-primary` on `--bg`, so the live count is far smaller than the grep suggests — an earlier revision of this file said "~20 call sites", which was wrong. Two (`.cc-chat-nav-go`, `.cc-fallback-link`) look genuinely exposed. Confirming this needs contrast measured on *rendered* elements; a detached-probe measurement was attempted and gave meaningless numbers, because gradients and inherited context do not resolve off-DOM. Blocked on the row below. |
| No automated contrast checking over rendered pages | **High** | The token-pair test covers the worst class, and the new card was measured by hand. Neither substitutes for axe (or equivalent) run against real rendered DOM in CI — which is the only way to settle the row above, and the only way this policy's §1.2 is actually enforced rather than asserted. |
| No screen-reader regression testing | Medium | Manual only today. |

---

## 5. Adding a new surface

1. Read this file.
2. Compute the contrast ratios of every colour pairing, against the composited
   background, in light and dark. Do not skip because it "looks fine".
3. Use existing tokens. Never a raw hex for text or background.
4. Run `pytest tests/test_accessibility_contract.py`.
5. Do the five manual checks in §3.
