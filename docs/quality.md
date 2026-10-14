# Quality: nonconformance, corrective actions and FMEA

**Quality** in the left-hand menu holds three registers. Each record keeps photos and files, a discussion, and a
full history; nothing is ever deleted (cancelled records stay in the register with their number).

## Who does what

| | Engineer | Quality role | Approver |
| --- | --- | --- | --- |
| Report a nonconformance, start a corrective action or an FMEA | yes | yes | yes |
| Decide what happens to the parts, close a report | | yes | yes |
| Check that a corrective action worked and close it | | yes, if not its owner | yes, if not its owner |
| Set the RPN limit | | yes | yes |

The quality role is an extra role an administrator gives on top of any other (for example an inspector who is
otherwise a viewer): **Admin → Users**, then **Access** on their row.

## Nonconformance reports (NCR-YY-NNNN)

Something does not match its drawing: found at incoming inspection, in production, at assembly, at the customer or
at the supplier. The report names the part and revision, the batch or serial numbers, the quantity and the
supplier, with photos.

    Reported  →  Decided  →  Closed          (or Cancelled while still open)

**Decide disposition** picks one of:

- **Rework to drawing**: the parts are brought back to the drawing.
- **Repair**: the parts are made usable but stay different from the drawing.
- **Use as is**: the parts are used as they are.
- **Scrap**.
- **Return to supplier** (the supplier must be named).

*Use as is* and *repair* leave the part different from its drawing, so they need a reason and Kosha raises a
**draft deviation** for the part, batch and quantity, which is then approved the usual way (four eyes). **Close
report** records what was done with the parts.

A report can start a corrective action, or be linked to one that is already open (several reports with the same
cause share one).

## Corrective and preventive actions (CAPA-YY-NNNN)

A corrective action removes the cause of a problem that happened; a preventive one stops a problem before it
happens. Each has an owner and a due date.

    Find the cause  →  Actions  →  Check it worked  →  Closed      (or Cancelled before the check)

1. **Find the cause**: the problem and the immediate containment (stock checked, parts held). Moving on needs the
   **root cause** written down (five whys: ask "why" until you reach something you can change).
2. **Actions**: each action has someone who does it and a date; overdue ones are marked, and everyone sees their
   open actions on the Quality screen and as a badge on the menu. Moving on needs every action done.
   **Raise a change request** starts an engineering change request with the problem and root cause when the
   design has to change.
3. **Check it worked**: someone other than the owner, with the approver or quality role, writes down how they
   checked (for example "3 months, 600 shafts, none out of tolerance"). *It did not work* sends it back to finding
   the cause, keeping what was learned.

## FMEA

A **design FMEA** asks how a part or assembly can fail; a **process FMEA** asks how making it can go wrong. Each row
is one failure mode: function, failure mode, effect, severity (S), cause, occurrence (O), prevention, detection
control, detection (D), each score from 1 to 10.

- **RPN** = S × O × D, worked out as you type.
- A row needs attention when its RPN is above the company's **RPN limit** (100 unless changed) or its severity is
  9 or 10 (safety or the law), whatever the RPN. A row that needs attention and has no action says so.
- After the action, score the row again (S, O, D after); the row is then judged on the new scores. A severity of 9
  or 10 stays flagged, because only a design change lowers severity.
- **Excel** downloads the worksheet with the rows that need attention marked.

Leaving the page with unsaved rows saves them; a row that cannot be saved (for example a missing score) is named
in the message.

## Through the API

| | |
| --- | --- |
| `GET /api/quality` | counts for the Quality screen, choices, what you may do |
| `GET/POST /api/ncrs`, `GET/PATCH /api/ncrs/{id}` | register (`state=open`, `item_id`, `q`), new report, read, edit |
| `POST /api/ncrs/{id}/disposition` `{disposition, note}` | decide; `use_as_is`/`repair` raise a draft deviation |
| `POST /api/ncrs/{id}/close` `{note}`, `.../cancel` `{reason}` | close, cancel |
| `POST /api/ncrs/{id}/capa` `{capa_id?}` | start a corrective action from the report, or link it to one |
| `GET/POST /api/capas`, `GET/PATCH /api/capas/{id}` | register (`state`, `mine=1`, `q`), new, read, edit |
| `POST /api/capas/{id}/actions`, `PATCH/DELETE .../actions/{aid}` | add, tick off (`done`, `note`), remove |
| `POST /api/capas/{id}/advance` | next step (`root_cause`; `effectiveness`, `effective`) |
| `POST /api/capas/{id}/change-request`, `.../cancel`, `.../ncrs` | raise an ECR, cancel, link a report |
| `GET/POST /api/fmeas`, `GET/PUT /api/fmeas/{id}`, `.../export` | list, new, read, save all rows, Excel |
| `PUT /api/quality/rpn-limit` `{rpn_limit}` | the company's RPN limit (1 to 1000) |
| `POST/GET/DELETE /api/ncrs/{id}/attachments[/{aid}]` (and `capas`) | photos and files |
