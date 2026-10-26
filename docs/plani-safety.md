# Plani safety checks

Plani first uses Groq's dedicated `openai/gpt-oss-safeguard-20b` with a school
policy. If Groq rejects that request because of request size (413), rate limits
(429), a transient server failure, connection failure or timeout, it tries
`openai/gpt-oss-120b`, then `openai/gpt-oss-20b` on Groq if capacity is still
unavailable. Each classifier is attempted at most once. These fallbacks
use the identical policy, full context, target, verified terminology and strict
verdict validation. They do not use the tutor generation chain or another data
processor. A completed unsafe verdict is never retried; malformed or truncated
verdicts and authentication/policy failures still withhold the response.

The production safeguard quota was 2,000 tokens/minute when this recovery was
added, versus 8,000 for each GPT OSS fallback. A long study prompt could therefore fail
with 413 even when the safeguard model's context window had room. Retrying the
same oversized request could not fix it. Exact quotas depend on the Groq account;
if all classifiers are unavailable, the response is still withheld.

Set `GROQ_SAFETY_API_KEY`, or use the existing `GROQ_API_KEY`. The institution
must permit Groq processing. Missing credentials or failure to obtain a valid
verdict returns a retryable `safety_unavailable` error, never permission to proceed.

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
