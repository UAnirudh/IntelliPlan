# Google Drive and OneDrive study files

> The owner's full setup checklist (console links, every scope, env var and
> redirect URI, and the `drive.file` vs `drive.readonly` decision) is in
> [INTEGRATIONS_SETUP.md](INTEGRATIONS_SETUP.md).

IntelliPlan lets a signed-in student choose individual Drive or OneDrive files
and copy their extractable text into the student's study notes. Imported notes
are part of IntelliPlan's retrieval context. Disconnecting a provider revokes
IntelliPlan's stored connection and keeps the student's already imported copy;
deleting the note removes it from IntelliPlan.

## Google Drive and Google Docs

Configure the existing Google OAuth web client with the redirect URI in
`GOOGLE_REDIRECT_URI` (`https://intelliplan.tech/oauth2callback` on production).
Enable the Google Drive API and Google Docs API in the same Google Cloud
project. OAuth requests the `drive.file` scope only for this connection. The
student grants access to selected files through Google Picker; IntelliPlan does
not ask for broad access to the entire Drive.

Set these server-side environment variables:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`
- `GOOGLE_PICKER_API_KEY` (optional until file selection is enabled)
- `GOOGLE_PICKER_APP_ID` (the numeric Google Cloud project number)

Restrict the Picker API key to the production web origin and the Google Picker
and Google Drive APIs.
The OAuth consent screen must include the `drive.file` scope and publish or add
test users as appropriate for the Google project. Until Picker's API key and
project number are configured, the Drive account can connect but the Choose
files button stays unavailable.

Google Docs text can be edited from the study-files page. Saving replaces the
document body with plain text and uses Google's revision ID to reject stale
edits; it does not preserve rich formatting. Documents with tables or layout
elements are read-only. Google Sheets export as CSV and Slides export as text
for study context; those formats are not edited here.

## OneDrive

Register a Microsoft Entra web application and add the exact redirect URI from
`MICROSOFT_REDIRECT_URI` (`https://intelliplan.tech/oauth/outlook/callback` on
production). Use a confidential web client and configure delegated Graph
permissions `User.Read`, `Files.ReadWrite`, and `offline_access`. The same
client ID, secret, and callback are used by the existing Outlook Calendar
connection. Users authorize OneDrive separately from Outlook Calendar.

Set `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, and
`MICROSOFT_REDIRECT_URI` on the web service. Word and PDF files are imported as
study context where text extraction is supported. IntelliPlan edits plain text,
Markdown, and CSV files in OneDrive; Word files are read-only.

## Data limits and lifecycle

Files larger than 2 MB are rejected. Extracted text is capped at 50,000
characters. OAuth tokens are stored through IntelliPlan's encrypted token
column. Each API request checks ownership of the connection and imported note.
Concurrent edits use a Google revision ID or OneDrive ETag; stale saves return
a conflict and must be reloaded. Disconnecting leaves the imported note in
IntelliPlan, while deleting the note removes the local study context and link.
