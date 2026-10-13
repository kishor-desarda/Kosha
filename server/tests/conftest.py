# SPDX-License-Identifier: AGPL-3.0-or-later
"""pytest runs the unit tests here. The two older end-to-end suites start their servers themselves, so they run
as scripts instead:  python tests/test_api.py  and  python tests/test_import.py
"""
import os
import sys
from pathlib import Path

collect_ignore = ["test_api.py", "test_import.py"]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


import pytest  # noqa: E402

from harness import KoshaServer  # noqa: E402
from packs_fixtures import kdb, packs_copy  # noqa: E402,F401  (database and packs-folder fixtures)


@pytest.fixture(scope="module")
def kosha():
    """A fresh, empty Kosha server for this test module."""
    s = KoshaServer().start()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def demo_kosha():
    """A Kosha server with the Demo Engineering data set."""
    s = KoshaServer(demo=True).start()
    yield s
    s.stop()


def pytest_runtest_logreport(report):
    """In GitHub Actions, every failing test also becomes an annotation on the run (readable without the log)."""
    if not os.environ.get("GITHUB_ACTIONS") or not report.failed:
        return
    path, line = report.location[0], (report.location[1] or 0) + 1
    text = str(report.longrepr)
    tail = "\n".join(text.splitlines()[-25:])
    msg = tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:4000]
    sys.__stdout__.write(f"::error file=server/{path},line={line},title={report.nodeid} ({report.when})::{msg}\n")
    sys.__stdout__.flush()
