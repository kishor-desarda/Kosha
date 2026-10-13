# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release readiness check (scripts/release_check.py) that the release workflow runs before it builds."""
import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts" / "release_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def test_this_repository_is_ready():
    assert rc.problems() == [], rc.problems()
    assert rc.main([]) == 0
    assert rc.main(["--tag", "v" + rc.server_version()]) == 0


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "server" / "kosha").mkdir(parents=True)
    (tmp_path / "server" / "kosha" / "__init__.py").write_text('__version__ = "1.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0]\n\n- Things.\n\n## [1.1.0]\n")
    (tmp_path / "addins" / "erp").mkdir(parents=True)
    (tmp_path / "addins" / "erp" / "kosha-addin.yaml").write_text('id: x\nkosha: ">=1.0,<2.0"\n')
    (tmp_path / "docs").mkdir()
    for g in rc.GUIDES:
        (tmp_path / g).write_text("# Guide\n")
    return tmp_path


def test_a_ready_repository(repo):
    assert rc.problems(repo) == []
    assert rc.problems(repo, tag="v1.2.0") == []
    assert rc.changelog_versions(repo) == ["1.2.0", "1.1.0"]


def test_every_problem_is_listed(repo):
    (repo / "server" / "kosha" / "__init__.py").write_text('__version__ = "2.0.0.dev0"\n')
    (repo / "addins" / "erp" / "kosha-addin.yaml").write_text('id: x\nkosha: ">=1.0,<2.0"\n')
    (repo / "addins" / "old").mkdir()
    (repo / "addins" / "old" / "kosha-addin.yaml").write_text("id: y\n")
    (repo / "docs" / "admin-guide.md").unlink()
    found = rc.problems(repo, tag="v2.0.0")
    assert any("development version" in p for p in found)
    assert any("does not match" in p and "v2.0.0.dev0" in p for p in found)
    assert any("CHANGELOG.md has no section '## [2.0.0.dev0]'" in p for p in found)
    assert any("erp add-in says it works with Kosha >=1.0,<2.0" in p for p in found)
    assert any("addins/old" in p and "no 'kosha'" in p for p in found)
    assert any("admin-guide.md is missing" in p for p in found)


def test_bad_version_and_bad_range(repo):
    (repo / "server" / "kosha" / "__init__.py").write_text('__version__ = "one"\n')
    assert any("not a release number" in p for p in rc.problems(repo))
    (repo / "server" / "kosha" / "__init__.py").write_text('__version__ = "1.2.0rc1"\n')
    (repo / "CHANGELOG.md").write_text("## 1.2.0rc1\n")
    (repo / "addins" / "erp" / "kosha-addin.yaml").write_text('kosha: "about 1"\n')
    found = rc.problems(repo)
    assert len(found) == 1 and "cannot read version rule" in found[0]
    shutil.rmtree(repo / "addins")
    assert rc.problems(repo) == []
