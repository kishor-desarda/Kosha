# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""The file add-in contracts: file-type handlers, storage and importers."""
import pytest

from kosha_sdk import AddinContext, describe_permission
from kosha_sdk.files import FilePreview, FileTypeHandler, Importer, StorageProvider
from kosha_sdk.manifest import KINDS


def test_base_classes_need_their_methods():
    ctx = AddinContext("com.example.files")
    for cls in (FileTypeHandler, StorageProvider, Importer):
        with pytest.raises(TypeError):
            cls(ctx)

    class Handler(FileTypeHandler):
        extensions = ("abc",)

        def preview(self, filename, data):
            return FilePreview(summary={"type": "text", "lines": [data.decode()]})

    h = Handler(ctx)
    assert h.health().ok and "abc" in h.health().message
    assert h.preview("a.abc", b"hi").summary["lines"] == ["hi"] and h.preview("a.abc", b"").meta == {}


def test_kinds_and_permissions_are_known():
    assert {"file-type", "storage", "importer"} <= KINDS
    for p in ("files:preview", "storage:copy", "import:provide"):
        assert "not a standard permission" not in describe_permission(p)
