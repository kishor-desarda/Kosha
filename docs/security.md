# Security

What Kosha does to keep drawings with the company (specification section 15). Access rules and roles are in
[access.md](access.md); directory sign-in in [directory-sign-in.md](directory-sign-in.md); file safety in
[file-safety.md](file-safety.md).

## Two-step sign-in

After the password, a six-digit code from an authenticator app (Microsoft Authenticator, Google Authenticator,
FreeOTP and others; RFC 6238, 30 seconds). Anyone can switch it on in the menu behind their initials. An admin makes
it a must under Admin › Sign-in & sessions:

- **for roles** (admins and approvers is the usual choice), and
- **for everyone outside the office networks** (list them, for example `192.168.1.0/24 10.8.0.0/16`).

People who must use it and have not set it up are led through the set-up the next time they sign in. Switching it
on gives ten one-time **recovery codes** for a lost phone. An admin can reset it for a person (Admin › Users ›
Security), which also signs them out. A code works once; wrong codes count towards the same sign-in pause as wrong
passwords. Single sign-on through OpenID Connect uses the provider's own second step.

Programs sign in with their own flow: `POST /api/auth/login` answers `{"two_step": "verify" | "setup",
"challenge": ...}` instead of a token; send `POST /api/auth/two-step {challenge, code}` (or, for set-up,
`/api/auth/two-step/setup` and `/api/auth/two-step/setup/confirm`). Programs that run unattended use API keys
([programs-and-events.md](programs-and-events.md)), which are not asked for codes.

## New places

When someone signs in from a network they never used before (the same /24, or /48 for IPv6), admins get a notice.
The first sign-in of an account is not news. It can be switched off under Admin › Sign-in & sessions.

## Bulk-download alarm

More downloads than the limit in an hour (default 200, Admin › Sign-in & sessions; 0 switches it off) pause that
person's downloads and tell the admins. Viewing still works. An admin lets the downloads continue under Admin ›
Users › Security after checking with the person.

## Audit chain

Every audit entry carries a SHA-256 fingerprint of the one before it. Changing or removing an entry breaks the
chain from that point; Admin › Audit log › **Check the audit chain** (or `GET /api/v1/audit/verify`) recomputes it
and names the first entry that does not fit. Entries written before this feature are chained in their order when
the database is upgraded. The chain shows tampering inside the log; a copy of the log in the backups shows what was
there before.

## Personal data

Admin › Users › Security › **Export** gives everything Kosha holds about a person as one JSON file (profile, sign-in
history and places, directory links, notification settings, comments, audit entries). **Erase** removes what
identifies them: the account becomes *Former user 17*, is switched off and signed out, and its sign-in history,
places, directory links and settings go. Drawings, approvals and the audit log stay (records of engineering
decisions are kept), naming the person only by number.

## Watermarks

Released PDFs (released, spares only, obsolete) are downloaded with one line along the bottom of every page: the
person's name, the date and the company's wording (*Company confidential* by default; Admin › Sign-in & sessions ›
Watermarks). Files in work are downloaded unchanged so they can be edited and checked in again. PDFs opened through a
share link always carry the recipient's name. Watermarking uses pdfium (the `pypdfium2` package from
`requirements.txt`); without it, or for a PDF it cannot open, the file goes out unchanged and the audit entry says
so. Images are not watermarked yet.

## Share links

From a released file's page, **Share outside** makes a link for a customer or supplier who has no Kosha account:
who it is for, how many days it works (14 by default, at most 90), an optional password (send it another way), and
view only or download. The recipient opens `https://<kosha>/#/share/<token>` and sees the files with their
revision; when a newer revision is released later, the page says so. The link's secret is shown once and Kosha keeps
only its fingerprint; the part after `#` never reaches a server log. **My share links** (menu behind your initials)
lists your links with how often they were opened and downloaded, and **Withdraw** stops one at once. Admins see every
link. Making, opening, viewing and downloading are all in the audit log; wrong passwords count towards the sign-in
pause of that link.

## Secrets

The secrets Kosha keeps for other systems (the add-ins' ERP, AI and directory keys, the mail server password and
webhook signing secrets) are stored encrypted (`enc1:` values: HMAC-SHA256 keystream with encrypt-then-MAC). The key
comes from the installation's secret key (`secret.key` in the data folder, or `KOSHA_SECRET`), kept apart from the
database, so a copy of the database alone does not reveal them. Secrets stored before this was added are sealed at
the next start.
