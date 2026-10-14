# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configurable products: one product, its options, and the variants sold.

A machine family such as a mixer is sold with options: motor power, voltage, seal material. Instead of one BOM per
combination, the product keeps one BOM with every alternative on it, and a **rule** on each line that is not always
used says when it is ("MOTOR = 7K5", "VOLTAGE in (415, 480) and SEAL = PTFE"; see ``configrules``).

- **Options** belong to the product (the top assembly): a code and a name, and the choices with their codes and
  names. Rules can be on lines at any level of the product's BOM; they are kept with the product, by the assembly
  and the part, so they survive BOM edits, imports and new revisions.
- **Constraints** forbid combinations: "if MOTOR = 11K then VOLTAGE = 415", with the reason shown to whoever chooses.
- **Configure** takes one choice per option and returns the BOM that combination builds (the released BOMs, or the
  ones in work for a preview), every level, with the lines left out removed together with what is below them.
- **Check every combination** works through all allowed combinations and says which lines are never used, which
  choices change nothing, and which combinations build an empty assembly.
- A **variant** is a saved configuration for an order: its number (``<product>-V<nn>``), the choices, the customer
  and order, and the resolved BOM kept as it was, so it does not change when the design moves on. A machine can be
  recorded from a variant: its as-built BOM is the variant's.
