# SPDX-License-Identifier: AGPL-3.0-or-later
"""pytest runs the unit tests here. The two end-to-end suites start real servers on fixed
ports, so they run as scripts instead:  python tests/test_api.py  and  python tests/test_import.py
"""
import sys
from pathlib import Path

collect_ignore = ["test_api.py", "test_import.py"]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
