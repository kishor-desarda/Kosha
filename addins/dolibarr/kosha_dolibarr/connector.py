# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Dolibarr ERP/CRM connector, through Dolibarr's REST API (`<address>/api/index.php`, header `DOLAPIKEY`).

    Item  -> /products (ref = part number): create, or update only what changed
    BOM   -> /boms + /boms/{id}/lines (BOM module): a new BOM, validated and made the product's default BOM,
             when the lines differ from the BOM Dolibarr uses now
    Stock <- /products/{id}/stock per warehouse; cost <- the product's weighted average price or cost price
    Reconciliation <- /products and /boms, read only

Every method is idempotent, as the ErpConnector contract requires: Kosha looks before it creates, and a call
that creates something is never repeated automatically. A BOM is built in three steps (create a draft, add
the lines, validate); if Kosha stops half-way, the next send finishes the draft it started instead of adding
a second one.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter

from kosha_sdk import AddinContext, Health
from kosha_sdk.erp import (ErpBom, ErpBomLine, ErpBomRecord, ErpConnector, ErpError, ErpItem, ErpItemRecord,
                           ErpNotSupported, ErpResult, StockInfo)
from kosha_sdk.http import HttpError

API_PATH = "/api/index.php"
BOM_DRAFT, BOM_VALIDATED, BOM_CANCELLED = 0, 1, 9
COST_CHOICES = {"Weighted average price (WAP)": "pmp", "Cost price": "cost_price"}
_REV_LABEL = re.compile(r"^Rev\s+(.+)$", re.IGNORECASE)
_SAFE_REF = re.compile(r"^[^'()\\]+$")          # refs that can go into a Dolibarr list filter as they are
FILTER_CHUNK = 20


def dolibarr_message(raw: str) -> str:
    """The most useful human message inside a Dolibarr (Restler) error answer."""
    try:
        j = json.loads(raw)
    except (ValueError, TypeError):
        text = re.sub(r"<[^>]+>", " ", raw or "")
        return " ".join(text.split())[:300]
    err = j.get("error") if isinstance(j, dict) else None
    if isinstance(err, dict) and err.get("message"):
        return " ".join(str(err["message"]).split())[:500]
    if isinstance(j, dict) and j.get("message"):
        return " ".join(str(j["message"]).split())[:500]
    return str(raw)[:300]


def _num(value, default: float = 0.0) -> float:
    """Dolibarr sends most numbers as text ("2.00000000") or null."""
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, "", "0", 0) else None
    except (TypeError, ValueError):
        return None


def _text(value) -> str:
    return "" if value is None else str(value)


def _field(name: str) -> str:
    """The key of a Dolibarr extra field (complementary attribute) inside `array_options`."""
    name = (name or "").strip()
    if not name:
        return ""
    return name if name.startswith("options_") else f"options_{name}"


