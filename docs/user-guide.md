# Kosha user guide

This guide is for the people who use Kosha every day: engineers, checkers, approvers and viewers. It walks through
the common jobs in the order they usually happen. Each section links to a longer page when there is more to say.
Press **F1** on any screen for help about that screen, and **Ctrl+K** to jump to any part, file or page by typing.

## Finding your way

The rail on the left holds the main screens: **Home**, **Explorer** (folders and files), **Inputs** (customer
inputs and projects), **BOMs**, **Changes**, **Deviations**, **ERP sync**, **Duplicates** and **Library**
(standard parts). Approvers and admins also see **Admin**.

- **Search** from the top bar finds parts, files and changes by number, description or old number. Hinglish and
  common spellings are understood ("bolt", "bolt hex", "nut-bolt"). See [language and locale](language-and-locale.md).
- **Favourites, saved views and pinned pages** keep the things you open often one click away. **Undo** reverses
  your last change where it can be undone safely. See [workbench](workbench.md).
- **Theme, accent colour and text size** are under your name in the top bar. See [themes](themes.md).
- The first time you sign in, a short **tour** shows the main screens; you can start it again from Help. The
  **practice folder** is a safe place to try things; nothing in it reaches the ERP. See
  [shop floor and learning](shop-floor-and-learning.md).

## Adding files and parts

1. Open **Explorer**, choose a folder and drop files on it, or use **Upload**. Large uploads continue where they
   stopped if the network drops. Program files (.exe and similar) are refused. See [file safety](file-safety.md).
2. Kosha reads the part number from the file name, the drawing's title block or the CAD file's properties and
   links the file to that **item**. If no number is found, use **New part number**: it suggests the next number in
   your company's format and shows near matches so you do not create a duplicate.
3. From the CAD programs, Word, Excel and PowerPoint you can check files in and out without leaving the program:
   see [Kosha Desktop](desktop.md) and the [Office add-in](office-addin.md).

Previews are made for drawings, 3D models, PDFs, images, Office files, text and CSV files, ZIP archives, e-mails,
STEP files and videos. See [file previews](file-previews.md).

## Changing a file

- **Check out** locks the file for you. Edit it, then **Check in**: a new version is kept and the old one stays.
- A **released** revision cannot be changed. Use **Revise** to start the next revision (A, B, C …).
- **Compare** shows what changed between versions: drawings overlaid in red and blue, and BOM rows added, removed
  or changed.

## Review and release

1. **Submit for review** when the work is done. Kosha first checks the things that block a release (for example a
   missing material or an unreleased part in the BOM) and tells you what to fix.
2. A checker or approver **approves** or **sends back** with a comment.
3. On **Release**, Kosha stamps the PDF and makes a release sheet, then sends the item and BOM to the ERP when an
   ERP is connected. Printed copies carry an "uncontrolled when printed" line, and a superseded revision is
   marked as such. See [release outputs](release-outputs.md).

## BOMs

**BOMs** are built from the CAD assembly at check-in, imported from a table, or edited by hand. Switch the view
between *latest released*, *in work*, *as of a date* and *as built* for a machine, and turn on **Cost** for a
cost roll-up and what-if prices. See [BOM views and cost](bom-views-and-cost.md).

## Changes and deviations

- A **change request** (ECR) records a problem or an idea. An approved request becomes a **change order** (ECO)
  that collects the affected files, starts their new revisions and routes them to the approvers. **Implement**
  releases everything together. See [changes](changes.md).
- A **deviation** allows a temporary difference from the drawing for a limited quantity or time.

## Sharing with outside people

- **Transmittals** send a set of released files to a vendor with a transmittal sheet; Kosha warns if a file was
  superseded since. See [transmittals](transmittals.md).
- **Share links** give an outside person a time-limited link to a file, with a watermark on PDFs. See
  [security](security.md).

## On the shop floor

The **shop view** shows only the released drawing for a part number, large and simple, and each part can carry a
**QR label** that opens it. See [shop floor and learning](shop-floor-and-learning.md).

## AI assistance

If your admin has switched it on, Kosha can suggest categories and materials, draft change logs, check a drawing
against simple rules, read a title block, explain why two parts look alike, take spoken input, and answer
questions under **Ask Kosha**. Nothing is saved until you accept it, and you only see answers from records you
may open. See [AI assistance](ai.md).

## When something goes wrong

- Every error message says what happened, whether your work is safe, and what to do next.
- A deleted file goes to the **recycle bin** and can be brought back. See [file safety](file-safety.md).
- What you type in long forms is kept in your browser until it is saved, so a closed tab does not lose it.
