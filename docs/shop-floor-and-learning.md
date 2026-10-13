# Shop floor and learning

## Shop view

**Go to › Shop view** (or `#/shop`) is a large, read-only page for tablets and big screens on the shop floor.
Type a part number, or press **Scan QR code** to read the label on a job card with the tablet's camera.

- Only released revisions are shown. While rev B is being worked on, the shop keeps seeing rev A, with a note.
- A part that is obsolete or spares only says so in red above its drawings.
- A part with no released drawing says so: nothing unreleased reaches the shop floor.
- **Open the stamped PDF to print** opens the released PDF with its release stamp (when the drawing has one).
- **Full screen** fills the screen; Esc goes back.

Scanning needs a browser that can read QR codes with the camera (Chrome and Edge on Android tablets can). Where
the browser cannot, the button is greyed out and says why; typing the part number always works.

## QR labels

On a part's page, **Print QR label** shows a QR code with the part number and description under it.
**Open for printing** opens it on its own, to print on the job card or nameplate. Scanning it with any phone or
tablet opens `…/#/shop?pn=<part number>` on Kosha.

The link uses Kosha's address set under **Admin › Notifications** (the address people reach Kosha on), or the
address the label was made from. The QR code is made inside Kosha (`server/kosha/qr.py`); nothing is sent outside.

API: `GET /api/shop?pn=MX-100-020` and `GET /api/items/{id}/qr.svg`.

## First-login tour

The first time someone signs in, a small card in the bottom-right corner walks through five steps: find a drawing,
preview it, check it out, check it in, and send it for approval. It never covers the page: people can follow along,
go back, or close it. Closing it (or pressing **Done**) is remembered for that person. **Take the tour** in the
account menu starts it again.

## Practice folder

When Kosha starts it makes the folder **Training — safe to try** under the top folder (setting `practice_folder`,
on by default; an admin who deletes the folder is not overruled).

- People try every action there with their own permissions.
- Parts whose files are all in the practice folder are never sent to the ERP: their ERP jobs show as *skipped*.
- Once a day the health worker moves every file in it to the recycle bin, so it starts empty. Its sub-folders stay.
  Nothing is lost: the recycle bin keeps the files for the usual number of days.

Part numbers used in practice stay taken in the item list; use a series such as `TRAIN-` for practice parts.