class DolibarrConnector(ErpConnector):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self.cfg = s
        base = (s.get("base_url") or "").strip().rstrip("/")
        if base.lower().endswith(API_PATH):
            base = base[: -len(API_PATH)]
        self._base = base
        self._key = s.get("api_key") or ""
        timeout = float(s.get("timeout_s") or 30)
        verify = bool(s.get("verify_tls", True))
        headers = {"DOLAPIKEY": self._key} if self._key else {}
        api = base + API_PATH
        # reads may be retried after a network hiccup; calls that create records are sent once only
        self._http = ctx.http(api, headers=headers, timeout_s=timeout, verify_tls=verify, retries=2) if base else None
        self._http_once = ctx.http(api, headers=headers, timeout_s=timeout, verify_tls=verify, retries=0) if base else None
        self._units: dict[str, int | None] | None = None
        self._unit_codes: dict[int, str] = {}
        self._warehouses: dict[str, str] = {}
        self._refs: dict[int, str] = {}

    # ------------------------------------------------------------ plumbing
    def _call(self, method: str, path: str, body=None, params=None, allow_404: bool = False, once: bool = False):
        http = self._http_once if once else self._http
        if http is None:
            raise ErpError("The Dolibarr address is not set (ERP settings).")
        if not self._key:
            raise ErpError("Fill in the Dolibarr API key (ERP settings).")
        try:
            r = http.request(method, path, json_body=body, params=params, allow_404=allow_404)
        except HttpError as e:
            if e.status is None:
                raise ErpError(f"Cannot reach Dolibarr at {self._base}. {e}") from None
            msg = dolibarr_message(e.detail)
            if e.status == 401:
                raise ErpError(f"Dolibarr refused the API key ({msg}). Check the key on the integration user's card in "
                               "Dolibarr, and that the API REST module is switched on.", status=401,
                               detail=e.detail[:4000]) from None
            if e.status == 403:
                raise ErpError(f"Dolibarr refused the request because the integration user lacks a permission ({msg}).",
                               status=403, detail=e.detail[:4000]) from None
            raise ErpError(f"Dolibarr refused the request: {msg}" if msg else f"Dolibarr answered HTTP {e.status}.",
                           status=e.status, detail=e.detail[:4000]) from None
        if r is None:
            return None
        try:
            return r.json()
        except ValueError:
            raise ErpError("Dolibarr's answer was not JSON. Check the address: it should be the page where you sign "
                           "in to Dolibarr.", detail=r.text[:4000]) from None

    def _list(self, path: str, params: dict) -> list[dict]:
        """A Dolibarr list. Older Dolibarr versions answer 404 instead of an empty list."""
        rows = self._call("GET", path, params=params, allow_404=True)
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def _custom(self) -> tuple[str, str]:
        return _field(self.cfg.get("revision_field") or ""), _field(self.cfg.get("material_field") or "")

    def _use_units(self) -> bool:
        return bool(self.cfg.get("use_units", False))

    # ------------------------------------------------------------ products
    def _products(self, codes) -> dict[str, dict]:
        """{CODE: product} for the codes Dolibarr has (whatever their sale or purchase status)."""
        wanted = [c for c in dict.fromkeys(str(c) for c in codes) if c]
        out: dict[str, dict] = {}
        plain = [c for c in wanted if _SAFE_REF.match(c)]
        for start in range(0, len(plain), FILTER_CHUNK):
            chunk = plain[start:start + FILTER_CHUNK]
            flt = " or ".join(f"(t.ref:=:'{c}')" for c in chunk)
            for p in self._list("/products", {"sqlfilters": flt, "limit": len(chunk) * 2, "sortfield": "t.rowid",
                                              "sortorder": "ASC"}):
                out.setdefault(_text(p.get("ref")).upper(), p)
        for c in wanted:
            if c in plain:
                continue
            p = self._call("GET", f"/products/ref/{urllib.parse.quote(c, safe='')}", allow_404=True)
            if isinstance(p, dict) and p.get("id"):
                out.setdefault(_text(p.get("ref")).upper(), p)
        return out

    def _product(self, code: str) -> dict | None:
        return self._products([code]).get(code.upper())

    def _ref_of(self, product_id: int) -> str:
        if product_id not in self._refs:
            p = self._call("GET", f"/products/{product_id}", allow_404=True)
            self._refs[product_id] = _text(p.get("ref")) if isinstance(p, dict) else ""
        return self._refs[product_id]

    def _load_units(self) -> None:
        if self._units is not None:
            return
        rows = self._list("/setup/dictionary/units", {"limit": 1000, "sortfield": "code", "sortorder": "ASC"})
        self._units = {}
        for u in rows:
            uid = _int(u.get("id") or u.get("rowid"))
            if not uid or str(u.get("active", "1")) == "0":
                continue
            self._unit_codes[uid] = _text(u.get("code"))
            for key in ("code", "short_label", "label"):
                name = _text(u.get(key)).strip().lower()
                if name:
                    self._units.setdefault(name, uid)

    def _unit_id(self, name: str) -> int | None:
        name = (name or "").strip().lower()
        if not name:
            return None
        self._load_units()
        return self._units.get(name)

    def _unit_code(self, unit_id) -> str:
        uid = _int(unit_id)
        if not uid:
            return ""
        self._load_units()
        return self._unit_codes.get(uid, str(uid))

    def _require_unit(self, name: str) -> int | None:
        if not self._use_units() or not name:
            return None
        uid = self._unit_id(name)
        if uid is None:
            raise ErpError(f"Dolibarr has no unit called {name!r}. Map Kosha's unit to the code of a Dolibarr unit "
                           "(Setup › Dictionaries › Units, e.g. P for piece) under ERP sync › Connection settings › "
                           "Unit mapping, or switch off 'Send units'.")
        return uid

    # ------------------------------------------------------------ BOMs
    def _boms_of(self, product_id: int) -> list[dict]:
        """Every manufacturing BOM of a product, newest first, cancelled ones included."""
        rows = self._list("/boms", {"sqlfilters": f"(t.fk_product:=:{int(product_id)})", "limit": 200,
                                    "sortfield": "t.rowid", "sortorder": "DESC"})
        rows = [b for b in rows if _int(b.get("fk_product")) == int(product_id) and _int(b.get("bomtype")) in (None, 0)]
        return sorted(rows, key=lambda b: -int(b["id"]))

    @staticmethod
    def _status(bom: dict) -> int:
        return int(_num(bom.get("status", bom.get("statut")), 0))

    def _current_bom(self, product: dict, boms: list[dict]) -> dict | None:
        """The BOM Dolibarr uses now: the product's default BOM, else the newest validated one, else the newest draft."""
        live = [b for b in boms if self._status(b) != BOM_CANCELLED]
        default = _int(product.get("fk_default_bom"))
        for b in live:
            if default and int(b["id"]) == default:
                return b
        validated = [b for b in live if self._status(b) == BOM_VALIDATED]
        if validated:
            return validated[0]
        return live[0] if live else None

    def _bom_lines(self, bom_id: int) -> list[dict]:
        rows = self._call("GET", f"/boms/{int(bom_id)}/lines", allow_404=True)
        if not isinstance(rows, list):          # Dolibarr versions without the lines API: lines come with the BOM
            bom = self._call("GET", f"/boms/{int(bom_id)}", allow_404=True)
            rows = (bom.get("lines") or []) if isinstance(bom, dict) else []
        out = []
        for ln in rows:
            if not isinstance(ln, dict):
                continue
            pid = _int(ln.get("fk_product"))
            out.append({"id": ln.get("id") or ln.get("rowid"), "code": self._ref_of(pid) if pid else "",
                        "qty": _num(ln.get("qty")), "unit": self._unit_code(ln.get("fk_unit")) if self._use_units() else ""})
        return out

    @staticmethod
    def _pairs(lines) -> list[tuple[str, float]]:
        return sorted((str(code).upper(), round(float(qty), 6)) for code, qty in lines)

    def _bom_ref(self, bom: ErpBom, boms: list[dict]) -> str:
        rev = re.sub(r"[^0-9A-Za-z._-]+", "", bom.revision or "") or "KOSHA"
        ref = f"{bom.item.code}-{rev}"[:120]
        taken = {_text(b.get("ref")).upper() for b in boms}
        n = 1
        candidate = ref
        while candidate.upper() in taken:
            n += 1
            candidate = f"{ref}.{n}"
        return candidate

    def _is_ours(self, ref: str, bom: ErpBom) -> bool:
        """The ref is one Kosha gives the BOM of this revision (code-rev, or code-rev.2 when that was taken)."""
        mine = self._bom_ref(bom, []).upper()
        return ref.upper() == mine or ref.upper().startswith(mine + ".")

    def _finish_bom(self, bom_id: int, product: dict) -> list[str]:
        """Validate the BOM and make it the product's default, as the settings ask. Returns what was done."""
        done = []
        if self.cfg.get("validate_boms", True):
            self._call("PUT", f"/boms/{bom_id}", {"status": BOM_VALIDATED})
            done.append("validated")
        if self.cfg.get("set_default_bom", True) and _int(product.get("fk_default_bom")) != bom_id:
            self._call("PUT", f"/products/{int(product['id'])}", {"fk_default_bom": bom_id})
            done.append("made the default BOM")
        return done

    # ------------------------------------------------------------ contract
    def health(self) -> Health:
        try:
            user = self._call("GET", "/users/info") or {}
        except ErpError as e:
            if e.status == 404:
                return Health.down(f"Dolibarr has no REST API at {self._base}{API_PATH}. Check the address, and switch "
                                   "on the API REST module in Dolibarr (Home › Setup › Modules).")
            return Health.down(str(e))
        version = ""
        try:
            status = self._call("GET", "/status") or {}
            version = _text((status.get("success") or {}).get("dolibarr_version")) if isinstance(status, dict) else ""
        except ErpError:
            pass                        # the version is only for the message
        login = _text(user.get("login")) if isinstance(user, dict) else ""
        name = f"Dolibarr {version}" if version else "Dolibarr"
        return Health.up(f"Connected to {name} at {self._base} as {login or 'the API user'}.")

    def _item_values(self, item: ErpItem) -> tuple[dict, dict]:
        """(plain fields, extra fields) Kosha keeps in step for an item."""
        rev_field, mat_field = self._custom()
        values: dict = {"label": (item.description or item.code)[:255]}
        if item.weight_kg:
            values["weight"] = float(item.weight_kg)
            values["weight_units"] = 0                  # 0 = kilogram (a power of ten, as Dolibarr stores it)
        extra: dict = {}
        if rev_field:
            extra[rev_field] = item.revision
        if mat_field and item.material:
            extra[mat_field] = item.material
        return values, extra

    def upsert_item(self, item: ErpItem) -> ErpResult:
        values, extra = self._item_values(item)
        existing = self._product(item.code)
        if existing:
            changed = {}
            for k, v in values.items():
                have = existing.get(k)
                same = abs(_num(have) - float(v)) < 1e-9 if k in ("weight", "weight_units") else _text(have) == v
                if not same:
                    changed[k] = v
            have_extra = existing.get("array_options") if isinstance(existing.get("array_options"), dict) else {}
            if any(_text(have_extra.get(k)) != v for k, v in extra.items()):
                changed["array_options"] = {**have_extra, **extra}   # all extra fields, so none is cleared
            if not changed:
                return ErpResult.unchanged("unchanged", _text(existing.get("ref")))
            self._call("PUT", f"/products/{int(existing['id'])}", changed)
            shown = {k: v for k, v in changed.items() if k != "array_options"}
            shown.update({k: v for k, v in extra.items() if _text(have_extra.get(k)) != v})
            return ErpResult.updated("updated", shown, _text(existing.get("ref")))
        unit_id = self._require_unit(item.uom)
        payload = {"ref": item.code, **values, "description": item.description or item.code,
                   "type": "0", "status": "1", "status_buy": "1"}
        if unit_id:
            payload["fk_unit"] = unit_id
        if item.make_or_buy.lower() in ("make", "buy"):
            payload["finished"] = "1" if item.make_or_buy.lower() == "make" else "0"
        if extra:
            payload["array_options"] = extra
        new_id = self._call("POST", "/products", payload, once=True)
        return ErpResult.created(f"created (product {new_id})", payload, item.code)

    def upsert_bom(self, bom: ErpBom) -> ErpResult:
        parent = self._product(bom.item.code)
        if not parent:
            raise ErpError(f"{bom.item.code} is not in Dolibarr yet, so its BOM cannot be added.")
        boms = self._boms_of(int(parent["id"]))
        current = self._current_bom(parent, boms)
        wanted = self._pairs((ln.code, ln.qty) for ln in bom.lines)
        if current:
            have = self._pairs((ln["code"], ln["qty"]) for ln in self._bom_lines(int(current["id"])))
            if have == wanted:
                ref = _text(current.get("ref"))
                if self._status(current) == BOM_DRAFT and self._is_ours(ref, bom) and self.cfg.get("validate_boms", True):
                    done = self._finish_bom(int(current["id"]), parent)     # Kosha stopped before validating it
                    return ErpResult.updated(f"finished BOM {ref} (Rev {bom.revision}, {' and '.join(done)})",
                                             {"ref": ref}, str(current["id"]))
                return ErpResult.unchanged(f"unchanged ({_text(current.get('ref')) or 'BOM ' + str(current['id'])})",
                                           str(current["id"]))
        children = self._products([ln.code for ln in bom.lines])
        missing = [ln.code for ln in bom.lines if ln.code.upper() not in children]
        if missing:
            raise ErpError(f"These parts are not in Dolibarr yet: {', '.join(missing[:10])}.")
        lines = []
        for pos, ln in enumerate(bom.lines, start=1):
            vals = {"fk_product": int(children[ln.code.upper()]["id"]), "qty": float(ln.qty), "qty_frozen": 0,
                    "disable_stock_change": 0, "efficiency": 1, "position": pos}
            unit_id = self._require_unit(ln.uom)
            if unit_id:
                vals["fk_unit"] = unit_id
            lines.append(vals)
        # a draft Kosha started for this revision but did not finish (stopped half-way): finish it, never add another
        draft = next((b for b in boms if self._status(b) == BOM_DRAFT and self._is_ours(_text(b.get("ref")), bom)), None)
        if draft:
            bom_id, ref, verb = int(draft["id"]), _text(draft.get("ref")), "finished"
            self._sync_draft_lines(bom_id, lines)
        else:
            ref = self._bom_ref(bom, boms)
            payload = {"ref": ref, "label": f"Rev {bom.revision}", "fk_product": int(parent["id"]), "qty": 1,
                       "bomtype": 0, "efficiency": 1, "status": BOM_DRAFT,
                       "description": f"Released in Kosha: {bom.item.code} rev {bom.revision}"}
            try:
                bom_id = int(self._call("POST", "/boms", payload, once=True))
            except ErpError as e:
                if e.status == 404:
                    raise ErpError("Dolibarr has no BOM API. Switch on the Bills of Materials module in Dolibarr "
                                   "(Home › Setup › Modules), then send the BOM again.", status=404,
                                   detail=e.detail) from None
                raise
            verb = "created"
            for vals in lines:
                self._call("POST", f"/boms/{bom_id}/lines", vals, once=True)
        done = self._finish_bom(bom_id, parent)
        payload = {"ref": ref, "label": f"Rev {bom.revision}", "fk_product": int(parent["id"]), "lines": lines}
        suffix = f", {' and '.join(done)}" if done else ", left as a draft"
        return ErpResult.created(f"{verb} BOM {ref} (Rev {bom.revision}{suffix})", payload, str(bom_id))

    def _sync_draft_lines(self, bom_id: int, lines: list[dict]) -> None:
        """Make a draft BOM's lines match: keep lines that are right, delete the others, add what is missing."""
        refs = {int(v["fk_product"]): v for v in lines}
        want = Counter((int(v["fk_product"]), round(float(v["qty"]), 6)) for v in lines)
        rows = self._call("GET", f"/boms/{bom_id}/lines", allow_404=True) or []
        for ln in rows if isinstance(rows, list) else []:
            key = (_int(ln.get("fk_product")) or 0, round(_num(ln.get("qty")), 6))
            if want[key] > 0:
                want[key] -= 1
            else:
                self._call("DELETE", f"/boms/{bom_id}/lines/{int(ln.get('id') or ln.get('rowid'))}")
        for (pid, qty), n in want.items():
            for _ in range(n):
                self._call("POST", f"/boms/{bom_id}/lines", {**refs[pid], "qty": qty}, once=True)

    def stock_and_cost(self, code: str) -> StockInfo:
        p = self._product(code)
        if not p:
            return StockInfo(exists=False)
        data = self._call("GET", f"/products/{int(p['id'])}/stock", allow_404=True) or {}
        per_wh = data.get("stock_warehouses") if isinstance(data, dict) else None
        warehouses = []
        for wid, row in (per_wh or {}).items():
            qty = _num(row.get("real") if isinstance(row, dict) else row)
            warehouses.append({"warehouse": self._warehouse(str(wid)), "actual_qty": qty})
        field = COST_CHOICES.get(self.cfg.get("cost_source") or "", "pmp")
        cost = p.get(field)
        if field == "cost_price" and cost in (None, ""):
            cost = None
        return StockInfo(exists=True, stock=sum(w["actual_qty"] for w in warehouses),
                         cost=_num(cost) if cost not in (None, "") else None, warehouses=tuple(warehouses))

    def _warehouse(self, wid: str) -> str:
        if wid not in self._warehouses:
            try:
                w = self._call("GET", f"/warehouses/{urllib.parse.quote(wid, safe='')}", allow_404=True) or {}
            except ErpError:
                w = {}
            self._warehouses[wid] = _text(w.get("ref") or w.get("label") or w.get("libelle")) or f"Warehouse {wid}"
        return self._warehouses[wid]

    # ------------------------------------------------- reading back (reconciliation)
    def fetch_items(self, codes) -> dict[str, ErpItemRecord]:
        rev_field, mat_field = self._custom()
        out = {}
        for p in self._products(codes).values():
            extra = p.get("array_options") if isinstance(p.get("array_options"), dict) else {}
            rev = _text(extra.get(rev_field)) if rev_field else None
            out[_text(p.get("ref"))] = ErpItemRecord(
                code=_text(p.get("ref")), description=_text(p.get("label")),
                uom=self._unit_code(p.get("fk_unit")) if self._use_units() else None,
                revision=rev, material=_text(extra.get(mat_field)) if mat_field else None,
                active=_text(p.get("status")) == "1" or _text(p.get("status_buy")) == "1",
                from_kosha=bool(rev) if rev_field else None)
        return out

    def fetch_bom(self, code: str) -> ErpBomRecord | None:
        p = self._product(code)
        if not p:
            return None
        b = self._current_bom(p, self._boms_of(int(p["id"])))
        if not b:
            return None
        m = _REV_LABEL.match(_text(b.get("label")).strip())
        lines = tuple(ErpBomLine(ln["code"], ln["qty"], ln["unit"]) for ln in self._bom_lines(int(b["id"])))
        return ErpBomRecord(code=_text(p.get("ref")), lines=lines, revision=m.group(1).strip() if m else None,
                            reference=_text(b.get("ref")) or f"BOM {b['id']}")

    def list_items_from_kosha(self) -> list[str]:
        rev_field, _ = self._custom()
        if not rev_field:
            raise ErpNotSupported("Set the revision extra field in the Dolibarr connection settings, so Kosha can "
                                  "recognise the products it created in Dolibarr.")
        column = rev_field[len("options_"):]
        out: list[str] = []
        page, size = 0, 500
        while True:
            rows = self._list("/products", {"sqlfilters": f"(ef.{column}:!=:'')", "limit": size, "page": page,
                                            "sortfield": "t.rowid", "sortorder": "ASC"})
            for p in rows:
                extra = p.get("array_options") if isinstance(p.get("array_options"), dict) else {}
                if _text(extra.get(rev_field)) and _text(p.get("ref")):
                    out.append(_text(p.get("ref")))
            if len(rows) < size:
                return out
            page += 1
