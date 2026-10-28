"""Public NYC property APIs and numeric/source helpers. No Notion or user settings."""
from __future__ import annotations
import datetime as dt
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
import requests

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


POOL = 6  # Bound Open Data concurrency, including paginated ACRIS queries.


T0 = time.time()


def _parallel(fn, items: list) -> list:
    """fn over items, POOL at a time, results in input order."""
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(POOL) as ex:
        return list(ex.map(fn, items))


def soda(ds: str, params: dict, host: str = NYC, tries: int = 4) -> list[dict]:
    """Read every ordered page unless the caller explicitly requests a limit."""
    explicit_limit = "$limit" in params
    query = {"$limit": 5000, "$order": ":id", **params}
    result = []
    while True:
        for attempt in range(tries):
            try:
                response = SESSION.get(host + ds + ".json", params=query, timeout=90)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
                    raise RuntimeError(f"{ds} did not return source rows")
                break
            except requests.RequestException:
                if attempt == tries - 1:
                    raise
                time.sleep(2 * (attempt + 1))
        result.extend(page)
        if explicit_limit or len(page) < query["$limit"]:
            return result
        query["$offset"] = query.get("$offset", 0) + len(page)


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


def acris_deeds(boro: str, pairs: list[tuple[str, str]], since_iso: str | None, min_amt: float = 0) -> list[dict]:
    """Deeds for (block, lot) pairs: [{block, lot, document_id, date, amt, lots_in_doc}]."""
    by_block: dict[str, list[str]] = {}
    for b, l in pairs:
        by_block.setdefault(str(int(b)), []).append(str(int(l)))
    if not by_block:
        return []
    queries = [(block, lots[k:k + 60]) for block, lots in by_block.items() for k in range(0, len(lots), 60)]
    # OR exact block/lot clauses together, rather than requesting each block.
    # Bound the encoded URL size; soda still reads every page of each batch.
    batches, batch = [], []
    pair_order = {}
    for index, (block, lots) in enumerate(queries):
        for lot in lots:
            pair_order[(block, lot)] = index
        clause = f"(block='{block}' AND lot in ({','.join(repr(lot) for lot in lots)}))"
        if batch and len(urlencode({"$where": f"borough='{boro}' AND ({' OR '.join(batch + [clause])})"})) > 6000:
            batches.append(batch)
            batch = []
        batch.append(clause)
    batches.append(batch)

    def legals_for(clauses: list[str]) -> list[dict]:
        return soda(DS["legals"], {"$where": f"borough='{boro}' AND ({' OR '.join(clauses)})", "$select": "document_id,block,lot"})

    master_select = "document_id,document_date,document_amt,recorded_datetime"

    def deeds_for(chunk_ids: list[str]) -> list[dict]:
        chunk = ",".join(f"'{x}'" for x in chunk_ids)
        where = f"document_id in ({chunk}) AND doc_type='DEED'"
        if since_iso:
            where += f" AND document_date >= '{since_iso}T00:00:00.000'"
        if min_amt:
            where += f" AND document_amt >= {min_amt}"
        return soda(DS["master"], {"$where": where, "$select": master_select})

    legals = [x for rows in _parallel(legals_for, batches) for x in rows]
    # Preserve the previous per-block/chunk order, including same-day sale ties.
    legals.sort(key=lambda x: pair_order[(str(int(x["block"])), str(int(x["lot"])))])
    ids = sorted({x["document_id"] for x in legals})
    since = dt.date.fromisoformat(since_iso) if since_iso else None
    today = dt.date.today()
    if since and 0 <= (today - since).days <= 730 and len(ids) > 3000:
        # Dense neighborhood searches otherwise request tens of thousands of
        # historical mortgage/document IDs just to discard almost all of them.
        # Read recent qualifying deeds citywide and join by the same IDs instead.
        # Disjoint date windows allow bounded concurrency; the last is unbounded
        # above so even future-dated source records retain the original coverage.
        starts = [since]
        while starts[-1] + dt.timedelta(days=120) < today:
            starts.append(starts[-1] + dt.timedelta(days=120))
        windows = [(start, starts[i + 1] if i + 1 < len(starts) else None) for i, start in enumerate(starts)]

        def recent_deeds(window: tuple[dt.date, dt.date | None]) -> list[dict]:
            start, end = window
            where = f"doc_type='DEED' AND document_date >= '{start.isoformat()}T00:00:00.000'"
            if end:
                where += f" AND document_date < '{end.isoformat()}T00:00:00.000'"
            if min_amt:
                where += f" AND document_amt >= {min_amt}"
            return soda(DS["master"], {"$where": where, "$select": master_select})

        pages = _parallel(recent_deeds, windows)
    else:
        # Single-property history, sparse areas and long historical windows
        # remain scoped to their document IDs rather than a citywide download.
        pages = _parallel(deeds_for, [ids[k:k + 150] for k in range(0, len(ids), 150)])
    deeds = {m["document_id"]: m for rows in pages for m in rows}
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


