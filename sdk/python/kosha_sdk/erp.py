# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""ERP connector contract.

Kosha keeps the queue, the retries, the dry run and the unit mapping. A connector only
translates between Kosha's data and one ERP. Every method must be idempotent: Kosha may call
it again after a crash or a retry, and the second call must not create a second record.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from .context import AddinContext, Health
from .errors import AddinError


class ErpError(AddinError):
    """The ERP refused a request or could not be reached. The message comes from the ERP when possible."""


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

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class ErpBomLine:
    code: str
    qty: float
    uom: str = ""


@dataclass(frozen=True)
class ErpBom:
    item: ErpItem
    revision: str
    lines: tuple[ErpBomLine, ...]


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
    """Something that changed in the ERP and matters to Kosha: `item_changed`, `stock_changed`, `bom_cancelled`."""
    kind: str
    code: str
    detail: str = ""


@dataclass(frozen=True)
class StockInfo:
    exists: bool
    stock: float = 0.0
    cost: float | None = None
    warehouses: tuple[dict[str, Any], ...] = ()


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
