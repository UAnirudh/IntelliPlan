# Plani safety checks

Plani uses Groq's dedicated `openai/gpt-oss-safeguard-20b` with a school policy,
instead of routing the classifier through the ordinary tutor model chain.
Set `GROQ_SAFETY_API_KEY`, or use the existing `GROQ_API_KEY`. The institution
must permit Groq processing. Missing credentials, timeouts, rate limits,
malformed decisions and incomplete model output withhold the response with a
retryable `safety_unavailable` error; they never mean permission to proceed.

Input checks cover the tutor, agent, legacy chatbot, photo-question text and
optional AI onboarding. Output checks cover the replies and structured content,
including premium clarification questions. Agent tool calls are checked before
execution. Later agent rounds include tool results in the same conversation
window used for generation and moderation.

Both use the same bounded window of up to16 whole turns and32000 characters.
Long prior messages retain their suffixes; whole oldest turns are removed from
both windows together. The current target is never silently shortened for the
classifier. Tutor history is loaded through the existing account/conversation
ownership checks. This does not search unrelated private chats or share them
with teachers.

Weapon construction and weapon-specific enabling chemistry remain restricted
when reframed as science, fiction or an indirect follow-up. Safe general
chemistry, historical discussion and clinical education remain available.
Sexualized or exploitative requests are evaluated regardless of a public
figure's name or alias; a name alone is not a ban or an allegation.

Teachers can choose bounded clinical vocabulary for institution-assigned
classrooms at `/teacher/terminology`. Institution settings lets authorized
admins create classrooms under schools and assign teachers/students. District
and school ceilings remain enforced. A progress invitation does not grant
classroom authority, and approved terminology never bypasses intent/output
classification or authorizes sexual, weapon, self-harm or harassment content.

## Verification

`python -m pytest tests/test_plani_safety.py tests/test_teacher_safety_terms.py -q`
checks context, strict decisions, provider outages, policy scope and prevention
of side effects. Browser checks are in `scripts/verify_teacher_terms_browser.py`.

For a small synthetic live provider probe, run
`python scripts/verify_plani_safety_provider.py --case "weapon semantics"`
in an environment with the configured key. No user data or response reasoning
is logged. Individual `--case` probes help respect the provider's request/token
limits; a throttled probe is unavailable, not a successful safe verdict.

References: [Groq safeguard model](https://console.groq.com/docs/model/openai/gpt-oss-safeguard-20b),
[OpenAI policy-prompting guide](https://developers.openai.com/cookbook/articles/gpt-oss-safeguard-guide).
