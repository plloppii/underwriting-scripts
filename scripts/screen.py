#!/usr/bin/env python
"""Stages 0 to 3 for one NYC property: PLUTO / ACRIS / DOB / HPD facts, the flood zone from FEMA's effective and the city's 2015
preliminary maps, zoning envelope, classification, closed comps from ACRIS with a renovation signal from DOB alteration jobs,
native map locations, and (with --notion) the deal row, comp rows and page body in the shared Properties database (see
docs/NOTION.md). Progress lines carry the seconds since start and are flushed as they happen, so a log file shows where a run is.

  python3 scripts/screen.py "63 Irving Place" --borough BK --ask 499999 --strategy Build --listing https://... --notion
  python3 scripts/screen.py --bbl 3019840005 --ask 499999 --out $TMPDIR/irving     # dry run: .md and .json; add --map-images for PNGs
  python3 scripts/screen.py --check                                                 # environment check

Python 3.10 or newer. Required: requests. Optional: matplotlib and pillow for the two maps (without them the run still produces
every fact, the comps and the pin list), GOOGLE_MAPS_API_KEY in the environment for the Street View photo and the
cafe/restaurant layer with ratings, NOTION_TOKEN for --notion. Maps are OpenStreetMap street tiles with the city's neighborhood
outlines and numbered pins, no text in the image; the pin list and the source caption become Notion blocks under the image.
Normal runs reuse the shared area directory; --refresh-area explicitly fetches area sources again. --map-images enables
legacy map image rendering. TILE_URL overrides the tile server for those explicit exports.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import shlex
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from comp_review import apply_review, listing_cell, read_review, review_template


def load_local_env(path: Path) -> None:
    """Read only known integration settings, without executing shell code or overriding exports."""
    if not path.is_file():
        return
    allowed = {"GOOGLE_MAPS_API_KEY", "GOOGLE_PLACES_API_KEY", "NOTION_TOKEN", "NOTION_API_TOKEN"}
    for line in path.read_text().splitlines():
        try:
            fields = shlex.split(line, comments=True)
        except ValueError:
            continue
        if fields[:1] == ["export"]:
            fields = fields[1:]
        if len(fields) == 1:
            name, separator, value = fields[0].partition("=")
            if separator and name in allowed and value:
                os.environ.setdefault(name, value)


load_local_env(ROOT / ".env")

NYC = "https://data.cityofnewyork.us/resource/"
NYS = "https://data.ny.gov/resource/"
DS = dict(pluto="64uk-42ks", legals="8h5j-fqxa", master="bnx9-e6tj", parties="636b-3b5g", dobviol="3h2n-5cm9",
          ecb="6bgk-3dad", bisjobs="ic3t-wcy2", dobnow="w9ak-ipjd", hpd="wvxf-dwi5", nta="9nt8-h7nd",
          parks="enfh-gkve", schools="wg9x-4ke6", subway="39hk-dx4f", buildings="u9wf-3gbt")
BORO_CODE = {"MN": "1", "BX": "2", "BK": "3", "QN": "4", "SI": "5"}
BORO_NAME = {"MN": "Manhattan", "BX": "Bronx", "BK": "Brooklyn", "QN": "Queens", "SI": "Staten Island"}
LOTTYPE = {"3": "Corner", "4": "Through", "5": "Interior"}
MI_PER_DEG_LAT = 69.0
FT_PER_MI = 5280.0
SESSION = requests.Session()
AGENT = os.environ.get("UW_AGENT", "Instinct")  # the assistant named in the open items it resolves
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))  # matplotlib's cache inside the checkout (gitignored)
POOL = 6  # Open Data requests in flight at once; a half-mile comp search went from two minutes to about 40 seconds at six
T0 = time.time()


def say(msg: str) -> None:
    """One progress line with the seconds since start, flushed at once so a log file shows where a long run is."""
    print(f"[{time.time() - T0:4.0f}s] {msg}", flush=True)


def _parallel(fn, items: list) -> list:
    """fn over items, POOL at a time, results in input order."""
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(POOL) as ex:
        return list(ex.map(fn, items))


def have_maps() -> bool:
    """matplotlib and pillow draw the maps; without them the run still produces the facts, comps and page text."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("matplotlib", "PIL"))
SESSION.headers["User-Agent"] = "nyc-zr-screen/0.1"


# ----------------------------------------------------------------------------- open data
def soda(ds: str, params: dict, host: str = NYC, tries: int = 4) -> list[dict]:
    params = {"$limit": 5000, **params}
    for attempt in range(tries):
        try:
            r = SESSION.get(host + ds + ".json", params=params, timeout=90)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"{ds} -> {r.status_code}: {r.text[:300]}")
        except requests.RequestException as e:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def fnum(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def norm_bbl(x) -> str:
    return str(int(float(x)))


def haversine_mi(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _rings(geom: dict | None) -> list[list]:
    """Exterior rings of a GeoJSON Polygon or MultiPolygon, each a list of [lon, lat] points."""
    if not geom:
        return []
    if geom.get("type") == "Polygon":
        return [geom["coordinates"][0]]
    if geom.get("type") == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def contains(geom: dict | None, lon: float, lat: float) -> bool:
    """Point in polygon by ray casting over the exterior rings (holes ignored: enough to name a neighborhood)."""
    inside = False
    for ring in _rings(geom):
        for a, b in zip(ring, ring[1:]):
            x1, y1, x2, y2 = a[0], a[1], b[0], b[1]
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def rep_point(geom: dict) -> tuple[float, float]:
    """(lat, lon) of the vertex average of the largest ring: a point on or near the shape, good enough for a distance."""
    ring = max(_rings(geom), key=len)
    return sum(pt[1] for pt in ring) / len(ring), sum(pt[0] for pt in ring) / len(ring)


def nearest_park_point(geom: dict, lat: float, lon: float) -> tuple[float, float]:
    """Closest park boundary in a local distance projection; zero distance inside a park.

    This is an edge, not a verified entrance or a walking route. All polygon components
    and interior rings participate, so a disconnected park is measured to its near part.
    """
    polygons = [geom["coordinates"]] if geom.get("type") == "Polygon" else geom.get("coordinates", [])
    sx = math.cos(math.radians(lat))
    best = None
    for polygon in polygons:
        if not polygon:
            continue
        def inside(ring):
            return contains({"type": "Polygon", "coordinates": [ring]}, lon, lat)
        if inside(polygon[0]) and not any(inside(ring) for ring in polygon[1:]):
            return lat, lon
        for ring in polygon:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                ax, ay = (a[0] - lon) * sx, a[1] - lat
                bx, by = (b[0] - lon) * sx, b[1] - lat
                dx, dy = bx - ax, by - ay
                denom = dx * dx + dy * dy
                t = max(0, min(1, -(ax * dx + ay * dy) / denom)) if denom else 0
                x, y = ax + t * dx, ay + t * dy
                candidate = (x * x + y * y, lat + y, lon + x / sx)
                if best is None or candidate[0] < best[0]:
                    best = candidate
    if best is None:
        raise ValueError("Park geometry has no boundary")
    return best[1], best[2]


def bbox(lat, lon, radius_mi):
    d = radius_mi / MI_PER_DEG_LAT
    dlon = radius_mi / (MI_PER_DEG_LAT * math.cos(math.radians(lat)))
    return lat - d, lat + d, lon - dlon, lon + dlon


def pluto_by_address(address: str, borough: str) -> dict | None:
    addr = re.sub(r"\s+", " ", address.strip().upper())
    addr = re.sub(r"\bPL\b", "PLACE", addr)
    addr = re.sub(r"\bAVE\b", "AVENUE", addr)
    addr = re.sub(r"\bST\b", "STREET", addr)
    addr = re.sub(r"\bBLVD\b", "BOULEVARD", addr)
    rows = soda(DS["pluto"], {"$where": f"upper(address)='{addr}' AND borough='{borough}'"})
    if not rows:
        num, _, rest = addr.partition(" ")
        rows = soda(DS["pluto"], {"$where": f"upper(address) like '{num} {rest.split(' ')[0]}%' AND borough='{borough}'"})
    return rows[0] if rows else None


def pluto_by_bbl(bbl: str) -> dict | None:
    rows = soda(DS["pluto"], {"$where": f"bbl={int(bbl)}"})
    return rows[0] if rows else None


# ----------------------------------------------------------------------------- facts
def acris_deeds(boro: str, pairs: list[tuple[str, str]], since_iso: str | None, min_amt: float = 0) -> list[dict]:
    """Deeds for (block, lot) pairs: [{block, lot, document_id, date, amt, lots_in_doc}]."""
    by_block: dict[str, list[str]] = {}
    for b, l in pairs:
        by_block.setdefault(str(int(b)), []).append(str(int(l)))
    # one legals query per block (60 lots at a time), then the master records for those documents, POOL queries at once
    queries = [(block, lots[k:k + 60]) for block, lots in by_block.items() for k in range(0, len(lots), 60)]

    def legals_for(q: tuple[str, list[str]]) -> list[dict]:
        block, lots = q
        chunk = ",".join(f"'{x}'" for x in lots)
        return soda(DS["legals"], {"$where": f"borough='{boro}' AND block='{block}' AND lot in ({chunk})", "$select": "document_id,block,lot"})

    def deeds_for(chunk_ids: list[str]) -> list[dict]:
        chunk = ",".join(f"'{x}'" for x in chunk_ids)
        where = f"document_id in ({chunk}) AND doc_type='DEED'"
        if since_iso:
            where += f" AND document_date >= '{since_iso}T00:00:00.000'"
        if min_amt:
            where += f" AND document_amt >= {min_amt}"
        return soda(DS["master"], {"$where": where, "$select": "document_id,document_date,document_amt,recorded_datetime"})

    legals = [x for rows in _parallel(legals_for, queries) for x in rows]
    ids = sorted({x["document_id"] for x in legals})
    deeds = {m["document_id"]: m for rows in _parallel(deeds_for, [ids[k:k + 150] for k in range(0, len(ids), 150)]) for m in rows}
    # count how many lots each document covers (multi-lot sales)
    lots_per_doc: dict[str, set] = {}
    for x in legals:
        lots_per_doc.setdefault(x["document_id"], set()).add((x["block"], x["lot"]))
    out = []
    for x in legals:
        m = deeds.get(x["document_id"])
        if not m:
            continue
        out.append({"block": str(int(x["block"])), "lot": str(int(x["lot"])), "document_id": x["document_id"],
                    "date": (m.get("document_date") or m.get("recorded_datetime") or "")[:10],
                    "amt": fnum(m.get("document_amt"), 0.0), "lots_in_doc": len(lots_per_doc[x["document_id"]])})
    return out


def acris_parties(doc_id: str) -> dict:
    rows = soda(DS["parties"], {"$where": f"document_id='{doc_id}'", "$select": "party_type,name"})
    out = {"seller": [], "buyer": []}
    for r in rows:
        (out["seller"] if r.get("party_type") == "1" else out["buyer"]).append(r.get("name", "").strip())
    return out


def dob_facts(boro: str, block: str, lot: str, bbl: str) -> dict:
    b5, l5, l4 = block.zfill(5), lot.zfill(5), lot.zfill(4)
    viol = soda(DS["dobviol"], {"$where": f"boro='{boro}' AND block='{b5}' AND lot='{l5}'",
                                "$select": "bin,issue_date,violation_category,violation_type,disposition_date"})
    ecb = soda(DS["ecb"], {"$where": f"boro='{boro}' AND block='{b5}' AND lot='{l4}'",
                           "$select": "bin,issue_date,ecb_violation_status,severity,violation_description,balance_due"})
    bis = soda(DS["bisjobs"], {"$where": f"bbl='{bbl}'",
                               "$select": "job__,job_type,job_status_descrp,pre__filing_date,latest_action_date,proposed_dwelling_units,proposed_zoning_sqft,bin__,job_description"})
    now = soda(DS["dobnow"], {"$where": f"bbl='{bbl}'",
                              "$select": "job_filing_number,job_type,filing_status,filing_date,approved_date,signoff_date,proposed_dwelling_units,total_construction_floor_area,bin,job_description"})
    hpd = soda(DS["hpd"], {"$where": f"bbl='{bbl}'", "$select": "class,violationstatus,novissueddate,novdescription,registrationid,bin,buildingid"})
    bins = {x.get("bin") for x in viol} | {x.get("bin") for x in ecb} | {x.get("bin__") for x in bis} | {x.get("bin") for x in now} | {x.get("bin") for x in hpd}
    bins = sorted(str(b) for b in bins if b and re.fullmatch(r"[1-5]\d{6}", str(b)) and str(b)[1:] != "000000")
    bin_source, bin_error = "DOB / HPD", None
    if not bins:
        try:
            footprints = soda(DS["buildings"], {"$where": f"base_bbl='{bbl}'", "$select": "bin,base_bbl"})
            bins = sorted({str(r["bin"]) for r in footprints if re.fullmatch(r"[1-5]\d{6}", str(r.get("bin", ""))) and str(r["bin"])[1:] != "000000"})
            bin_source = "NYC building footprints" if bins else None
        except (RuntimeError, requests.RequestException) as e:
            bin_source, bin_error = None, type(e).__name__
    open_dob = [v for v in viol if "DISMISSED" not in (v.get("violation_category") or "").upper() and not v.get("disposition_date")]
    open_ecb = [v for v in ecb if (v.get("ecb_violation_status") or "").upper() not in ("RESOLVE", "RESOLVED", "DISMISSED")]
    ecb_balance = sum(fnum(v.get("balance_due"), 0.0) for v in ecb)
    hpd_open = [v for v in hpd if (v.get("violationstatus") or "").lower() == "open"]
    hpd_by_class = {c: sum(1 for v in hpd_open if v.get("class") == c) for c in ("A", "B", "C")}
    active_now = [j for j in now if (j.get("filing_status") or "") not in ("Signed Off", "Withdrawn", "Expired", "LOC Issued")]
    return {"bins": bins, "bin_source": bin_source, "bin_error": bin_error, "dob_viol_total": len(viol), "dob_viol_open": len(open_dob), "ecb_total": len(ecb),
            "ecb_open": len(open_ecb), "ecb_balance": ecb_balance, "bis_jobs": bis, "dobnow_jobs": now,
            "hpd_total": len(hpd), "hpd_open": len(hpd_open), "hpd_open_by_class": hpd_by_class,
            "hpd_registered": any(v.get("registrationid") for v in hpd), "active_dobnow": active_now,
            "hpd_building_id": next((v["buildingid"] for v in hpd if v.get("buildingid")), None)}


def near_corner_estimate(p: dict) -> dict | None:
    """Distance from the subject to the nearest corner lot on the same block, estimated along the street from PLUTO
    centroids. Assumed; the survey decides."""
    if p.get("lottype") == "3":
        return {"corner_lot": True}
    rows = soda(DS["pluto"], {"$where": f"borough='{p['borough']}' AND block='{p['block']}'",
                              "$select": "lot,address,lottype,lotfront,lotdepth,latitude,longitude"})
    lat, lon = fnum(p.get("latitude")), fnum(p.get("longitude"))
    best = None
    for r in rows:
        if r.get("lottype") != "3" or r.get("lot") == p.get("lot"):
            continue
        rl, rn = fnum(r.get("latitude")), fnum(r.get("longitude"))
        if None in (lat, lon, rl, rn):
            continue
        d_ft = haversine_mi(lat, lon, rl, rn) * FT_PER_MI
        if best is None or d_ft < best["centroid_ft"]:
            best = {"centroid_ft": d_ft, "corner_address": r.get("address"), "corner_front": fnum(r.get("lotfront"), 0.0)}
    if not best:
        return None
    front = fnum(p.get("lotfront"), 0.0)
    depth = fnum(p.get("lotdepth"), 0.0)
    near_ft = max(0.0, best["centroid_ft"] + best["corner_front"] / 2 - front / 2)
    far_radial = math.hypot(near_ft + front, depth)
    best.update({"near_ft": round(near_ft), "far_radial_ft": round(far_radial), "whole_lot_within_100": far_radial <= 100,
                 "part_within_100": near_ft <= 100, "corner_lot": False})
    return best


# ----------------------------------------------------------------------------- flood
FEMA_NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"  # effective maps: layer 28, Flood Hazard Zones
NYC_PFIRM = "https://services1.arcgis.com/8cuieNI8NbqQZQVJ/arcgis/rest/services/PFIRM/FeatureServer/0/query"  # FEMA's 2015 preliminary maps, NYC Emergency Management's copy


def _arcgis_point(url: str, lat: float, lon: float, fields: str, tries: int = 4) -> list[dict] | None:
    """Attributes of the polygons under a point in one ArcGIS REST layer; [] when none, None when the service is unreachable."""
    params = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
              "outFields": fields, "returnGeometry": "false", "f": "json"}
    for attempt in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=60)
            if r.status_code == 200 and "features" in (j := r.json()):
                return [f["attributes"] for f in j["features"]]
        except (requests.RequestException, ValueError):
            pass
        if attempt < tries - 1:
            time.sleep(2 * (attempt + 1))
    return None


