# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add-in registry: discovery, approval, settings, and the guard that keeps Kosha safe."""
import textwrap
import time

import pytest

from kosha import addins, config, db
from kosha_sdk import AddinError


@pytest.fixture
def registry(tmp_path, monkeypatch):
    settings: dict = {}
    monkeypatch.setattr(db, "get_setting", lambda k: settings.get(k))
    monkeypatch.setattr(db, "set_setting", lambda k, v: settings.__setitem__(k, v))
    monkeypatch.setattr(db, "audit", lambda *a, **k: None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "ADDIN_DIRS", [tmp_path / "local"])
    monkeypatch.setattr(config, "ADDIN_MAX_FAILURES", 3)
    monkeypatch.setattr(config, "ADDIN_TIMEOUT_SECONDS", 1.0)
    _make(tmp_path / "local" / "flaky", "com.example.flaky", "flaky_addin", permissions="[items:read]")
    _make(tmp_path / "local" / "future", "com.example.future", "future_addin", kosha=">=9.0")
    (tmp_path / "local" / "broken").mkdir(parents=True)
    (tmp_path / "local" / "broken" / "kosha-addin.yaml").write_text("id: Not Valid\n")
    r = addins.Registry()
    r.discover()
    return r


def _make(folder, addin_id, pkg, permissions="[]", kosha=">=0.5,<2.0"):
    (folder / pkg).mkdir(parents=True)
    (folder / "kosha-addin.yaml").write_text(textwrap.dedent(f"""\
        id: {addin_id}
        name: {pkg}
        version: 1.0.0
        publisher: Example
        licence: Apache-2.0
        kosha: "{kosha}"
        kind: [notifier]
        entry_points: {{server: "{pkg}.main:Addin"}}
        permissions: {permissions}
        settings:
          token: {{type: secret, label: Token}}
          retries: {{type: int, default: 2}}
        """))
    (folder / pkg / "__init__.py").write_text("")
    (folder / pkg / "main.py").write_text(textwrap.dedent("""\
        import time
        from kosha_sdk import AddinError, Health

        class Addin:
            def __init__(self, ctx):
                self.ctx = ctx
            def health(self):
                return Health.up("token set" if self.ctx.settings.get("token") else "no token")
            def crash(self):
                raise RuntimeError("boom")
            def refuse(self):
                raise AddinError("The other system said no.")
            def slow(self):
                time.sleep(3)
        """))


def test_official_erpnext_addin_is_found_and_enabled(registry):
    a = registry.get("org.kosha.erpnext")
    assert a is not None and a.status == "enabled" and a.source == "bundled"
    assert a.to_dict()["official"]


def test_third_party_addin_waits_for_approval(registry):
    a = registry.get("com.example.flaky")
    assert a.status == "awaiting_approval"
    with pytest.raises(AddinError, match="switched off"):
        registry.call(a.id, "health")
    registry.enable(a.id, user_id=1)
    assert registry.call(a.id, "health").message == "no token"


def test_incompatible_and_invalid_addins_are_reported_not_loaded(registry):
    assert registry.get("com.example.future").status == "incompatible"
    invalid = [a for a in registry.all() if a.status == "invalid"]
    assert invalid and "Manifest problem" in invalid[0].message
    with pytest.raises(AddinError):
        registry.enable("com.example.future", user_id=1)


def test_settings_are_typed_and_secrets_masked(registry):
    registry.enable("com.example.flaky", 1)
    shown = registry.save_settings("com.example.flaky", {"token": "s3cret", "retries": "5", "unknown": "x"}, 1)
    assert shown["token"] == addins.SECRET_MASK and shown["retries"] == 5 and "unknown" not in shown
    registry.save_settings("com.example.flaky", {"token": addins.SECRET_MASK}, 1)   # mask never overwrites
    assert registry.settings("com.example.flaky")["token"] == "s3cret"
    assert registry.call("com.example.flaky", "health").message == "token set"


def test_expected_errors_pass_through_without_counting(registry):
    registry.enable("com.example.flaky", 1)
    for _ in range(5):
        with pytest.raises(AddinError, match="said no"):
            registry.call("com.example.flaky", "refuse")
    assert registry.get("com.example.flaky").status == "enabled"


def test_repeated_crashes_switch_the_addin_off(registry):
    registry.enable("com.example.flaky", 1)
    for _ in range(3):
        with pytest.raises(AddinError, match="failed unexpectedly"):
            registry.call("com.example.flaky", "crash")
    a = registry.get("com.example.flaky")
    assert a.status == "disabled" and "automatically" in a.message
    registry.enable(a.id, 1)                       # an admin can switch it back on
    assert registry.call(a.id, "health").ok


def test_slow_addin_times_out_and_kosha_carries_on(registry):
    registry.enable("com.example.flaky", 1)
    t0 = time.time()
    with pytest.raises(AddinError, match="did not answer"):
        registry.call("com.example.flaky", "slow")
    assert time.time() - t0 < 2.5


def test_missing_method_is_a_plain_message(registry):
    registry.enable("com.example.flaky", 1)
    with pytest.raises(AddinError, match="does not offer"):
        registry.call("com.example.flaky", "upsert_item")
