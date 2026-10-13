# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""LDAP / Active Directory sign-in against a small LDAP server in the test process, through the SDK conformance kit."""
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "sdk" / "python")]

import fake_ldap as FL  # noqa: E402
from kosha_ldap import ber  # noqa: E402
from kosha_ldap.client import Connection, LdapError, escape_dn_value, first_rdn_value  # noqa: E402
from kosha_ldap.provider import LdapProvider  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.signin import Identity, SignInError  # noqa: E402
from kosha_sdk.testing import check_manifest, check_password_provider  # noqa: E402

AD_PEOPLE = f"OU=People,{FL.AD_BASE}"


@pytest.fixture(scope="module")
def cert():
    folder = FL.tls_folder()
    yield FL.make_certificate(folder)
    import shutil
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def ad(cert):
    s = FL.FakeLdap(FL.ad_entries(), FL.passwords(), cert=cert).start()
    yield s
    s.stop()


@pytest.fixture
def ol():
    s = FL.FakeLdap(FL.ol_entries(), FL.passwords()).start()
    yield s
    s.stop()


def ad_provider(server, **overrides) -> LdapProvider:
    settings = {"server_url": server.url, "directory_type": "active-directory", "base_dn": FL.AD_BASE,
                "bind_mode": "search", "bind_dn": f"CN=svc-kosha,OU=Service,{FL.AD_BASE}",
                "bind_password": FL.SERVICE_PASSWORD, "starttls": False, "timeout_s": 3, **overrides}
    return LdapProvider(AddinContext("org.kosha.ldap", settings=settings))


def ol_provider(server, **overrides) -> LdapProvider:
    settings = {"server_url": server.url, "directory_type": "openldap", "base_dn": FL.OL_BASE,
                "bind_dn": f"cn=kosha,ou=services,{FL.OL_BASE}", "bind_password": FL.SERVICE_PASSWORD,
                "starttls": False, "timeout_s": 3, **overrides}
    return LdapProvider(AddinContext("org.kosha.ldap", settings=settings))


# ------------------------------------------------------------------ BER and filters

def test_ber_matches_hand_made_bytes():
    # a BindRequest (RFC 4511 4.2) for "cn=a" with the password "pw", as message 1, written out by hand
    msg = ber.sequence(ber.integer(1), ber.sequence(ber.integer(3), ber.octets("cn=a"), ber.octets("pw", 0x80), tag=0x60))
    assert msg.hex() == "3012" "020101" "600d" "020103" "0404636e3d61" "80027077"
    assert ber.length(300) == b"\x82\x01\x2c" and ber.integer(128) == b"\x02\x02\x00\x80" and ber.integer(-1) == b"\x02\x01\xff"
    el, end = ber.decode(msg)
    assert end == len(msg) and el.children()[0].int() == 1 and el.children()[1].tag == 0x60
    assert ber.message_size(msg[:1]) is None and ber.message_size(msg[:2]) == len(msg)


@pytest.mark.parametrize("raw", [b"\x30", b"\x30\x05\x02\x01", b"\x30\x80\x00\x00", b"\x30\x85\x01\x01\x01\x01\x01", b"\x1f\x01\x00"])
def test_broken_ber_is_refused(raw):
    with pytest.raises(ber.BerError):
        ber.decode(raw)


def test_filters_compile_to_ber():
    assert ber.compile_filter("(uid=asha)").hex() == "a30b" "0403756964" "0404" "61736861"
    assert ber.compile_filter("objectClass=*") == b"\x87\x0bobjectClass"
    assert ber.compile_filter("(&(a=1)(!(b=2)))")[0] == 0xA0
    assert ber.compile_filter("(|(a=1)(b=2))")[0] == 0xA1
    assert ber.compile_filter("(cn=Ash*Ra*o)")[0] == 0xA4
    ext = ber.compile_filter("(userAccountControl:1.2.840.113556.1.4.803:=2)")
    assert ext[0] == 0xA9 and b"1.2.840.113556.1.4.803" in ext and b"userAccountControl" in ext
    assert ber.compile_filter("(cn=a\\29b)") == ber.compile_filter("(cn=a\\29b)")
    for bad in ("(uid=asha", "(&(uid=a)", "uid", "(=x)", "(uid=a\\zz)", "(uid=a))"):
        with pytest.raises(ber.BerError):
            ber.compile_filter(bad)


