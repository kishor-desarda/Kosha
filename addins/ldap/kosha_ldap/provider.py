# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""LDAP / Active Directory sign-in: checks the user name and password typed into Kosha's sign-in form.

Two ways to find a person:

* **search, then bind** (recommended): Kosha signs in to the directory with a read-only service account,
  searches for the person with the user filter, then checks their password by signing in as them;
* **direct bind**: Kosha signs in as the person straight away, as ``user@example.com`` (Active Directory) or with
  a DN template such as ``uid={username},ou=people,dc=example,dc=com`` (OpenLDAP), then reads their own entry.

Groups: Active Directory's ``memberOf`` (with nested groups through the in-chain matching rule), or a search for
group entries that list the person (``member``, ``uniqueMember``, ``memberUid``) on OpenLDAP. The stable id is
``objectGUID`` (AD) or ``entryUUID`` (OpenLDAP). Connections use LDAPS or StartTLS unless the admin switches
that off for a test directory.
"""
from __future__ import annotations

import re
import uuid

from kosha_sdk import AddinContext, Health
from kosha_sdk.signin import Identity, PasswordProvider, SignInError

from .client import (INVALID_CREDENTIALS, SCOPE_BASE, SIZE_LIMIT_EXCEEDED, SUCCESS, Connection, Entry, LdapError,
                     escape_dn_value, first_rdn_value)
from .ber import escape_filter_value

AD, OPENLDAP = "active-directory", "openldap"
DEFAULTS = {
    AD: {"user_filter": "(&(objectCategory=person)(objectClass=user)(sAMAccountName={username})"
                        "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
         "upn_filter": "(&(objectCategory=person)(objectClass=user)(userPrincipalName={username})"
                       "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
         "id_attribute": "objectGUID", "username_attribute": "sAMAccountName",
         "group_filter": "(&(objectClass=group)(member:1.2.840.113556.1.4.1941:={dn}))"},
    OPENLDAP: {"user_filter": "(&(objectClass=person)(uid={username}))", "upn_filter": "(&(objectClass=person)(mail={username}))",
               "id_attribute": "entryUUID", "username_attribute": "uid",
               "group_filter": "(|(&(objectClass=groupOfNames)(member={dn}))(&(objectClass=groupOfUniqueNames)"
                               "(uniqueMember={dn}))(&(objectClass=posixGroup)(memberUid={username})))"},
}
NAME_ATTRIBUTES = ["displayName", "cn", "givenName", "sn", "mail", "userPrincipalName", "memberOf"]
MAX_GROUPS = 500
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class LdapProvider(PasswordProvider):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self.kind = s.get("directory_type") if s.get("directory_type") in DEFAULTS else AD
        d = DEFAULTS[self.kind]
        self.url = (s.get("server_url") or "").strip()
        self.mode = s.get("bind_mode") if s.get("bind_mode") in ("search", "direct") else "search"
        self.bind_dn = (s.get("bind_dn") or "").strip()
        self.bind_password = s.get("bind_password") or ""
        self.base_dn = (s.get("base_dn") or "").strip()
        self.user_filter = (s.get("user_filter") or "").strip()
        self.upn_suffix = (s.get("upn_suffix") or "").strip().lstrip("@")
        self.dn_template = (s.get("user_dn_template") or "").strip()
        self.group_base = (s.get("group_base_dn") or "").strip() or self.base_dn
        self.nested = bool(s.get("nested_groups", True))
        self.group_format = s.get("group_format") if s.get("group_format") in ("name", "dn") else "name"
        self.id_attribute = (s.get("id_attribute") or "").strip() or d["id_attribute"]
        self.username_attribute = d["username_attribute"]
        self.group_filter = (s.get("group_filter") or "").strip() or d["group_filter"]
        self.starttls = bool(s.get("starttls", True))
        self.verify_tls = bool(s.get("verify_tls", True))
        self.ca_file = (s.get("ca_file") or "").strip()
        self.timeout = float(s.get("timeout_s") or 8)

    def label(self) -> str:
        return str(self.ctx.settings.get("label") or "").strip() or "network account"

    # ------------------------------------------------------------------ helpers
    def _connection(self) -> Connection:
        try:
            return Connection(self.url, timeout=self.timeout, starttls=self.starttls, verify_tls=self.verify_tls,
                              ca_file=self.ca_file)
        except LdapError as e:
            raise SignInError(str(e)) from None

    def _problem(self) -> str:
        if not self.url:
            return "Fill in the directory address first (ldaps://... or ldap://...)."
        if not self.base_dn:
            return "Fill in the search base first (for example DC=example,DC=com)."
        if self.mode == "search" and (not self.bind_dn or not self.bind_password):
            return "Fill in the service account and its password, or choose direct sign-in."
        if self.mode == "direct" and self.kind == OPENLDAP and "{username}" not in self.dn_template:
            return "Direct sign-in to OpenLDAP needs a DN template with {username}, e.g. uid={username},ou=people,dc=example,dc=com."
        if self.mode == "direct" and self.kind == AD and not self.upn_suffix and "{username}" not in self.dn_template:
            return "Direct sign-in to Active Directory needs the user name suffix (e.g. example.com)."
        return ""

    def _filter(self, username: str) -> str:
        template = self.user_filter or DEFAULTS[self.kind]["upn_filter" if "@" in username else "user_filter"]
        return template.replace("{username}", escape_filter_value(username))

    def _attributes(self) -> list[str]:
        return [self.id_attribute, self.username_attribute] + NAME_ATTRIBUTES

    def _service_bind(self, conn: Connection) -> None:
        r = conn.bind(self.bind_dn, self.bind_password)
        if r.code == INVALID_CREDENTIALS:
            raise SignInError("The directory refused Kosha's service account (wrong DN or password, or the account "
                              "is locked). Check it under Admin › Add-ins.")
        if r.code != SUCCESS:
            raise SignInError(f"The directory refused Kosha's service account: {r.describe()}.")

    def _find(self, conn: Connection, username: str) -> Entry | None:
        entries, r = conn.search(self.base_dn, self._filter(username), self._attributes(), size_limit=2)
        entries = [e for e in entries if e.dn]
        if len(entries) > 1 or r.code == SIZE_LIMIT_EXCEEDED:
            raise SignInError(f"More than one directory entry matches the user name {username!r}. Make the user "
                              "filter stricter so it finds exactly one person.")
        if r.code not in (SUCCESS, SIZE_LIMIT_EXCEEDED) and not entries:
            if r.code == 32:
                raise SignInError(f"The search base {self.base_dn!r} does not exist in the directory.")
            raise SignInError(f"The directory refused the search for the person: {r.describe()}.")
        return entries[0] if entries else None

    def _external_id(self, entry: Entry) -> str:
        raw = entry.first(self.id_attribute)
        if raw is None:
            self.ctx.log.warning("Directory entry %s has no %s; linking it by its DN", entry.dn, self.id_attribute)
            return "dn:" + entry.dn.lower()
        if self.id_attribute.lower() in ("objectguid", "msds-consistencyguid") and len(raw) == 16:
            return str(uuid.UUID(bytes_le=raw))
        return raw.decode("utf-8", "replace")

    def _groups(self, conn: Connection, entry: Entry, username: str) -> list[str]:
        dns: list[str] = []
        names: dict[str, str] = {}
        if self.kind == AD:
            dns += entry.texts("memberOf")
        if self.kind == OPENLDAP or self.nested:
            flt = self.group_filter.replace("{dn}", escape_filter_value(entry.dn)).replace(
                "{username}", escape_filter_value(username))
            found, r = conn.search(self.group_base, flt, ["cn"], size_limit=MAX_GROUPS)
            if r.code not in (SUCCESS, SIZE_LIMIT_EXCEEDED):
                self.ctx.log.warning("Group search failed: %s", r.describe())
            for g in found:
                dns.append(g.dn)
                if g.text("cn"):
                    names[g.dn.lower()] = g.text("cn")
        out, seen = [], set()
        for dn in dns:
            value = dn if self.group_format == "dn" else names.get(dn.lower()) or first_rdn_value(dn)
            if value and value.casefold() not in seen:
                seen.add(value.casefold())
                out.append(value)
        return out[:MAX_GROUPS]

    def _identity(self, entry: Entry, username: str, groups: list[str]) -> Identity:
        name = entry.text("displayName") or entry.text("cn") or \
            " ".join(p for p in (entry.text("givenName"), entry.text("sn")) if p)
        return Identity(external_id=self._external_id(entry),
                        username=entry.text(self.username_attribute) or username,
                        full_name=name, email=entry.text("mail"), groups=tuple(groups))

    # ---------------------------------------------------------------- contract
    def health(self) -> Health:
        problem = self._problem()
        if problem:
            return Health.down(problem)
        conn = self._connection()
        try:
            conn.open()
            security = "encrypted (LDAPS)" if conn.scheme == "ldaps" else \
                "encrypted (StartTLS)" if conn.encrypted else "NOT encrypted: passwords travel in plain text"
            if self.mode == "search":
                self._service_bind(conn)
                _, r = conn.search(self.base_dn, "(objectClass=*)", ["objectClass"], scope=SCOPE_BASE)
                if r.code != SUCCESS:
                    return Health.down(f"Signed in with the service account, but the search base {self.base_dn!r} "
                                       f"cannot be read: {r.describe()}.")
                return Health.up(f"Connected to {conn.where}, {security}; the service account can read {self.base_dn}.")
            return Health.up(f"Connected to {conn.where}, {security}. Direct sign-in: check it with a real account "
                             "under Admin › Sign-in & sessions.")
        except (LdapError, SignInError) as e:
            return Health.down(str(e))
        finally:
            conn.close()

    def authenticate(self, username: str, password: str) -> Identity | None:
        username = (username or "").strip()
        # an empty password would be an anonymous bind, which many directories accept: never "successful"
        if not password or not username or len(username) > 256 or _CONTROL.search(username):
            return None
        problem = self._problem()
        if problem:
            raise SignInError(problem)
        conn = self._connection()
        try:
            conn.open()
            if self.mode == "search":
                self._service_bind(conn)
                entry = self._find(conn, username)
                if entry is None:
                    return None
                r = conn.bind(entry.dn, password)
                if r.code == INVALID_CREDENTIALS:
                    return None
                if r.code != SUCCESS:
                    raise SignInError(f"The directory did not accept the sign-in: {r.describe()}.")
                self._service_bind(conn)                   # read groups with the service account's rights
            else:
                if self.kind == AD and "{username}" not in self.dn_template:
                    name = username if "@" in username else f"{username}@{self.upn_suffix}"
                else:
                    name = self.dn_template.replace("{username}", escape_dn_value(username))
                r = conn.bind(name, password)
                if r.code == INVALID_CREDENTIALS:
                    return None
                if r.code != SUCCESS:
                    raise SignInError(f"The directory did not accept the sign-in: {r.describe()}.")
                entry = self._find(conn, username)
                if entry is None:
                    raise SignInError("The password is right, but Kosha cannot read the person's own directory "
                                      "entry with the user filter. Check the search base and the user filter.")
            return self._identity(entry, username, self._groups(conn, entry, username))
        except LdapError as e:
            raise SignInError(str(e)) from None
        finally:
            conn.close()
