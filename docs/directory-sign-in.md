# Directory sign-in: Active Directory, LDAP and OpenID Connect

People can sign in to Kosha with an account they already have, instead of a separate Kosha password:

| Way | Add-in | What people see |
| --- | --- | --- |
| Active Directory, OpenLDAP or another LDAP directory | `addins/ldap` (LDAP / Active Directory sign-in) | Kosha's normal sign-in form; they type their network user name and password |
| Any OpenID Connect provider (Keycloak, Authentik, Zitadel, Dex, a cloud identity service) | `addins/oidc` (OpenID Connect sign-in) | A **Sign in with ...** button on the sign-in page |

Both are official add-ins (Apache-2.0) behind the SDK's sign-in interface
(`kosha_sdk.signin`, see [the SDK README](../sdk/python/README.md#sign-in-providers)); Kosha's core knows no
directory product. Other ways of signing in (SAML through a gateway, a different directory) are written as
add-ins of the same kind.

## What Kosha does, whatever the directory

- **The local admin always works.** The built-in `admin` account is never linked to a directory and signs in
  with its Kosha password, also when the directory cannot be reached. Keep its password somewhere safe.
- **Local accounts first.** A user name that belongs to a Kosha account not linked to a directory is checked
  against its Kosha password first; other user names (and a local name whose password did not match) are
  checked against the LDAP add-in. Failed attempts count towards the same pause as before (Admin ›
  Sign-in & sessions: failed sign-ins per account and per network address).
- **Accounts are linked by the directory's own id**, never by name: `objectGUID` (Active Directory),
  `entryUUID` (OpenLDAP) or issuer and subject (OpenID Connect). A renamed person stays the same Kosha user;
  a new person who gets an old user name does not inherit the old account.
- **No silent take-over.** At a person's first directory sign-in Kosha creates their account (you can switch
  that off). If a Kosha account with the same user name already exists, Kosha refuses and tells the person to
  ask you: link the account under **Admin › Users › Directory** (enter their directory user name; the link is
  completed at their next directory sign-in). Their history, files and work stay with the account.
- **Groups decide roles.** Under **Admin › Sign-in & sessions › Groups and Kosha roles** you list directory
  groups and the role each gives (viewer, engineer, checker, approver, admin); someone in several groups gets the
  highest. People in none of the groups cannot sign in, unless you choose a role for everyone else. With
  "Update the role from the groups at every sign-in" on, a change in the directory applies at the next
  sign-in.
- **Directory users change their password in the directory.** Kosha's own password of a linked user is no
  longer used; unlinking ends their sessions and lets them sign in with a Kosha password again (set one
  with Reset password).
- **Everything is in the audit log:** sign-ins (with the add-in used), refused directory sign-ins and why,
  accounts created at a first sign-in, role changes from groups, links and unlinks.
- **A directory outage is not a crash.** "The directory could not be reached" is an expected answer: the
  add-in is not switched off, people are told their work is safe, and those attempts do not count towards
  the pause.

## Active Directory

1. Create a service account for Kosha, for example `svc-kosha` in `OU=Service Accounts`. It needs no rights
   beyond reading users and groups (the default for domain users). Set its password not to expire, or plan
   to update it in Kosha.
2. Prefer **LDAPS** (`ldaps://dc1.example.com`, port 636). With plain `ldap://` keep **Use StartTLS** on.
   If your domain controllers use certificates from an internal CA, put the CA certificate (PEM) on the
   Kosha server and enter its path as **CA certificate file**.
3. **Admin › Add-ins › LDAP / Active Directory sign-in › Settings**:

   | Setting | Example |
   | --- | --- |
   | Directory address | `ldaps://dc1.example.com` |
   | Kind of directory | `active-directory` |
   | Search base | `DC=example,DC=com` |
   | How Kosha finds people | `search` |
   | Service account | `CN=svc-kosha,OU=Service Accounts,DC=example,DC=com` or `svc-kosha@example.com` |
   | Service account password | (the password) |
   | Name shown on the sign-in page | `Example network account` |

   The default user filter finds people by `sAMAccountName` (or `userPrincipalName` when the typed name
   contains `@`) and leaves out disabled accounts:
   `(&(objectCategory=person)(objectClass=user)(sAMAccountName={username})(!(userAccountControl:1.2.840.113556.1.4.803:=2)))`.
   Nested groups are included (in-chain matching rule) unless you switch that off.

   Press **Test**: it signs in with the service account and reads the search base.
4. Create groups such as `Kosha Viewers`, `Kosha Engineers`, `Kosha Approvers`, `Kosha Admins` (or use
   existing ones) and list them under **Admin › Sign-in & sessions**. Groups are matched by name; choose
   **Groups are matched by: dn** if two groups share a name in different OUs, and write the full DNs.
5. **Check a directory account** on the same screen with a real account: it shows the groups Kosha sees and
   the role and account the person would get, without signing anyone in.

Without a service account, choose **direct** and enter the **user name suffix** (`example.com`): Kosha then
signs in as `asha@example.com` and reads the person's own entry.

## OpenLDAP (and other LDAP directories)

| Setting | Example |
| --- | --- |
| Directory address | `ldap://ldap.example.org` (StartTLS on) or `ldaps://ldap.example.org` |
| Kind of directory | `openldap` |
| Search base | `dc=example,dc=org` |
| Service account | `cn=kosha,ou=services,dc=example,dc=org` |
| Groups under | `ou=groups,dc=example,dc=org` (empty: the search base) |

The default user filter is `(&(objectClass=person)(uid={username}))`; groups are the `groupOfNames` and
`groupOfUniqueNames` entries that list the person's DN and the `posixGroup` entries that list their `uid`.
Give your own **User filter** or **Group filter** when your tree differs; `{username}` and `{dn}` are
replaced by escaped values. The stable id is `entryUUID` (ask for another with **Attribute with the stable
id**; without one, Kosha falls back to the DN and logs a warning). For **direct** sign-in, give a **DN
template** such as `uid={username},ou=people,dc=example,dc=org`.

## OpenID Connect, for example Keycloak

1. In Keycloak, open your realm (for example `demo`) and create a client:
   - Client ID `kosha`, **Client authentication** on (confidential), **Standard flow** on, other flows off.
   - **Valid redirect URIs**: the redirect address shown under **Admin › Sign-in & sessions › Sign-in
     add-ins**, for example `https://plm.example.com/api/auth/sso/org.kosha.oidc/callback`. Kosha builds it
     from the address set under **Admin › Notifications** (Kosha's own address); set that first when Kosha
     runs behind a proxy.
   - **Advanced › Proof Key for Code Exchange**: `S256`.
   - **Client scopes › kosha-dedicated › Add mapper › Group Membership**: token claim name `groups`, "Full
     group path" off (or on: Kosha drops the leading `/`), added to the ID token.
   - Copy the client secret from **Credentials**.
2. **Admin › Add-ins › OpenID Connect sign-in › Settings**:

   | Setting | Example |
   | --- | --- |
   | Issuer address | `https://sso.example.com/realms/demo` |
   | Client ID | `kosha` |
   | Client secret | (from Keycloak) |
   | Button text | `Example account` → the button reads **Sign in with Example account** |

   Press **Test**: it reads the discovery document and the signing keys.
3. List the group names (`kosha-engineers` ...) under **Admin › Sign-in & sessions**.

Other providers work the same way: a confidential web client with the authorization code flow, the
redirect address above, PKCE, and a `groups` claim (or set **Claim with the groups** to the claim your
provider uses; if the ID token leaves groups out, Kosha asks the user-info endpoint). Kosha accepts only
public-key signatures (RS256 is the usual default). Install the Python package `cryptography` (it is in
`server/requirements.txt`) for PS*, ES* and EdDSA.

## Security notes

- Passwords typed into Kosha reach only the LDAP add-in, over LDAPS or StartTLS unless you switch that off
  (the add-in's Test says when passwords would travel unencrypted). An empty password is never sent:
  directories treat it as an anonymous bind that "succeeds".
- The OpenID Connect round trip uses a single-use `state`, a nonce and PKCE, is bound to the browser that
  started it (a cookie limited to `/api/auth/sso`), must come back within ten minutes, and gives the browser
  a one-time ticket (valid two minutes) instead of a token in an address.
- Third-party sign-in add-ins ask for the permission `users:sign-in`; they stay off until an admin has
  reviewed it under **Admin › Add-ins**, like every other add-in.
- Add-in calls run through the add-in guard with a time-out (20 seconds during sign-in).

## Troubleshooting

| Message | What to do |
| --- | --- |
| "Kosha could not reach the directory to check your password" | Check the directory address and the network; the admin's Test shows the exact reason. The local admin still signs in. |
| "The directory refused Kosha's service account" | Check the service account DN and password, and whether it is locked. |
| "More than one directory entry matches the user name" | Make the user filter stricter (for example add `(objectCategory=person)`). |
| "A Kosha account with the user name ... already exists" | Link it under Admin › Users › Directory. |
| "You are not in a group that gives access to Kosha" | Add the person's group under Groups and Kosha roles, or choose a role for everyone else. |
| "The provider calls itself ..., not ..." | Copy the issuer address exactly as the provider shows it (Keycloak: `.../realms/<realm>`). |
| "The ID token has expired" or "is dated in the future" | Fix the clock of the Kosha server or the provider (use NTP). |
| "This sign-in has expired or was started in another browser window" | Start the sign-in again in one browser window. |
