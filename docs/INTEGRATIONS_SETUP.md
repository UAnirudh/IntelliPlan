# Google Drive, OneDrive and Outlook Calendar: owner setup

The full checklist of what the owner does in Google Cloud and Microsoft Entra
before these integrations work in production. `docs/cloud-documents-setup.md`
describes the Study files page; this file covers every integration end to end.

Until the credentials below exist, nothing breaks. The Settings rows read
"Not available yet", the Integrations list shows "Coming soon", connect links
answer 503 or redirect back with a notice, and the matching endpoints return
empty results.

Production base URL: `https://intelliplan.tech`. For any other host, set
`APP_BASE_URL` to its origin and register that host's redirect URIs as well.

## What each integration does

| Integration | What the student gets | Reuses |
|---|---|---|
| Google Drive (and Docs) | Notes and handouts found for each assignment and fed to Plani's tutor and study map; "Create study doc" writes a guide to `IntelliPlan/` in Drive; Study files page to pick, import and edit files | The existing Google OAuth client (sign-in + Calendar) |
| OneDrive | Same matching and study-guide creation (as .docx); Study files import and text-file editing | The Outlook app registration |
| Outlook Calendar | Plans around Outlook events; study blocks pushed to Outlook (no duplicates on repeat pushes, in the student's own timezone) | n/a |

## Redirect URIs: exactly two

| Provider | Register this URI | Used by |
|---|---|---|
| Google | `https://intelliplan.tech/oauth2callback` | sign-in, Google Calendar **and** Google Drive (the app also serves the alias `/oauth/google/callback`) |
| Microsoft | `https://intelliplan.tech/oauth/outlook/callback` | Outlook Calendar **and** OneDrive |

---

## 1. Google (Drive, Docs, Calendar)

Drive is not a separate OAuth client. The app requests Drive scopes
*incrementally* on the client that already handles "Sign in with Google" and
Calendar (`include_granted_scopes=true`). That gives you one consent screen to
verify and one set of credentials.

### 1a. Enable the APIs

All in the Cloud project that owns `GOOGLE_CLIENT_ID`:

- Google Drive API: https://console.cloud.google.com/apis/library/drive.googleapis.com
- Google Docs API: https://console.cloud.google.com/apis/library/docs.googleapis.com (the Study files page reads and edits Google Docs through it; `drive.file` is enough to authorize it)
- Google Calendar API: https://console.cloud.google.com/apis/library/calendar-json.googleapis.com (probably already on)
- Google Picker API: https://console.cloud.google.com/apis/library/picker.googleapis.com (lets students choose which existing files IntelliPlan can see)

### 1b. OAuth consent screen and scopes

https://console.cloud.google.com/apis/credentials/consent. Under **Data access → Add or remove scopes**, add:

| Scope | Google's classification | Needed for |
|---|---|---|
| `https://www.googleapis.com/auth/drive.file` | Non-sensitive | Always requested for Drive. Lets the app create study guides, read files the student picks with Picker, and read/edit Google Docs among them |
| `https://www.googleapis.com/auth/drive.readonly` | **Restricted** | Only when `GOOGLE_DRIVE_SCOPE_MODE=readonly`. Lets assignment matching search the student's whole Drive |
| `https://www.googleapis.com/auth/calendar.events` | Sensitive | Google Calendar (already present) |
| `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` | Non-sensitive | Sign-in (already present) |

The `documents` scope is **not** requested. Study guides are created by
uploading HTML to Drive with conversion to a Google Doc, and Docs editing works
under `drive.file`.

> If the consent screen's publishing status is **Testing**, Google expires
> refresh tokens after 7 days and only listed test users can connect. Students
> would be asked to reconnect weekly. Switch to **In production** on the same
> page.

### 1c. Credentials

https://console.cloud.google.com/apis/credentials

1. Open the existing **OAuth 2.0 Client ID** (Web application). Under
   **Authorized redirect URIs**, confirm `https://intelliplan.tech/oauth2callback`
   is listed. Drive adds no new URI.
2. **Create credentials → API key** for the Picker. Restrict it with
   *Application restrictions → Websites* set to `https://intelliplan.tech/*`,
   and *API restrictions* set to Google Picker API + Google Drive API. Its value
   is `GOOGLE_PICKER_API_KEY`.
3. The Picker's app ID is the numeric **project number** (Cloud console home,
   *Project info*), not the client ID. Its value is `GOOGLE_PICKER_APP_ID`.

### 1d. The scope decision: `drive.file` + Picker (default) vs `drive.readonly`

**`drive.file` + Picker (default, `GOOGLE_DRIVE_SCOPE_MODE` unset or `file`). No Google verification needed for Drive.**
Assignment matching full-text-searches every file IntelliPlan can access. That
means the files a student chose in Study files (Picker) and the study guides
IntelliPlan created. Students can connect today with nothing more from Google.
The tradeoff: notes the student never picked are invisible to matching.
Settings says so on the Drive row ("Uses files you choose in Study files…"),
so expectations stay honest.