def test_user_input_cannot_change_a_filter_or_dn():
    assert ber.escape_filter_value("a*)(|(uid=*") == "a\\2a\\29\\28|\\28uid=\\2a"
    assert ber.escape_filter_value("back\\slash\x00") == "back\\5cslash\\00"
    assert escape_dn_value(" a,b+c=d#") == "\\ a\\,b\\+c\\=d#" and escape_dn_value("#x ") == "\\#x\\ "
    assert first_rdn_value("CN=Kosha Engineers,OU=Groups,DC=example,DC=com") == "Kosha Engineers"
    assert first_rdn_value("CN=Smith\\, Jo,OU=People") == "Smith, Jo"
    assert first_rdn_value("cn=Ren\\c3\\a9,ou=x") == "René"


# ------------------------------------------------------------------ Active Directory

def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance_active_directory(ad):
    assert check_password_provider(ad_provider(ad), "asha", FL.PASSWORD) == []


def test_conformance_openldap(ol):
    assert check_password_provider(ol_provider(ol), "sofia", FL.PASSWORD) == []


def test_active_directory_identity_and_nested_groups(ad):
    p = ad_provider(ad)
    asha = p.authenticate("asha", FL.PASSWORD)
    assert asha == Identity(external_id=str(FL.GUIDS["asha"]), username="asha", full_name="Asha Rao",
                            email="asha@demo.example.com", groups=("Kosha Engineers",))
    daniel = p.authenticate("DANIEL", FL.PASSWORD)
    assert set(daniel.groups) == {"Design Leads", "Kosha Approvers"}          # Kosha Approvers through Design Leads
    assert ad_provider(ad, nested_groups=False).authenticate("daniel", FL.PASSWORD).groups == ("Design Leads",)
    dns = ad_provider(ad, group_format="dn").authenticate("asha", FL.PASSWORD).groups
    assert dns == (f"CN=Kosha Engineers,OU=Groups,{FL.AD_BASE}",)
    assert p.describe() == {"style": "password", "label": "network account"}


def test_disabled_and_wrong_passwords_and_anonymous_binds(ad):
    p = ad_provider(ad)
    assert p.authenticate("tom", FL.PASSWORD) is None                          # disabled in the directory
    assert p.authenticate("asha", "wrong-password") is None
    ad.log.clear()
    assert p.authenticate("asha", "") is None
    assert not ad.log                                                         # never asked: an empty password
    assert p.authenticate("asha\nx", FL.PASSWORD) is None


def test_upn_and_direct_bind(ad):
    p = ad_provider(ad)
    assert p.authenticate("asha@demo.example.com", FL.PASSWORD).username == "asha"
    direct = ad_provider(ad, bind_mode="direct", bind_dn="", bind_password="", upn_suffix="demo.example.com")
    assert direct.authenticate("asha", FL.PASSWORD).external_id == str(FL.GUIDS["asha"])
    assert direct.authenticate("asha", "wrong-password") is None
    assert any(e[0] == "bind" and e[1] == "asha@demo.example.com" for e in ad.log)


def test_service_account_and_directory_problems_are_explained(ad):
    with pytest.raises(SignInError, match="service account"):
        ad_provider(ad, bind_password="wrong").authenticate("asha", FL.PASSWORD)
    with pytest.raises(SignInError, match="does not exist"):
        ad_provider(ad, base_dn="DC=nowhere,DC=example").authenticate("asha", FL.PASSWORD)
    ad.mode.add("duplicate")
    with pytest.raises(SignInError, match="More than one directory entry"):
        ad_provider(ad).authenticate("asha", FL.PASSWORD)
    ad.mode = {"busy"}
    with pytest.raises(SignInError, match="busy"):
        ad_provider(ad).authenticate("asha", FL.PASSWORD)


