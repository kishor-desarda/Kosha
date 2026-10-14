# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""ERP connector contract.

Kosha keeps the queue, the retries, the dry run and the unit mapping. A connector only
translates between Kosha's data and one ERP. Every method must be idempotent: Kosha may call
it again after a crash or a retry, and the second call must not create a second record.

Reading back (optional): `fetch_items`, `fetch_bom` and `list_items_from_kosha`
let Kosha compare its released items and BOMs with what the ERP holds (reconciliation). They
only read; a connector that does not offer them raises `ErpNotSupported` and Kosha says so.

Attachments (optional): `attach_file` puts the released, stamped PDF of a drawing on the ERP item
and takes off the PDFs of earlier revisions that Kosha attached.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from .context import AddinContext, Health
from .errors import AddinError


class ErpError(AddinError):
    """The ERP refused a request or could not be reached. The message comes from the ERP when possible."""


class ErpNotSupported(ErpError):
    """The connector does not offer this (optional) part of the contract. Kosha shows the message and carries on."""


@dataclass(frozen=True)
class ErpItem:
    code: str
    description: str
    revision: str = ""
    uom: str = ""                 # already mapped to the ERP's unit by Kosha
    material: str = ""
    weight_kg: float | None = None
    item_group: str = ""
    make_or_buy: str = ""
    hsn: str = ""                 # HSN code for GST (India), from the item or its category
    status: str = ""              # "" (in use), "spares_only" (not for new machines) or "obsolete" (disable it)
    oem_refs: tuple[str, ...] = ()  # "ABC 12-345 (model X)": other makers' numbers this part replaces
    customer: str = ""            # set when the part is restricted to one customer

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class ErpBomLine:
    code: str
    qty: float
    uom: str = ""
    op_no: int | None = None      # manufacturing BOMs: the operation that uses the part


@dataclass(frozen=True)
class ErpOperation:
    """One step of a manufacturing BOM's process plan (routing)."""
    op_no: int
    work_centre: str
    description: str
    setup_min: float = 0.0
    run_min: float = 0.0


@dataclass(frozen=True)
class ErpBom:
    """A BOM for the ERP. Engineering BOMs have no operations; a manufacturing BOM (when the ERP settings say the ERP
    builds from manufacturing BOMs) has its process plan in `operations`. A connector that cannot store operations
    sends the lines and leaves them out."""
    item: ErpItem
    revision: str
    lines: tuple[ErpBomLine, ...]
    operations: tuple[ErpOperation, ...] = ()


@dataclass(frozen=True)
class ErpResult:
    """What happened: `created`, `updated`, `unchanged` or `preview`, with the data sent."""
    action: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    external_id: str = ""

    @classmethod
    def created(cls, message: str, payload: dict | None = None, external_id: str = "") -> "ErpResult":
        return cls("created", message, payload or {}, external_id)

    @classmethod
    def updated(cls, message: str, payload: dict | None = None, external_id: str = "") -> "ErpResult":
        return cls("updated", message, payload or {}, external_id)

    @classmethod
    def unchanged(cls, message: str, external_id: str = "") -> "ErpResult":
        return cls("unchanged", message, {}, external_id)


@dataclass(frozen=True)
class ErpEvent:
    """Something that changed in the ERP and matters to Kosha:

    * `item_changed`, `stock_changed` (code: the item code), `bom_cancelled` (code: the item; detail: the BOM);
    * `sales_order` (code: the order number; detail: "customer|delivery date");
    * `purchase_order`, `rfq`, `subcontracting_order` (code: the order number; detail: "supplier|code,code,...");
    * `customer`, `supplier` (code: the name as the ERP shows it; detail: "disabled" when it was switched off);
    * `price_list` (code: the price list's name)."""
    kind: str
    code: str
    detail: str = ""


@dataclass(frozen=True)
class StockInfo:
    exists: bool
    stock: float = 0.0
    cost: float | None = None
    warehouses: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ErpItemRecord:
    """An item as the ERP holds it, read back for reconciliation.

    A field set to None means the ERP does not keep it (or the connector is not set up to read it,
    for example when no custom field for the revision is configured), so Kosha does not compare it.
    """
    code: str
    description: str | None = None
    uom: str | None = None
    revision: str | None = None
    material: str | None = None
    active: bool | None = None            # False when the item is disabled or archived in the ERP
    from_kosha: bool | None = None        # the ERP marks the item as created by Kosha


@dataclass(frozen=True)
class ErpBomRecord:
    """The BOM the ERP uses by default for an item, read back for reconciliation."""
    code: str                             # the parent item
    lines: tuple[ErpBomLine, ...]
    revision: str | None = None           # None when the ERP does not keep Kosha's revision on the BOM
    reference: str = ""                   # the ERP's own name for the BOM, shown to users


class ErpConnector(ABC):
    """Base class for ERP connectors. Subclasses get their settings and HTTP from the context."""

    def __init__(self, ctx: AddinContext):
        self.ctx = ctx

    @abstractmethod
    def health(self) -> Health:
        """Check the connection and credentials; say who Kosha is connected as."""

    @abstractmethod
    def upsert_item(self, item: ErpItem) -> ErpResult:
        """Create the item, or update it when it exists. Never create a duplicate."""

    @abstractmethod
    def upsert_bom(self, bom: ErpBom) -> ErpResult:
        """Make the ERP's default BOM match `bom`. Return `unchanged` when it already does.
        Kosha has already sent every component item."""

    @abstractmethod
    def stock_and_cost(self, code: str) -> StockInfo:
        """Read stock on hand and cost for one item."""

    def handle_webhook(self, headers: dict[str, str], body: bytes) -> list[ErpEvent]:
        """Check and read a call the ERP made to Kosha. Must verify its signature; raise ErpError if it is not genuine."""
        raise ErpError("This connector does not accept calls from the ERP.")

    # ------------------------------------------------- optional: reading back (reconciliation)
    def fetch_items(self, codes: Sequence[str]) -> dict[str, ErpItemRecord]:
        """Read the items with these codes. Return {code: record} with the codes as the ERP stores them;
        leave out codes the ERP does not have. Must only read. Kosha asks for at most 100 codes at a time."""
        raise ErpNotSupported("This connector cannot read items back from the ERP.")

    def fetch_bom(self, code: str) -> ErpBomRecord | None:
        """Read the ERP's default, active BOM for an item, or None when it has none. Must only read."""
        raise ErpNotSupported("This connector cannot read BOMs back from the ERP.")

    def list_items_from_kosha(self) -> list[str]:
        """Codes of every item the ERP marks as created by Kosha, so Kosha can find items it no longer has.
        Must only read."""
        raise ErpNotSupported("This connector cannot list the items Kosha created in the ERP.")

    # ------------------------------------------------- optional: attachments
    def attach_file(self, code: str, revision: str, filename: str, content: bytes) -> ErpResult:
        """Attach `content` (the released PDF of `revision`) to the item `code`, and remove the files of earlier
        revisions that Kosha attached before. Return `unchanged` when the same file is already there."""
        raise ErpNotSupported("This connector cannot attach files to ERP items.")
