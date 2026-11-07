# Deployment test cases: QMS Reminder System V1 (Windows server)

This protocol verifies a deployment on the company Windows server. It covers installation, configuration,
Microsoft 365, the reminder logic with prepared test data, security, operations and user acceptance.
It is organised like an IQ/OQ/PQ protocol so it can support your organisation's computerised-system
assessment. It does **not** by itself make the system GxP-validated (see [SECURITY.md](SECURITY.md)).

| Item | Value |
|---|---|
| System / version | QMS Reminder System V1 — backend commit `________`, frontend bundle run `________` |
| Server | `________________` (Windows Server ____) |
| Tester / date | `________________` |
| Reviewer / date | `________________` |

**How to record:** for every case, write **P** (pass), **F** (fail) or **N/A** with a reason. Attach evidence:
screenshots, `smoke-test.ps1` output, or the daily summary mail. Log each failure in the defect log (section 9).

**Automation:** cases marked **Auto (CI)** run on every push in GitHub Actions (*Backend CI → windows-deploy*, on a
clean Windows machine). Cases marked **Auto (server)** run on your server with
`powershell -ExecutionPolicy Bypass -File deploy\windows\smoke-test.ps1 -BaseUrl https://<server> [-Online]`.
Re-run the smoke test after every upgrade.

**Safety:** run sections 4–6 with a **dedicated test plant** (code `TEST`) whose department To/Cc addresses are
test mailboxes. While production plants exist, keep **reminders deactivated** until section 8.

---

## 1. Entry criteria
| ID | Check | Expected | Result |
|---|---|---|---|
| PRE-01 | Server meets [DEPLOYMENT_WINDOWS.md §1](DEPLOYMENT_WINDOWS.md) (Python 3.12 64-bit, local disk, service account) | Prerequisites confirmed by IT | |
| PRE-02 | Entra app registration done for the chosen mode ([PROVIDERS.md](PROVIDERS.md)); tenant ID, client ID and secret received | Values available, secret expiry date recorded: `________` | |
| PRE-03 | Shared mailbox exists (e.g. `qms.reminders@company.com`); test mailboxes for test departments exist | Mailboxes receive mail | |
| PRE-04 | Server reaches `login.microsoftonline.com` and `graph.microsoft.com` on 443 (`Test-NetConnection graph.microsoft.com -Port 443`) | `TcpTestSucceeded : True` | |
| PRE-05 | Backend code and frontend bundle (`qms-frontend-server` artifact) are from the reviewed commit | Commit IDs written above | |

## 2. Installation (IQ)
| ID | Steps | Expected | Auto | Result |
|---|---|---|---|---|
| INS-01 | Check `deploy\windows\*.ps1` for syntax errors | No parse errors | CI | |
| INS-02 | Run `deploy\windows\install.ps1` | Ends with "Done." and no red errors; `.venv`, `data\`, `logs\`, `.env` created; `.env` has a generated `DJANGO_SECRET_KEY` | CI | |
| INS-03 | Edit `.env` (hosts, FRONTEND_URL, GRAPH_*); copy the frontend into `frontend_dist\` | Values saved; `frontend_dist\index.html` exists | CI (partly) | |
| INS-04 | Create admin with `bootstrap_admin`; remove `ADMIN_PASSWORD` | "Administrator '…' created." | CI | |
| INS-05 | Run `register-tasks.ps1` as Administrator | Tasks *Web*, *Scheduler*, *Backup* exist; *Web* is running | CI | |
| INS-06 | `manage.py deploy_check` | No **FAIL** lines (WARN lines reviewed and accepted) | CI + server | |
| INS-07 | `GET https://<server>/api/health/` | `{"status":"ok","database":"ok",…}` | CI + server | |
| INS-08 | Open `https://<server>/` in a browser | Sign-in page "QMS Reminder System"; valid company certificate, no browser warning | server (manual for cert) | |
| INS-09 | Call an API without signing in (`/api/simple/plants/`) | HTTP 401 | CI + server | |
| INS-10 | Check the scheduled tasks' state and last result | Web running; Scheduler and Backup registered and enabled | CI + server | |
| INS-11 | Check `.env` permissions | Only Administrators and the service account can read it | server | |
| INS-12 | Restart the server (Windows reboot) and wait 3 minutes | Health endpoint OK again without anyone logging in (Web task starts at boot) | manual | |