def test_unreachable_and_silent_directories(ad):
    with pytest.raises(SignInError, match="Cannot reach the directory"):
        ad_provider(ad, server_url="ldap://127.0.0.1:9").authenticate("asha", FL.PASSWORD)
    ad.mode.add("hang")
    started = time.monotonic()
    with pytest.raises(SignInError, match="did not answer within 1 seconds"):
        ad_provider(ad, timeout_s=1).authenticate("asha", FL.PASSWORD)
    assert time.monotonic() - started < 5
    with pytest.raises(SignInError, match="must look like"):
        ad_provider(ad, server_url="https://dc1.example.com").authenticate("asha", FL.PASSWORD)


def test_health(ad):
    h = ad_provider(ad).health()
    assert h.ok and "NOT encrypted" in h.message and FL.AD_BASE in h.message
    assert not ad_provider(ad, bind_password="wrong").health().ok
    assert "Fill in the directory address" in ad_provider(ad, server_url="").health().message
    assert "service account" in ad_provider(ad, bind_dn="").health().message
    assert "suffix" in ad_provider(ad, bind_mode="direct", bind_dn="").health().message


def test_starttls_and_ldaps(ad, cert):
    if cert is None:
        pytest.skip("openssl is not installed")
    ad.mode.add("require_tls")
    with pytest.raises(SignInError, match="confidentiality"):
        ad_provider(ad).authenticate("asha", FL.PASSWORD)
    ca = str(cert[0])
    ad.log.clear()
    p = ad_provider(ad, starttls=True, ca_file=ca)
    assert p.authenticate("asha", FL.PASSWORD).username == "asha"
    assert all(e[3] for e in ad.log if e[0] == "bind")                       # every bind after StartTLS
    assert "encrypted (StartTLS)" in p.health().message
    with pytest.raises(SignInError, match="not trusted"):
        ad_provider(ad, starttls=True).authenticate("asha", FL.PASSWORD)     # self-signed and no CA file given
    ad.mode.add("no_starttls")
    with pytest.raises(SignInError, match="refused StartTLS"):
        ad_provider(ad, starttls=True, ca_file=ca).authenticate("asha", FL.PASSWORD)
    s = FL.FakeLdap(FL.ad_entries(), FL.passwords(), cert=cert, ldaps=True).start()
    try:
        p = ad_provider(s, ca_file=ca)
        assert p.authenticate("asha", FL.PASSWORD).username == "asha"
        assert "encrypted (LDAPS)" in p.health().message
    finally:
        s.stop()


# ------------------------------------------------------------------ OpenLDAP

def test_openldap_identity_and_groups(ol):
    sofia = ol_provider(ol).authenticate("sofia", FL.PASSWORD)
    assert sofia == Identity(external_id="3c9a1e52-5b1f-4f8e-9d43-000000000003", username="sofia",
                             full_name="Sofia Lindqvist", email="sofia@demo.example.org",
                             groups=("kosha-viewers", "cad-users"))
    direct = ol_provider(ol, bind_mode="direct", bind_dn="", bind_password="",
                         user_dn_template=f"uid={{username}},ou=people,{FL.OL_BASE}")
    assert direct.authenticate("sofia", FL.PASSWORD).username == "sofia"
    assert direct.authenticate("sofia", "nope-nope-nope") is None
    assert direct.authenticate("sofia,ou=people", FL.PASSWORD) is None       # escaped: cannot reach another DN


def test_missing_stable_id_falls_back_to_the_dn(ol):
    p = ol_provider(ol, id_attribute="nsUniqueId")
    assert p.authenticate("sofia", FL.PASSWORD).external_id == f"dn:uid=sofia,ou=people,{FL.OL_BASE}".lower()


def test_connection_reads_entries_and_skips_referrals(ad):
    with Connection(ad.url, starttls=False, timeout=3) as c:
        assert c.bind(f"CN=svc-kosha,OU=Service,{FL.AD_BASE}", FL.SERVICE_PASSWORD).code == 0
        entries, r = c.search(FL.AD_BASE, "(objectClass=group)", ["cn"])
        assert r.code == 0 and sorted(e.text("cn") for e in entries) == ["Design Leads", "Kosha Approvers", "Kosha Engineers"]
        entries, r = c.search(FL.AD_BASE, "(objectClass=group)", ["cn"], size_limit=1)
        assert r.code == 4 and len(entries) == 1
    with pytest.raises(LdapError, match="not valid"):
        with Connection(ad.url, starttls=False, timeout=3) as c:
            c.search(FL.AD_BASE, "(broken", [])