def dob_violation_rows(boro: str, block: str, lot: str) -> list[dict]:
    b5, l5, l4 = block.zfill(5), lot.zfill(5), lot.zfill(4)
    viol = soda(DS["dobviol"], {"$where": f"boro='{boro}' AND block='{b5}' AND lot='{l5}'",
                                "$select": "bin,issue_date,violation_category,violation_type,disposition_date"})

    return viol


def ecb_violation_rows(boro: str, block: str, lot: str) -> list[dict]:
    b5, l5, l4 = block.zfill(5), lot.zfill(5), lot.zfill(4)
    ecb = soda(DS["ecb"], {"$where": f"boro='{boro}' AND block='{b5}' AND lot='{l4}'",
                           "$select": "bin,issue_date,ecb_violation_status,severity,violation_description,balance_due"})

    return ecb


def hpd_violation_rows(bbl: str) -> list[dict]:
    hpd = soda(DS["hpd"], {"$where": f"bbl='{bbl}'", "$select": "class,violationstatus,novissueddate,novdescription,registrationid,bin,buildingid"})

    return hpd


def bis_job_rows(bbl: str) -> list[dict]:
    bis = soda(DS["bisjobs"], {"$where": f"bbl='{bbl}'",
                               "$select": "job__,job_type,job_status_descrp,pre__filing_date,latest_action_date,proposed_dwelling_units,proposed_zoning_sqft,bin__,job_description"})

    return bis


def dobnow_job_rows(bbl: str) -> list[dict]:
    now = soda(DS["dobnow"], {"$where": f"bbl='{bbl}'",
                              "$select": "job_filing_number,job_type,filing_status,filing_date,approved_date,signoff_date,proposed_dwelling_units,total_construction_floor_area,bin,job_description"})

    return now


def summarize_building_facts(bbl: str, viol: list, ecb: list, bis: list, now: list, hpd: list) -> dict:
    bins = ({x.get("bin") for x in viol} | {x.get("bin") for x in ecb}
            | {x.get("bin__") for x in bis} | {x.get("bin") for x in now}
            | {x.get("bin") for x in hpd})
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



def dob_facts(boro: str, block: str, lot: str, bbl: str) -> dict:
    """Combined compatibility view over the same independent source queries."""
    return summarize_building_facts(
        bbl, dob_violation_rows(boro, block, lot), ecb_violation_rows(boro, block, lot),
        bis_job_rows(bbl), dobnow_job_rows(bbl), hpd_violation_rows(bbl),
    )


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


def find_comps(p: dict, radius_mi: float, months: int, min_amt: float = 100000) -> list[dict]:
    lat, lon = fnum(p["latitude"]), fnum(p["longitude"])
    la, lb, lo, lhi = bbox(lat, lon, radius_mi)
    product, prefixes, basis = product_family(p.get("bldgclass", ""), int(fnum(p.get("unitsres"), 0) or 0))
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
        if dist > radius_mi:
            continue
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


def nta_polygons(lat, lon, radius_mi):
    la, lb, lo, lhi = bbox(lat, lon, radius_mi * 1.2)
    poly = f"POLYGON(({lo} {la},{lhi} {la},{lhi} {lb},{lo} {lb},{lo} {la}))"
    return soda(DS["nta"], {"$where": f"intersects(the_geom, '{poly}')", "$select": "ntaname,nta2020,the_geom"})


def _month(d: str) -> str:
    try:
        return dt.date.fromisoformat(str(d)[:10]).strftime("%B %Y")
    except ValueError:
        return str(d)


def say(message: str) -> None:
    print(f"[{time.time() - T0:4.0f}s] {message}", file=sys.stderr, flush=True)

SESSION.headers["User-Agent"] = "nyc-property-tools/1.0"
