# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""`kosha-sdk`: start and check add-ins.

    kosha-sdk new erp-connector my-erp --publisher com.example
    kosha-sdk new notifier my-chat --publisher com.example
    kosha-sdk check addins/my-erp
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__
from .manifest import load_manifest
from .permissions import describe_permission
from .testing import check_manifest

TEMPLATES = {
    "erp-connector": {
        "kosha-addin.yaml": """\
id: {publisher}.{slug}
name: {title}
version: 0.1.0
publisher: {publisher}
licence: Apache-2.0
kosha: ">=0.5,<2.0"
kind: [erp-connector]
description: Sends released items and BOMs to {title} and reads stock and cost back.
entry_points:
  server: {pkg}.connector:Connector
permissions: [items:read, boms:read, erp:sync]
settings:
  base_url: {{type: url, required: true, label: Address, hint: "e.g. https://erp.example.com"}}
  api_key:  {{type: secret, required: true, label: API key}}
""",
        "{pkg}/__init__.py": "# SPDX-License-Identifier: Apache-2.0\n",
        "{pkg}/connector.py": """\
# SPDX-License-Identifier: Apache-2.0
\"\"\"{title} connector for Kosha. Every method is idempotent.\"\"\"
from kosha_sdk import Health
from kosha_sdk.erp import ErpBom, ErpConnector, ErpError, ErpItem, ErpResult, StockInfo


class Connector(ErpConnector):
    def __init__(self, ctx):
        super().__init__(ctx)
        s = ctx.settings
        self.http = ctx.http(s.get("base_url", ""), headers={{"Authorization": f"Bearer {{s.get('api_key', '')}}"}})

    def health(self) -> Health:
        r = self.http.get("/ping", allow_404=True)
        return Health.up("Connected") if r and r.ok else Health.down("No answer from {title}")

    def upsert_item(self, item: ErpItem) -> ErpResult:
        existing = self.http.get(f"/items/{{item.code}}", allow_404=True)
        payload = {{"code": item.code, "name": item.description, "unit": item.uom}}
        if existing is None:
            self.http.post("/items", payload, idempotency_key=item.payload_hash)
            return ErpResult.created(f"created {{item.code}}", payload, item.code)
        if existing.json() == payload:
            return ErpResult.unchanged(f"unchanged {{item.code}}", item.code)
        self.http.put(f"/items/{{item.code}}", payload)
        return ErpResult.updated(f"updated {{item.code}}", payload, item.code)

    def upsert_bom(self, bom: ErpBom) -> ErpResult:
        raise ErpError("BOM transfer is not written yet")

    def stock_and_cost(self, code: str) -> StockInfo:
        r = self.http.get(f"/items/{{code}}/stock", allow_404=True)
        if r is None:
            return StockInfo(exists=False)
        j = r.json()
        return StockInfo(exists=True, stock=float(j.get("qty", 0)), cost=j.get("cost"))
""",
        "tests/test_manifest.py": """\
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from kosha_sdk.testing import check_manifest


def test_manifest_is_valid():
    assert check_manifest(Path(__file__).parent.parent) == []
""",
        "pyproject.toml": """\
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "{slug}"
version = "0.1.0"
license = {{ text = "Apache-2.0" }}
dependencies = ["kosha-sdk>=0.5"]

[project.entry-points."kosha.addins"]
{slug} = "{pkg}"
""",
        "README.md": "# {title}\n\nA Kosha add-in. Run `kosha-sdk check .` and `pytest` before publishing.\n",
    },
    "notifier": {
        "kosha-addin.yaml": """\
id: {publisher}.{slug}
name: {title}
version: 0.1.0
publisher: {publisher}
licence: Apache-2.0
kosha: ">=0.5,<2.0"
kind: [notifier]
description: Posts Kosha notices (approvals, ERP failures, releases) to {title}.
entry_points:
  server: {pkg}.notifier:Notifier
permissions: [notify:send]
settings:
  webhook_url: {{type: secret, required: true, label: Webhook address, hint: "Treat it like a password"}}
""",
        "{pkg}/__init__.py": "# SPDX-License-Identifier: Apache-2.0\n",
        "{pkg}/notifier.py": """\
# SPDX-License-Identifier: Apache-2.0
\"\"\"{title} notifier for Kosha.\"\"\"
import urllib.parse

from kosha_sdk import Health
from kosha_sdk import notify
from kosha_sdk.http import HttpError


class Notifier(notify.Notifier):
    def __init__(self, ctx):
        super().__init__(ctx)
        url = urllib.parse.urlsplit(ctx.settings.get("webhook_url") or "")
        self.path = url.path + (f"?{{url.query}}" if url.query else "")
        self.http = ctx.http(f"{{url.scheme}}://{{url.netloc}}") if url.netloc else None

    def health(self) -> Health:
        return Health.up("Webhook address is set") if self.http else Health.down("Set the webhook address first")

    def send(self, notice: notify.Notice) -> None:
        if self.http is None:
            raise notify.NotifyError("The webhook address is not set.")
        try:
            self.http.post(self.path, {{"text": notice.text()}}, idempotency_key=notice.key or None)
        except HttpError as e:
            raise notify.NotifyError(f"{title} refused the message ({{e}}).") from None
""",
        "tests/test_manifest.py": """\
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from kosha_sdk.testing import check_manifest


def test_manifest_is_valid():
    assert check_manifest(Path(__file__).parent.parent) == []
""",
        "pyproject.toml": """\
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "{slug}"
version = "0.1.0"
license = {{ text = "Apache-2.0" }}
dependencies = ["kosha-sdk>=0.5"]

[project.entry-points."kosha.addins"]
{slug} = "{pkg}"
""",
        "README.md": "# {title}\n\nA Kosha notifier add-in. Run `kosha-sdk check .` and `pytest` before publishing.\n",
    },
}


def _new(kind: str, name: str, publisher: str, dest: Path) -> int:
    if kind not in TEMPLATES:
        print(f"No template for {kind!r} yet. Available: {', '.join(TEMPLATES)}", file=sys.stderr)
        return 2
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    pkg = slug.replace("-", "_")
    title = name.replace("-", " ").title()
    root = dest / slug
    if root.exists():
        print(f"{root} already exists; nothing was changed.", file=sys.stderr)
        return 1
    for rel, text in TEMPLATES[kind].items():
        p = root / rel.format(pkg=pkg)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.format(publisher=publisher, slug=slug, pkg=pkg, title=title), encoding="utf-8")
    print(f"Created {root}. Next: kosha-sdk check {root}")
    return 0


def _check(path: Path) -> int:
    problems = check_manifest(path)
    if problems:
        for p in problems:
            print(f"  problem: {p}")
        return 1
    m = load_manifest(path)
    print(f"{m.name} {m.version} ({m.id}) for Kosha {m.kosha}")
    print("  It asks to: " + ("; ".join(describe_permission(p) for p in m.permissions) or "nothing"))
    print("  Manifest OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kosha-sdk", description=f"Kosha add-in SDK {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="start a new add-in from a template")
    n.add_argument("kind", choices=sorted(TEMPLATES))
    n.add_argument("name")
    n.add_argument("--publisher", default="com.example", help="reverse domain name of the publisher")
    n.add_argument("--dest", default=".", type=Path)
    c = sub.add_parser("check", help="check an add-in's manifest")
    c.add_argument("path", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "new":
        return _new(a.kind, a.name, a.publisher, a.dest)
    return _check(a.path)


if __name__ == "__main__":
    sys.exit(main())