def _zone(attrs: list[dict]) -> dict:
    a = sorted(attrs, key=lambda x: x.get("SFHA_TF") != "T")[0]  # on a boundary, the stricter polygon
    bfe = fnum(a.get("STATIC_BFE"))
    return {"zone": (a.get("FLD_ZONE") or "").strip() or None, "subtype": (a.get("ZONE_SUBTY") or "").strip() or None,
            "sfha": a.get("SFHA_TF") == "T", "bfe": bfe if bfe is not None and bfe > -9999 else None}


def flood_zone(lat: float, lon: float) -> dict:
    """Flood zone under the lot from two map services, no key: FEMA's effective National Flood Hazard Layer, which sets flood
    insurance, and NYC Emergency Management's copy of FEMA's 2015 preliminary maps, which the Building Code uses for the design
    flood elevation of new construction. Keys: zone, subtype, sfha, bfe (effective); pzone, psfha, pbfe (preliminary);
    effective_ok and preliminary_ok say whether each service answered."""
    eff = _arcgis_point(FEMA_NFHL, lat, lon, "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE")
    pre = _arcgis_point(NYC_PFIRM, lat, lon, "FLD_ZONE,SFHA_TF,STATIC_BFE")
    out = {"effective_ok": eff is not None, "preliminary_ok": pre is not None, "zone": None, "subtype": None, "sfha": False, "bfe": None,
           "pzone": None, "psfha": False, "pbfe": None}
    if eff:
        out.update(_zone(eff))
    if pre:
        z = _zone(pre)
        out.update(pzone=z["zone"], psfha=z["sfha"], pbfe=z["bfe"])
    return out


def flood_text(fl: dict) -> str:
    """The Flood row's Finding, in words, or "" when nothing is mapped (unshaded X with no preliminary zone, or no answer)."""
    if fl.get("sfha"):
        return f"zone {fl['zone']}, high risk · flood insurance required" + (f" · base flood elevation {fl['bfe']:g} ft" if fl.get("bfe") else "")
    if fl.get("psfha"):
        now = f"zone {fl['zone']} now" if fl.get("zone") else "no zone now"
        return f"{now}, {fl['pzone']} on 2015 preliminary maps · no insurance required yet"
    if fl.get("zone") and "0.2 PCT" in (fl.get("subtype") or ""):
        return f"zone {fl['zone']} shaded, moderate risk · no insurance required"
    return ""


# ----------------------------------------------------------------------------- envelope + classify
def envelope(p: dict, corner: dict | None) -> dict:
    lotarea = fnum(p.get("lotarea"), 0.0)
    far = fnum(p.get("residfar"), 0.0)
    bldg = fnum(p.get("bldgarea"), 0.0)
    max_zfa = lotarea * far
    unused = max_zfa - bldg
    U = (bldg / max_zfa) if max_zfa else None
    flags = []
    if p.get("histdist"):
        flags.append("Historic district")
    if p.get("landmark"):
        flags.append("Landmark")
    depth = fnum(p.get("lotdepth"), 0.0)
    front = fnum(p.get("lotfront"), 0.0)
    if 0 < depth < 95 and p.get("lottype") != "3":
        flags.append("Shallow lot")
    if corner and not corner.get("corner_lot") and (corner.get("part_within_100")):
        flags.append("Near corner")
    if 0 < front < 18:
        flags.append("Narrow lot")
    if str(p.get("irrlotcode")).lower() == "true":
        flags.append("Irregular lot")
    owner = (p.get("ownername") or "").upper()
    if owner.startswith("NYC ") or "CITY OF NEW YORK" in owner or "HOUSING PRESERVATION" in owner:
        flags.append("City owned")
    zone = p.get("zonedist1", "")
    r6plus = bool(re.match(r"R(6|7|8|9|1[0-2])", zone))
    return {"lotarea": lotarea, "far": far, "bldgarea": bldg, "max_zfa": max_zfa, "unused": unused, "U": U,
            "flags": flags, "lot_type": LOTTYPE.get(p.get("lottype", ""), "Other"), "zone": zone,
            "far_cite": "23-22" if r6plus else "23-21", "height_cite": "23-432" if r6plus else "23-42",
            "coverage_cite": "23-362" if r6plus else "23-361", "yard_cite": "23-342", "density_cite": "23-52",
            "coverage_text": ("100% corner, 80% interior/through" if r6plus else "see 23-361"),
            "height_text": {"R6B": "base 30-45 ft, max 55 ft; 65 ft affordable", "R6A": "base 40-65 ft, max 75 ft",
                            "R6": "base 30-45 ft, max 55 ft (narrow street)", "R7A": "base 40-75 ft, max 85 ft",
                            "R7B": "base 40-65 ft, max 75 ft", "R7D": "base 60-85 ft, max 105 ft",
                            "R8A": "base 60-95 ft, max 125 ft", "R8B": "base 55-65 ft, max 75 ft"}.get(zone, "see table")}


def classify(p: dict, env: dict, dob: dict, flood: dict | None = None) -> dict:
    U = env["U"]
    units = int(fnum(p.get("unitsres"), 0) or 0)
    year = int(fnum(p.get("yearbuilt"), 0) or 0)
    vacant = (p.get("bldgclass") or "").startswith("V") or env["bldgarea"] == 0
    historic = "Historic district" in env["flags"] or "Landmark" in env["flags"]
    # A
    if vacant or (U is not None and U < 0.4):
        a = "Development or enlargement site"
    elif U is not None and U <= 0.8:
        a = "Enlargement possible; renovation economics"
    else:
        a = "Renovation or income; land value irrelevant"
    # B
    b = "No demolition (LPC); development only if vacant" if historic else "Demolition and new building are options"
    # C
    reg_signal = units >= 6 and 0 < year < 1974
    if units > 0 and reg_signal:
        c = "Income in place likely regulated (6+ units, pre-1974); lawful rents"
    elif units > 0:
        c = "Occupied status unknown; confirm leases and rent-regulation fee on tax bill"
    else:
        c = "No units; vacant delivery"
    # branch
    zfa = env["max_zfa"]
    if vacant:
        deal = "Development"
    elif historic and U is not None and U < 0.8:
        deal = "Enlargement"
    elif U is not None and U < 0.4:
        deal = "Development"
    elif U is not None and U <= 0.8:
        deal = "Enlargement"
    elif units >= 5:
        deal = "Income"
    else:
        deal = "Renovation"
    if zfa < 2500:
        product = "single-family or two-family house"
    elif zfa <= 6000:
        product = "2-4 family"
    else:
        product = "5+ units (condo or rental building)"
    exits = {"Development": ["Sell house", "House hack"] if zfa < 2500 else (["Sell house", "Rent + refi", "House hack"] if zfa <= 6000 else ["Sell condos", "Rent + refi"]),
             "Enlargement": ["Sell house", "Rent + refi", "House hack"],
             "Renovation": ["House hack", "Sell house", "Rent + refi"],
             "Income": ["Hold", "Rent + refi"]}[deal]
    flags = list(env["flags"])
    if reg_signal:
        flags.append("Rent regulated?")
    if dob["dob_viol_open"] or dob["ecb_open"]:
        flags.append("Open DOB violations")
    if dob["hpd_open"]:
        flags.append("Open HPD violations")
    if dob["active_dobnow"]:
        flags.append("Active DOB job")
    if flood and (flood.get("sfha") or flood.get("psfha")):
        flags.append("Flood zone")
    return {"A": a, "B": b, "C": c, "deal_type": deal, "product": product, "exits": exits, "flags": flags,
            "vacant": vacant, "units": units, "year": year}


# ----------------------------------------------------------------------------- comps
def product_family(bldgclass: str, units: int) -> tuple[str, list[str], str]:
    """(product label, PLUTO class prefixes to match, area basis)"""
    c = (bldgclass or "")[:2]
    if c.startswith("V"):
        return "Vacant lot", ["V"], "Buildable zoning"
    if c.startswith("A"):
        return "1-family", ["A", "B"], "PLUTO building"
    if c.startswith("B"):
        return "2-family", ["A", "B"], "PLUTO building"
    if c in ("C0", "C3") or c.startswith("S3") or c.startswith("S4") or (c.startswith("C") and units <= 4):
        return "3-4 family", ["C0", "C3", "B"], "PLUTO building"
    if c.startswith("C") or c.startswith("S"):
        return "5+ walk-up", ["C", "S"], "PLUTO building"
    if c.startswith("D"):
        return "Elevator building", ["D"], "PLUTO building"
    return "Other", [c[:1]], "PLUTO building"


