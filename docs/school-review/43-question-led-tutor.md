# Question-led tutor and controlled visual exercises

This checkpoint replaces contradictory answer-revealing instructions with one
shared question-led teaching contract across the standard, adaptive, premium
and photo tutor paths. It requests one manageable question, a brief hint when
needed, and a wait for the learner's attempt. Checking a proposed answer and
direct safety support remain appropriate. Worksheet mode identifies problems
and asks the student to choose one, rather than solving an entire worksheet.

Visual exercises now use declarative JSON interpreted by repository-owned
interaction code. Linear graphs and fraction models have keyboard sliders,
SVG descriptions and live text alternatives. Generated multiple-choice checks
give feedback only after a choice and explicitly do not claim scored mastery.
Generated markup, JavaScript and legacy HTML artifacts display as inert source
text with a request to retry. This intentionally retires arbitrary generated
script execution; unsupported visual subjects can continue with text guidance.

Fish Audio accepts FISH_AUDIO_KEY, FISH_AUDIO_API_KEY and FISH_API_KEY in that
order, preserving the existing institutional processing gate. Provider error
bodies are withheld. Audio cancellation aborts pending requests, ignores stale
blobs and releases object URLs. Explicit Auditory/Blended selection enables
read-aloud; Reading/Visual selection disables it. The status explains browser
fallback when server audio fails. The existing voice toggle remains available.
Railway's plugin returned variable names only and confirms FISH_AUDIO_KEY is
present; no credential was read and no live Fish synthesis was performed.
The configured request shape follows the [Fish developer API](https://fish.audio/developers/).

Local evidence:

- 167 focused Python tests passed, including actual text/photo HTTP prompt
  assembly, adaptive routing, scored checks, premium behavior and mocked Fish
  requests/policy denials/errors. A subsequent final route/provider run passed
  16 tests after the photo default wording was updated.
- Seven Node tests execute the production audio/format functions with controlled
  delayed requests: stop, replacement, delayed blobs, cleanup, fallback and
  explicit format-to-voice behavior, policy denials and serialized format saves.
- Chromium at1280 and390 pixels exercises the production visual renderer with
  its production artifact CSS. Keyboard manipulation changes the actual SVG;
  improper fractions, quiz feedback and long-label mobile wrapping pass.
  Hostile HTML/script/SVG strings make zero outbound requests and execute no
  code. These are isolated component fixtures, not the full signed-in tutor.
- Screenshots and logs are under .codex/verification/tutor-browser locally;
  scripts/verify_tutor_exercises_browser.py and the Node checks run in CI.

The first complete local unit run reported4574 passed,33 skipped and3 failed
follow-through endpoint cases. Those fixtures used host-local date.today() but
the newly shared scheduler clock correctly used UTC for zero-offset owners;
after UTC midnight their definitions of today differed. The endpoint fixture
now freezes the clock to the same08:00TODAY already used to construct its plans.
All32 follow-through endpoint tests then pass. Production clock logic is
unchanged. A complete fresh run and hosted PR94 checks remain release gates.

Independent Python, JavaScript and spec review identified slider display and
mobile option styling issues, which were corrected and rechecked. Hosted and
release results are tracked separately; this document does not claim deployment.

Still incomplete: live provider question-led behavior and adversarial teaching
benchmarks, live Fish playback/provider terms and age eligibility, the full
signed-in conversation/audio/visual flow on real devices, visuals for broader
subjects, and educational quality validation. Instructions and mocked requests
do not establish effective learning or guaranteed provider compliance. Paid-plan
launch readiness, direct phone/IP cameras and the broader original brief remain
open.
