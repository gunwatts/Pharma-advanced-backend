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

## Microsoft 365: Microsoft Graph `sendMail` (recommended for M365 tenants)

This does not depend on SMTP AUTH, which many tenants disable.

1. **Entra ID → App registrations → New registration** (single tenant). Record the *tenant ID* and *client ID*.
2. **Certificates & secrets → New client secret**. Record the value and its expiry date, and plan to rotate it.
3. **API permissions → Microsoft Graph → Application permissions → `Mail.Send`**, then **Grant admin consent**.
   - Optional: `User.Read.All` lets *Test connection* confirm the mailbox exists. It is not needed to send.
4. **Restrict the app to the sender mailbox** (strongly recommended). Use an Exchange Online
   [application access policy or RBAC for Applications](https://learn.microsoft.com/graph/auth-limit-mailbox-access)
   so the app can send only as the reminder mailbox.
5. Set these on the server:
   ```
   SENDER_MAIN_GRAPH_TENANT_ID=...
   SENDER_MAIN_GRAPH_CLIENT_ID=...
   SENDER_MAIN_GRAPH_CLIENT_SECRET=...
   ```
6. In Quick Start, create a sender with provider *Microsoft 365*, set *From* to the shared or reminder mailbox and
   the prefix to `SENDER_MAIN`, then run **Test connection** and **Send test email**.

Graph returns `202 Accepted` with no message ID. The ledger stores the Graph `request-id` and adds an
`X-QA-Idempotency-Key` header to each message so it can be found during reconciliation.

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