def find_comps(p: dict, env: dict, cls: dict, radius_mi: float, months: int, min_amt: float = 100000) -> list[dict]:
    lat, lon = fnum(p["latitude"]), fnum(p["longitude"])
    la, lb, lo, lhi = bbox(lat, lon, radius_mi)
    product, prefixes, basis = product_family(p.get("bldgclass", ""), cls["units"])
    like = " OR ".join(f"bldgclass like '{x}%'" for x in prefixes)
    rows = soda(DS["pluto"], {"$where": f"latitude between {la} and {lb} AND longitude between {lo} and {lhi} AND ({like}) AND lotarea > 400",
                              "$select": "address,bbl,block,lot,lotarea,lotfront,lotdepth,bldgarea,unitsres,zonedist1,residfar,bldgclass,latitude,longitude,ownername,histdist"})
    cands = {}
    for r in rows:
        owner = (r.get("ownername") or "").upper()
        if owner.startswith("NYC ") or "CITY OF NEW YORK" in owner or "HOUSING" in owner or "MTA" in owner:
            continue
        if norm_bbl(r["bbl"]) == norm_bbl(p["bbl"]):
            continue
        cands[(str(int(r["block"])), str(int(r["lot"])))] = r
    since = (dt.date.today() - dt.timedelta(days=30 * months)).isoformat()
    say(f"comps: {len(cands)} candidate lots on {len({b for b, _ in cands})} blocks; reading their ACRIS deeds since {since}")
    deeds = acris_deeds(BORO_CODE[p["borough"]], list(cands), since, min_amt)
    out = []
    for d in deeds:
        r = cands.get((d["block"], d["lot"]))
        if not r:
            continue
        lotarea = fnum(r.get("lotarea"), 0.0)
        far = fnum(r.get("residfar"), 0.0)
        bldg = fnum(r.get("bldgarea"), 0.0)
        area = lotarea * far if basis == "Buildable zoning" else bldg
        dist = haversine_mi(lat, lon, fnum(r["latitude"]), fnum(r["longitude"]))
        out.append({"address": (r.get("address") or "").title(), "bbl": norm_bbl(r["bbl"]), "block": d["block"], "lot": d["lot"],
                    "date": d["date"], "price": d["amt"], "area": area, "basis": basis, "lot_sf": lotarea,
                    "lot_dims": f"{fnum(r.get('lotfront'), 0):g} × {fnum(r.get('lotdepth'), 0):g}" if fnum(r.get("lotfront"), 0) and fnum(r.get("lotdepth"), 0) else "",
                    "bldg_sf": bldg, "units": int(fnum(r.get("unitsres"), 0) or 0), "zone": r.get("zonedist1", ""),
                    "bldgclass": r.get("bldgclass", ""), "dist": dist, "lat": fnum(r["latitude"]), "lon": fnum(r["longitude"]),
                    "doc": d["document_id"], "multi_lot": d["lots_in_doc"] > 1, "product": product,
                    "psf": (d["amt"] / area) if area else None, "historic": bool(r.get("histdist"))})
    # group lots sold on one deed into one comp (combined area), then one row per lot group: latest deed
    by_doc: dict[str, list[dict]] = {}
    for c in out:
        by_doc.setdefault(c["doc"], []).append(c)
    grouped = []
    for doc, items in by_doc.items():
        items.sort(key=lambda c: c["address"])
        g = dict(items[0])
        if len(items) > 1:
            g["address"] = " + ".join(dict.fromkeys(i["address"] for i in items)) + f" ({len(items)} lots)"
            g["area"] = sum(i["area"] for i in items)
            g["lot_sf"] = sum(i["lot_sf"] for i in items)
            g["lot_dims"] = ""
            g["bldg_sf"] = sum(i["bldg_sf"] for i in items)
            g["units"] = sum(i["units"] for i in items)
            g["dist"] = min(i["dist"] for i in items)
            g["psf"] = (g["price"] / g["area"]) if g["area"] else None
        g["partial_doc"] = g["multi_lot"] and len(items) < max(1, next(d["lots_in_doc"] for d in deeds if d["document_id"] == doc))
        g["multi_lot"] = len(items) > 1 or g["partial_doc"]
        grouped.append(g)
    best: dict[str, dict] = {}
    for c in grouped:
        if c["bbl"] not in best or c["date"] > best[c["bbl"]]["date"]:
            best[c["bbl"]] = c
    # scale filter: comparable size, within 0.1x to 10x of the subject's area on the same basis
    subj_area = (fnum(p.get("lotarea"), 0.0) * fnum(p.get("residfar"), 0.0)) if basis == "Buildable zoning" else fnum(p.get("bldgarea"), 0.0)
    comps = [c for c in best.values() if not subj_area or (c["area"] and 0.1 * subj_area <= c["area"] <= 10 * subj_area)]
    comps.sort(key=lambda c: c["dist"])
    return comps


def _job_date(s: str | None) -> dt.date | None:
    """BIS dates are MM/DD/YYYY, DOB NOW dates ISO; either one to a date."""
    if not s:
        return None
    try:
        return dt.datetime.strptime(s[:10], "%m/%d/%Y").date() if "/" in s[:10] else dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


RENO_STATUS = {"loc issued": "letter of completion issued", "signed off": "signed off", "approved": "approved", "plan exam - approved": "approved",
               "permit entire": "permitted", "permit issued": "permitted", "permit issued - entire job/work": "permitted", "permit issued - partial job": "permitted"}


def renovation_signal(comps: list[dict], months_before: int = 36, lead_days: int = 180) -> None:
    """DOB alteration jobs (BIS A1 and A2, DOB NOW Alteration) filed in the three years before a comp's deed and at least six
    months ahead of it, so they are the seller's work rather than the buyer's: a sign the sale was renovated, for the listing
    pass to confirm. Work done without permits leaves no signal. Sets c["reno"] to the Listing-column text, or ""."""
    def one(c: dict) -> str:
        sold = _job_date(c.get("date"))
        if not sold:
            return ""
        lo, hi = sold - dt.timedelta(days=30 * months_before), sold - dt.timedelta(days=lead_days)
        jobs: list[tuple[dt.date, str, str]] = []
        for j in soda(DS["bisjobs"], {"$where": f"bbl='{c['bbl']}' AND job_type in ('A1','A2')", "$select": "job_type,job_status_descrp,pre__filing_date"}):
            d = _job_date(j.get("pre__filing_date"))
            if d and lo <= d <= hi:
                jobs.append((d, j["job_type"], (j.get("job_status_descrp") or "").lower()))
        for j in soda(DS["dobnow"], {"$where": f"bbl='{c['bbl']}' AND job_type like 'Alteration%'", "$select": "job_type,filing_status,filing_date"}):
            d = _job_date(j.get("filing_date"))
            if d and lo <= d <= hi:
                jobs.append((d, "Alteration", (j.get("filing_status") or "").lower()))
        jobs = sorted(j for j in jobs if "withdrawn" not in j[2] and "expired" not in j[2])
        if not jobs:
            return ""
        d, kind, status = jobs[-1]
        what = f"{len(jobs)} alteration jobs, latest" if len(jobs) > 1 else f"{kind} job"
        return f"renovation signal: {what} filed {_month(d.isoformat())}, {RENO_STATUS.get(status, status)}"

    for c, note in zip(comps, _parallel(one, comps)):
        c["reno"] = note


# ----------------------------------------------------------------------------- maps
def nta_polygons(lat, lon, radius_mi):
    la, lb, lo, lhi = bbox(lat, lon, radius_mi * 1.2)
    poly = f"POLYGON(({lo} {la},{lhi} {la},{lhi} {lb},{lo} {lb},{lo} {la}))"
    return soda(DS["nta"], {"$where": f"intersects(the_geom, '{poly}')", "$select": "ntaname,nta2020,the_geom"})


def is_food_primary(kind: str | None) -> bool:
    """A food business's primary type; secondary cafes inside retail stores do not qualify."""
    return bool(kind and (kind in {"restaurant", "cafe", "bakery", "coffee_shop", "tea_house"} or kind.endswith("_restaurant")))


def places_food(lat: float, lon: float, radius_m: int = 800, limit: int = 5) -> list[dict] | None:
    """Cafes, restaurants and bakeries with ratings from Google Places (New). None when no key is set."""
    key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    r = SESSION.post("https://places.googleapis.com/v1/places:searchNearby", timeout=30,
                     headers={"X-Goog-Api-Key": key, "Content-Type": "application/json",
                              "X-Goog-FieldMask": "places.displayName,places.location,places.rating,places.userRatingCount,places.primaryType"},
                     json={"includedTypes": ["restaurant", "cafe", "bakery"], "maxResultCount": 20, "rankPreference": "POPULARITY",
                           "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": radius_m}}})
    if r.status_code >= 400:
        raise RuntimeError(f"places -> {r.status_code}: {r.text[:200]}")
    out = []
    for pl in r.json().get("places", []):
        if not pl.get("rating") or not is_food_primary(pl.get("primaryType")):
            continue
        out.append({"name": pl["displayName"]["text"], "kind": (pl.get("primaryType") or "restaurant").replace("_", " "),
                    "rating": pl["rating"], "ratings": int(pl.get("userRatingCount") or 0),
                    "lat": pl["location"]["latitude"], "lon": pl["location"]["longitude"]})
    return sorted(out, key=lambda f: -(f["rating"] * math.log10(f["ratings"] + 1)))[:limit]


def area_layers(p: dict, radius_mi: float) -> dict:
    """Every map layer; a layer that fails is recorded in `failed` and left empty instead of stopping the run."""
    lat, lon = fnum(p["latitude"]), fnum(p["longitude"])
    la, lb, lo, lhi = bbox(lat, lon, radius_mi)
    poly = f"POLYGON(({lo} {la},{lhi} {la},{lhi} {lb},{lo} {lb},{lo} {la}))"
    out: dict = {"failed": []}

    def layer(name, fn, default):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            print(f"layer {name} failed:", str(e)[:160])
            out["failed"].append(name)
            out[name] = default

    layer("subway", lambda: soda(DS["subway"], {"$where": f"gtfs_latitude::number between {la} and {lb} AND gtfs_longitude::number between {lo} and {lhi}",
                                                "$select": "stop_name,daytime_routes,gtfs_latitude,gtfs_longitude"}, host=NYS), [])
    layer("parks", lambda: soda(DS["parks"], {"$where": f"intersects(multipolygon, '{poly}') AND typecategory in ('Neighborhood Park','Community Park','Flagship Park','Playground','Garden','Triangle/Plaza')",
                                              "$select": "signname,typecategory,acres,multipolygon"}), [])
    layer("food", lambda: places_food(lat, lon), None)
    if out["food"] is None and "food" not in out["failed"]:
        out["failed"].append("food: no Google key")
    layer("ntas", lambda: nta_polygons(lat, lon, radius_mi), [])
    return out


def shared_area_layers(p: dict, radius_mi: float, from_notion: bool = False) -> dict:
    """Reuse the shared amenity directory; no Places search or map tile download."""
    catalog_path = ROOT / "data" / "amenities.json"
    records = json.loads(catalog_path.read_text()) if catalog_path.exists() else []
    if from_notion:
        from notion_api import load_state, req
        db = load_state()["databases"]["properties"]
        geometry = {r["source_id"]: r for r in records if r["type"] == "Park"}
        records, cursor = [], None
        while True:
            query = {"page_size": 100, "filter": {"or": [{"property": "Type", "select": {"equals": t}}
                     for t in ("Restaurant", "Park", "Subway station")]}}
            if cursor:
                query["start_cursor"] = cursor
            response = req("POST", f"/databases/{db}/query", query, version="2022-06-28")
            for row in response["results"]:
                props = row["properties"]
                location = props.get("Location", {}).get("place")
                if not location:
                    continue
                def plain(name):
                    return "".join(x.get("plain_text", "") for x in props.get(name, {}).get("rich_text", []))
                sid = plain("Source ID") or "notion:" + row["id"]
                record = dict(geometry.get(sid, {}))
                record.update({"source_id": sid, "name": "".join(x.get("plain_text", "") for x in props["Name"]["title"]),
                               "type": props["Type"]["select"]["name"], "lat": location["lat"], "lon": location["lon"]})
                records.append(record)
            if not response.get("has_more"):
                break
            cursor = response["next_cursor"]
    lat, lon = fnum(p["latitude"]), fnum(p["longitude"])
    la, lb, lo, hi = bbox(lat, lon, radius_mi)
    near = [r for r in records if la <= r["lat"] <= lb and lo <= r["lon"] <= hi]
    stations = [{"stop_name": re.sub(r"\s*\(.*?\)$", "", r["name"]),
                 "daytime_routes": r.get("routes") or (re.search(r"\((.*?)\)$", r["name"])[1] if re.search(r"\((.*?)\)$", r["name"]) else ""),
                 "gtfs_latitude": r["lat"], "gtfs_longitude": r["lon"]} for r in near if r["type"] == "Subway station"]
    food = [{"name": r["name"], "kind": "restaurant", "lat": r["lat"], "lon": r["lon"]} for r in near if r["type"] == "Restaurant"]
    food.sort(key=lambda f: haversine_mi(lat, lon, f["lat"], f["lon"]))
    parks = []
    for record in records:
        if record["type"] != "Park" or not record.get("geometry"):
            continue
        edge_lat, edge_lon = nearest_park_point(record["geometry"], lat, lon)
        if haversine_mi(lat, lon, edge_lat, edge_lon) <= radius_mi * math.sqrt(2):
            parks.append({"signname": record["name"], "acres": record.get("acres"), "multipolygon": record["geometry"]})
    return {"subway": stations, "food": food[:5], "parks": parks, "ntas": nta_polygons(lat, lon, radius_mi),
            "food_source": "shared place directory", "failed": []}


