# Roles and access

Kosha separates **what a person can do** (roles) from **what a person can see** (access rules).

## Roles

Everyone has one main role. Each role can do everything the roles above it in this list can:

| Role | Can |
| --- | --- |
| Viewer | Search, view and print released drawings. |
| Engineer | Create parts and drawings, take files for editing, build BOMs, raise changes. |
| Checker | Everything an engineer does, and review drawings before the approver. |
| Approver | Everything a checker does, and approve releases, change orders and deviations. |
| Administrator | Everything: users, access, backups, add-ins and settings. |

Extra roles can be added beside the main one (Admin › Users › Access): **Data steward** (numbering, categories,
duplicates, allowing a locked customer part to be reused), **Spares and service**, **Quality and purchase**.

## Kinds of account

- **Person:** staff, signs in normally.
- **Vendor or outside designer:** sees only the parts and files of the projects named on the account, can only read,
  and stops working after its end date. It cannot sign in until it has at least one project.
- **Service account:** for programs (ERP connector, add-ins); it never signs in on a screen.

Any account can be **view only** (previews, no downloads) and can have an **end date**; the day after, the account
and all its open sessions stop working.

## Access rules (Admin › Access)

Everyone sees everything unless a rule limits it. A rule names the people and roles who may see:

- a **folder** (with its sub-folders and files),
- a **project** (with its parts and their files),
- a **category** (parts whose number starts with that code, and their files),
- a **customer**: who may see that customer's *restricted* parts. A part can only be restricted once its customer
  has a rule, so nobody is locked out by accident.

Hidden things answer "not found", exactly like things that do not exist. Administrators always see everything.

## Access review

Admin › Access (or Access review, for approvers) lists every account with its roles, projects, the limited areas
it can see, its end date and last sign-in. Rows that need a look (never signed in, end date passed) are marked.
Download it as CSV and go through it every quarter.