## 3. Configuration and Microsoft 365
| ID | Steps | Expected | Result |
|---|---|---|---|
| CFG-01 | Sign in as the admin | Lands on **Today's reminders**; menu shows Today, Setup, History, Users, Audit trail | |
| CFG-02 | Change the admin password (top bar → Password) and sign in again | New password works; old one rejected | |
| CFG-03 | Setup → Mail & schedule: time 08:00, days Mon–Sat, timezone Asia/Kolkata, summary recipient = CQA head | Saved; "Next automatic send" shows the next weekday at 08:00 | |
| M365-01 | *(IT app mode)* Setup → Microsoft 365 → **IT-registered app** → **Test connection** | "Connected to Microsoft 365." | |
| M365-02 | *(Sign-in mode)* choose **Admin signs in** → **Sign in with Microsoft** → sign in | Returns to Setup with "Signed in with Microsoft" and the account name shown | |
| M365-03 | *(Sign-in mode)* Start a sign-in, then reuse the browser's back button / old callback link | Error "Sign-in link expired"; no change to the stored connection | |
| M365-04 | Enter the shared mailbox, select Microsoft 365, **Save & test connection** | "Mailbox saved and connection works." | |
| M365-05 | **Send a test email to me** with sample data | Email arrives only in your mailbox, subject starts with **[TEST]**, From = shared mailbox | |
| M365-06 | `smoke-test.ps1 -Online` | `PASS Microsoft 365 token`, and `PASS Workbook <code>` for every plant with a linked Excel | |
| M365-07 | Temporarily set a wrong `GRAPH_CLIENT_SECRET`, restart the *Web* task, **Test connection**; then restore it | Clear message "Microsoft rejected the app details…"; works again after restoring | |
| M365-08 | *(IT app mode)* IT confirms the mailbox access policy: try sending as another mailbox | Send is refused by Microsoft (policy restricts the app to the shared mailbox) | |

## 4. Setup functions (test plant)
| ID | Steps | Expected | Result |
|---|---|---|---|
| SET-01 | Setup → Excel layout & rules → **Download Excel template** | Workbook with sheets for every event type plus **Action Plans**, with the configured headers | |
| SET-02 | Setup → Plants & departments → **Add plant** `TEST` / "Test Plant" | Plant card appears with "Excel not set" | |
| SET-03 | Add departments A–D (e.g. Quality Assurance, Quality Control, Production, Engineering) with test mailbox To/Cc | Saved; card shows "4 department(s)" | |
| SET-04 | Enter an invalid address (`qa@`) or remove all To addresses of an active department and save | Clear error; nothing saved | |
| SET-05 | Generate the test workbook on the server: `.venv\Scripts\python.exe manage.py make_test_workbook --plant TEST --out D:\temp\TEST.xlsx` | File written; the expected-results table is printed (keep it as evidence) | |
| SET-06 | Upload `TEST.xlsx` to OneDrive, **Share → Copy link**, paste it into the plant's *Excel file* → **Use link** | Check result: every sheet **OK**, 0 rejected | |
| SET-07 | Paste a link to a folder or to a non-Excel file | Clear error ("points to a folder" / "not an .xlsx"); previous file kept | |
| SET-08 | Add a new event type (e.g. "Market Complaint", sheet "Complaints") | Appears in the list and the template; a plant Excel without that sheet now shows a problem for it on **Check** | |
| SET-09 | Deactivate the new event type again | No longer required in the workbooks; nothing deleted | |
| SET-10 | Users → add a **Plant QA head** for plant TEST only | User created; requires choosing at least one plant | |