TILE_URL = os.environ.get("TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_CREDIT = "OpenStreetMap tiles © OpenStreetMap contributors"
TILE_CACHE = ROOT / ".cache" / "tiles"
ZOOM = 16
PIN_COLORS = {"subway": "#1f5fbf", "food": "#c0392b", "park": "#2e8b57", "comp": "#222222"}  # three area groups + comps


def _tile_xy(lat: float, lon: float, z: int = ZOOM) -> tuple[float, float]:
    """Fractional Web Mercator tile coordinates."""
    n = 2 ** z
    lat_r = math.radians(lat)
    return (lon + 180.0) / 360.0 * n, (1.0 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2.0 * n


def _fetch_tile(z: int, x: int, y: int):
    from PIL import Image
    TILE_CACHE.mkdir(parents=True, exist_ok=True)
    f = TILE_CACHE / f"{z}-{x}-{y}.png"
    if not f.exists():
        r = SESSION.get(TILE_URL.format(z=z, x=x, y=y), timeout=30)
        r.raise_for_status()
        f.write_bytes(r.content)
    return Image.open(f).convert("RGB")


class Basemap:
    """Street tiles stitched over a square of radius_mi around the subject, north up; converts lat/lon to pixels."""

    def __init__(self, lat0: float, lon0: float, radius_mi: float, z: int = ZOOM, pad: float = 1.08):
        from PIL import Image
        self.lat0, self.lon0, self.radius_mi, self.z = lat0, lon0, radius_mi, z
        self.dlat = radius_mi / MI_PER_DEG_LAT
        self.dlon = radius_mi / (MI_PER_DEG_LAT * math.cos(math.radians(lat0)))
        x0, y0 = _tile_xy(lat0 + self.dlat * pad, lon0 - self.dlon * pad, z)  # north-west corner
        x1, y1 = _tile_xy(lat0 - self.dlat * pad, lon0 + self.dlon * pad, z)  # south-east corner
        self.tx0, self.ty0 = math.floor(x0), math.floor(y0)
        tx1, ty1 = math.floor(x1), math.floor(y1)
        img = Image.new("RGB", ((tx1 - self.tx0 + 1) * 256, (ty1 - self.ty0 + 1) * 256), "white")
        self.tiles = self.failed = 0
        for tx in range(self.tx0, tx1 + 1):
            for ty in range(self.ty0, ty1 + 1):
                try:
                    img.paste(_fetch_tile(z, tx, ty), ((tx - self.tx0) * 256, (ty - self.ty0) * 256))
                    self.tiles += 1
                except Exception as e:  # noqa: BLE001
                    self.failed += 1
                    print("tile failed:", z, tx, ty, e)
        self.crop = ((x0 - self.tx0) * 256, (y0 - self.ty0) * 256)
        self.image = img.crop((int(self.crop[0]), int(self.crop[1]), int((x1 - self.tx0) * 256), int((y1 - self.ty0) * 256)))

    def px(self, lat: float, lon: float) -> tuple[float, float]:
        x, y = _tile_xy(lat, lon, self.z)
        return (x - self.tx0) * 256 - self.crop[0], (y - self.ty0) * 256 - self.crop[1]


_PINS: list[tuple[float, float]] = []


def _figure(bm: Basemap):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    w, h = bm.image.size
    fig = plt.figure(figsize=(w / 150, h / 150), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(bm.image, interpolation="bilinear", zorder=0)
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    return fig, ax, plt


def _draw_frame(ax, bm: Basemap, ntas: list[dict]) -> None:
    """Neighborhood outlines, the dashed half-mile frame and the subject star. No text anywhere on the image."""
    import matplotlib.patches as mpatches
    for n in ntas:
        for ring in _rings(n["the_geom"]):
            xs, ys = zip(*[bm.px(pt[1], pt[0]) for pt in ring])
            ax.plot(xs, ys, color="#7b3f99", lw=1.5, alpha=0.85, zorder=3)  # plum, so the outline is not read as a street
    x0, y0 = bm.px(bm.lat0 + bm.dlat, bm.lon0 - bm.dlon)
    x1, y1 = bm.px(bm.lat0 - bm.dlat, bm.lon0 + bm.dlon)
    ax.add_patch(mpatches.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ls=(0, (6, 4)), lw=1.4, ec="#222222", zorder=4))
    sx, sy = bm.px(bm.lat0, bm.lon0)
    ax.plot(sx, sy, marker="*", ms=26, mfc="#d62728", mec="white", mew=1.5, zorder=12)
    _PINS.clear()
    _PINS.append((sx, sy))


def pin_position(x: float, y: float, occupied: list, width: float, height: float, gap: float) -> tuple[float, float]:
    """Find a nonoverlapping label inside the image; distances remain tied to the actual location."""
    for radius in range(0, int(max(width, height)) + 1, max(1, int(gap))):
        count = max(1, math.ceil(2 * math.pi * radius / gap))
        for k in range(count):
            px, py = x + radius * math.cos(2 * math.pi * k / count), y + radius * math.sin(2 * math.pi * k / count)
            if gap / 2 <= px <= width - gap / 2 and gap / 2 <= py <= height - gap / 2 and all(math.hypot(px - a, py - b) >= gap for a, b in occupied):
                return px, py
    raise ValueError("Map is too crowded for readable pins")


def _pin(ax, x: float, y: float, n: int, color: str, filled: bool = True) -> None:
    """Keep numbered circles apart at output resolution; leaders show displaced locations."""
    anchor = x, y
    width, height = max(ax.get_xlim()), max(ax.get_ylim())
    gap = 23 * ax.figure.dpi / 72  # marker diameters are in points, map positions in pixels
    x, y = pin_position(x, y, _PINS, width, height, gap)
    if math.hypot(x - anchor[0], y - anchor[1]) > 1:
        ax.plot([anchor[0], x], [anchor[1], y], color=color, lw=0.8, zorder=7)
        ax.plot(*anchor, marker=".", ms=3, color=color, zorder=7)
    _PINS.append((x, y))
    ax.plot(x, y, marker="o", ms=17, mfc=color if filled else "white", mec=color if filled else color, mew=2.0, zorder=8)
    ax.text(x, y, str(n), ha="center", va="center", fontsize=7.5, color="white" if filled else color, weight="bold", zorder=9)


def area_items(p: dict, layers: dict) -> list[tuple[str, str, float, float]]:
    """(kind, line, lat, lon) for every pin, in pin order: trains, food, parks. No schools, no new-building projects (Noah, 2026-10-17)."""
    lat0, lon0 = fnum(p["latitude"]), fnum(p["longitude"])

    def dist(la, lo):
        return haversine_mi(lat0, lon0, la, lo)

    items = []
    for s in sorted(layers["subway"], key=lambda s: dist(fnum(s["gtfs_latitude"]), fnum(s["gtfs_longitude"]))):
        la, lo = fnum(s["gtfs_latitude"]), fnum(s["gtfs_longitude"])
        items.append(("subway", f"{s['stop_name']} ({s['daytime_routes'].strip()}) · subway · {dist(la, lo):.2f} mi", la, lo))
    for f in layers.get("food") or []:
        rating = f" · {f['rating']} ({f['ratings']:,} ratings)" if f.get("rating") is not None and f.get("ratings") is not None else ""
        items.append(("food", f"{f['name']} · {f['kind']}{rating} · {dist(f['lat'], f['lon']):.2f} mi", f["lat"], f["lon"]))
    parks = sorted(layers["parks"], key=lambda k: -(fnum(k.get("acres"), 0) or 0))
    big = [pk for pk in parks if (fnum(pk.get("acres"), 0) or 0) >= 1.0][:2] or parks[:1]
    for pk in big:
        la, lo = nearest_park_point(pk["multipolygon"], lat0, lon0)
        items.append(("park", f"{pk['signname']} · park, {fnum(pk.get('acres'), 0):.1f} acres · {dist(la, lo):.2f} mi to edge", la, lo))
    return items


def render_area_map(p: dict, layers: dict, radius_mi: float, path: Path, checked: str) -> list[tuple[str, str]]:
    """Street-tile map with numbered pins in three color groups and no text; returns (kind, line) per pin for the list under it."""
    lat0, lon0 = fnum(p["latitude"]), fnum(p["longitude"])
    bm = Basemap(lat0, lon0, radius_mi)
    fig, ax, plt = _figure(bm)
    _draw_frame(ax, bm, layers["ntas"])
    pins = []
    for n, (kind, line, la, lo) in enumerate(area_items(p, layers), 1):
        x, y = bm.px(la, lo)
        _pin(ax, x, y, n, PIN_COLORS[kind])
        pins.append((kind, line))
    fig.savefig(path, dpi=150, pad_inches=0)
    plt.close(fig)
    layers["tiles"] = (bm.tiles, bm.failed)
    return pins


def render_comps_map(p: dict, comps: list[dict], ntas: list[dict], radius_mi: float, path: Path, checked: str) -> list[str]:
    """Same basemap; comps numbered in table order, filled for closed sales, hollow for listings. The table is the legend."""
    lat0, lon0 = fnum(p["latitude"]), fnum(p["longitude"])
    bm = Basemap(lat0, lon0, radius_mi)
    fig, ax, plt = _figure(bm)
    _draw_frame(ax, bm, ntas)
    for n, c in enumerate(comps, 1):
        x, y = bm.px(c["lat"], c["lon"])
        _pin(ax, x, y, n, PIN_COLORS["comp"], filled=c.get("type", "Closed sale") == "Closed sale")
    fig.savefig(path, dpi=150, pad_inches=0)
    plt.close(fig)
    return []


# ----------------------------------------------------------------------------- notion
NOTES_HEADING = "Noah's notes"

BLDG_CLASS = {
    "A0": "cape cod one-family", "A1": "two-story detached one-family", "A2": "one-story one-family", "A3": "large suburban one-family",
    "A4": "city residence one-family", "A5": "attached or semi-detached one-family", "A6": "summer cottage", "A7": "mansion-type one-family",
    "A8": "bungalow colony one-family", "A9": "one-family, other",
    "B1": "brick two-family", "B2": "frame two-family", "B3": "converted two-family", "B9": "two-family, other",
    "C0": "three-family walk-up", "C1": "walk-up, over six families, no stores", "C2": "walk-up, five or six families", "C3": "four-family walk-up",
    "C4": "old-law tenement walk-up", "C5": "converted walk-up", "C6": "walk-up co-op", "C7": "walk-up, over six families, with stores",
    "C8": "walk-up co-op conversion", "C9": "garden apartments",
    "D0": "elevator co-op conversion", "D1": "semi-fireproof elevator building", "D2": "artists-in-residence elevator building", "D3": "fireproof elevator building",
    "D4": "elevator co-op", "D5": "converted elevator building", "D6": "fireproof elevator building with stores", "D7": "semi-fireproof elevator building with stores",
    "D8": "luxury elevator building", "D9": "elevator building, other",
    "S0": "one-family with store", "S1": "one-family with store or office", "S2": "two-family with store or office", "S3": "three-family with store or office",
    "S4": "four-family with store or office", "S5": "five or six families with store", "S9": "mixed residential and commercial, other",
    "V0": "vacant land, zoned residential", "V1": "vacant land, zoned commercial (or Manhattan residential)", "V2": "vacant land, zoned commercial next to a one-family",
    "V3": "vacant land, zoned primarily residential", "V9": "vacant land, other",
    "G0": "residential garage", "G1": "parking garage", "G6": "licensed parking lot", "G7": "unlicensed parking lot",
    "K1": "one-story retail", "K2": "multi-story retail", "K4": "store with apartments above", "K9": "retail, other",
    "M1": "church or synagogue", "M9": "religious, other", "W1": "public school",
    "O1": "one-story office", "O5": "office with commercial", "RR": "condominium rental", "R4": "condominium unit, elevator building",
}
CLASS_FAMILY = {"A": "one-family", "B": "two-family", "C": "walk-up apartments", "D": "elevator apartments", "S": "mixed residential and commercial",
                "V": "vacant land", "R": "condominium", "K": "retail", "O": "office", "G": "garage or parking", "E": "warehouse", "F": "factory",
                "H": "hotel", "I": "hospital or health", "J": "theater", "L": "loft", "M": "religious", "N": "asylum or home", "P": "place of assembly",
                "Q": "outdoor recreation", "T": "transportation", "U": "utility", "W": "educational", "Y": "government", "Z": "other"}
JOB_TYPE = {"NB": "new building", "A1": "major alteration", "A2": "alteration", "A3": "minor alteration", "DM": "demolition", "PA": "place of assembly", "SG": "sign"}


def bldg_class_text(code: str | None) -> str:
    code = (code or "").strip().upper()
    if code in BLDG_CLASS:
        return BLDG_CLASS[code]
    return CLASS_FAMILY.get(code[:1], "building") + (f" (class {code})" if code else "")


def building_text(p: dict) -> str:
    """One line for the Deals "Building" property: dimensions, floors, year, kind, all in words."""
    kind = bldg_class_text(p.get("bldgclass"))
    if (p.get("bldgclass") or "").upper().startswith("V") or not fnum(p.get("bldgarea"), 0):
        return kind[0].upper() + kind[1:]
    parts = []
    bf, bd = fnum(p.get("bldgfront"), 0) or 0, fnum(p.get("bldgdepth"), 0) or 0
    if bf and bd:
        parts.append(f"{bf:g} × {bd:g} ft")
    nf = fnum(p.get("numfloors"), 0) or 0
    if nf:
        parts.append(f"{nf:g} floor{'s' if nf != 1 else ''}")
    yb = int(fnum(p.get("yearbuilt"), 0) or 0)
    if yb:
        parts.append(f"built {yb}")
    parts.append(kind)
    return " · ".join(parts)


def job_text(j: dict, bis: bool) -> str:
    if bis:
        kind = JOB_TYPE.get((j.get("job_type") or "").upper(), (j.get("job_type") or "").lower())
        status = (j.get("job_status_descrp") or "").lower().replace(" - entire job/work", "").replace(" - ", ", ")
        year = (j.get("pre__filing_date") or "")[-4:]
    else:
        kind = (j.get("job_type") or "").lower()
        status = (j.get("filing_status") or "").lower()
        year = str(j.get("filing_date") or "")[:4]
    return " ".join(x for x in (kind, status, year) if x)


ROMAN = ("", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv")
NYC_ZR = Path(os.environ.get("NYC_ZR", Path.home() / "nyc-zr"))  # github.com/plloppii/nyc-zr
_ZR_URLS: dict[str, str] = {}


def zr_url(section: str) -> str:
    """Official zr.planning.nyc.gov URL of a Zoning Resolution section ("23-344(a)" -> the 23-344 page). Read from the
    nyc-zr index (~/nyc-zr or $NYC_ZR) when it is present, otherwise derived from the id (23-344 = Article II, Chapter 3),
    which yields the same URL."""
    base = section.split("(")[0].strip()
    if base in _ZR_URLS:
        return _ZR_URLS[base]
    url = None
    db = NYC_ZR / "data" / "zr.sqlite"
    if db.exists():
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            row = con.execute("SELECT url FROM sections WHERE id = ?", (base,)).fetchone()
            con.close()
            url = row[0] if row else None
        except sqlite3.Error as e:
            print(f"zr index lookup failed for {base}: {str(e)[:120]}")
    if not url:
        head = base.split("-")[0]
        article, chapter = (int(head[:-1]), int(head[-1])) if len(head) >= 2 and head.isdigit() else (0, 0)
        url = (f"https://zr.planning.nyc.gov/article-{ROMAN[article]}/chapter-{chapter}/{base}" if 0 < article < len(ROMAN)
               else f"https://zr.planning.nyc.gov/search?q={base}")
    _ZR_URLS[base] = url
    return url


def zr_link(section: str) -> str:
    """Markdown link to the official page of a ZR section, labeled like 'ZR 23-344(a)'."""
    return f"[ZR {section}]({zr_url(section)})"


def _plain(b: dict) -> str:
    t = b["type"]
    return "".join(x.get("plain_text", "") for x in b.get(t, {}).get("rich_text", []))


def _copy_block(b: dict) -> dict | None:
    """Re-creatable copy of a text block (used only when Noah's notes must be moved)."""
    t = b["type"]
    src = b.get(t, {})
    if "rich_text" not in src:
        return None
    rts = []
    for x in src["rich_text"]:
        if x.get("type") == "text":
            rts.append({"type": "text", "text": x["text"], "annotations": x.get("annotations", {})})
        else:
            rts.append({"type": "text", "text": {"content": x.get("plain_text", "")}})
    out = {"type": t, t: {"rich_text": rts}}
    if t == "to_do":
        out[t]["checked"] = src.get("checked", False)
    if t == "code":
        out[t]["language"] = src.get("language", "plain text")
    return out


PIN_TEXT_COLOR = {"subway": "blue", "food": "red", "park": "green", "comp": "default"}


def notion_write(res: dict, area_png: Path | None, comps_png: Path | None, photo: Path | None = None, refresh: bool = False) -> str:
    """Upsert the deal row and its comp rows in the Properties database (one row per building) and rebuild the page body
    above "Noah's notes"."""
    from notion_api import append, list_children, load_state, location_value, md_to_blocks, page_url, req
    V = "2022-06-28"
    state = load_state()
    db = state["databases"]["deals"]  # the Properties database keeps the historical key
    p, env, cls = res["pluto"], res["envelope"], res["classification"]
    bbl = res["bbl"]
    have = set(req("GET", f"/databases/{db}", version=V)["properties"])

    def upload(path: Path) -> dict | None:
        try:
            ctype = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            fu = req("POST", "/file_uploads", {"mode": "single_part", "filename": path.name, "content_type": ctype}, version=V)
            tok = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_TOKEN")
            with open(path, "rb") as fh:
                r = requests.post(fu["upload_url"], headers={"Authorization": f"Bearer {tok}", "Notion-Version": V},
                                  files={"file": (path.name, fh, ctype)}, timeout=120)
            if r.status_code >= 400:
                print("upload failed:", r.status_code, r.text[:200])
                return None
            return {"type": "image", "image": {"type": "file_upload", "file_upload": {"id": fu["id"]}}}
        except Exception as e:  # noqa: BLE001
            print("upload error:", e)
            return None

    def append_after(page_id: str, blocks: list[dict], after_id: str) -> str:
        for k in range(0, len(blocks), 100):
            r = req("PATCH", f"/blocks/{page_id}/children", {"children": blocks[k:k + 100], "after": after_id}, version=V)
            if r.get("results"):
                after_id = r["results"][-1]["id"]
        return after_id

    def find(b: str) -> dict | None:
        q = req("POST", f"/databases/{db}/query", {"filter": {"property": "BBL", "rich_text": {"equals": b}}}, version=V)
        return q["results"][0] if q["results"] else None

    def text(v: str) -> dict:
        return {"rich_text": [{"type": "text", "text": {"content": v[:2000]}}] if v else []}

    # ---- comps: one Comp row per building; a building that is already a Deal row is linked, not changed
    by_bbl: dict[str, list[dict]] = {}
    for c in res["comps"][:8] + (res.get("exit_comps", [])[:8] if res["body_md"].get("exit") else []):
        by_bbl.setdefault(c["bbl"], []).append(c)
    say(f"notion: {len(by_bbl)} comp rows")
    comp_ids = []
    for cb, sales in by_bbl.items():
        sales.sort(key=lambda c: str(c["date"]), reverse=True)
        last, prior = sales[0], (sales[1] if len(sales) > 1 else None)
        row = find(cb)
        if row and (row["properties"].get("Type", {}).get("select") or {}).get("name") == "Deal":
            comp_ids.append(row["id"])
            continue
        buildable = last["basis"] == "Buildable zoning"
        props = {
            "Name": {"title": [{"type": "text", "text": {"content": last["address"] or cb}}]},
            "BBL": text(cb),
            "Type": {"select": {"name": "Comp"}},
            "Borough": {"select": {"name": BORO_NAME[p["borough"]]}},
            "Neighborhood": {"select": {"name": res.get("nta") or "Unknown"}},
            "Zoning": {"multi_select": [{"name": last["zone"]}] if last.get("zone") else []},
            "Lot": text(last.get("lot_dims") or ""),
            "Lot sf": {"number": last["lot_sf"]},
            "Existing sf": {"number": None if buildable else (round(last["area"]) if last["area"] else None)},
            "Max ZFA": {"number": round(last["area"]) if buildable and last["area"] else None},
            "Units": {"number": last["units"]},
            "Building": text(bldg_class_text(last.get("bldgclass")).capitalize() + ("; historic district" if last.get("historic") else "")),
            "Last sale price": {"number": last["price"]},
            "Last sale date": {"date": {"start": last["date"]}} if last["date"] else {"date": None},
            "Prior sale": text(f"${prior['price']:,.0f} · {_month(prior['date'])}" if prior else ""),
        }
        review = last.get("review") or {}
        location = location_value(last["address"], last.get("lat"), last.get("lon"))
        if location:
            props["Location"] = location
        if review.get("url"):
            props["Listing URL"] = {"url": review["url"]}
        if review.get("beds_baths"):
            props["Beds/baths"] = text(review["beds_baths"])
        if row:
            props.pop("Type")  # keep whatever it is (Comp)
            if prior is None:
                props.pop("Prior sale")  # a review refresh does not erase a previously researched deed
            props = {k: v for k, v in props.items() if k in have}
            req("PATCH", f"/pages/{row['id']}", {"properties": props}, version=V)
            comp_ids.append(row["id"])
        else:
            props["Strategy"] = {"select": {"name": "Not set"}}
            props = {k: v for k, v in props.items() if k in have}
            comp_ids.append(req("POST", "/pages", {"parent": {"database_id": db}, "properties": props}, version=V)["id"])

    # ---- deal row
    say("notion: deal row, images and page body")
    row = find(bbl)
    if refresh:
        if not row:
            raise ValueError("A section refresh needs an existing deal page")
        return notion_refresh_sections(res, row["id"], area_png, comps_png, comp_ids, upload)
    zoning = [z for z in (p.get("zonedist1"), p.get("zonedist2"), p.get("overlay1"), p.get("spdist1")) if z]
    rules_cited = ", ".join(dict.fromkeys(s.split("(")[0].strip() for s in res.get("rules", [])))  # "23-22, 23-344"
    props = {
        "Name": {"title": [{"type": "text", "text": {"content": p["address"].title()}}]},
        "BBL": text(bbl),
        "Type": {"select": {"name": "Deal"}},
        "Strategy": {"select": {"name": res["strategy"]}},
        "Borough": {"select": {"name": BORO_NAME[p["borough"]]}},
        "Neighborhood": {"select": {"name": res.get("nta") or "Unknown"}},
        "Zoning": {"multi_select": [{"name": z} for z in zoning]},
        "Lot": text(f"{fnum(p.get('lotfront'), 0):g} × {fnum(p.get('lotdepth'), 0):g}" if fnum(p.get("lotfront"), 0) and fnum(p.get("lotdepth"), 0) else ""),
        "Lot sf": {"number": env["lotarea"]},
        "Existing sf": {"number": env["bldgarea"]},
        "Max ZFA": {"number": round(env["max_zfa"])},
        "Flags": {"multi_select": [{"name": f} for f in cls["flags"]]},
        "Units": {"number": cls["units"]},
        "Building": text(building_text(p)),
        "Verdict": {"select": {"name": res["verdict"]}},
        "Ask": {"number": res.get("ask")},
        "Owner": text((p.get("ownername") or "").title()[:200]),
        "Last sale price": {"number": res.get("sale_price")},
        "Last sale date": {"date": {"start": res["sale_date"]}} if res.get("sale_date") else {"date": None},
        "Prior sale": text(res.get("prior_sale") or ""),
        "Listing URL": {"url": res.get("listing") or None},
        "Comps used": {"relation": [{"id": i} for i in comp_ids if i != (row or {}).get("id")]},
        "Rules that mattered": text(rules_cited),
    }
    location = location_value(p["address"].title(), p.get("latitude"), p.get("longitude"),
                              f"{p['address'].title()}, {BORO_NAME[p['borough']]}, NY")
    if location:
        props["Location"] = location
    if row and res["strategy"] == "Not set" and (row["properties"].get("Strategy", {}).get("select") or {}).get("name") not in (None, "Not set"):
        props.pop("Strategy")  # never downgrade a strategy Noah or Instinct already set
    props = {k: v for k, v in props.items() if k in have}

    # ---- page body: photo, links paragraph, generated body; Noah's notes live below and are never touched
    links_md, rest_md = res["body_md"]["top"].split("\n\n", 1)
    head = ([img] if photo and (img := upload(photo)) else []) + [md_to_blocks(links_md)[0]]
    body = md_to_blocks(rest_md)
    if res.get("floor_plan") and (img := upload(Path(res["floor_plan"]))):
        # Place the plan before Details, following the opening paragraph.
        body[1:1] = md_to_blocks("### Floor plan") + [img]
    area = md_to_blocks("### Area")
    if area_png and (img := upload(area_png)):
        area.append(img)
    area += md_to_blocks(res["area_caption"])
    for kind, line in res.get("area_pins", []):  # one numbered-list block per pin, text colored like its pin group
        area.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": [
            {"type": "text", "text": {"content": line}, "annotations": {"color": PIN_TEXT_COLOR.get(kind, "default")}}]}})
    area += md_to_blocks(res.get("area_tail") or "")
    body += area
    if comps_png and (img := upload(comps_png)):
        body += md_to_blocks("### Purchase comps") + [img] + md_to_blocks(res["body_md"]["comps"])
    else:
        body += md_to_blocks("### Purchase comps\n\n" + res["body_md"]["comps"])
    if res["body_md"].get("exit"):
        body += md_to_blocks("### Exit comps\n\n" + res["body_md"]["exit"])
    body += md_to_blocks(res["body_md"]["bottom"])
    notes = md_to_blocks(f"### {NOTES_HEADING}") + [{"type": "paragraph", "paragraph": {"rich_text": []}}]

    if row:
        page_id = row["id"]
        req("PATCH", f"/pages/{page_id}", {"properties": props}, version=V)
        old = list_children(page_id)
        notes_idx = next((i for i, b in enumerate(old) if b["type"].startswith("heading") and _plain(b).strip().lower() == NOTES_HEADING.lower()), None)
        if notes_idx == 0:
            raise ValueError("Page starts with Noah's notes; refusing to move or recreate the notes")
        if notes_idx is not None:
            # insert the new body after the first old block, delete the other old blocks above Noah's notes, then that first block;
            # the notes heading and everything below it are never touched
            for b in old[1:notes_idx]:
                req("DELETE", f"/blocks/{b['id']}", version=V)
            append_after(page_id, head + body, old[0]["id"])
            req("DELETE", f"/blocks/{old[0]['id']}", version=V)
            kept = True
        else:
            kept_blocks = [cb for b in (old[notes_idx + 1:] if notes_idx is not None else []) if (cb := _copy_block(b))]
            for b in old:
                req("DELETE", f"/blocks/{b['id']}", version=V)
            append(page_id, head + body + (md_to_blocks(f"### {NOTES_HEADING}") + kept_blocks if kept_blocks else notes))
            kept = bool(kept_blocks)
        created = False
        print("Noah's notes:", "preserved" if kept else "section added")
    else:
        r = req("POST", "/pages", {"parent": {"database_id": db}, "properties": props}, version=V)
        page_id = r["id"]
        append(page_id, head + body + notes)
        created = True
    print("deal page", "created" if created else "updated", page_url(page_id), "| strategy", res["strategy"], "| comps linked", len(comp_ids))
    return page_url(page_id)


