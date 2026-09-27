# September 2026 thank-you note

## Purpose and copy

Thank people for using IntelliPlan and invite feature requests and bug reports at `uanirudh0811@gmail.com`. The exact HTML and text bodies are in `Main_Project/templates/emails/thank_you.html` and `intelliplan/email/text/thank_you.txt`. Reply-To is the same Gmail address, regardless of the general marketing reply setting.

## Recipient and safety contract

The admin campaign sends only to student-role accounts with a valid address, recorded birth year, dated marketing opt-in, no suppression, and a conservative age of at least 13. Educational parental consent alone never authorizes marketing to an under-13 account. Parent and teacher roles need their own consent path and are excluded. Each send passes the eligibility gate again immediately before dispatch. The campaign uses `thank_you_feedback_2026_09` in the email ledger so retries and parallel requests do not duplicate accepted sends.

This is a commercial/feedback solicitation, so each version includes an unsubscribe link and physical postal address. The send path refuses to run without `MARKETING_POSTAL_ADDRESS`, a working provider, verified sending domain, and HTTPS base URL. The admin preview shows an eligible count, not recipient addresses. The Resend "General" segment is currently empty and must not be assumed to reflect the app's eligible audience.

## Operation and evidence

Open the restricted Admin page while signed in as the configured administrator. Use **Preview audience**, then **Send thank-you note**. The UI sends at most 25 people per request and continues in batches. It stops on a provider failure so the operator can inspect diagnostics. The count displayed after sending means accepted by the provider, not delivered or opened. Reopening the page and sending again only attempts eligible people without a settled ledger entry; failed sends can be retried.

Production variables `RESEND_API_KEY`, `RESEND_FROM`, `MARKETING_POSTAL_ADDRESS`, `MARKETING_REPLY_TO`, and `APP_BASE_URL` are configured by name in Railway, but the connected Railway integration does not reveal their values. Run `/api/admin/email/preflight` in an authenticated session if a send is blocked. Do not paste account credentials or API keys into a task transcript.
