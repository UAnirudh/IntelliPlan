# Plani provider setup

This guide describes server-managed AI configuration for student-facing IntelliPlan features. A configured key does not override a student's school policy, privacy setting, plan, or provider allowlist.

## Recommended baseline

Set `GROQ_API_KEY` in the deployment's secret manager. The app uses this provider for its server-managed student AI fallback chain, subject to each request's policy checks. Keep the key out of source control and never print its value in logs or support tickets.

`GEMINI_API_KEY` (also accepted under the legacy name `GOOGLE_API_KEY`) is **not** used as a server-managed Gemini student provider. A key from Google AI Studio does not authorize sending student data to the Gemini Developer API. Adding this key alone will not restore Plani.

## Optional Gemini Vertex

Server-managed Gemini requests use Vertex AI, and require all of the following:

1. A written contractual exception that explicitly permits this student audience and processing purpose.
2. `GEMINI_MINORS_CONTRACT_APPROVED=true` set only after that exception is documented and reviewed.
3. `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` configured.
4. The production service identity has working Vertex AI permissions and billing/project access.

The environment flag is an operator assertion; it does not establish approval or verify the service identity. Do not enable it based only on a Developer API key or standard Cloud terms. If there is no written exception, leave it unset and use a provider that the product's current policy permits.

## Paid model

`ANTHROPIC_API_KEY` is used only for users whose plan and current account policy allow the paid Claude path. It does not make Plani available to free users by itself.

## Diagnose configuration without exposing secrets

Run this command in the same environment as the app:

```bash
python scripts/ai_provider_doctor.py
```

It reports whether required environment variables are present, never their values. It does not make a provider request and cannot validate external credentials, billing, network connectivity, school authorization, or a contractual exception. For an actual outage, check deployment logs for provider name, HTTP status, and error class; do not log prompts, student records, or API keys.

To understand why an individual user is restricted, check the account's institution and privacy policy. Do not bypass those controls to restore a provider.
