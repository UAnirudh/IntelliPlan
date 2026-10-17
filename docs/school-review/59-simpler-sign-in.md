# Simpler sign-in and school connections

The sign-in page previously presented Google, two email actions and five
school connection buttons together. School connections now sit behind one
native, keyboard-operable disclosure. Google and email actions remain visible.
All five connection destinations are preserved, with the session-expiry warning
shown before choosing guest access. Native navigation links also work without
JavaScript and support ordinary browser navigation.

Local Chromium checks exercise desktop, tablet and phone widths, light/dark
themes and JavaScript enabled/disabled: twelve layout combinations, keyboard
open/close, no horizontal overflow, and all seven email/school destinations
in both JavaScript modes. These checks use the real login template and shared
styles, with a minimal page shell and simulated destination pages; they do
not prove OAuth, credential authentication or complete signed-in workflows.
The existing inline-handler suite passes all fifteen tests. Independent
standards and spec reviews report no findings. Public login regressions now
open the disclosure when present and still run against the previous live
layout before release. Hosted and production evidence follows separately.

The FoundrList badge already exists on the public homepage; it is not
duplicated. PR108's exact revision, Railway release, visible badge and loaded
image were verified, and its postmerge Tests/Security passed.

This is a focused reduction in sign-in choice overload. Whole-product
chat-first simplification, localization and the original acceptance ledger
remain incomplete. No authentication, school policy or billing settings change.

Release verification: PR109 merged at279fbecae4b646e1d4ddc35825ad5ce1477c1259.
The exact public revision/schema check passed. Fresh production Chromium
checks at375/1280px, JavaScript enabled/disabled, keyboard disclosure and
visible preserved destinations passed. The homepage rawHTML and single visible
loaded FoundrList badge also passed. Railway web5cc3d08b and newsletterf28c572b
were SUCCESS/noissues; filtered startup errors0. Postmerge Tests39005377997
and Security39005377999 passed before the next release.
