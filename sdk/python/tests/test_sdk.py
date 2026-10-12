# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kosha_sdk import ManifestError  # noqa: E402
from kosha_sdk.cli import main  # noqa: E402
from kosha_sdk.manifest import parse_manifest  # noqa: E402
from kosha_sdk.testing import check_manifest  # noqa: E402
from kosha_sdk.versions import satisfies  # noqa: E402

GOOD = {
    "id": "com.example.my-erp", "name": "My ERP", "version": "1.0.0", "publisher": "Example",
    "licence": "Apache-2.0", "kosha": ">=0.5,<2.0", "kind": ["erp-connector"],
    "entry_points": {"server": "my_erp.connector:Connector"}, "permissions": ["items:read", "erp:sync"],
    "settings": {"base_url": {"type": "url", "required": True}, "api_key": {"type": "secret"}},
}


def test_good_manifest_parses():
    m = parse_manifest(GOOD)
    assert m.id == "com.example.my-erp" and m.runs_on_server and m.setting("api_key").type == "secret"


@pytest.mark.parametrize("key,value,words", [
    ("id", "MyErp", "reverse domain"),
    ("version", "1.0", "semantic version"),
    ("kosha", "latest", "kosha"),
    ("kind", ["teleporter"], "unknown kind"),
    ("permissions", ["root"], "unknown permission"),
    ("entry_points", {"server": "not a target"}, "package.module:ClassName"),
])
def test_bad_manifest_names_the_problem(key, value, words):
    with pytest.raises(ManifestError, match=words):
        parse_manifest(GOOD | {key: value})


def test_secret_cannot_have_default():
    bad = GOOD | {"settings": {"api_key": {"type": "secret", "default": "abc"}}}
    with pytest.raises(ManifestError, match="secret"):
        parse_manifest(bad)


def test_version_ranges():
    assert satisfies("0.5.0.dev0", ">=0.5,<2.0")
    assert not satisfies("2.0.0", ">=0.5,<2.0")
    assert satisfies("1.4", ">=1.2")


def test_setting_coercion():
    m = parse_manifest(GOOD | {"settings": {"on": {"type": "bool"}, "n": {"type": "int"}, "u": {"type": "url"}}})
    assert m.setting("on").coerce("yes") is True
    assert m.setting("n").coerce("7") == 7
    with pytest.raises(ManifestError):
        m.setting("u").coerce("ftp://x")


def test_template_creates_a_valid_addin(tmp_path):
    assert main(["new", "erp-connector", "acme-erp", "--publisher", "com.example", "--dest", str(tmp_path)]) == 0
    assert check_manifest(tmp_path / "acme-erp") == []
    assert main(["new", "erp-connector", "acme-erp", "--dest", str(tmp_path)]) == 1   # never overwrites
