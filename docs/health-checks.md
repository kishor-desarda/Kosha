# Health checks

Kosha watches its own data and tells the admins when something needs them (specification 16.5 to 16.7). Everything
below runs by itself in a background thread, ten minutes after Kosha starts and then every few minutes; Admin ›
Backups shows the results and has buttons to run the checks now.

## Stored files read back every month

Every file in the store is read back and checked against its SHA-256 fingerprint at least once every 30 days. The
work is spread out: each hourly run reads the files checked longest ago, at most 2 GB, so a large store is covered
over the month without slowing anyone down.

A file whose content no longer matches has been damaged on the disk. Kosha looks for the same file in the folder of
the last backup (`<backup folder>/blobs/`), checks that copy, and puts it back. The damaged file is never deleted: it
is moved to `damaged/` in the data folder. The repair is in the audit log and the admins are told. When there is no
good copy, the file stays as it is, the admins are told every day, and the Backups tab lists it: restore it from an
older backup.

## Nightly consistency check

Once a day Kosha confirms that every file version points to a stored file that exists, and every BOM line to an
existing BOM and item. Files whose content two admins purged on purpose are not counted. Problems are listed under
Admin › Backups with links to the records.

## Alerts

Every hour Kosha looks for these problems and tells the admins, under the bell and on the channels they chose for
*Server health* under My notifications (e-mail by default; Teams, Slack or Telegram through their add-ins). The same
alert is sent at most once a day.

| Problem | When |
| --- | --- |
| Disk filling up | The disk with the data folder is 80% full |
| Backups | No backup recorded, the last one failed, or it is older than two days |
| Restore test | None recorded, the last one failed, or it is older than 35 days |
| ERP queue stuck | A transfer has waited or failed for more than two hours |
| Notifications failing | A notice could not be delivered in the last day |
| Damaged files | A stored file does not match its fingerprint and could not be repaired |
| Consistency | The nightly check found problems |
| HTTPS certificate | The certificate in `KOSHA_TLS_CERT` (PEM file) ends within 21 days |

## Drafts kept in the browser

Unsaved edits to a file's properties, a BOM's lines and a change log are kept in the browser while they are typed.
After a crash, a closed tab or a reload, the page offers them again: **Restore them** or **Discard**. A draft is
removed once the edits are saved. Drafts stay in that browser only and belong to the person signed in.
