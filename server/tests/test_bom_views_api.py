# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: BOM views (latest released, in work, as of a date, as built), machine builds, cost roll-up with
estimates, what-if and the cost export (specification 6.10, 6.12 and 4.2)."""


def _bom(c, pn, desc, lines, revision=None, item_id=None):
    body = {"item_id": item_id, "revision": revision} if item_id else {"part_number": pn, "description": desc}
    bid = c.call("POST", "/api/boms", body)["bom_id"]
    c.call("PUT", f"/api/boms/{bid}/lines", {"lines": lines})
    return bid


def _rows(v):
    return {(r["level"], r["part_number"]): r for r in v["rows"]}


def test_views_machines_and_cost(kosha):
    admin = kosha.client()
    eng = kosha.add_user("bv-eng", "engineer")
    sub = _bom(admin, "BV-100-020", "DRIVE UNIT", [{"part_number": "BV-100-021", "description": "GEAR", "qty": 3}])
    top = _bom(admin, "BV-100-000", "MIXER 500 L", [
        {"part_number": "BV-100-010", "description": "TANK SHELL", "qty": 2},
        {"part_number": "BV-100-020", "description": "DRIVE UNIT", "qty": 1},
        {"part_number": "STD-BLT-M16", "description": "HEX BOLT M16", "qty": 8}])
    with kosha.db() as con:
        con.execute("UPDATE items SET weight_kg = 10, material = 'S235' WHERE part_number = 'BV-100-010'")
        con.execute("UPDATE items SET erp_cost = 100 WHERE part_number = 'BV-100-021'")
        con.execute("UPDATE items SET erp_cost = 12.5, make_buy = 'buy' WHERE part_number = 'STD-BLT-M16'")
    item = admin.call("GET", f"/api/boms/{top}")["bom"]["item_id"]
    assert admin.call("GET", f"/api/items/{item}/bom/view")["empty_why"] == "This part has no released BOM yet."
    admin.call("POST", f"/api/boms/{sub}/release")
    admin.call("POST", f"/api/boms/{top}/release")

    v = admin.call("GET", f"/api/items/{item}/bom/view?view=released&cost=1")
    rows = _rows(v)
    assert v["revision"] == "A" and rows[(2, "BV-100-021")]["ext_qty"] == 3
    assert v["missing"] == 1 and v["total"] == 400          # tank shell has no rate yet: 3 x 100 + 8 x 12.5
    eng.call("PUT", "/api/cost/rates", {"material_rates": {"S235": 90}}, expect=403)
    admin.call("PUT", "/api/cost/rates", {"material_rates": {"s235": 90}, "process_rate_per_kg": 10, "currency": "INR"})
    v = admin.call("GET", f"/api/items/{item}/bom/view?cost=1")
    rows = _rows(v)
    assert v["total"] == 2400 and v["estimated"] == 2000 and v["missing"] == 0
    assert rows[(1, "BV-100-010")]["cost_source"] == "estimate" and rows[(1, "BV-100-020")]["cost_source"] == "rolled_up"
    assert rows[(1, "BV-100-020")]["unit_cost"] == 300 and v["drivers"][0]["part_number"] == "BV-100-010"
    data, ctype = admin.call("GET", f"/api/items/{item}/bom/cost/export", raw=True)
    assert len(data) > 100

    # as of a date: the top BOM was released on 10 January 2025, the drive unit only today
    with kosha.db() as con:
        con.execute("UPDATE boms SET released_at = '2025-01-10T09:00:00+00:00' WHERE id = ?", (top,))
    assert admin.call("GET", f"/api/items/{item}/bom/view?view=as_of&date=2025-01-09")["rows"] == []
    old = _rows(admin.call("GET", f"/api/items/{item}/bom/view?view=as_of&date=2025-02-01"))
    assert old[(1, "BV-100-020")]["has_children"] is False and (2, "BV-100-021") not in old
    admin.call("GET", f"/api/items/{item}/bom/view?view=as_of&date=1-Mar-2025", expect=400)

    # a machine is built from the released BOM; a deviation for its serial shows in its as-built BOM
    m = eng.call("POST", "/api/machines", {"item_id": item, "customer": "Example Foods", "site": "Plant 2",
                                           "family": "BV", "order_ref": "SO-0042"})["machine"]
    serial = m["serial"]
    assert serial.startswith("BV") and m["revision"] == "A" and m["rows"] == 4
    eng.call("POST", "/api/machines", {"item_id": item, "serial": serial}, expect=409)
    gear = _rows(admin.call("GET", f"/api/items/{item}/bom/view"))[(2, "BV-100-021")]["item_id"]
    with kosha.db() as con:
        now = "2026-10-13T00:00:00+00:00"
        dev = con.execute("INSERT INTO deviations(number, title, state, serials, created_at, updated_at) VALUES "
                          "('DEV-26-0099', 'Gear from stock material', 'active', ?, ?, ?)", (f"{serial}, BV26-9999", now, now)).lastrowid
        con.execute("INSERT INTO deviation_affected(deviation_id, item_id) VALUES (?, ?)", (dev, gear))

    # revision B in work: the changes show line by line
    b = _bom(admin, None, None, [
        {"part_number": "BV-100-010", "description": "TANK SHELL", "qty": 3},
        {"part_number": "BV-100-020", "description": "DRIVE UNIT", "qty": 1},
        {"part_number": "BV-100-030", "description": "SIGHT GLASS", "qty": 1}], revision="B", item_id=item)
    w = admin.call("GET", f"/api/items/{item}/bom/view?view=in_work")
    st = {r["part_number"]: r["status"] for r in w["rows"] if r["level"] == 1}
    assert w["revision"] == "B" and w["changes"] == 3
    assert st == {"BV-100-010": "changed", "BV-100-020": "", "BV-100-030": "added", "STD-BLT-M16": "removed"}
    admin.call("POST", f"/api/boms/{b}/release")

    # the machine keeps what it was built with
    built = admin.call("GET", f"/api/items/{item}/bom/view?view=as_built&serial={serial.lower()}&cost=1")
    rows = _rows(built)
    assert rows[(1, "BV-100-010")]["qty"] == 2 and (1, "STD-BLT-M16") in rows
    assert rows[(2, "BV-100-021")]["deviations"][0]["number"] == "DEV-26-0099"
    assert admin.call("GET", "/api/machines?q=Example")["machines"][0]["deviations"] == ["DEV-26-0099"]
    admin.call("GET", f"/api/items/{item}/bom/view?view=as_built&serial=XX99-0001", expect=404)

    # what-if: the delivered machine against the new revision, biggest difference first
    wi = admin.call("POST", "/api/boms/what-if", {"a": {"item_id": item, "view": "as_built", "serial": serial},
                                                  "b": {"item_id": item, "view": "released"}})
    assert wi["a"]["total"] == 2400 and wi["b"]["total"] == 3300 and wi["difference"] == 900
    first = wi["lines"][0]
    assert first["part_number"] == "BV-100-010" and first["difference"] == 1000
    changes = {x["part_number"]: x["change"] for x in wi["lines"]}
    assert changes["STD-BLT-M16"] == "removed" and changes["BV-100-030"] == "added"

    # the installed base: edit, and withdraw (kept, with a reason)
    eng.call("PATCH", f"/api/machines/{m['id']}", {"commissioned_on": "2026-11-02"})
    eng.call("POST", f"/api/machines/{m['id']}/withdraw", {"reason": "typo"}, expect=403)
    admin.call("POST", f"/api/machines/{m['id']}/withdraw", {"reason": ""}, expect=400)
    admin.call("POST", f"/api/machines/{m['id']}/withdraw", {"reason": "recorded twice"})
    assert admin.call("GET", "/api/machines")["machines"] == []
    assert admin.call("GET", "/api/machines?withdrawn=1")["machines"][0]["commissioned_on"] == "2026-11-02"


def test_machine_needs_released_bom(kosha):
    admin = kosha.client()
    bid = _bom(admin, "BV-200-000", "PUMP SET", [{"part_number": "BV-200-010", "description": "PUMP", "qty": 1}])
    item = admin.call("GET", f"/api/boms/{bid}")["bom"]["item_id"]
    r = admin.call("POST", "/api/machines", {"item_id": item, "family": "PU"}, expect=409)
    assert r["code"] == "no_released_bom"