## 5. Functional reminder logic (OQ), using the generated test workbook
Run on a send day (not Sunday), after SET-06. Use the default rule: daily window 7 days, periodic every 15 days
from the target date, horizon 90 days, only "Open" reminded. Open **Today's reminders → Read Excel files now**,
filter to Test Plant, and compare with the printed table. Every row's description starts with its case ID.

| ID | Test row | Expected result | Auto | Result |
|---|---|---|---|---|
| F01 | Event, Dept A, due **today** | Dept A mail, **Action required**, days left **TODAY** | CI | |
| F02 | Event, Dept A, due in **7** days | Dept A, **Action required** (last day of window) | CI | |
| F03 | Event, Dept A, due in **8** days | **Not mailed** today ("not a periodic reminder day") | CI | |
| F04 | Event, Dept A, due in **15** days | Dept A, **Periodic reminders** | CI | |
| F05 | Event, Dept A, due in **30** days | Dept A, **Periodic reminders** | CI | |
| F06 | Event, Dept A, due **yesterday** | **Not mailed** (overdue is never emailed) | CI | |
| F07 | Event, Dept A, due in **105** days | **Not mailed** (beyond the 90-day horizon) | CI | |
| F08 | Event, status **Closed** | Not mailed and not stored | CI | |
| F09 | Event, Dept C, status **Under Review** (unknown) | Not mailed; flagged; Dept C mail **held**; listed in the daily summary | CI | |
| F10 | Event, Dept C, **empty** target date | Not mailed; flagged; listed in the daily summary | CI | |
| F11 | Action plan of F01's event, Dept **B**, due in 2 days | **Dept B** mail, Action required; parent event (F01) shown under the description | CI | |
| F12 | Action plan of F01's event, Dept **A**, due in 45 days | Dept A, **Periodic reminders**, parent shown | CI | |
| F13 | Action plan, status **Completed** | Not mailed, not stored | CI | |
| F14 | Action plan with an unknown QMS number, Dept B, due tomorrow | Dept B, Action required; no parent shown | CI | |
| F15 | Action plan of F02's event, Dept A | In Dept A's mail only, **never** in Dept B's | CI | |
| F16 | Dept D has no items | Dept D gets the **"Nothing pending"** mail | CI | |
| F20 | `make_test_workbook --plant TEST --variant unknown_department --upload`, read again | Whole Test Plant **held**: no department mails, including no "nothing pending"; Today shows the reason; previous data kept | CI | |
| F21 | `--variant duplicate_id` | Same as F20 (duplicate record ID) | CI | |
| F22 | Restore the normal test workbook (`--upload` without variant) and read again | Plant back to normal (F01–F16 results) | manual | |
| F23 | Change the Action Plan rule to daily window **3** days and save; read again | F11 (2 days) still Action required; F12 periodic unchanged; the blue rule text updates | manual | |
| F24 | Set Action Plan "Count the cycle" to **from first appearance**, every 10 days | Action plans appear on their first day, then every 10 days | manual | |

**Mail content:** use **View the email**, then the test mails in section 6.
| ID | Check | Expected | Result |
|---|---|---|---|
| MAIL-01 | Dept A email | One email only, with two sections in this order: *Action required – due soon*, *Periodic reminders – upcoming items* | |
| MAIL-02 | Columns | QMS No., Event type, Action ID, Description (with "Event: …" for action plans), Target date, Days left, Status | |
| MAIL-03 | Subject / greeting | Plant and department names and today's date are correct; counts match the sections | |
| MAIL-04 | Put `<b>bold</b>` in a test row description and read again | Shown as literal text, not bold (no HTML injection) | |
| MAIL-05 | Edit the mail wording with a wrong placeholder `{{ plant }}` | Rejected with "Unknown placeholder"; only listed placeholders can be inserted | |

