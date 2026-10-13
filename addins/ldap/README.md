# LDAP / Active Directory sign-in for Kosha

Licence: Apache-2.0. People sign in to Kosha with their network user name and password, checked
against Active Directory or an LDAP directory (OpenLDAP, 389 Directory Server, ...). Their
directory groups decide their Kosha role. Setup for both kinds of directory, with examples, is in
[`docs/directory-sign-in.md`](../../docs/directory-sign-in.md).

## Set up (short version)

1. **Admin › Add-ins › LDAP / Active Directory sign-in › Settings**: the directory address
   (`ldaps://dc1.example.com`, or `ldap://...` with StartTLS), the kind of directory, the search base
   and a read-only service account. Press **Test**.
2. **Admin › Sign-in & sessions**: map directory groups to Kosha roles, then **Check account** with
   a real account to see its groups and the role it would get.
3. People now sign in on Kosha's normal sign-in page. The local `admin` account always keeps
   working with its Kosha password, also when the directory is down.

## How it behaves

- **Search, then bind** (default): Kosha binds with the service account, finds exactly one person
  with the user filter (the typed name is escaped, so it can never change the filter), then checks
  the password by binding as that person. **Direct bind** signs in as `user@example.com` (AD) or with
  a DN template (OpenLDAP) and needs no service account.
- An **empty password is refused before anything is sent**: directories treat a bind without a
  password as an anonymous bind that "succeeds".
- The stable id is `objectGUID` (AD) or `entryUUID` (OpenLDAP); Kosha links accounts by it, so a
  renamed person stays the same Kosha user and a new person with an old name does not inherit an account.
- Groups: `memberOf`, plus nested groups through AD's in-chain matching rule; on OpenLDAP the
  `groupOfNames`, `groupOfUniqueNames` and `posixGroup` entries that list the person. Disabled AD
  accounts are left out by the default filter.
- A directory that cannot be reached, a refused service account or an ambiguous user filter give a
  plain message to the admin (and "the directory could not be reached" to the person); they never
  count as a crash, so the add-in is not switched off during an outage.

## Why no LDAP library

Kosha needs four LDAP operations (bind, search, StartTLS, unbind). The common pure-Python library is
LGPL, which the project's licence rules do not allow inside an add-in, and the C binding needs a
compiler and OpenLDAP's libraries on every server. So `kosha_ldap/ber.py` and `kosha_ldap/client.py`
implement just those operations of LDAP v3 (RFC 4511, filters as in RFC 4515) with the Python
standard library (`socket`, `ssl`).

## Tests

```bash
pytest -q addins/ldap/tests
```

`tests/fake_ldap.py` is a small LDAP server with an Active Directory-like and an OpenLDAP-like tree of
the invented *Demo Engineering*; the tests run the SDK conformance kit
(`kosha_sdk.testing.check_password_provider`) against both, and StartTLS/LDAPS with a throw-away
certificate when the `openssl` program is installed.