def notion_refresh_sections(res, page_id, area_png, comps_png, comp_ids, upload):
    """Replace Area and Purchase comps in place; leave all hand-finished facts and notes intact."""
    from notion_api import list_children, md_to_blocks, page_url, req
    version = "2022-06-28"
    old = list_children(page_id)
    notes_index = next((i for i, b in enumerate(old) if b["type"].startswith("heading") and _plain(b).strip().lower() == NOTES_HEADING.lower()), None)
    if notes_index is None:
        raise ValueError("Refresh requires the Noah's notes boundary")
    headings = {_plain(b).strip().lower(): i for i, b in enumerate(old[:notes_index]) if b["type"].startswith("heading")}
    for name in ("area", "purchase comps"):
        if name not in headings or headings[name] >= notes_index:
            raise ValueError(f"Refresh requires an existing {name} section above Noah's notes")
    # Prepare every upload before modifying existing page content.
    area_blocks = []
    if area_png:
        img = upload(area_png)
        if img is None:
            raise RuntimeError("Area map upload failed; page left intact")
        area_blocks.append(img)
    area_blocks += md_to_blocks(res["area_caption"])
    for kind, line in res.get("area_pins", []):
        area_blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": [
            {"type": "text", "text": {"content": line}, "annotations": {"color": PIN_TEXT_COLOR.get(kind, "default")}}]}})
    area_blocks += md_to_blocks(res.get("area_tail") or "")
    comp_blocks = []
    if comps_png and res["comps"]:
        img = upload(comps_png)
        if img is None:
            raise RuntimeError("Comps map upload failed; page left intact")
        comp_blocks.append(img)
    comp_blocks += md_to_blocks(res["body_md"]["comps"])
    replacements = {"area": area_blocks, "purchase comps": comp_blocks}
    insertions = []
    if res["body_md"].get("exit"):
        if "exit comps" in headings and headings["exit comps"] < notes_index:
            replacements["exit comps"] = md_to_blocks(res["body_md"]["exit"])
        else:
            oi = headings.get("open items")
            if oi is None or oi == 0 or oi >= notes_index:
                raise ValueError("Cannot place exit comps safely")
            insertions.append((old[oi - 1]["id"], md_to_blocks("### Exit comps\n\n" + res["body_md"]["exit"])))
    if res.get("floor_plan"):
        img = upload(Path(res["floor_plan"]))
        if img is None:
            raise RuntimeError("Floor plan upload failed; page left intact")
        if "floor plan" in headings and headings["floor plan"] < notes_index:
            replacements["floor plan"] = [img]
        else:
            details = headings.get("details")
            if details is None or details == 0 or details >= notes_index:
                raise ValueError("Cannot place floor plan safely")
            insertions.append((old[details - 1]["id"], md_to_blocks("### Floor plan") + [img]))
    for after, blocks in insertions:
        req("PATCH", f"/blocks/{page_id}/children", {"children": blocks, "after": after}, version=version)
    for name, blocks in replacements.items():
        i = headings[name]
        end = next((j for j in range(i + 1, notes_index + 1) if old[j]["type"].startswith("heading")), notes_index)
        # Keep original content until the full replacement has been appended successfully.
        after = old[i]["id"]
        for k in range(0, len(blocks), 100):
            response = req("PATCH", f"/blocks/{page_id}/children", {"children": blocks[k:k + 100], "after": after}, version=version)
            after = response["results"][-1]["id"]
        for b in old[i + 1:end]:
            req("DELETE", f"/blocks/{b['id']}", version=version)
    req("PATCH", f"/pages/{page_id}", {"properties": {"Comps used": {"relation": [{"id": i} for i in comp_ids if i != page_id]}}}, version=version)
    log = md_to_blocks(f"Run log: {res['checked']} · maps and comp review refreshed · {len(res['comps'])} purchase comps; listing facts and notes preserved")
    # Insert before the untouched notes heading, after the last surviving original block.
    current = list_children(page_id)
    ni = next(i for i, b in enumerate(current) if b["id"] == old[notes_index]["id"])
    req("PATCH", f"/blocks/{page_id}/children", {"children": log, "after": current[ni - 1]["id"]}, version=version)
    say(f"deal page refreshed {page_url(page_id)}; Noah's notes preserved")
    return page_url(page_id)