"""
from __future__ import annotations

import io
import itertools
import json
import re

from . import access, boms, bomviews, configrules, db
from .web import bad, conflict, not_found

CODE = re.compile(r"^[A-Z0-9][A-Z0-9_.+\-]{0,23}$")
MAX_OPTIONS = 30
MAX_CHOICES = 50
MAX_COMBINATIONS = 5000
KEEP = ("item_id", "part_number", "description", "qty", "uom", "item_uom", "find_number", "material", "make_buy",
        "revision", "level", "ext_qty", "parent", "has_children")


def _code(value, what: str) -> str:
    c = str(value or "").strip().upper()
    if not CODE.match(c):
        raise bad(f"{what} needs a short code of letters and digits (dots, dashes and _ allowed), for example "
                  f"{'MOTOR' if 'option' in what.lower() else '7K5'}.")
    return c


def _item(item_id: int) -> dict:
    access.check_item(item_id)
    it = db.one("SELECT id, part_number, description, revision, state, item_type FROM items WHERE id = ?",
                (int(item_id),))
    if not it:
        raise not_found("Item not found")
    return it


# ----------------------------------------------------------------- reading --

def options(item_id: int) -> list[dict]:
    out = db.rows("SELECT id, code, name, position FROM config_options WHERE item_id = ? ORDER BY position, id",
                  (item_id,))
    for o in out:
        o["choices"] = db.rows("SELECT code, label FROM config_choices WHERE option_id = ? ORDER BY position, id",
                               (o["id"],))
    return out


def _option_map(opts: list[dict]) -> dict[str, set[str]]:
    return {o["code"]: {c["code"] for c in o["choices"]} for o in opts}


def rules(item_id: int) -> dict[tuple[int, int], str]:
    return {(r["parent_item_id"], r["child_item_id"]): r["rule"] for r in db.rows(
        "SELECT parent_item_id, child_item_id, rule FROM config_rules WHERE item_id = ?", (item_id,))}


def constraints(item_id: int) -> list[dict]:
    return db.rows("SELECT id, if_rule, then_rule, message FROM config_constraints WHERE item_id = ? ORDER BY id",
                   (item_id,))


def products() -> list[dict]:
    return db.rows(
        "SELECT i.id AS item_id, i.part_number, i.description, i.state, "
        "(SELECT COUNT(*) FROM config_options o WHERE o.item_id = i.id) AS options, "
        "(SELECT COUNT(*) FROM config_rules r WHERE r.item_id = i.id) AS rules, "
        "(SELECT COUNT(*) FROM variants v WHERE v.item_id = i.id AND v.withdrawn_at IS NULL) AS variants "
        "FROM items i WHERE i.id IN (SELECT item_id FROM config_options)" + access.scope().items("i")
        + " ORDER BY i.part_number")


def _root(item_id: int, source: str) -> dict | None:
    if source == "in_work":
        return bomviews._working(item_id) or bomviews._released(item_id)
    return bomviews._released(item_id)


def _pick(source: str):
    if source == "in_work":
        return lambda iid: bomviews._working(iid) or bomviews._released(iid)
    return bomviews._released


def structure(item_id: int, source: str = "released", choice: dict | None = None) -> dict:
    """The product's BOM, every level, with each line's rule. With ``choice``, only the lines that combination uses
    (and below them); without, every line with ``rule``."""
    it = _item(item_id)
    root = _root(item_id, source)
    if not root:
        return {"bom_id": None, "revision": None, "rows": [],
                "empty_why": f"{it['part_number']} has no {'released ' if source == 'released' else ''}BOM yet."}
    rs = rules(item_id)
    trees = {}
    for k, text in rs.items():
        try:
            trees[k] = configrules.parse(text)
        except configrules.RuleError:
            trees[k] = None             # kept, shown as an error by the checks
    pick = _pick(source)
    out: list[dict] = []

    def walk(bom: dict, parent_id: int, level: int, mult: float, path: tuple, parent_part: str):
        for ln in boms.lines(bom["id"]):
            rule = rs.get((parent_id, ln["item_id"]), "")
            if choice is not None and rule:
                tree = trees.get((parent_id, ln["item_id"]))
                if tree is None or not tree.eval(choice):
                    continue
            child = pick(ln["item_id"])
            row = {**ln, "level": level, "ext_qty": round(ln["qty"] * mult, 6), "parent": parent_part,
                   "parent_item_id": parent_id, "path": "/".join(map(str, path + (ln["item_id"],))),
                   "has_children": bool(child), "rule": rule, "bom_id": bom["id"], "bom_revision": bom["revision"]}
            out.append(row)
            if child and level < bomviews.MAX_DEPTH and ln["item_id"] not in path:
                walk(child, ln["item_id"], level + 1, mult * ln["qty"], path + (ln["item_id"],), ln["part_number"])

    walk(root, item_id, 1, 1.0, (item_id,), it["part_number"])
    if choice is not None:              # an assembly whose every line was left out is not an assembly here
        for r in out:
            r["has_children"] = any(x["path"].startswith(r["path"] + "/") for x in out)
    return {"bom_id": root["id"], "revision": root["revision"], "state": root["state"], "rows": out, "empty_why": ""}


def detail(item_id: int, source: str = "released") -> dict:
    it = _item(item_id)
    opts = options(item_id)
    if source == "released" and not bomviews._released(item_id):
        source = "in_work"               # nothing released yet: show the BOM being worked on
    s = structure(item_id, source)
    variants = db.rows("SELECT id, number, name, customer, order_ref, created_at, withdrawn_at FROM variants "
                       "WHERE item_id = ? ORDER BY id DESC", (item_id,))
    return {"item": it, "options": opts, "constraints": constraints(item_id), "structure": s, "source": source,
            "variants": variants, "rule_problems": rule_problems(item_id, opts)}


def rule_problems(item_id: int, opts: list[dict] | None = None) -> list[dict]:
    """Rules that no longer read (an option or choice was renamed or removed)."""
    omap = _option_map(opts if opts is not None else options(item_id))
    names = {r["id"]: r["part_number"] for r in db.rows(
        "SELECT i.id, i.part_number FROM items i WHERE i.id IN (SELECT child_item_id FROM config_rules WHERE "
        "item_id = ?)", (item_id,))}
    out = []
    for (parent, child), text in rules(item_id).items():
        try:
            configrules.check(text, omap)
        except configrules.RuleError as e:
            out.append({"parent_item_id": parent, "child_item_id": child, "part_number": names.get(child, "?"),
                        "rule": text, "message": str(e)})
    return out


# ----------------------------------------------------------------- writing --

def save_options(user: dict, item_id: int, raw) -> dict:
    """Replace the product's options and choices. Refused when a rule or constraint would then refer to an option or
    choice that no longer exists (nothing is saved); rename codes by editing the rules too."""
    it = _item(item_id)
    if not isinstance(raw, list):
        raise bad("Send the options as a list.")
    if len(raw) > MAX_OPTIONS:
        raise bad(f"A product can have at most {MAX_OPTIONS} options.")
    clean, seen = [], set()
    for k, o in enumerate(raw, start=1):
        if not isinstance(o, dict):
            raise bad(f"Option {k} is not valid.")
        code = _code(o.get("code"), f"Option {k}")
        if code in seen:
            raise bad(f"Option {code} is listed twice.")
        seen.add(code)
        name = " ".join(str(o.get("name") or "").split())[:80] or code
        choices, cseen = [], set()
        for c in o.get("choices") or []:
            cc = _code((c or {}).get("code"), f"A choice of {code}")
            if cc in cseen:
                raise bad(f"{code} has the choice {cc} twice.")
            cseen.add(cc)
            choices.append({"code": cc, "label": " ".join(str(c.get("label") or "").split())[:80] or cc})
        if len(choices) < 2:
            raise bad(f"Option {code} needs at least two choices; an option with one choice is not a choice.")
        if len(choices) > MAX_CHOICES:
            raise bad(f"Option {code} can have at most {MAX_CHOICES} choices.")
        clean.append({"code": code, "name": name, "choices": choices})
    omap = {o["code"]: {c["code"] for c in o["choices"]} for o in clean}
    problems = []
    for (_, child), text in rules(item_id).items():
        try:
            configrules.check(text, omap)
        except configrules.RuleError as e:
            pn = db.scalar("SELECT part_number FROM items WHERE id = ?", (child,))
            problems.append(f"{pn} ({text}): {e}")
    for c in constraints(item_id):
        for text in (c["if_rule"], c["then_rule"]):
            try:
                configrules.check(text, omap)
            except configrules.RuleError as e:
                problems.append(f"constraint {c['if_rule']} → {c['then_rule']}: {e}")
    if problems:
        raise conflict("Rules still use what you removed. Change these first: " + "; ".join(problems[:5]),
                       "options_in_use")
    with db.tx():
        old = [r["id"] for r in db.rows("SELECT id FROM config_options WHERE item_id = ?", (item_id,))]
        for oid in old:
            db.execute("DELETE FROM config_choices WHERE option_id = ?", (oid,))
        db.execute("DELETE FROM config_options WHERE item_id = ?", (item_id,))
        for p, o in enumerate(clean):
            oid = db.insert("config_options", {"item_id": item_id, "code": o["code"], "name": o["name"], "position": p})
            for q, c in enumerate(o["choices"]):
                db.insert("config_choices", {"option_id": oid, "code": c["code"], "label": c["label"], "position": q})
        db.audit(user["id"], "config_options", "item", item_id,
                 f"{it['part_number']}: " + ", ".join(f"{o['code']} ({len(o['choices'])})" for o in clean))
    return detail(item_id)


def save_rules(user: dict, item_id: int, raw) -> dict:
    """Set the rules of lines (``parent_item_id``, ``child_item_id``, ``rule``; an empty rule means always used).
    Only the lines sent change."""
    it = _item(item_id)
    if not isinstance(raw, list):
        raise bad("Send the rules as a list.")
    omap = _option_map(options(item_id))
    if not omap:
        raise conflict("Set up the product's options first; rules choose between them.", "no_options")
    clean = []
    for r in raw:
        if not isinstance(r, dict):
            raise bad("A rule is not valid.")
        try:
            parent, child = int(r.get("parent_item_id")), int(r.get("child_item_id"))
        except (TypeError, ValueError):
            raise bad("Each rule names the assembly and the part of its line.") from None
        pn = db.scalar("SELECT part_number FROM items WHERE id = ?", (child,)) or "?"
        text = str(r.get("rule") or "").strip()
        try:
            configrules.check(text, omap)
        except configrules.RuleError as e:
            raise bad(f"{pn}: {e}") from None
        clean.append((parent, child, configrules.normal(text)))
    now = db.now()
    with db.tx():
        for parent, child, text in clean:
            db.execute("DELETE FROM config_rules WHERE item_id = ? AND parent_item_id = ? AND child_item_id = ?",
                       (item_id, parent, child))
            if text:
                db.insert("config_rules", {"item_id": item_id, "parent_item_id": parent, "child_item_id": child,
                                           "rule": text, "updated_by": user["id"], "updated_at": now})
        db.audit(user["id"], "config_rules", "item", item_id, f"{it['part_number']}: {len(clean)} line rule(s)")
    return detail(item_id)


def save_constraints(user: dict, item_id: int, raw) -> dict:
    it = _item(item_id)
    if not isinstance(raw, list):
        raise bad("Send the constraints as a list.")
    omap = _option_map(options(item_id))
    clean = []
    for k, c in enumerate(raw, start=1):
        if not isinstance(c, dict):
            raise bad(f"Constraint {k} is not valid.")
        when, then = str(c.get("if") or c.get("if_rule") or "").strip(), str(c.get("then") or c.get("then_rule") or "").strip()
        if not when or not then:
            raise bad(f"Constraint {k} needs both parts: if … then ….")
        try:
            configrules.check(when, omap)
            configrules.check(then, omap)
        except configrules.RuleError as e:
            raise bad(f"Constraint {k}: {e}") from None
        clean.append({"if_rule": configrules.normal(when), "then_rule": configrules.normal(then),
                      "message": " ".join(str(c.get("message") or "").split())[:200]})
    with db.tx():
        db.execute("DELETE FROM config_constraints WHERE item_id = ?", (item_id,))
        for c in clean:
            db.insert("config_constraints", {"item_id": item_id, **c})
        db.audit(user["id"], "config_constraints", "item", item_id, f"{it['part_number']}: {len(clean)} constraint(s)")
    return detail(item_id)


# --------------------------------------------------------------- configure --

def _choice(item_id: int, raw, opts: list[dict] | None = None) -> dict:
    opts = opts if opts is not None else options(item_id)
    if not opts:
        raise conflict("This product has no options yet.", "no_options")
    raw = raw if isinstance(raw, dict) else {}
    raw = {str(k).strip().upper(): str(v or "").strip().upper() for k, v in raw.items()}
    out = {}
    for o in opts:
        v = raw.get(o["code"], "")
        codes = [c["code"] for c in o["choices"]]
        if not v:
            raise bad(f"Choose the {o['name'].lower()} ({o['code']}).")
        if v not in codes:
            raise bad(f"{o['code']} has no choice {v}. Choose one of {', '.join(codes)}.")
        out[o["code"]] = v
    extra = set(raw) - set(out)
    if extra:
        raise bad(f"This product has no option {sorted(extra)[0]}.")
    return out


def blocked_by(item_id: int, choice: dict, cons: list[dict] | None = None) -> list[str]:
    """Why a combination is not allowed (empty when it is)."""
    out = []
    for c in (cons if cons is not None else constraints(item_id)):
        if configrules.parse(c["if_rule"]).eval(choice) and not configrules.parse(c["then_rule"]).eval(choice):
            out.append(c["message"] or f"When {c['if_rule']}, {c['then_rule']} is needed.")
    return out


def configure(item_id: int, raw_choice, source: str = "released") -> dict:
    opts = options(item_id)
    choice = _choice(item_id, raw_choice, opts)
    why = blocked_by(item_id, choice)
    s = structure(item_id, source, choice)
    labels = {o["code"]: {c["code"]: c["label"] for c in o["choices"]} for o in opts}
    return {**s, "choice": choice, "allowed": not why, "blocked_by": why, "source": source,
            "summary": ", ".join(f"{o['name']} {labels[o['code']][choice[o['code']]]}" for o in opts)}


def check_all(item_id: int, source: str = "released") -> dict:
    """Every allowed combination: lines never used, choices that change nothing, empty assemblies."""
    it = _item(item_id)
    opts = options(item_id)
    if not opts:
        raise conflict("This product has no options yet.", "no_options")
    total = 1
    for o in opts:
        total *= len(o["choices"])
    if total > MAX_COMBINATIONS:
        raise conflict(f"{total} combinations is more than Kosha checks at once ({MAX_COMBINATIONS}). Check with "
                       "fewer options, or split the product.", "too_many_combinations")
    cons = constraints(item_id)
    full = structure(item_id, source)
    if not full["bom_id"]:
        raise conflict(full["empty_why"], "no_bom")
    ruled = {(r["parent_item_id"], r["item_id"]): r for r in full["rows"] if r["rule"]}
    used: set[tuple[int, int]] = set()
    by_combo: dict[tuple, frozenset] = {}
    allowed = blocked = 0
    empty: list[dict] = []
    codes = [o["code"] for o in opts]
    for combo in itertools.product(*[[c["code"] for c in o["choices"]] for o in opts]):
        choice = dict(zip(codes, combo))
        if blocked_by(item_id, choice, cons):
            blocked += 1
            continue
        allowed += 1
        rows = structure(item_id, source, choice)["rows"]
        keys = frozenset((r["parent_item_id"], r["item_id"], r["ext_qty"]) for r in rows)
        by_combo[combo] = keys
        used |= {(r["parent_item_id"], r["item_id"]) for r in rows if r["rule"]}
        hollow = [r["part_number"] for r in full["rows"] if r["has_children"] and
                  any(x["path"] == r["path"] for x in rows) and not any(x["path"].startswith(r["path"] + "/") for x in rows)]
        if hollow and len(empty) < 20:
            empty.append({"choice": choice, "assemblies": hollow})
    issues = []
    for key, r in ruled.items():
        if key not in used:
            issues.append({"severity": "warning", "part_number": r["part_number"],
                           "message": f"Never used: no allowed combination meets its rule ({r['rule']})."})
    for e in empty:
        issues.append({"severity": "error", "part_number": ", ".join(e["assemblies"]),
                       "message": "Left empty by " + ", ".join(f"{k} = {v}" for k, v in e["choice"].items()) + "."})
    for i, o in enumerate(opts):            # a choice that never changes the BOM against another choice
        for c in o["choices"]:
            same = True
            for combo, keys in by_combo.items():
                if combo[i] != c["code"]:
                    continue
                for other in o["choices"]:
                    if other["code"] == c["code"]:
                        continue
                    alt = combo[:i] + (other["code"],) + combo[i + 1:]
                    if alt in by_combo and by_combo[alt] != keys:
                        same = False
                        break
                if not same:
                    break
            if same and any(combo[i] == c["code"] for combo in by_combo):
                issues.append({"severity": "info", "part_number": "",
                               "message": f"{o['code']} = {c['code']} builds the same BOM as the other choices of "
                                          f"{o['code']}: no line has a rule for it."})
    if allowed == 0:
        issues.insert(0, {"severity": "error", "part_number": it["part_number"],
                          "message": "The constraints allow no combination at all."})
    for p in rule_problems(item_id, opts):
        issues.insert(0, {"severity": "error", "part_number": p["part_number"], "message": p["message"]})
    return {"combinations": total, "allowed": allowed, "blocked": blocked, "issues": issues,
            "distinct_boms": len(set(by_combo.values()))}


# ----------------------------------------------------------------- variants --

def _variant_number(item: dict) -> str:
    """<product>-V01, -V02 …, never reused (withdrawn variants keep their number). Inside db.tx()."""
    prefix = f"{item['part_number']}-V"
    rows = db.rows("SELECT number FROM variants WHERE item_id = ?", (item["id"],))
    highest = max([int(r["number"][len(prefix):]) for r in rows if r["number"].startswith(prefix)
                   and r["number"][len(prefix):].isdigit()] or [0])
    return f"{prefix}{highest + 1:02d}"


def save_variant(user: dict, item_id: int, body: dict) -> dict:
    it = _item(item_id)
    c = configure(item_id, body.get("choice"), "released")
    if not c["bom_id"]:
        raise conflict(f"{it['part_number']} has no released BOM. Release it first, so the variant's BOM is the one "
                       "production builds from.", "no_released_bom")
    if not c["allowed"]:
        raise conflict("This combination is not allowed: " + " ".join(c["blocked_by"]), "not_allowed")
    lines = [{k: r.get(k) for k in KEEP} | {"rule": r["rule"]} for r in c["rows"]]
    now = db.now()
    with db.tx():
        number = _variant_number(it)
        vid = db.insert("variants", {
            "item_id": item_id, "number": number, "name": " ".join(str(body.get("name") or "").split())[:120]
            or c["summary"][:120], "choice_json": json.dumps(c["choice"]), "summary": c["summary"][:500],
            "bom_id": c["bom_id"], "revision": c["revision"], "lines_json": json.dumps(lines),
            "customer": " ".join(str(body.get("customer") or "").split())[:120],
            "order_ref": " ".join(str(body.get("order_ref") or "").split())[:60],
            "note": str(body.get("note") or "").strip()[:1000], "created_by": user["id"], "created_at": now})
        db.audit(user["id"], "variant_save", "variant", vid, f"{number}: {c['summary']}")
    return variant_or_404(vid)


def variant_or_404(vid: int) -> dict:
    v = db.one("SELECT v.*, i.part_number, i.description FROM variants v JOIN items i ON i.id = v.item_id "
               "WHERE v.id = ?", (int(vid),))
    if not v:
        raise not_found("Variant not found")
    access.check_item(v["item_id"])
    v["choice"] = json.loads(v.pop("choice_json") or "{}")
    v["lines"] = json.loads(v.pop("lines_json") or "[]")
    from .lifecycle import user_names
    v["created_by_name"] = user_names().get(v["created_by"])
    v["machines"] = db.rows("SELECT id, serial, customer, built_on FROM machine_builds WHERE variant_id = ? "
                            "AND deleted_at IS NULL ORDER BY id", (v["id"],))
    return v


def variants(item_id: int | None = None, q: str = "", include_withdrawn: bool = False) -> list[dict]:
    sql = ("SELECT v.id, v.item_id, v.number, v.name, v.summary, v.customer, v.order_ref, v.revision, v.created_at, "
           "v.withdrawn_at, i.part_number, i.description FROM variants v JOIN items i ON i.id = v.item_id WHERE 1 = 1"
           + access.scope().items("i"))
    args: list = []
    if item_id:
        sql += " AND v.item_id = ?"
        args.append(int(item_id))
    if not include_withdrawn:
        sql += " AND v.withdrawn_at IS NULL"
    if q.strip():
        sql += " AND (v.number LIKE ? OR v.name LIKE ? OR v.customer LIKE ? OR v.order_ref LIKE ?)"
        args += [f"%{q.strip()}%"] * 4
    return db.rows(sql + " ORDER BY v.id DESC LIMIT 500", args)


def withdraw_variant(user: dict, vid: int, reason: str) -> dict:
    v = variant_or_404(vid)
    reason = " ".join(str(reason or "").split())[:300]
    if not reason:
        raise bad("Say why the variant is withdrawn.")
    if not v["withdrawn_at"]:
        with db.tx():
            db.update("variants", v["id"], {"withdrawn_at": db.now(), "withdrawn_by": user["id"],
                                            "withdrawn_reason": reason})
            db.audit(user["id"], "variant_withdraw", "variant", v["id"], f"{v['number']}: {reason}")
    return variant_or_404(vid)


def export_variant(vid: int) -> tuple[bytes, str]:
    import openpyxl
    from openpyxl.styles import Font

    v = variant_or_404(vid)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Variant BOM"
    ws.append([f"{v['number']} {v['name']}", f"{v['part_number']} rev {v['revision']}"])
    ws.append([v["summary"]])
    ws.append([f"Customer: {v['customer']}" if v["customer"] else "", f"Order: {v['order_ref']}" if v["order_ref"] else ""])
    ws.append([])
    ws.append(["Level", "Part number", "Description", "Qty", "Total qty", "Unit", "Material", "Make/Buy", "Revision",
               "Used when"])
    for c in ws[5]:
        c.font = Font(bold=True)
    for r in v["lines"]:
        ws.append([r["level"], r["part_number"], r["description"], r["qty"], r["ext_qty"], r["uom"] or r["item_uom"],
                   r["material"], r["make_buy"], r["revision"], r.get("rule") or ""])
    for col, width in zip("ABCDEFGHIJ", (7, 20, 40, 8, 10, 8, 16, 10, 9, 30)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"Variant_{v['number']}.xlsx"
