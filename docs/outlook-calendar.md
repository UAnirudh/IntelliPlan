# Connect Outlook to IntelliPlan

Outlook requires a Microsoft application registration owned by IntelliPlan. The production service was checked during this release: `MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET` were absent. Code fixes alone cannot complete Microsoft sign-in until those settings exist.

## Configure Microsoft and Railway

1. In the [Microsoft Entra admin center](https://entra.microsoft.com/), open **App registrations → New registration**. Use an account with permission to register applications. Follow [Microsoft's registration guide](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app).
2. Name the application IntelliPlan. To support both school/work and personal Outlook accounts, select the supported account type covering **any organizational directory and personal Microsoft accounts**. A school-only deployment can use its own tenant instead.
3. Add a **Web** redirect URI, exactly `https://intelliplan.tech/oauth/outlook/callback`. This is a server application, not a single-page application callback.
4. Under Microsoft Graph **delegated** permissions, configure `User.Read`, `Calendars.ReadWrite`, `offline_access`, `openid` and `profile`. Outlook reads and writes the signed-in user's calendar; it does not need application-wide access to every student's mailbox. OneDrive permissions are separate and unnecessary for calendar-only setup.
5. Create a client secret in **Certificates & secrets**. Record its expiry and copy its **value**, not its secret ID. Put it directly into Railway's protected variables; do not paste it into chat, a source file, or a screenshot.
6. On the existing IntelliPlan **web service**, in its **production** environment, configure:

   ```text
   MICROSOFT_CLIENT_ID=<Application (client) ID>
   MICROSOFT_CLIENT_SECRET=<Client secret value>
   MICROSOFT_REDIRECT_URI=https://intelliplan.tech/oauth/outlook/callback
   MICROSOFT_TENANT=common
   ```

   Keep `APP_BASE_URL` on the actual public HTTPS origin. A deliberately single-tenant application should use its tenant ID instead of `common` and offer the corresponding account type. Apply the variable changes through Railway and verify the resulting deployment is healthy.

7. Sign in to IntelliPlan and connect Outlook from **Settings**, or open `/oauth/outlook`. Complete Microsoft's real account/consent flow. A school may require its administrator to approve the application; IntelliPlan cannot bypass that restriction. Connecting the Outlook connector in Codex does not connect a student's account inside IntelliPlan.

## Verify the real student flow

Use a consenting test account, not a student's private calendar:

- Complete Microsoft sign-in and its callback; confirm the Outlook account appears connected in Settings.
- Check an existing ordinary appointment, an all-day event and a recurring occurrence. Verify dates in the account's actual timezone, including a daylight-saving boundary where relevant.
- On Scheduler, click **Auto**. It checks Google and Outlook together in one three-day range and includes class time. A failed or revoked calendar must show unavailable time rather than offer it as free. A fully booked day must not invent a 7 PM slot.
- Generate a plan and confirm its study blocks avoid appointments and class time. Inspect any deferred work instead of treating an infeasible day as a successful placement.
- Explicitly export a small test plan. Confirm the local start/end times in Outlook, and retry the same export to verify it does not create duplicates. Partial failures must identify which calendar needs attention.
- Repeat after the access token expires to verify refresh-token persistence and school consent. A transient outage should preserve the saved connection and plan.

The release's isolated provider and browser tests cover these failure patterns. Actual production Microsoft login, token refresh and calendar writes remain unverified until the registration is configured and this real-account flow succeeds. No live calendar events are created by the automated test suite.

## Calendar safety contracts

Microsoft [calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0) includes recurring occurrences and exceptions in a bounded time range. Graph's [dateTimeTimeZone](https://learn.microsoft.com/en-us/graph/api/resources/datetimetimezone?view=graph-rest-1.0) has a separate timezone field; IntelliPlan preserves explicit offsets and supports IANA plus common Windows names. Unknown naive zones and incomplete pagination fail availability checks rather than silently creating free time.

Exports validate a complete calendar preflight before writing and use deterministic provider event identities for retry protection. `skip_overlaps=false` permits an intentional overlap; it does not bypass the preflight needed to avoid duplicate exports. Export never invites attendees or changes an existing event belonging to another application.
