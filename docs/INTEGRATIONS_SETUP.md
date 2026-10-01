# Google Drive, OneDrive and Outlook Calendar setup

What the owner has to do before these integrations work in production. The
code ships switched off: until the env vars below are set, the Settings rows
read "Not available yet", the Integrations list shows "Coming soon", and every
route returns a redirect or a clean 409/503. Nothing errors.

Production base URL: `https://intelliplan.tech`. If you run a staging host,
set `APP_BASE_URL` to its origin and register that host's redirect URIs too.

## Summary

| Integration | Reuses | New redirect URI to register | Switch it on with |
|---|---|---|---|
| Google Drive | The existing Google OAuth client (sign-in + Calendar) | None. It uses `https://intelliplan.tech/oauth2callback` | `GOOGLE_DRIVE_ENABLED=1` |
| Outlook Calendar | n/a (this is the Microsoft app) | `https://intelliplan.tech/oauth/outlook/callback` | `MICROSOFT_CLIENT_ID` + `MICROSOFT_CLIENT_SECRET` |
| OneDrive | The Outlook app registration | None. Same `/oauth/outlook/callback` | `ONEDRIVE_ENABLED=1` |

Tokens are stored in the `cloud_docs_integrations` table (Drive, OneDrive)
and `outlook_integrations` (Outlook Calendar), encrypted with
`DATA_ENCRYPTION_KEY` like every other third-party token (see
`secret_box.py`). If that key is not set yet, set it first.

---

## 1. Google Drive

Drive does not get its own OAuth client. The app asks for Drive scopes
*incrementally* on the client already used for "Sign in with Google" and
Google Calendar (`include_granted_scopes=true`). That means one consent
screen to verify and one set of credentials.

### 1a. Enable the APIs

In the same Google Cloud project that holds `GOOGLE_CLIENT_ID`:

- Google Drive API: https://console.cloud.google.com/apis/library/drive.googleapis.com, then **Enable**
- Google Calendar API (should already be on): https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
- Google Docs API: https://console.cloud.google.com/apis/library/docs.googleapis.com.
  *Optional.* The code does **not** request the `documents` scope or call the
  Docs API. Study guides are created by uploading HTML to Drive with
  conversion to a Google Doc, which `drive.file` already allows. Enable it only
  if you later add Docs-API editing.

### 1b. OAuth consent screen

https://console.cloud.google.com/apis/credentials/consent

Add these scopes to the consent screen's scope list (**Data access → Add or
remove scopes**):

| Scope | Google's classification | Why |
|---|---|---|
| `https://www.googleapis.com/auth/drive.file` | Non-sensitive | Create study guides; read files IntelliPlan created |
| `https://www.googleapis.com/auth/drive.readonly` | **Restricted** | Search and read the student's existing notes. Only requested when `GOOGLE_DRIVE_SCOPE_MODE=readonly` (the default) |
| `https://www.googleapis.com/auth/calendar.events` | Sensitive | Already present for Google Calendar |
| `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` | Non-sensitive | Already present for sign-in |

### 1c. Credentials and redirect URI

https://console.cloud.google.com/apis/credentials, then open the existing OAuth 2.0 Client ID (type *Web application*).

Under **Authorized redirect URIs**, confirm these are present. Drive needs nothing new:

- `https://intelliplan.tech/oauth2callback` (what `GOOGLE_REDIRECT_URI` defaults to)
- `https://intelliplan.tech/oauth/google/callback` (alias that the app also serves)

### 1d. The scope decision: `drive.readonly` vs `drive.file` + Picker

This is the one real decision.

