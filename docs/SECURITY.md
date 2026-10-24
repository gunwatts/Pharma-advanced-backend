# Security model and checklist

## Controls implemented

- **Authentication**: Django password hashing and validators (minimum length 10). Login returns a random bearer token.
  Only its SHA-256 hash is stored, it expires (`AUTH_TOKEN_TTL_HOURS`) and logout revokes it. Admin password resets,
  deactivation and password changes revoke existing tokens.
- **Brute force**: a per-IP login throttle (`THROTTLE_LOGIN`) and a per-username lockout (`LOGIN_MAX_FAILURES`,
  `LOGIN_LOCKOUT_MINUTES`). All logins use the same generic error message. Failed attempts are audited.
- **Authorisation**: role permissions are checked server-side on every endpoint. A unit/department **data scope** is
  applied to record lists, details, searches, children, CSV exports, dashboard widgets and drill-downs.
  Dashboards a user is not assigned to return 404 by direct URL. Parent context on a child record shows only minimal
  fields, and a parent's children are filtered by scope.
- **No code or SQL from users**: rules, widget filters and templates use fixed fields and operators. Templates allow
  only `{{ placeholder }}` from an allow-list, and unknown placeholders are rejected. All dynamic email content is
  HTML-escaped.
- **Files**: server folders are restricted to `SOURCE_ROOTS` (resolved-path check, no traversal, absolute paths never
  shown in the UI). Only `.xlsx`/`.xlsm` files are accepted, with size and row limits. Workbooks are parsed in memory.
  CSV exports guard against formula injection.
- **Secrets**: kept only in environment variables. The API reports only whether each is set. Secret-like keys in
  sender settings are rejected, the log filter redacts secrets, and error responses carry only an error reference.
- **CORS**: an explicit origin allow-list, with no credentials (header-based auth).
- **HTTP**: HTTPS redirect, HSTS (configurable), nosniff, `X-Frame-Options: DENY` and a strict referrer policy.
- **Audit trail**: every configuration create, update and delete (with a diff), activation, permission change,
  manual sync, run, retry, reconciliation, export, login and logout is recorded. The ledger and audit trail are never
  removed by record cleanup.

## Deployment checklist

- [ ] Unique `DJANGO_SECRET_KEY` and `DJANGO_DEBUG=false`
- [ ] Correct `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`
- [ ] HTTPS only. Set `SECURE_HSTS_SECONDS` (for example `31536000`) once HTTPS is confirmed.
- [ ] `.env` readable only by the service account. `ADMIN_PASSWORD` removed after bootstrap.
- [ ] Least-privilege provider permissions (Graph `Mail.Send` restricted to one mailbox; `Sites.Selected` for files)
- [ ] Read-only service account for network shares
- [ ] Backups scheduled, copied off-server, and a restore test done
- [ ] `ENABLE_DJANGO_ADMIN=false` unless needed (and then behind VPN or IP allow-listing)
- [ ] Client secret expiry dates tracked, with a rotation plan

## Token storage trade-off

The frontend keeps the session token in `sessionStorage`, which is cleared when the tab closes, because the GitHub
Pages and PythonAnywhere origins cannot share an HttpOnly cookie reliably. This exposes the token to any XSS on the
frontend origin. That risk is reduced by React's escaping, the absence of third-party scripts, short token lifetimes
and server-side revocation. For production on a single internal origin, you could switch to Django session cookies
(HttpOnly, SameSite=Strict, with CSRF).

## Regulatory note (GxP / CSV)

This system has **not** been validated, and it does not claim compliance with 21 CFR Part 11, EU GMP Annex 11 or any
other regulation. Your organisation must decide whether GxP computerised-system validation, electronic records or
signature controls, record-retention rules or data-integrity (ALCOA+) requirements apply to this tool. If they do,
plan risk assessment, specifications, IQ/OQ/PQ testing and change control. The application is designed as a
**reminder aid**: the QMS workbooks remain the system of record, and reminders do not replace the QMS itself.

## Reference project

The legacy reference archive (`Pharma_QMS-main.zip`) was **not available** when V1 was built, so it could not be
reviewed. If that project contains a Django `SECRET_KEY`, email passwords or other credentials, treat them as exposed
and **rotate them**. Do not reuse them here.
