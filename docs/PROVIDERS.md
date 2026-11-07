# Provider setup

Credentials are always **server-side environment variables**. They are never stored in the database or shown in the
UI, which only reports whether each variable is set. Each sender profile has a *secret env prefix* (for example
`SENDER_MAIN`). Variables are named `<PREFIX>_<NAME>`.

Delivery results are reported precisely:

- **accepted/sent**: the provider accepted the message. This does not prove the recipient received it.
- **failed**: the provider definitely rejected or never received it, so a retry is safe.
- **uncertain**: a timeout or server error after the request was sent. It is never resent automatically. An admin
  checks the Sent folder or the mailbox, then chooses *Mark as sent* or *Authorize retry*.

The application never creates drafts, and it never reports a draft or a log-only message as delivered.

## Microsoft 365 (recommended): plant Excel files + shared mailbox

A **single Entra app registration** is used both to read the plant workbooks in OneDrive/SharePoint and to send
from the shared mailbox. In **Setup → Microsoft 365** the admin picks one of two modes. IT prepares the matching
permissions.

### Common steps (IT)
1. In **Microsoft Entra admin center → App registrations → New registration**, name it "QMS Reminder System" and
   choose *Single tenant*. Note the **Directory (tenant) ID** and **Application (client) ID**.
2. Under **Certificates & secrets → New client secret**, copy the value and note the expiry date.
3. Put these on the server, in `.env`:
   ```
   GRAPH_TENANT_ID=...
   GRAPH_CLIENT_ID=...
   GRAPH_CLIENT_SECRET=...
   ```
4. Create or choose the **shared mailbox**, for example `qms.reminders@company.com`.

### Option A: "IT-registered app" (app-only, most robust)
- Under **API permissions → Microsoft Graph → Application permissions**, add:
  - `Files.Read.All`, to read the plant workbooks. You can use `Sites.Selected` instead and grant read on the
    specific SharePoint site(s), but pasting OneDrive *personal* sharing links then needs `Files.Read.All`.
  - `Mail.Send`.
- Click **Grant admin consent**.
- **Restrict sending to the shared mailbox only** (strongly recommended). Use an Exchange Online application
  access policy or *RBAC for Applications*, scoped to `qms.reminders@company.com`. Without this, the app could send
  as any mailbox.
- Nothing expires when people change passwords or leave. Only the client secret has an expiry date.

### Option B: "Admin signs in with Microsoft" (delegated)
- Under **Authentication → Add a platform → Web**, set the redirect URI to
  `https://<server>/api/m365/callback/`. This is also shown in Setup and must match `M365_REDIRECT_URI` in `.env`.
- Under **API permissions → Microsoft Graph → Delegated permissions**, add: `User.Read`, `Files.Read.All`,
  `Mail.Send`, `Mail.Send.Shared`, `offline_access`, `openid`, `profile`, `email`. Click **Grant admin consent** if
  your tenant requires it.
- In the **Exchange admin center**, give the admin's account **Send As** permission on the shared mailbox.
- The admin clicks **Sign in with Microsoft** in Setup. The app can then read every workbook that account can open.
  Only an encrypted refresh token is stored on the server. If the account's password changes, or the sign-in is
  revoked or expires, the daily summary reports it and the admin signs in again.

### Linking each plant's workbook
In Setup → Plants & departments → *Excel file*, open the workbook in OneDrive/SharePoint, choose **Share → Copy
link** (or copy the address bar), paste it and click **Use link**. The app resolves the link to the file once, then
reads that file every day. Plants keep editing the same file.

Delivery results: Graph returns `202 Accepted` (no message ID). The ledger stores the Graph `request-id`, and each
message carries an `X-QA-Idempotency-Key` header so it can be found in the Sent items.

## Google: Gmail API

This works for Google Workspace and consumer Gmail. It uses an OAuth client and a refresh token for the sender
account with the `https://www.googleapis.com/auth/gmail.send` scope.

1. In Google Cloud Console, create a project and enable the **Gmail API**.
2. Configure the OAuth consent screen. For Workspace, choose *Internal*.
3. Create an **OAuth client ID** (type *Desktop app* or *Web application*).
4. Get a refresh token for the sender account with the `gmail.send` scope, for example with Google's OAuth 2.0
   Playground using your own client credentials, or a one-off local script.
5. Set these on the server:
   ```
   SENDER_MAIN_GMAIL_CLIENT_ID=...
   SENDER_MAIN_GMAIL_CLIENT_SECRET=...
   SENDER_MAIN_GMAIL_REFRESH_TOKEN=...
   ```
6. Create a sender with provider *Gmail API* and test it.

Workspace admins may prefer a service account with domain-wide delegation. That variant is not implemented in V1.

## SMTP (only where permitted)

Use SMTP only if your provider allows it. For Microsoft 365, SMTP AUTH must be enabled for the mailbox, and many
tenants disable it. For Gmail, it requires an app password with 2-step verification.

- In sender settings, set host, port, security (`starttls`, `ssl` or `none`) and username.
- On the server, set `SENDER_MAIN_SMTP_PASSWORD=...`.

## Log only (console)

For testing only. Nothing is delivered: only metadata (counts and an idempotency key, never content) is written to
the server log. The UI flags this provider clearly.

## Data sources: OneDrive for Business / SharePoint via Microsoft Graph

1. Register an app as above, or reuse one.
2. Least privilege: grant **`Sites.Selected`** (application) and then grant the app *read* access to the specific
   site. Alternatively, use `Files.Read.All` (application), which is broader. Grant admin consent.
3. Set on the server:
   ```
   GRAPH_TENANT_ID=...
   GRAPH_CLIENT_ID=...
   GRAPH_CLIENT_SECRET=...
   ```
4. In Quick Start → Data source, choose *OneDrive / SharePoint*. Search for the site (or enter a OneDrive user UPN),
   pick the document library, browse to the workbook, then **Check source**.

## Data sources: network shared folders

The **application server's service account** must be able to read the share. A folder you can open from your own PC
is not automatically readable by the server.

1. Mount or allow access to the share on the server, for example with an SMB mount using a read-only service account.
2. Register it in `SOURCE_ROOTS=qms=/mnt/qms-share`. Only these roots can be browsed, and paths outside them are
   rejected (no path traversal).
3. In the UI, choose *Server / network folder*, pick the root and browse to the workbook.