**`drive.readonly` (default, `GOOGLE_DRIVE_SCOPE_MODE=readonly`).**
This is what makes "find my notes for this assignment" work across the
student's whole Drive. It is a **restricted** scope. Before Google lets more
than 100 users grant it, the app has to pass
[OAuth verification](https://support.google.com/cloud/answer/13463073) plus an
annual third-party security assessment
([CASA](https://appdefensealliance.dev/casa)). Until then:
- users see the "Google hasn't verified this app" screen (the existing
  `GOOGLE_OAUTH_UNVERIFIED=1` notice page is shown first for Drive too), and
- you are limited to 100 users for the lifetime of the unverified app.

**`drive.file` only (`GOOGLE_DRIVE_SCOPE_MODE=file`). No verification needed for Drive.**
`drive.file` is non-sensitive. In this mode "Create study doc" works fully,
but search only sees files IntelliPlan created, plus files the student
explicitly opens with IntelliPlan. The standard way to let a student "open"
existing files is the [Google Picker](https://developers.google.com/drive/picker/guides/overview):
the student picks files in a Google-hosted dialog and the app gets
`drive.file` access to exactly those files.

**The Picker is not wired up in this branch.** Adding it needs:
1. A browser API key restricted to `intelliplan.tech` (Credentials → Create credentials → API key). Store it as `GOOGLE_PICKER_API_KEY`.
2. The Cloud project number (the Picker's `appId`).
3. CSP additions: `https://apis.google.com` in `script-src`, and `https://docs.google.com` in `frame-src`.
4. A small endpoint that hands the browser a short-lived access token for the Picker.

After that, picked files show up in matching with no other change, because
matching just runs `files.list`, and `files.list` returns every file the app
can access.

**Recommendation.** Ship with `GOOGLE_DRIVE_SCOPE_MODE=file` now, so
document creation works for everyone with no Google review. Add the Picker
next. Move to `readonly` only once you decide whole-Drive search is worth the
CASA assessment. In `file` mode Settings tells the student "Can see files
IntelliPlan created", so expectations stay honest.

> Publishing status: if the consent screen is still in **Testing**, Google
> expires refresh tokens after 7 days and only listed test users can sign in.
> Students will see "Reconnect Google Drive" weekly. Move it to
> **In production** (https://console.cloud.google.com/apis/credentials/consent).

### 1e. Env vars

| Var | Value | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` | existing | already set for sign-in |
| `GOOGLE_CLIENT_SECRET` | existing | already set |
| `GOOGLE_REDIRECT_URI` | optional | defaults to `https://intelliplan.tech/oauth2callback` |
| `GOOGLE_DRIVE_ENABLED` | `1` | the on switch. Without it the Drive row stays hidden even though the client ID exists |
| `GOOGLE_DRIVE_SCOPE_MODE` | `file` or `readonly` | default `readonly`; see 1d |
| `GOOGLE_OAUTH_UNVERIFIED` | `1` until verified | existing; also shows the warning page before Drive consent |

Disconnecting Drive in IntelliPlan deletes our stored token but does **not**
revoke it at Google. Because the grant is shared with sign-in and Calendar,
revoking it would also disconnect the student's calendar. Students can
revoke everything at https://myaccount.google.com/permissions.

---

## 2. Microsoft app (Outlook Calendar + OneDrive)

One app registration serves both. OneDrive is a separate grant with its own
scopes, stored in its own row, so disconnecting one never affects the other.

### 2a. Register the app

https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade, then **New registration**:

- **Name:** IntelliPlan
- **Supported account types:** *Accounts in any organizational directory and personal Microsoft accounts* (students use both school and personal accounts; the code uses the `common` authority)
- **Redirect URI:** platform **Web**, `https://intelliplan.tech/oauth/outlook/callback`

Copy the **Application (client) ID**. That value is `MICROSOFT_CLIENT_ID`.

### 2b. Client secret

App → **Certificates & secrets** → **New client secret**. Copy the *Value*
(not the Secret ID). That value is `MICROSOFT_CLIENT_SECRET`. Secrets expire
(24 months at most), so put a renewal reminder in the calendar. An expired
secret makes every Outlook/OneDrive refresh fail, and students see
"Reconnect".

### 2c. API permissions

App → **API permissions** → **Add a permission** → **Microsoft Graph** →
**Delegated permissions**. Add:

| Permission | Used by |
|---|---|
| `openid` | both |
| `profile` | both |
| `offline_access` | both (refresh tokens) |
| `User.Read` | both (account email shown in Settings) |
| `Calendars.ReadWrite` | Outlook Calendar: read busy time, write study blocks |
| `Files.ReadWrite` | OneDrive: search/read files, create study guides in `/IntelliPlan` |

None of these need admin consent for personal accounts. Many school tenants
block user consent to third-party apps. A student on such a tenant comes
back with "School accounts sometimes need IT approval first", and their
school's IT has to grant tenant-wide consent. A personal Microsoft account
always works.

Why `Files.ReadWrite` and not `Files.Read` + `Files.ReadWrite.AppFolder`:
the app-folder scope would put study guides in `Apps/IntelliPlan`, which
students don't know exists. `Files.ReadWrite` lets the guide land in a plain
`IntelliPlan` folder at the root of their OneDrive.

### 2d. Env vars

| Var | Value | Notes |
|---|---|---|
| `MICROSOFT_CLIENT_ID` | Application (client) ID | turns on Outlook Calendar |
| `MICROSOFT_CLIENT_SECRET` | secret *Value* | |
| `MICROSOFT_REDIRECT_URI` | optional | defaults to `${APP_BASE_URL}/oauth/outlook/callback`, i.e. `https://intelliplan.tech/oauth/outlook/callback`. Before this branch it was required, and Outlook stayed hidden without it |
| `MICROSOFT_TENANT` | optional | defaults to `common`. Set to one tenant ID only to restrict sign-in to that tenant |
| `ONEDRIVE_ENABLED` | `1` | the on switch for OneDrive, once `Files.ReadWrite` is added in 2c |

---

## 3. Shared

| Var | Value |
|---|---|
| `DATA_ENCRYPTION_KEY` | Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Required for tokens to be encrypted at rest |
| `APP_BASE_URL` | `https://intelliplan.tech` (default). Only set it for another host |

The new table `cloud_docs_integrations` is created by the existing boot-time
`db.create_all()`, so no manual migration is needed.

## 4. Smoke test after configuring

1. Settings → Integrations: the Google Drive, OneDrive and Outlook Calendar rows show **Connect**.
2. Connect each one. You should land back on Settings (Drive/OneDrive) or Command Center (Outlook) with a "connected" notice.
3. Dashboard → open an assignment. **Your related documents** lists matching files. **Create study doc** saves a guide to `IntelliPlan/` in Drive or OneDrive and shows a link to it.
4. Ask Plani to schedule your week. The blocks appear in Outlook, and asking again does not duplicate them.
5. Tutor with a selected assignment: the reply can cite "Your Google Drive: …".

## Routes reference

| Route | Purpose |
|---|---|
| `GET /oauth/google-drive` | start Drive consent (callback: `/oauth2callback`) |
| `GET /oauth/onedrive` | start OneDrive consent (callback: `/oauth/outlook/callback`) |
| `GET /oauth/outlook` | start Outlook Calendar consent (callback: `/oauth/outlook/callback`) |
| `POST /api/cloud-docs/disconnect/<google_drive\|onedrive>` | forget a connection |
| `POST /oauth/outlook/disconnect` | forget Outlook Calendar |
| `GET /api/cloud-docs/status` | configured/connected per provider |
| `GET /api/cloud-docs/match?title=&course=&description=` | matched documents for an assignment |
| `POST /api/cloud-docs/create` `{title, course, description, due_date, provider?, steps?}` | write a study guide |
