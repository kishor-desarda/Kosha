# Security policy

Kosha holds companies' drawings and product data, so security reports get priority.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting instead: open the repository's
**Security** tab and press **Report a vulnerability**. Include what you found, how to reproduce
it, the version or commit, and what an attacker could do with it.

What happens next:

| Step | Target |
| --- | --- |
| We acknowledge your report | within 3 working days |
| We confirm the problem and its severity | within 10 working days |
| A fix is released for problems that put data at risk | as a patch release, as fast as we safely can |
| A security advisory is published | with the fix, crediting you if you wish |

These are targets of a volunteer project, not a contract. Please give us a reasonable time to
release a fix before you publish details.

## Supported versions

Until 1.0, only the latest release gets security fixes. From 1.0, the latest minor release and the
one before it are supported.

## Scope

In scope: the Kosha server and web client, the SDKs, and the official add-ins in this repository.
Out of scope: third-party add-ins (report to their authors), and problems that need an attacker to
already be an admin of the Kosha server.

## For people running Kosha

- Put Kosha behind HTTPS (Caddy or nginx) and do not expose port 8080 directly.
- Change the first admin password at once; give people the lowest role that lets them work.
- Switch on only the add-ins you need, and review the permissions each one asks for.
- Back up every night, keep a copy away from the server, and test a restore every month.
- Keep the server's operating system and Kosha up to date.
