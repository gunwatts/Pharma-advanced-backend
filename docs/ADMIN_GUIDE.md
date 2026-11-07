# Admin guide: daily QMS reminder mails

## What the system does
Every send day (Monday–Saturday by default) at 08:00 the system:
1. Reads each plant's Excel workbook from OneDrive/SharePoint.
2. Finds the QMS events and action plans that are still open (status **Open** by default).
3. Sends **one mail per department per plant**. It contains only that department's items, in two sections:
   - **Action required:** items due within the next few days (7 by default), every day until closed.
   - **Periodic reminders:** items with later target dates, every N days (15 by default).
4. Sends you (the corporate QA head) one **daily summary**: what each department received, plus any problem.

An action plan is mailed only to **its** department, and the mail shows its parent event (for example the change
control) for context. A change control with actions for QC, Stores and Production therefore produces three
different department mails. Items **past their target date are not mailed**.

## The plant workbook (same layout in every plant)
- One sheet per QMS event type: **Change Control, Deviation, Incident, CAPA, OOS, OOT** (you can add more).
  Columns: **QMS Number, Department, Description, Target Date, Status**.
- One sheet **Action Plans** for all events of the plant. Columns: **Action ID, QMS Number, Department,
  Description, Target Date, Status**. The QMS Number links the action to its event.
- Download the ready-made template in **Setup → Excel layout & rules → Download Excel template**.
- Department names must match the department list (you can add other spellings, for example "Prodn" for Production).
- Dates can be real Excel dates or text such as 24-10-2026.

## Setup in 5 steps
1. **Microsoft 365 & mailbox:** choose how the app connects (IT-registered app, or *Sign in with Microsoft*). Enter
   the shared mailbox and click **Save & test connection**, then **Send a test email to me**.
2. **Excel layout & rules:** for each sheet, check the sheet name, column headers, statuses and the reminder rule.
   The blue box explains each rule in plain words.
   - *Action required daily from … days before target*: for example 7.
   - *Periodic reminder every … days*: for example 15, or empty for none.
   - *Count the cycle*: back from the target date (e.g. 45, 30, 15 days before), or from when the item first
     appeared in the Excel.
   - *Only items due within … days*: for long-dated items, for example 90. Items due further away are not mailed yet.
3. **Plants & departments:** **Add plant**, then on its card:
   - **Excel file:** paste the OneDrive/SharePoint link and click **Use link**. The check shows, sheet by sheet,
     whether the columns match and how many rows were rejected.
   - **Departments & recipients:** add each department with its **To** (department head) and **Cc** (QMS team,
     plant QA head), then **Save**.
4. **Mail & schedule:** time, days, the "nothing pending" option, your summary address, and the mail wording.
5. **Check & activate:** when everything is "done", tick the confirmation and click **Activate daily mails**.

## Every day
- **Today's reminders** shows what every department will receive and why other items are left out.
  **Read Excel files now** reads the latest data. **Send today's mails now** sends immediately; departments
  already mailed today are skipped.
- **History** lists every mail sent.
- Your daily summary mail tells you if a plant's Excel could not be read. That plant's mails are then **held**,
  so nobody receives a wrong "nothing pending" mail. Fix the file, then use **Send today's mails now**.

## Adding things later
- **New plant:** Setup → Plants & departments → **Add plant**, paste its Excel link, add departments.
- **New department:** add the name in *Department names*, then add it to the plants that have it.
- **New event type:** Setup → Excel layout & rules → **Add event type** (sheet name, columns, rule), and add the
  sheet to the plant workbooks.
- **Plant QA heads:** Users → **Add user**, role *Plant QA head*, choose their plant(s). They can see that plant's
  Today page and History, but cannot change the setup.
