# SPDX-License-Identifier: AGPL-3.0-or-later
"""pytest runs the unit tests here. The two end-to-end suites start real servers on fixed
ports, so they run as scripts instead:  python tests/test_api.py  and  python tests/test_import.py
"""
import sys
from pathlib import Path

collect_ignore = ["test_api.py", "test_import.py"]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


import pytest  # noqa: E402

from harness import KoshaServer  # noqa: E402


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
