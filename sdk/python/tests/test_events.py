# SPDX-License-Identifier: Apache-2.0
"""Event names add-ins can listen to."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kosha_sdk import events  # noqa: E402


def test_deviation_approved_is_a_known_event():
    assert "deviation.approved" in events.EVENTS
    assert "deviation" in events.EVENTS["deviation.approved"]

    @events.on_event("deviation.approved")
    def tell_purchasing(ctx, event):   # pragma: no cover - only registered here
        return None

    assert ("deviation.approved", tell_purchasing) in events.registered()


def test_unknown_events_are_refused_with_the_known_names():
    with pytest.raises(ValueError, match="deviation.approved"):
        events.on_event("deviation.aproved")
