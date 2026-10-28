#!/usr/bin/env python
"""Create the Properties database under the Real Estate page, migrate it to the current schema and backfill derived fields
(idempotent; ids stored in data/notion_state.json under databases.deals, kept under that key for the scripts that use it).

History: 2026-10-15 created Deals + Comps; 2026-10-16 merged them into one Properties database (one row per building, Type
Deal or Comp) and trashed Comps; 2026-10-17 trimmed nine fields the page already carries (Lot became text); 2026-10-21 Rules that mattered became text
(the Notion mirror of the Zoning Resolution was retired; zoning text comes from the nyc-zr CLI). Env: NOTION_TOKEN or NOTION_API_TOKEN; NOTION_UW_PARENT overrides the parent page id.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from notion_api import load_state, location_value, page_url, req, rt, save_state  # noqa: E402

PARENT = os.environ.get("NOTION_UW_PARENT", "60a139f3-f569-40cf-9976-8d0c40dae1c5")  # "Real Estate"
V = "2022-06-28"
TITLE = "Properties"
DESCRIPTION = ("Properties and nearby places in one database. Type is Deal, Comp, Restaurant, Park or Subway station. "
               "Property records are keyed by BBL; amenities use Source ID. Location powers native map views. "
               "Only Noah sets \"Noah's call\".")

STRATEGIES = ("Flip", "Rent", "Build", "Not set")
TYPES = ("Deal", "Comp", "Restaurant", "Park", "Subway station")
FLAGS = ("Historic district", "Landmark", "Shallow lot", "Near corner", "Narrow lot", "Irregular lot", "Rent regulated?",
         "Open DOB violations", "Open HPD violations", "Active DOB job", "Flood zone", "City owned", "Litigation", "No rent history")


def sel(*names):
    return {"select": {"options": [{"name": n} for n in names]}}


def multi(*names):
    return {"multi_select": {"options": [{"name": n} for n in names]}}


def num(fmt="number"):
    return {"number": {"format": fmt}}


# Created on a fresh database; the formulas and the self-relation are added by migrate() because they refer to other properties.
SCHEMA = {
    "Name": {"title": {}},
    "BBL": {"rich_text": {}},
    "Type": sel(*TYPES),
    "Location": {"place": {}},
    "Source ID": {"rich_text": {}},
    "Source URL": {"url": {}},
    "Strategy": sel(*STRATEGIES),
    "Borough": sel("Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"),
    "Neighborhood": {"select": {"options": []}},
    "Zoning": {"multi_select": {"options": []}},
    "Lot": {"rich_text": {}},
    "Lot sf": num(),
    "Existing sf": num(),
    "Max ZFA": num(),
    "Flags": multi(*FLAGS),
    "Units": num(),
    "Building": {"rich_text": {}},
    "Verdict": sel("Investigate", "Pass", "Closed by others"),
    "Noah's call": sel("Pursue", "Pass", "Watch"),
    "Ask": num("dollar"),
    "Owner": {"rich_text": {}},
    "Last sale price": num("dollar"),
    "Last sale date": {"date": {}},
    "Prior sale": {"rich_text": {}},
    "Beds/baths": {"rich_text": {}},
    "Listing URL": {"url": {}},
    "Rules that mattered": {"rich_text": {}},  # section numbers cited on the page, "23-22, 23-344" (text since 2026-10-21)
}
PSF_FORMULA = ('if(prop("Last sale price") > 0 and prop("Existing sf") > 0, prop("Last sale price") / prop("Existing sf"), '
               'if(prop("Last sale price") > 0 and prop("Max ZFA") > 0, prop("Last sale price") / prop("Max ZFA"), toNumber("")))')
DROP = ("Exits", "Returns at ask", "Stage", "Building class", "Year built", "Confidence",  # merged into other fields, 2026-10-16
        "Lot width", "Lot depth", "Lot type", "Unused ratio", "Deal type", "List price", "Days on market", "ZoLa URL", "Last run")  # the page carries these, 2026-10-17


def lot_text(width, depth) -> str:
    """"17.5 × 100" from PLUTO lotfront and lotdepth; empty when either is missing."""
    return f"{width:g} × {depth:g}" if width and depth else ""


def _rt(s: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": s[:2000]}}] if s else []}


def _section_key(sid: str) -> tuple:
    """Sort key for Zoning Resolution ids: 23-10 < 23-22 < 23-342 < 23-344."""
    head, _, tail = sid.partition("-")
    return (int(head) if head.isdigit() else 999, tail.ljust(4, "0"))


def ensure(state: dict, schema: dict) -> str:
    dbs = state.setdefault("databases", {})
    if dbs.get("deals"):
        try:
            d = req("GET", f"/databases/{dbs['deals']}", version=V)
            if not (d.get("archived") or d.get("in_trash")):
                print(f"properties: exists {page_url(dbs['deals'])}")
                return dbs["deals"]
        except RuntimeError:
            pass
    d = req("POST", "/databases", {"parent": {"type": "page_id", "page_id": PARENT}, "icon": {"type": "emoji", "emoji": "🏘️"},
                                   "title": rt(TITLE), "description": rt(DESCRIPTION), "properties": schema}, version=V)
    dbs["deals"] = dbs["properties"] = d["id"]
    save_state(state)
    print(f"properties: created {page_url(d['id'])}")
    return d["id"]


def _rows(db_id: str, body: dict | None = None) -> list[dict]:
    out, cursor = [], None
    while True:
        q = req("POST", f"/databases/{db_id}/query", {"page_size": 100, **(body or {}), **({"start_cursor": cursor} if cursor else {})}, version=V)
        out += q["results"]
        if not q.get("has_more"):
            return out
        cursor = q["next_cursor"]


def _text(pr: dict | None) -> str:
    if not pr:
        return ""
    return "".join(x["plain_text"] for x in pr.get(pr["type"], []) or []) if pr["type"] in ("rich_text", "title") else ""


def _patch(db_id: str, props: dict, label: str) -> None:
    if props:
        req("PATCH", f"/databases/{db_id}", {"properties": props}, version=V)
        print(f"properties: {label}", sorted(props))


def migrate(state: dict, db_id: str) -> None:
    """Bring the database to the current schema: title, new fields, formulas, self-relation, dropped fields (idempotent)."""
    d = req("GET", f"/databases/{db_id}", version=V)
    props = d["properties"]
    if "Role" in props and "Type" not in props:
        _patch(db_id, {props["Role"]["id"]: {"name": "Type"}}, "Role -> Type (same property ID)")
        props = req("GET", f"/databases/{db_id}", version=V)["properties"]
    if "".join(t["plain_text"] for t in d["title"]) != TITLE:
        req("PATCH", f"/databases/{db_id}", {"title": rt(TITLE), "description": rt(DESCRIPTION), "icon": {"type": "emoji", "emoji": "🏘️"}}, version=V)
        print(f"properties: renamed to {TITLE!r}")
    # plain fields first
    _patch(db_id, {k: v for k, v in SCHEMA.items() if k not in props and k != "Name"}, "added")
    # Verdict / Strategy / Flags option sets (add missing options; Notion ignores renames by id)
    opt_patch = {}
    for name, wanted in (("Strategy", STRATEGIES), ("Flags", FLAGS), ("Type", TYPES)):
        cur = props.get(name)
        if cur:
            kind = cur["type"]
            have = [o["name"] for o in cur[kind]["options"]]
            if any(w not in have for w in wanted):
                opt_patch[name] = {kind: {"options": [{"id": o["id"], "name": o["name"]} for o in cur[kind]["options"]] + [{"name": w} for w in wanted if w not in have]}}
    _patch(db_id, opt_patch, "options")
    # Lot was a formula over Lot width and Lot depth until 2026-10-17; carry its text over before those two fields are dropped
    if props.get("Lot", {}).get("type") == "formula":
        texts = {r["id"]: lot_text(r["properties"].get("Lot width", {}).get("number"), r["properties"].get("Lot depth", {}).get("number")) for r in _rows(db_id)}
        try:
            _patch(db_id, {"Lot": {"rich_text": {}}}, "Lot formula -> text")
        except RuntimeError as e:
            print(f"properties: Lot type change refused ({str(e)[:100]}); recreating")
            _patch(db_id, {"Lot": None}, "dropped")
            _patch(db_id, {"Lot": {"rich_text": {}}}, "added")
        for pid, t in texts.items():
            if t:
                req("PATCH", f"/pages/{pid}", {"properties": {"Lot": _rt(t)}}, version=V)
        print(f"properties: Lot text written on {sum(1 for t in texts.values() if t)} rows")
    # formula (second call: it refers to the fields above)
    if props.get("Sale $/sf", {}).get("type") != "formula" or props["Sale $/sf"]["formula"]["expression"] != PSF_FORMULA:
        v = {"formula": {"expression": PSF_FORMULA}}
        try:
            _patch(db_id, {"Sale $/sf": v}, "formula")
        except RuntimeError as e:
            print(f"properties: Sale $/sf refused ({str(e)[:100]}); recreating")
            _patch(db_id, {"Sale $/sf": None}, "dropped")
            _patch(db_id, {"Sale $/sf": {"formula": {"expression": PSF_FORMULA.replace('toNumber("")', "0")}}}, "formula")
    # self-relation: a deal links the property rows that served as its comps; the reverse side is "Used by"
    if "Comps used" not in props:
        _patch(db_id, {"Comps used": {"relation": {"database_id": db_id, "type": "dual_property", "dual_property": {}}}}, "relation")
        props = req("GET", f"/databases/{db_id}", version=V)["properties"]
    for name, pr in props.items():
        if pr["type"] == "relation" and pr["relation"].get("database_id", "").replace("-", "") == db_id.replace("-", "") \
                and pr["relation"].get("dual_property", {}).get("synced_property_name") == "Comps used" and name != "Used by":
            _patch(db_id, {name: {"name": "Used by"}}, "renamed reverse relation to 'Used by'")
    # Rules that mattered was a relation to the Notion mirror of the ZR Sections until 2026-10-21; that mirror is retired
    # (zoning text comes from the nyc-zr CLI). Carry the cited section numbers over as text, then recreate the field as text.
    if props.get("Rules that mattered", {}).get("type") == "relation":
        cited: dict[str, str] = {}
        section_ids: dict[str, str] = {}
        for r in _rows(db_id, {"filter": {"property": "Rules that mattered", "relation": {"is_not_empty": True}}}):
            ids = []
            for rel in r["properties"]["Rules that mattered"]["relation"]:
                if rel["id"] not in section_ids:  # the related row stays readable while its database sits in Trash
                    try:
                        section_ids[rel["id"]] = _text(req("GET", f"/pages/{rel['id']}", version=V)["properties"].get("Section ID"))
                    except RuntimeError as e:
                        print(f"properties: could not read ZR row {rel['id']} ({str(e)[:80]})")
                        section_ids[rel["id"]] = ""
                if section_ids[rel["id"]]:
                    ids.append(section_ids[rel["id"]])
            cited[r["id"]] = ", ".join(dict.fromkeys(sorted(ids, key=_section_key)))
        _patch(db_id, {"Rules that mattered": None}, "dropped the relation to the retired ZR Sections mirror")
        _patch(db_id, {"Rules that mattered": {"rich_text": {}}}, "added")
        for pid, t in cited.items():
            if t:
                req("PATCH", f"/pages/{pid}", {"properties": {"Rules that mattered": _rt(t)}}, version=V)
        print(f"properties: Rules that mattered carried over as text on {sum(1 for t in cited.values() if t)} rows")
    # dropped fields (after the data they held has been carried over by backfill/merge, which run before this on a second pass)
    _patch(db_id, {k: None for k in DROP if k in props}, "dropped")


def pluto_rows(bbls: set[str]) -> dict[str, dict]:
    from screen import DS, norm_bbl, soda
    if not bbls:
        return {}
    rows = soda(DS["pluto"], {"$where": "bbl in (" + ",".join(sorted(bbls)) + ")",
                              "$select": "bbl,address,borough,zonedist1,zonedist2,overlay1,spdist1,lotarea,lotfront,lotdepth,lottype,irrlotcode,"
                                         "bldgclass,yearbuilt,numfloors,unitsres,bldgarea,bldgfront,bldgdepth,residfar,ownername,histdist,landmark,latitude,longitude"})
    return {norm_bbl(r["bbl"]): r for r in rows}


def props_from_pluto(p: dict) -> dict:
    from screen import BORO_NAME, building_text, fnum
    lotarea = fnum(p.get("lotarea"), 0) or 0
    far = fnum(p.get("residfar"), 0) or 0
    bldg = fnum(p.get("bldgarea"), 0) or 0
    zfa = round(lotarea * far)
    zoning = [z for z in (p.get("zonedist1"), p.get("zonedist2"), p.get("overlay1"), p.get("spdist1")) if z]
    boro = p.get("borough", "BK")
    out = {
        "Borough": {"select": {"name": BORO_NAME.get(boro, "Brooklyn")}},
        "Zoning": {"multi_select": [{"name": z} for z in zoning]},
        "Lot": _rt(lot_text(fnum(p.get("lotfront")), fnum(p.get("lotdepth")))),
        "Lot sf": {"number": lotarea or None}, "Existing sf": {"number": bldg or None}, "Max ZFA": {"number": zfa or None},
        "Units": {"number": int(fnum(p.get("unitsres"), 0) or 0)},
        "Building": _rt(building_text(p)),
        "Owner": _rt((p.get("ownername") or "").title()[:200]),
    }
    location = location_value((p.get("address") or "").title(), p.get("latitude"), p.get("longitude"))
    if location:
        out["Location"] = location
    return out


def _month(d: str) -> str:
    try:
        return dt.date.fromisoformat(d[:10]).strftime("%B %Y")
    except ValueError:
        return d


def merge_comps(state: dict, db_id: str) -> None:
    """Move every row of the old Comps database into Properties as Type Comp (one row per BBL, latest sale on the row,
    earlier sale in Prior sale), relink the deals through 'Comps used', then trash the old database. Idempotent."""
    comps_db = state["databases"].get("comps")
    if not comps_db:
        return
    try:
        cdb = req("GET", f"/databases/{comps_db}", version=V)
    except RuntimeError:
        cdb = None
    if not cdb or cdb.get("archived") or cdb.get("in_trash"):
        state["databases"].pop("comps", None)
        save_state(state)
        return
    from screen import norm_bbl
    rows = _rows(comps_db)
    by_bbl: dict[str, list[dict]] = {}
    for r in rows:
        b = _text(r["properties"].get("BBL"))
        if b and r["properties"].get("Date", {}).get("date"):
            by_bbl.setdefault(norm_bbl(b), []).append(r)
    existing = {norm_bbl(_text(r["properties"].get("BBL"))): r for r in _rows(db_id) if _text(r["properties"].get("BBL"))}
    pluto = pluto_rows(set(by_bbl) - set(existing))
    new_ids: dict[str, str] = {}
    deal_links: dict[str, set[str]] = {}  # deal page id -> property ids used as comps
    for bbl, sales in by_bbl.items():
        sales.sort(key=lambda r: r["properties"]["Date"]["date"]["start"], reverse=True)
        last, prior = sales[0]["properties"], (sales[1]["properties"] if len(sales) > 1 else None)
        for s in sales:
            for d_ in s["properties"].get("Deals", {}).get("relation", []):
                deal_links.setdefault(d_["id"], set())
        if bbl in existing:
            pid = existing[bbl]["id"]  # a deal row already covers this building; keep it, only link
        else:
            p = pluto.get(bbl, {})
            props = props_from_pluto(p) if p else {}
            props.update({
                "Name": {"title": [{"type": "text", "text": {"content": _text(last["Name"]) or (p.get("address") or bbl).title()}}]},
                "BBL": {"rich_text": [{"type": "text", "text": {"content": bbl}}]},
                "Type": {"select": {"name": "Comp"}},
                "Strategy": {"select": {"name": "Not set"}},
                "Neighborhood": {"select": {"name": last["Neighborhood"]["select"]["name"]}} if last.get("Neighborhood", {}).get("select") else {"select": None},
                "Last sale price": {"number": last["Price"]["number"]},
                "Last sale date": {"date": {"start": last["Date"]["date"]["start"]}},
                "Prior sale": {"rich_text": [{"type": "text", "text": {"content": f"${prior['Price']['number']:,.0f} · {_month(prior['Date']['date']['start'])}"}}]} if prior and prior["Price"]["number"] else {"rich_text": []},
                "Beds/baths": {"rich_text": [{"type": "text", "text": {"content": _text(last.get("Beds/baths"))}}]} if _text(last.get("Beds/baths")) else {"rich_text": []},
                "Listing URL": {"url": last.get("Listing URL", {}).get("url")},
            })
            if not p:  # no PLUTO: carry what the comp row had
                props.update({"Lot sf": {"number": last.get("Lot sf", {}).get("number")}, "Units": {"number": last.get("Units", {}).get("number")},
                              "Zoning": {"multi_select": [{"name": last["Zoning"]["select"]["name"]}] if last.get("Zoning", {}).get("select") else []},
                              "Existing sf": {"number": last.get("Area sf", {}).get("number")}})
            pid = req("POST", "/pages", {"parent": {"database_id": db_id}, "properties": props}, version=V)["id"]
            print("properties: comp row", _text(last["Name"]), bbl, "last sale", last["Date"]["date"]["start"], "prior" if prior else "")
        new_ids[bbl] = pid
        for s in sales:
            for d_ in s["properties"].get("Deals", {}).get("relation", []):
                deal_links[d_["id"]].add(pid)
    for deal_id, pids in deal_links.items():
        pids.discard(deal_id)
        req("PATCH", f"/pages/{deal_id}", {"properties": {"Comps used": {"relation": [{"id": i} for i in sorted(pids)]}}}, version=V)
        print(f"properties: deal {deal_id} -> Comps used {len(pids)}")
    # retire the old database
    try:
        req("PATCH", f"/databases/{comps_db}", {"archived": True}, version=V)
    except RuntimeError:
        req("DELETE", f"/blocks/{comps_db}", version=V)
    print("comps: old database moved to Trash")
    state["databases"]["comps_trashed"] = state["databases"].pop("comps")
    save_state(state)


def backfill(db_id: str) -> None:
    """Type/Strategy defaults, Last sale price/date from the old text, lot and building fields from PLUTO (idempotent)."""
    from screen import fnum, norm_bbl
    rows = _rows(db_id)
    bbls = {norm_bbl(_text(r["properties"].get("BBL"))): r for r in rows if _text(r["properties"].get("BBL"))
            and (r["properties"].get("Type", {}).get("select") or {}).get("name") in (None, "Deal", "Comp")}
    pluto = pluto_rows(set(bbls))
    for bbl, r in bbls.items():
        pr = r["properties"]
        patch: dict = {}
        if not pr.get("Type", {}).get("select"):
            patch["Type"] = {"select": {"name": "Comp" if pr.get("Last sale price", {}).get("number") and not pr.get("Ask", {}).get("number") and not pr.get("Verdict", {}).get("select") else "Deal"}}
        if not pr.get("Strategy", {}).get("select"):
            patch["Strategy"] = {"select": {"name": "Not set"}}
        old = _text(pr.get("Last sale"))
        if old and pr.get("Last sale price", {}).get("number") is None and not pr.get("Last sale date", {}).get("date"):
            m_date = re.search(r"\d{4}-\d{2}-\d{2}", old)
            m_amt = re.search(r"\$([\d,]+)", old)
            if m_date:
                patch["Last sale date"] = {"date": {"start": m_date.group(0)}}
            patch["Last sale price"] = {"number": int(m_amt.group(1).replace(",", "")) if m_amt else 0}
        p = pluto.get(bbl)
        if p:
            want = props_from_pluto(p)
            for k in ("Lot sf", "Existing sf", "Max ZFA", "Units"):
                if pr.get(k, {}).get("number") != want[k]["number"] and want[k]["number"] is not None:
                    patch[k] = want[k]
            for k in ("Building", "Lot"):
                if want[k]["rich_text"] and _text(pr.get(k)) != want[k]["rich_text"][0]["text"]["content"]:
                    patch[k] = want[k]
            if want.get("Location") and not pr.get("Location", {}).get("place"):
                patch["Location"] = want["Location"]
        if patch:
            req("PATCH", f"/pages/{r['id']}", {"properties": patch}, version=V)
            print("properties: backfilled", _text(pr["Name"]), sorted(patch))


def main():
    state = load_state()
    schema = dict(SCHEMA)
    db = ensure(state, schema)
    state["databases"]["properties"] = db
    save_state(state)
    migrate(state, db)       # adds the new fields (drop list only removes fields no longer holding needed data)
    backfill(db)             # carries Last sale text into price/date, Type/Strategy defaults, PLUTO fields
    merge_comps(state, db)   # old Comps rows -> Comp rows, relink, trash old database
    d = req("GET", f"/databases/{db}", version=V)["properties"]
    stale = {k: None for k in ("Last sale", "Comps") if k in d}
    _patch(db, stale, "dropped")
    print("properties", db, page_url(db))


if __name__ == "__main__":
    main()
