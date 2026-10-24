# Architecture

## Modules (`apps/`)

| App | Responsibility |
|---|---|
| `core` | System settings (singleton), audit trail, DB run locks, JSON logging with redaction, safe exception handler, health |
| `accounts` | Roles (fixed permission vocabulary), profiles, unit/department scopes, expiring token auth, lockout |
| `catalog` | Units, departments, recipient groups, categories, fields, statuses, relationships, impact analysis |
| `sources` | Data sources, worksheet mappings, connectors (upload / server folder / Graph), Excel parsing, **sync service** |
| `records` | Minimal current records and the scoped records API (list / detail / export / drill-down) |
| `reminders` | Senders, rules, templates, schedules, runs, **delivery ledger**; services: `eligibility`, `rules`, `templating`, `compose`, `providers`, `delivery`, `workflow`, `scheduler` |
| `dashboards` | Dashboards, widgets, assignments; services: `query` (scoped widget data), `access` |

Business rules live in service modules (`apps/*/services`, `apps/sources/sync.py`), and views stay thin.

## Key decisions

- **SQLite3** as required, with short `IMMEDIATE` transactions, optional WAL, and a DB lock table instead of Redis.
- **Bearer tokens** instead of cookies, because the frontend (GitHub Pages) and the API (PythonAnywhere) are cross-site.
- **Trust model**: each mapping's sync is `success`, `incomplete` or `failed`. A group is held when any reminder
  category (or the parent category supplying its context) has an untrusted mapping covering that group's unit.
  `fixed_unit` on a mapping limits a failure's impact to that unit. Records with blocking data issues also suppress
  the "no pending" email for their group.
- **Idempotency key** = `run_date:unit:department`, so there is never more than one successful consolidated email per
  group per day, even if configuration changes during the day. The configuration version is stored on each ledger
  row for traceability.
- **Parent/child**: explicit `Relationship(parent_category, child_category, link_field)`. A child's email row shows
  its parent as context, and each department's email lists only that department's children.
- **Seeds**: a data migration creates the example categories, roles, the default template and schedules. All of them
  are editable.

## Data flow (daily run)

```
run_daily ─ lock ─ sync_all(relevant categories) ─ _trust() ─ evaluate_record() per record
         └─ per RecipientGroup: held | reminder | no_pending | none ─ ledger_entry ─ deliver()/mark_held()
         └─ summary on DailyRun ─ admin alert if attention needed
```