## 6. Operations
| ID | Steps | Expected | Result |
|---|---|---|---|
| OPS-01 | Activate (Setup → Check & activate) with only the TEST plant active; on a send day after 08:00 wait ≤ 15 min (or Today → **Send today's mails now**) | Each test department mailbox receives **exactly one** mail; the CQA head receives the **daily summary**; History shows each mail as *sent* | |
| OPS-02 | Click **Send today's mails now** again | No department receives a second mail; History shows "already sent today" | |
| OPS-03 | Next day: let the 08:00 run happen on its own (nobody signed in) | Mails arrive between 08:00 and 08:15; `logs\scheduler.log` shows the run | |
| OPS-04 | Sunday (not a send day) | No mails; Monday's run includes periodic items whose cycle day was Sunday | |
| OPS-05 | Backup: run the *Backup* task, then `manage.py verify_backup <latest file>` | "Backup OK" with table counts | |
| OPS-06 | Restore drill on a **copy** of the server (or after hours): follow the restore steps in DEPLOYMENT_WINDOWS.md §4 | App starts; History and setup are as at backup time | |
| OPS-07 | Make the Test Plant Excel unreadable (rename or move the file), then run | Test Plant mails **held**; summary mail says the Excel could not be read; other plants unaffected | |
| OPS-08 | Restore the file, then **Send today's mails now** | Held departments receive today's mail once; already-sent ones are not resent | |
| OPS-09 | Stop the *Web* task during the day | Scheduler still sends at 08:00 the next day (mails do not depend on the web server); restart *Web* afterwards | |
| OPS-10 | Check `logs\web.log` and `logs\scheduler.log` | JSON lines; no passwords, secrets or email bodies | |
| OPS-11 | Pause (Setup → Pause daily mails) and wait for the next 08:00 | No department mails; History shows the run as *skipped – not activated* | |

## 7. Security
| ID | Steps | Expected | Result |
|---|---|---|---|
| SEC-01 | Sign in with a wrong password 5 times | Generic "Invalid username or password"; the 6th attempt is blocked for 15 minutes; failures in the Audit trail | |
| SEC-02 | Sign in as the plant QA head of TEST | Sees only Today and History for Test Plant; no Setup, Users or Audit in the menu | |
| SEC-03 | As the plant QA head, open `https://<server>/#/setup` and call `/api/simple/plants/` directly | "Access denied" page / HTTP 403 | |
| SEC-04 | As the plant QA head, check History | Only Test Plant department mails; no other plants, no admin summary or test mails | |
| SEC-05 | Sign out, then use the browser's back button | Returns to the sign-in page; the old session no longer works | |
| SEC-06 | Inspect Setup → Microsoft 365 and the browser developer tools (Network tab) | Client secret and refresh token never appear; only "set / missing" status | |
| SEC-07 | Audit trail after sections 3–6 | Shows sign-ins, configuration changes (with before/after), activation and manual sends, with user and time | |
| SEC-08 | Deactivate a user | That user is signed out and cannot sign in | |

## 8. User acceptance (PQ), with real plant data
| ID | Steps | Expected | Result |
|---|---|---|---|
| UAT-01 | For each real plant: link its Excel and **Check** | All sheets OK; rejected rows are 0, or explained and fixed by the plant | |
| UAT-02 | Configure every real department with real To/Cc; review with each plant QA head | Recipients confirmed by each plant (sign-off below) | |
| UAT-03 | **Read Excel files now**; for 2 departments per plant, compare Today's lists with the Excel by hand | Same items, sections and dates; anything left out has a correct reason | |
| UAT-04 | Run 1–2 weeks with reminders active, real recipients, and the CQA head reviewing the daily summary | Departments confirm one mail per day with the correct items; no duplicates; held days explained | |
| UAT-05 | Deactivate the TEST plant (Setup → plant → Rename → Deactivate plant) | No mails for TEST; history kept | |

## 9. Defect log
| # | Case ID | Description | Severity (High/Med/Low) | Resolution / re-test | Closed by / date |
|---|---|---|---|---|---|
| 1 | | | | | |

## 10. Sign-off
| Role | Name | Decision (accept / reject) | Signature / date |
|---|---|---|---|
| Corporate QA head (system owner) | | | |
| IT (infrastructure) | | | |
| Tester | | | |