**`drive.readonly` (`GOOGLE_DRIVE_SCOPE_MODE=readonly`).**
Matching searches the student's entire Drive with no picking step, which is the
better experience. It is a **restricted** scope. Before more than 100 users can
grant it, the app must pass
[OAuth app verification](https://support.google.com/cloud/answer/13463073)
plus an annual independent security assessment
([CASA](https://appdefensealliance.dev/casa)). Until then students see Google's
"unverified app" screen, and the app is capped at 100 users for its lifetime.
(`GOOGLE_OAUTH_UNVERIFIED=1` makes IntelliPlan explain that screen first, for
Drive as for Calendar.)

**Recommendation:** ship on the default (`file` + Picker). Move to `readonly`
only if whole-Drive search proves worth a CASA assessment.

### 1e. Google env vars

| Var | Value | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` | existing | Drive is available as soon as this and the secret are set |
| `GOOGLE_CLIENT_SECRET` | existing | |
| `GOOGLE_REDIRECT_URI` | optional | defaults to `https://intelliplan.tech/oauth2callback` |
| `GOOGLE_PICKER_API_KEY` | API key from 1c | without it, Drive connects but "Choose files" stays unavailable |
| `GOOGLE_PICKER_APP_ID` | project number | |
| `GOOGLE_DRIVE_SCOPE_MODE` | `file` (default) or `readonly` | see 1d |
| `GOOGLE_OAUTH_UNVERIFIED` | `1` until verified | existing flag; also guards the Drive connect |

Disconnecting Drive deletes IntelliPlan's stored token but does **not** revoke
it at Google. The grant is shared with sign-in and Calendar, so revoking it
would also disconnect the calendar. Students can revoke everything at
https://myaccount.google.com/permissions.

---

## 2. Microsoft (Outlook Calendar + OneDrive)

One app registration serves both. OneDrive is a separate grant with its own
scopes, stored in its own row, so disconnecting one never affects the other.

### 2a. Register the app

https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade → **New registration**

- **Name:** IntelliPlan
- **Supported account types:** *Accounts in any organizational directory and personal Microsoft accounts*. Students have both kinds; the code uses the `common` authority.
- **Redirect URI:** platform **Web**, `https://intelliplan.tech/oauth/outlook/callback`

The **Application (client) ID** shown on the overview page is `MICROSOFT_CLIENT_ID`.

### 2b. Client secret

App → **Certificates & secrets** → **New client secret**. Copy the **Value**
(not the Secret ID); that is `MICROSOFT_CLIENT_SECRET`. Secrets expire after at
most 24 months, so set a renewal reminder. An expired secret makes every
Outlook and OneDrive refresh fail, and students are asked to reconnect.

### 2c. API permissions

App → **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**:

| Permission | Used by |
|---|---|
| `openid`, `profile` | both |
| `offline_access` | both (refresh tokens) |
| `User.Read` | both (account shown in Settings) |
| `Calendars.ReadWrite` | Outlook Calendar: busy time and study blocks |
| `Files.ReadWrite` | OneDrive: search/read files, import, edit text files, write study guides to `/IntelliPlan` |

None of these need admin consent for personal accounts. Many school tenants
block user consent for third-party apps, though. A student on such a tenant
returns to Settings with "School accounts sometimes need IT approval first",
and their school's IT must grant tenant-wide consent. Personal Microsoft
accounts always work.

`Files.ReadWrite` is used instead of `Files.ReadWrite.AppFolder` because the
app-folder scope would hide study guides under `Apps/IntelliPlan`, a folder
students don't know exists.

### 2d. Microsoft env vars

| Var | Value | Notes |
|---|---|---|
| `MICROSOFT_CLIENT_ID` | Application (client) ID | enables Outlook Calendar and OneDrive |
| `MICROSOFT_CLIENT_SECRET` | secret **Value** | |
| `MICROSOFT_REDIRECT_URI` | optional | defaults to `${APP_BASE_URL}/oauth/outlook/callback` = `https://intelliplan.tech/oauth/outlook/callback`. It used to be mandatory, and Outlook stayed hidden without it |
| `MICROSOFT_TENANT` | optional | default `common`. Set a tenant ID only to restrict sign-in to one organization |

---

## 3. Shared

| Var | Value |
|---|---|
| `DATA_ENCRYPTION_KEY` | Fernet key used to encrypt every stored OAuth token: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `APP_BASE_URL` | `https://intelliplan.tech` (default) |

The tables (`google_drive_integrations`, `onedrive_integrations`,
`cloud_document_links`) are created by the existing boot-time
`db.create_all()`; there is no manual migration.

## 4. Smoke test after configuring

1. **Settings → Integrations:** the Outlook Calendar, Google Drive and OneDrive rows show **Connect**.
2. **Connect Google Drive**, then open **Study files** (`/study-files`) and choose a few notes with the Picker.
3. **Dashboard → open an assignment.** "Your related documents" lists matching files; **Create study doc** saves a guide and shows a link to it.
4. **Tutor with a selected assignment:** replies can cite "Your Google Drive: …". An assignment that isn't from Canvas can be studied too, grounded in Drive/OneDrive.
5. **Connect Outlook and ask Plani to schedule your week:** blocks appear at the right local time, and asking again does not duplicate them.

## Routes reference

| Route | Purpose |
|---|---|
| `GET /oauth/google-drive` | start Drive consent (returns via `/oauth2callback`) |
| `GET /oauth/onedrive` | start OneDrive consent with PKCE (returns via `/oauth/outlook/callback`) |
| `GET /oauth/outlook` | start Outlook Calendar consent (returns via `/oauth/outlook/callback`) |
| `POST /oauth/google-drive/disconnect`, `/oauth/onedrive/disconnect`, `/oauth/outlook/disconnect` | forget a connection |
| `GET /api/cloud-documents/status` | connected / configured per provider |
| `GET /api/cloud-documents/match?title=&course=&description=` | documents matched to an assignment (excerpts, not full text) |
| `POST /api/cloud-documents/create` `{title, course, description, due_date, provider?, steps?}` | write a study guide |
| `GET /study-files` and `/api/cloud-documents/*` | pick, import and edit individual files |
| `GET /calendar/events`, `POST /calendar/export` | merged Google + Outlook events; push study blocks to both |