# ----------------------------------------------------------------------------- compose
def _month(d: str) -> str:
    try:
        return dt.date.fromisoformat(str(d)[:10]).strftime("%B %Y")
    except ValueError:
        return str(d)


def compose(p, env, cls, dob, corner, comps, layers, legends, last_sale, parties, nta, ask, listing, checked, photo=None, strategy=None, deeds=None, flood=None) -> dict:
    boro = BORO_CODE[p["borough"]]
    block, lot = str(int(p["block"])), str(int(p["lot"]))
    bbl = norm_bbl(p["bbl"])
    zola = f"https://zola.planning.nyc.gov/l/lot/{boro}/{block}/{lot}"
    acris = f"https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?borough={boro}&block={block}&lot={lot}"
    dof = f"https://propertyinformationportal.nyc.gov/parcels/parcel/{bbl}"
    bins = dob["bins"]
    bis = f"https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin={bins[0]}" if bins else None
    bis_link = f"[BIS]({bis})" if bis else ""
    front, depth = fnum(p.get("lotfront"), 0.0), fnum(p.get("lotdepth"), 0.0)
    lot_text = f"{front:g} × {depth:g} ft, {env['lotarea']:,.0f} sf, {env['lot_type'].lower()}" + (" (irregular)" if "Irregular lot" in env["flags"] else "")
    ver = p.get("version", "")
    addr = p["address"].title()
    place = nta or BORO_NAME[p["borough"]]
    zone = env["zone"]
    flags = cls["flags"]
    flood = flood or {}
    vacant = cls["vacant"]
    historic = p.get("histdist") or p.get("landmark")
    streeteasy = "https://streeteasy.com/building/" + re.sub(r"[^a-z0-9]+", "-", p["address"].lower()).strip("-") + "-" + BORO_NAME[p["borough"]].lower().replace(" ", "-")
    hpd_url = f"https://hpdonline.nyc.gov/hpdonline/building/{dob['hpd_building_id']}/overview" if dob.get("hpd_building_id") else None
    link_items = [f"[StreetEasy]({streeteasy})", f"[ZoLa]({zola})", bis_link, f"[ACRIS]({acris})", f"[DOF]({dof})"]
    links = ("" if photo else "No photo · ") + (f"[Listing]({listing}) · " if listing else "Listing: none given · ") + " · ".join(x for x in link_items if x)
    rules: list[str] = [env["far_cite"]]

    # price basis: buildable sf for land/development, PLUTO sf for standing buildings
    buildable_basis = (comps[0]["basis"] == "Buildable zoning") if comps else (vacant or cls["deal_type"] in ("Development", "Enlargement"))
    basis_area = env["max_zfa"] if buildable_basis else (env["bldgarea"] or env["max_zfa"])
    basis_word = "buildable sf" if buildable_basis else "sf"
    ask_psf = ask / basis_area if ask and basis_area else None
    shown = comps[:8]
    flagged = [(n, c) for n, c in enumerate(shown, 1) if c.get("reno")]  # renovation signal: not an as-is analog until the listing pass says so
    analogs = [(n, c) for n, c in enumerate(shown, 1)
               if c.get("psf") and not c.get("partial_doc") and not c.get("reno") and (c.get("zone") or "") == zone and c.get("area") and basis_area
               and basis_area / 3 <= c["area"] <= basis_area * 3]
    psfs = [c["psf"] for c in shown if c.get("psf") and not c.get("partial_doc") and (c.get("review") or {}).get("condition") == "as_is"]
    yard = max(10.0, 30 - 0.5 * (95 - depth)) if depth else 30.0
    corner_ok = bool(corner) and not corner.get("corner_lot") and corner.get("near_ft") is not None
    corner_name = (corner.get("corner_address") or "").title() if corner else ""

    # ---- top paragraph: three sentences, no labels
    if vacant:
        what = f"vacant {front:g} × {depth:g} ft {zone} {env['lot_type'].lower()} lot in {place} that builds a {env['max_zfa']:,.0f} sf {cls['product']}"
    else:
        what = (f"{bldg_class_text(p.get('bldgclass'))} with {cls['units']} units, built {cls['year'] or 'year unknown'}, on a {front:g} × {depth:g} ft {zone} lot in {place}; "
                f"{env['bldgarea']:,.0f} sf stand against {env['max_zfa']:,.0f} sf allowed")
    verdict = "Pass" if "City owned" in flags else "Investigate"
    s1 = f"Pass: {addr} is city-owned, so there is no path to possession; {what}." if verdict == "Pass" else f"Investigate: {what}."
    if ask_psf and analogs:
        n0, a0 = analogs[0]
        s2 = f"The ask is ${ask_psf:,.0f} per {basis_word}; the nearest {zone} sale of similar size, {a0['address']}, closed at ${a0['psf']:,.0f}"
        if len(analogs) > 1:
            others = [c["psf"] for _, c in analogs[1:]]
            s2 += f", and {len(others)} other{'s' if len(others) > 1 else ''} at ${min(others):,.0f} to ${max(others):,.0f}" if len(others) > 1 else f", and one other at ${others[0]:,.0f}"
        s2 += "."
    elif ask_psf and psfs:
        s2 = f"The ask is ${ask_psf:,.0f} per {basis_word}; no closed sale matches the subject's district and size, and the nearest same-class sales ran ${min(psfs):,.0f} to ${max(psfs):,.0f}."
    elif ask_psf:
        s2 = f"The ask is ${ask_psf:,.0f} per {basis_word}; no closed same-class sale turned up within {RADIUS_NOTE}."
    else:
        s2 = "No asking price was given, so there is no price test yet."
    deciders = []
    if "Near corner" in flags and corner_ok:
        deciders.append(f"a survey showing the lot sits within 100 ft of the street corner at {corner_name or 'the block corner'} removes the rear yard")
        if "Shallow lot" in flags:
            deciders.append(f"failing that, proof of the 1961 lot depth gives a {yard:g} ft yard")
    elif "Shallow lot" in flags:
        deciders.append(f"proof of the lot's depth on December 15, 1961 cuts the rear yard to {yard:g} ft")
    if cls["units"] > 0:
        deciders.append("the legal unit count and whether the tenants are rent regulated decide the product")
    if historic:
        deciders.append("Landmarks review governs exterior work and rules out demolition")
    if flood.get("sfha"):
        deciders.append(f"the lot is in flood zone {flood['zone']}, so flood insurance and the elevation rules for new construction apply")
    if not deciders:
        deciders.append("no zoning gate turned up, so title and the cost quotes decide it")
    s3 = deciders[0][0].upper() + deciders[0][1:] + ("; " + "; ".join(deciders[1:]) if len(deciders) > 1 else "") + "."
    top_para = " ".join([s1, s2, s3])

    # ---- details table: Field, Finding, Source
    rows: list[tuple[str, str, str]] = []
    rows.append(("Ask", f"${ask:,.0f}" if ask else "not given", f"[listing]({listing})" if listing else "—"))
    rows.append(("Lot", f"{boro}-{block.zfill(5)}-{lot.zfill(4)} · BIN {', '.join(bins) or 'unknown'} · {lot_text}", f"[ZoLa]({zola})"))
    zon = p.get("zonedist1", "") + (f"/{p['zonedist2']}" if p.get("zonedist2") else "")
    zon += f" · overlay {p['overlay1']}" if p.get("overlay1") else " · no overlay"
    zon += f" · special district {p['spdist1']}" if p.get("spdist1") else " · no special district"
    zon += f" · {p['histdist']}" if p.get("histdist") else " · not historic"
    zon += f" · landmark: {p['landmark']}" if p.get("landmark") else ""
    zon += f" · E-designation {p['edesignum']}" if p.get("edesignum") else ""  # hazardous materials, air or noise conditions on new construction
    rows.append(("Zoning", zon, f"[ZoLa]({zola})"))
    if vacant:
        rows.append(("Existing building", bldg_class_text(p.get("bldgclass")).capitalize(), f"[ZoLa]({zola})"))
    else:
        rows.append(("Existing building", f"{bldg_class_text(p.get('bldgclass')).capitalize()} · {cls['units']} units · built {cls['year'] or '?'} · {fnum(p.get('numfloors'), 0):g} floors · {env['bldgarea']:,.0f} sf gross", f"[ZoLa]({zola})"))
    rows.append(("Buildable", f"{env['lotarea']:,.0f} × {env['far']:g} = {env['max_zfa']:,.0f} sf · {env['bldgarea']:,.0f} built · {env['unused']:,.0f} unused", zr_link(env["far_cite"])))
    rows.append(("Owner · last deed", f"{(p.get('ownername') or '').title()} · {last_sale}", f"[ACRIS]({acris})"))
    opens = []
    if dob["dob_viol_open"]:
        opens.append(f"DOB {dob['dob_viol_open']}")
    if dob["ecb_open"]:
        opens.append(f"ECB {dob['ecb_open']} (${dob['ecb_balance']:,.0f} due)")
    if dob["hpd_open"]:
        hc = dob["hpd_open_by_class"]
        opens.append(f"HPD {dob['hpd_open']} (A {hc['A']}, B {hc['B']}, C {hc['C']})")
    viol = ("open: " + ", ".join(opens)) if opens else "none open (DOB, ECB, HPD)"
    jobs = [job_text(j, True) for j in dob["bis_jobs"][:2]] + [job_text(j, False) for j in dob["dobnow_jobs"][:2]]
    rows.append(("Violations · jobs", viol + (" · jobs: " + "; ".join(jobs) if jobs else " · no DOB jobs on record"), ", ".join(x for x in (bis_link, f"[HPD]({hpd_url})" if hpd_url else "HPD") if x)))
    if cls["units"] > 0:
        signal = "likely regulated: 6+ units built before 1974" if "Rent regulated?" in flags else "no public regulation signal"
        rows.append(("Taxes · rent regulation", f"taxes unknown · {signal}" + (" · HPD registration: none on record" if not dob["hpd_registered"] else ""), f"[DOF]({dof})"))
    if (ftxt := flood_text(flood)):
        fema = "https://msc.fema.gov/portal/search?AddressQuery=" + urllib.parse.quote(f"{addr}, {BORO_NAME[p['borough']]}, NY")
        rows.append(("Flood", ftxt, f"[FEMA]({fema})"))
    details = "| Field | Finding | Source |\n| --- | --- | --- |\n" + "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)

    # ---- zoning: lot-specific numbers and the rules that apply
    zlines = [f"Buildable: {env['lotarea']:,.0f} sf × FAR {env['far']:g} = {env['max_zfa']:,.0f} sf ({zr_link(env['far_cite'])})"
              + (f" · {env['bldgarea']:,.0f} sf built, {env['U']:.0%} of the allowance" if not vacant and env["U"] is not None else "")]
    if "Near corner" in flags and corner_ok:
        extent = "the whole lot sits" if corner["whole_lot_within_100"] else ("part of the lot sits" if corner["part_within_100"] else "no part of the lot sits")
        zlines.append(f"Rear yard, corner exemption: no rear yard is required within 100 ft of a street corner; {extent} about {corner['near_ft']} to {corner['far_radial_ft']} ft "
                      f"from the corner at {corner_name or 'the block corner'}, estimated from lot centroids. A survey proves it. ({zr_link('23-344(a)')})")
        rules.append("23-344")
    if "Shallow lot" in flags:
        zlines.append(f"Rear yard, shallow lot: a lot under 95 ft deep loses 6 in of rear yard per foot short, floor 10 ft; 30 − 0.5 × (95 − {depth:g}) = {yard:g} ft. "
                      f"Proof of the lot's depth on December 15, 1961 is required. ({zr_link('23-342(b)')})")
        rules.append("23-342")
    if "Narrow lot" in flags or env["lotarea"] < 1700:
        zlines.append(f"Lot size: {front:g} ft wide and {env['lotarea']:,.0f} sf, under the 18 ft and 1,700 sf minimums ({zr_link('23-12')}), "
                      f"but exempt as a zoning lot that existed on December 5, 2024 and has not shrunk since ({zr_link('23-10')}).")
        rules += ["23-12", "23-10"]
    if "Irregular lot" in flags:
        zlines.append("Irregular lot: front × depth overstates the area; use the survey.")
    if historic:
        zlines.append(f"Historic: {historic}; Landmarks Preservation Commission review on exterior work, no demolition.")
    top = "\n\n".join([links, top_para, "### Details", details, "### Zoning", "\n\n".join(zlines)])

    # ---- area: caption line, one numbered line per pin, a negative if any (no development-pace sentence: Noah, 2026-10-17)
    drawn = layers.get("tiles") is not None  # set by render_area_map; without matplotlib there is no image, only the pin list
    sources = ([TILE_CREDIT, "NYC 2020 neighborhood boundaries"] if drawn else []) + ["MTA stations"]
    if layers.get("food"):
        sources.append(layers.get("food_source") or "Google Places")
    sources.append("NYC Parks")
    caption = ("Map: " if drawn else "Nearby: ") + " · ".join(sources) + f" · checked {checked} · straight-line miles"
    gaps = {"subway": "subway: source unreachable", "parks": "parks: source unreachable", "ntas": "neighborhood outlines: source unreachable",
            "food": "cafes and restaurants: source unreachable", "food: no Google key": "cafes and restaurants: no Google key"}
    notes = [gaps.get(f, f"{f}: unavailable") for f in layers.get("failed", [])]
    if layers.get("tiles") and layers["tiles"][1]:
        notes.append(f"{layers['tiles'][1]} map tiles missing")
    if notes:
        caption += " · " + "; ".join(notes)
    state_path = ROOT / "data" / "notion_state.json"
    view_state = json.loads(state_path.read_text()) if state_path.exists() else {}
    map_id = view_state.get("views", {}).get("neighborhood")
    if map_id:
        caption += f" · [Map](https://www.notion.so/{view_state['databases']['properties'].replace('-', '')}?v={map_id.replace('-', '')})"
    area_pins = legends.get("area") or []
    pins = "\n".join(f"{n}. {line}" for n, (_, line) in enumerate(area_pins, 1))
    neg = f"{p['histdist']}: Landmarks review on exterior work, no demolition." if p.get("histdist") else ""
    area_md = "\n\n".join(x for x in (caption, pins, neg) if x)
    area_tail = neg

    # ---- strategy: Noah's word, else inferred only when exactly one fits
    if strategy:
        strat = strategy
    elif vacant and not historic:
        strat = "Build"
    elif cls["units"] > 0 and "Rent regulated?" in flags:
        strat = "Rent"
    else:
        strat = "Not set"

    # ---- sale history for the row
    deeds = deeds or []
    sale_price = (deeds[0]["amt"] or 0) if deeds else None
    sale_date = deeds[0]["date"] if deeds else None
    prior = (f"${deeds[1]['amt']:,.0f} · {_month(deeds[1]['date'])}" if deeds[1]["amt"] else f"$0 (non-market) · {_month(deeds[1]['date'])}") if len(deeds) > 1 else ""

    # ---- comps table and the analog sentence
    if shown:
        trows = ["| # | Address | Closed | Price | Zoning | Area · basis | $/sf | Listing |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for n, c in enumerate(shown, 1):
            basis = "buildable" if c["basis"] == "Buildable zoning" else "building"
            psf = f"${c['psf']:,.0f}" if c.get("psf") else "n/a"
            src = f"[ACRIS](https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?borough={boro}&block={c['block']}&lot={c['lot']})"
            price = f"${c['price']:,.0f}" + (" (multi-lot)" if c.get("partial_doc") else "")
            trows.append(f"| {n} | {c['address']} {src} · {c['dist']:.2f} mi | {c['date']} | {price} | {c.get('zone') or '?'} | {c['area']:,.0f} sf · {basis} | {psf} | {listing_cell(c)} |")
        if analogs:
            sent = "Best analogs: " + ", ".join(f"#{n} {c['address']} at ${c['psf']:,.0f} per {basis_word}" for n, c in analogs)
            sent += f"; the ask is ${ask_psf:,.0f}." if ask_psf else "."
            if len(analogs) < len(shown):
                sent += " The other rows are different districts" + (", carry a renovation signal" if flagged else "") + " or fall outside one-third to three times the subject's size."
        else:
            sent = "No row matches the subject's district and size; the nearest same-class sales are shown for scale" + (f", against an ask of ${ask_psf:,.0f} per {basis_word}." if ask_psf else ".")
        if flagged:
            sent += " Renovation signal on " + " and ".join(f"#{n} {c['address']}" for n, c in flagged) + ": alteration jobs filed in the three years before the sale; confirm in the listing pass and move a renovated sale to exit comps."
        by_lot: dict[str, list[tuple[int, dict]]] = {}
        for n, c in enumerate(shown, 1):
            by_lot.setdefault(c["bbl"], []).append((n, c))
        for pair in by_lot.values():
            if len(pair) > 1:
                pair.sort(key=lambda t: str(t[1]["date"]))
                (n1, c1), (n2, c2) = pair[0], pair[-1]
                sent += f" #{n1} and #{n2} are the same lot, {c1['address']}, sold twice: ${c1['price']:,.0f} in {_month(c1['date'])} and ${c2['price']:,.0f} in {_month(c2['date'])}."
        comps_md = "\n".join(trows) + f"\n\n{sent} Closed ACRIS deeds within {RADIUS_NOTE}, same class, nearest first; prices are recorded consideration, not adjusted."
    else:
        comps_md = f"No closed sales of the same class within {RADIUS_NOTE}. Widen the radius or time, or use listings as asks."

    # ---- open items and run log
    open_items = []
    if not bins:
        open_items.append(f"| Building identification number | Legal building record and unit count | NYC building footprints; DOB address record | {AGENT} |")
    if "Near corner" in flags or "Shallow lot" in flags:
        open_items.append("| Survey distance to the corner and the 1961 lot depth | Rear yard 0, 10 or 30 ft, and lot coverage | Survey; DOB ZRD1 pre-determination | Noah → surveyor, architect |")
    if cls["units"] > 0:
        open_items.append("| Legal unit count and certificate of occupancy | Product and lender eligibility | CO or I-card; DOB BIS | Architect |")
        open_items.append("| Occupancy and rent regulation | Vacant delivery versus in-place income | Leases, DHCR registrations, DOF bill | Attorney |")
    open_items.append("| Title and liens | Can block closing | Title report; ACRIS mortgages and satisfactions | Attorney |")
    if dob["dob_viol_open"] or dob["ecb_open"] or dob["hpd_open"]:
        open_items.append(f"| Open violations: DOB {dob['dob_viol_open']}, ECB {dob['ecb_open']} (${dob['ecb_balance']:,.0f} due), HPD {dob['hpd_open']} | Cost to cure and permit holds | BIS and HPD records | Expediter |")
    if ask is None:
        open_items.append("| Asking price | Needed for the price test | Listing or broker | Noah |")
    if flood.get("sfha") or flood.get("psfha"):
        pre = ((f"zone {flood['pzone']}" + (f", base flood elevation {flood['pbfe']:g} ft" if flood.get("pbfe") else "")) if flood.get("psfha")
               else ("no zone" if flood.get("preliminary_ok") else "unknown, service unreachable"))
        open_items.append(f"| Flood zone: insurance cost and design elevation | Carrying cost; new construction must sit above the base flood elevation; the 2015 preliminary maps the Building Code uses show {pre} here | Insurance quote; elevation certificate | Noah, architect |")
    elif not flood.get("effective_ok"):
        open_items.append(f"| Flood zone | Flood insurance and elevation rules | FEMA flood map lookup; the service was unreachable on this run | {AGENT} |")
    flood_note = "" if flood.get("effective_ok") and flood.get("preliminary_ok") else (" unreachable" if not flood.get("effective_ok") else ", preliminary maps unreachable")
    bottom = "\n\n".join([
        "### Open items", "| Missing fact | Moves the deal how | Evidence needed | Who |\n| --- | --- | --- | --- |\n" + "\n".join(open_items[:6]),
        f"Run log: {checked} · stages 0-3 · screen.py (city lot record {ver}; ACRIS, DOB, HPD, DOB NOW on Open Data; FEMA flood maps{flood_note}{'' if drawn else '; maps not drawn'})",
    ])
    return {"bbl": bbl, "zola": zola, "lot_text": lot_text, "verdict": verdict, "ask": ask, "ask_psf": ask_psf,
            "listing": listing, "photo": str(photo) if photo else None, "last_sale_text": last_sale, "nta": nta, "rules": rules,
            "strategy": strat, "sale_price": sale_price, "sale_date": sale_date, "prior_sale": prior, "flood": flood,
            "area_caption": caption, "area_pins": area_pins, "area_tail": area_tail,
            "analogs": [c["address"] for _, c in analogs],
            "body_md": {"top": top, "area": area_md, "comps": comps_md, "bottom": bottom}}


RADIUS_NOTE = ""
RADIUS_MI = 0.5


def fetch_photo(src: str | None, p: dict, path: Path) -> Path | None:
    """The listing photo from a file path or URL; else Google Street View when GOOGLE_MAPS_API_KEY is set; else None."""
    try:
        if src and Path(src).is_file():
            return Path(src)
        if src:
            r = SESSION.get(src, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
            return path
        key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not key:
            return None
        loc = f"{fnum(p['latitude'])},{fnum(p['longitude'])}"
        meta = SESSION.get("https://maps.googleapis.com/maps/api/streetview/metadata", params={"location": loc, "key": key}, timeout=30).json()
        if meta.get("status") != "OK":
            print("street view:", meta.get("status"))
            return None
        r = SESSION.get("https://maps.googleapis.com/maps/api/streetview", params={"size": "640x400", "location": loc, "fov": 70, "key": key}, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        return path
    except Exception as e:  # noqa: BLE001
        print("photo failed:", type(e).__name__)  # request URLs may contain the Google key
        return None


def write_screen(raw: dict, args) -> dict:
    """Render one snapshot with persisted listing decisions; refresh never refetches city data."""
    from listing_media import fetch_floorplan
    p, layers = raw["pluto"], raw["layers"]
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", p["address"].lower()).strip("-")
    review = read_review(args.review) if args.review else raw.get("review", {"sales": []})
    candidates = raw["candidate_comps"]
    purchase, exits, excluded = apply_review(candidates, review)
    area_png, comps_png = out / f"{slug}-area.png", out / f"{slug}-comps.png"
    if have_maps() and args.map_images:
        legends = {"area": render_area_map(p, layers, args.radius, area_png, raw["checked"])}
        render_comps_map(p, purchase[:8], layers["ntas"], args.radius, comps_png, raw["checked"])
    else:
        say("using native database maps; PNG rendering skipped" if not args.map_images else "maps skipped: install matplotlib and pillow")
        layers.pop("tiles", None)
        legends = {"area": [(kind, line) for kind, line, _, _ in area_items(p, layers)]}
        area_png = comps_png = None
    photo = Path(raw["photo"]) if raw.get("photo") and Path(raw["photo"]).is_file() else None
    if args.photo:
        photo = fetch_photo(args.photo, p, out / f"{slug}-photo.jpg")
    elif not args.refresh and not photo:
        photo = fetch_photo(None, p, out / f"{slug}-photo.jpg")
    floor_plan = raw.get("floor_plan")
    media_status = raw.get("floor_plan_status", "not checked")
    if args.floor_plan or args.listing_html or not args.refresh:
        plan, media_status = fetch_floorplan(args.listing, out / f"{slug}-floor-plan.png", args.floor_plan, args.listing_html, args.browser_url)
        floor_plan = str(plan) if plan else None
        say(f"floor plan: {media_status}")
    if floor_plan and not Path(floor_plan).is_file():
        floor_plan = None
    res = compose(p, raw["envelope"], raw["classification"], raw["dob"], raw["corner"], purchase, layers,
                  legends, raw["last_sale"], raw["parties"], raw["nta"], args.ask, args.listing,
                  raw["checked"], photo, args.strategy, raw["deeds"], flood=raw["flood"])
    if exits and res["strategy"] in ("Flip", "Build"):
        qualifying = exits if res["strategy"] == "Flip" else [c for c in exits if c["review"]["condition"] == "new"]
        if qualifying:
            exit_body = compose(p, raw["envelope"], raw["classification"], raw["dob"], raw["corner"], qualifying,
                                layers, legends, raw["last_sale"], raw["parties"], raw["nta"], args.ask, args.listing,
                                raw["checked"], photo, args.strategy, raw["deeds"], flood=raw["flood"])["body_md"]["comps"]
            res["body_md"]["exit"] = exit_body.split("\n\n", 1)[0] + "\n\nClosed sales identified as renovated or new in the linked listings."
    raw.update({"review": review, "photo": str(photo) if photo else None, "floor_plan": floor_plan,
                "floor_plan_status": media_status, "options": {"radius": args.radius, "months": args.months,
                "ask": args.ask, "listing": args.listing, "strategy": args.strategy}})
    summary_keys = [{"bbl": c["bbl"], "date": c["date"]} for c in purchase[:8]]
    if (review.get("purchase_summary") and review.get("summary_ask") == args.ask
            and review.get("summary_purchase") == summary_keys and args.radius == 0.5 and args.months == 18):
        table = res["body_md"]["comps"].split("\n\n", 1)[0]
        res["body_md"]["comps"] = table + "\n\n" + review["purchase_summary"]
    res.update({"pluto": p, "envelope": raw["envelope"], "classification": raw["classification"], "dob": raw["dob"],
                "comps": purchase[:8], "exit_comps": exits[:8], "excluded_comps": excluded, "corner": raw["corner"],
                "legends": legends, "checked": raw["checked"], "floor_plan": floor_plan,
                "floor_plan_status": media_status, "snapshot": raw})
    if not args.refresh:
        template = out / f"{slug}-review.json"
        if not template.exists():
            template.write_text(json.dumps(review_template(candidates), indent=2))
    (out / f"{slug}.json").write_text(json.dumps(res, indent=1, default=str))
    sections = [res["body_md"]["top"]]
    if floor_plan:
        sections[0] = sections[0].replace("### Details", f"### Floor plan\n\n![Floor plan]({floor_plan})\n\n### Details", 1)
    sections += ["### Area", res["body_md"]["area"], "### Purchase comps", res["body_md"]["comps"]]
    if res["body_md"].get("exit"):
        sections += ["### Exit comps", res["body_md"]["exit"]]
    sections += [res["body_md"]["bottom"], f"### {NOTES_HEADING}"]
    (out / f"{slug}.md").write_text("\n\n".join(sections))
    say(f"wrote {out / (slug + '.md')}; {len(purchase[:8])} purchase comps, {len(exits)} renovated/new, {len(excluded)} excluded")
    if args.notion or args.notion_refresh:
        notion_write(res, area_png, comps_png if purchase else None, photo, refresh=args.notion_refresh or bool(args.refresh))
    return res


def main():
    global RADIUS_NOTE, RADIUS_MI
    ap = argparse.ArgumentParser()
    ap.add_argument("address", nargs="?")
    ap.add_argument("--borough", default="BK", help="MN BX BK QN SI")
    ap.add_argument("--bbl")
    ap.add_argument("--ask", type=float)
    ap.add_argument("--listing")
    ap.add_argument("--radius", type=float, default=0.5, help="miles for maps and comps")
    ap.add_argument("--months", type=int, default=18)
    ap.add_argument("--min-amt", type=float, default=100000)
    ap.add_argument("--out", default=os.environ.get("TMPDIR", "/tmp"))
    ap.add_argument("--photo", help="listing photo as a file path or URL; otherwise Street View when GOOGLE_MAPS_API_KEY is set")
    ap.add_argument("--floor-plan", help="explicit floor-plan image path or URL; otherwise extract from the rendered listing")
    ap.add_argument("--listing-html", help="saved rendered listing HTML (browser alternative for floor-plan extraction)")
    ap.add_argument("--browser-url", default="http://127.0.0.1:9222", help="existing local Chrome debugging endpoint")
    ap.add_argument("--review", help="sale-specific listing review JSON; renovated/new sales leave purchase comps")
    ap.add_argument("--refresh", help="saved screen JSON: rebuild maps and comps with --review, without refetching city records")
    ap.add_argument("--notion-refresh", action="store_true", help="refresh maps, comp table/relation and floor plan only; preserve hand-finished facts and notes")
    ap.add_argument("--map-images", action="store_true", help="also render legacy map PNGs; native database maps are the default")
    ap.add_argument("--refresh-area", action="store_true", help="fetch neighborhood layers and Google Places again; otherwise reuse the shared directory")
    ap.add_argument("--strategy", choices=["Flip", "Rent", "Build"], help="Noah's strategy word; inferred only when exactly one fits")
    ap.add_argument("--notion", action="store_true")
    ap.add_argument("--check", action="store_true", help="print the Python version, which optional packages are present and whether the Notion token is set, then exit")
    args = ap.parse_args()
    if args.check:
        tok = bool(os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_TOKEN"))
        print(f"python {sys.version.split()[0]} · requests {requests.__version__} · maps {'yes' if have_maps() else 'no (pip install matplotlib pillow)'} · "
              f"Notion token {'set' if tok else 'missing (export NOTION_TOKEN)'} · Places key {'set' if os.environ.get('GOOGLE_PLACES_API_KEY') or os.environ.get('GOOGLE_MAPS_API_KEY') else 'not set (optional)'} · "
              f"Street View key {'set' if os.environ.get('GOOGLE_MAPS_API_KEY') else 'not set (optional)'}")
        return
    if args.refresh:
        raw = json.loads(Path(args.refresh).read_text())["snapshot"]
        if args.address or args.bbl:
            ap.error("--refresh uses the saved property; omit the address and --bbl")
        for key in ("radius", "months"):
            if "--" + key in sys.argv and getattr(args, key) != raw["options"][key]:
                ap.error(f"Changing --{key} needs a fresh screen, not --refresh")
        for key, value in raw["options"].items():
            option = "--" + key.replace("_", "-")
            if option not in sys.argv and not any(a.startswith(option + "=") for a in sys.argv):
                setattr(args, key, value)
        RADIUS_NOTE = f"{args.radius:g} mi and {args.months} months"
        RADIUS_MI = args.radius
        if not args.refresh_area and (ROOT / "data" / "amenities.json").exists():
            raw["layers"] = shared_area_layers(raw["pluto"], args.radius, args.notion or args.notion_refresh)
        write_screen(raw, args)
        return
    if not args.bbl and not args.address:
        ap.error("give an address (with --borough) or --bbl")
    checked = dt.date.today().isoformat()
    RADIUS_NOTE = f"{args.radius:g} mi and {args.months} months"
    RADIUS_MI = args.radius
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # progress reaches a log file as it happens, not when the run ends

    p = pluto_by_bbl(args.bbl) if args.bbl else pluto_by_address(args.address, args.borough)
    if not p:
        sys.exit("no PLUTO record found")
    p["bbl"] = norm_bbl(p["bbl"])
    saved_review = ROOT / "data" / "reviews" / f"{p['bbl']}.json"
    if not args.review and saved_review.is_file():
        args.review = str(saved_review)
        say(f"using saved comp review for BBL {p['bbl']}")
    say(f"PLUTO {p.get('version')}: {p['address']} BBL {p['bbl']} {p.get('zonedist1')} lot {p.get('lotfront')}x{p.get('lotdepth')} class {p.get('bldgclass')}")
    boro, block, lot = BORO_CODE[p["borough"]], str(int(p["block"])), str(int(p["lot"]))

    say("facts: ACRIS, DOB, ECB, HPD, DOB NOW and the two flood maps")
    corner = near_corner_estimate(p)
    env = envelope(p, corner)
    dob = dob_facts(boro, block, lot, p["bbl"])
    lat, lon = fnum(p.get("latitude")), fnum(p.get("longitude"))
    flood = flood_zone(lat, lon) if lat and lon else {"effective_ok": False, "preliminary_ok": False}
    say(f"flood: {dict((k, flood.get(k)) for k in ('zone', 'subtype', 'sfha', 'bfe', 'pzone', 'psfha', 'pbfe', 'effective_ok', 'preliminary_ok'))}")
    cls = classify(p, env, dob, flood)
    deeds = sorted(acris_deeds(boro, [(block, lot)], None), key=lambda d: d["date"], reverse=True)
    last_sale, parties = "no deed found in ACRIS Open Data", {}
    if deeds:
        d = deeds[0]
        parties = acris_parties(d["document_id"])
        amt = f"${d['amt']:,.0f}" if d["amt"] else "$0 (non-market)"
        last_sale = f"deed {d['date']} {amt}" + (f" to {parties['buyer'][0].title()}" if parties.get("buyer") else "")
    say(f"envelope: {dict((k, env[k]) for k in ('max_zfa', 'unused', 'U', 'flags', 'lot_type'))} corner: {corner}")
    say(f"classify: {cls['deal_type']} {cls['product']}")
    comps = find_comps(p, env, cls, args.radius, args.months, args.min_amt)
    say(f"comps: {len(comps)} closed {comps[0]['product'] if comps else ''} sales within {args.radius} mi / {args.months} mo; checking DOB alteration jobs on the {min(len(comps), 8)} shown")
    renovation_signal(comps[:8])
    say("area: refreshing sources" if args.refresh_area else "area: reusing the shared place directory")
    layers = area_layers(p, args.radius) if args.refresh_area else shared_area_layers(p, args.radius, args.notion or args.notion_refresh)
    nta = next((n["ntaname"] for n in layers["ntas"] if contains(n["the_geom"], lon, lat)), None)
    raw = {"pluto": p, "envelope": env, "classification": cls, "dob": dob, "corner": corner,
           "candidate_comps": comps, "layers": layers, "last_sale": last_sale, "parties": parties,
           "nta": nta, "checked": checked, "deeds": deeds, "flood": flood}
    write_screen(raw, args)
    say("screen complete")


if __name__ == "__main__":
    main()
