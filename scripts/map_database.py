"""Populate native map locations and import reusable, sourced amenities into Properties.

Only row properties are written; property page bodies and Noah's notes are untouched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from notion_api import load_state, req
from notion_deals import _rows, _text, pluto_rows
from screen import BORO_NAME, DS, NYS, contains, fnum, haversine_mi, is_food_primary, nearest_park_point, rep_point, soda, _parallel

V = "2022-06-28"
PLACE_TYPES = {"Restaurant", "Park", "Subway station"}


def normalized_name(name):
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", plain)


def discover_amenities(inventory_path, cache):
    """Resolve places already named in deal pages against reusable public city data.

    Google ratings are not imported into the permanent directory. Unresolved names
    stay in a report for review; they are never assigned invented locations.
    """
    inventory = json.loads(inventory_path.read_text())
    city = json.loads((cache / "city-lots.json").read_text())
    coordinates = {r["id"]: city.get(_text(r["properties"].get("BBL")), {})
                   for r in json.loads((cache / "rows-after-rename.json").read_text())}
    foods, parks, stations, skipped = {}, {}, {}, []
    for entry in inventory:
        line = entry["line"]
        name = re.sub(r"^\d+\.\s*", "", line.split(" · ")[0])
        if "ratings)" in line or "restaurant" in line or "cafe" in line:
            kind = line.split(" · ")[1].lower().replace(" ", "_")
            if not is_food_primary(kind):
                skipped.append({"name": name, "reason": "Existing entry is not a restaurant, cafe or bakery"})
                continue
            foods.setdefault(name, []).append(entry)
        elif " · park" in line:
            parks.setdefault(name, []).append(entry)
        elif " · subway" in line or re.search(r"\([A-Z0-9 ]+\)$", name):
            stations.setdefault(name, []).append(entry)
    nta = soda(DS["nta"], {"$select": "ntaname,the_geom"})
    (cache / "neighborhoods.json").write_text(json.dumps(nta))
    def neighborhood(lat, lon):
        return next((n["ntaname"] for n in nta if contains(n["the_geom"], lon, lat)), None)
    def distance(row, entries):
        distances = []
        for entry in entries:
            lot = coordinates.get(entry["page_id"], {})
            if lot.get("latitude") and lot.get("longitude"):
                distances.append(haversine_mi(float(lot["latitude"]), float(lot["longitude"]), row["lat"], row["lon"]))
        return min(distances, default=float("inf"))
    records, unresolved = {}, skipped
    subway = soda(DS["subway"], {"$select": "gtfs_stop_id,stop_name,daytime_routes,gtfs_latitude,gtfs_longitude"}, host=NYS)
    for name, entries in stations.items():
        match = re.match(r"^(.*?)\s*\((.*?)\)$", name)
        base, routes = (match[1], set(match[2].split())) if match else (name, set())
        matches = []
        for station in subway:
            if normalized_name(base) != normalized_name(station["stop_name"]):
                continue
            if routes and not routes.intersection(set(station["daytime_routes"].split())):
                continue
            record = {"source_id": "mta:" + station["gtfs_stop_id"], "name": station["stop_name"] + " (" + station["daytime_routes"].strip() + ")",
                      "type": "Subway station", "lat": float(station["gtfs_latitude"]), "lon": float(station["gtfs_longitude"]),
                      "routes": station["daytime_routes"], "source_url": "https://data.ny.gov/resource/39hk-dx4f.json?gtfs_stop_id=" + station["gtfs_stop_id"]}
            if distance(record, entries) < 1:
                matches.append(record)
        if matches:
            for record in matches:
                records[record["source_id"]] = record
        else:
            unresolved.append({"name": name, "type": "Subway station"})
    names = ",".join("'" + name.replace("'", "''").upper() + "'" for name in parks)
    park_rows = soda(DS["parks"], {"$where": f"upper(signname) in ({names})", "$select": "gispropnum,signname,acres,multipolygon"}) if names else []
    for name, entries in parks.items():
        matches = [p for p in park_rows if normalized_name(p["signname"]) == normalized_name(name)]
        for park in matches:
            lat, lon = rep_point(park["multipolygon"])
            if not contains(park["multipolygon"], lon, lat):
                lat, lon = nearest_park_point(park["multipolygon"], lat, lon)
            record = {"source_id": "nyc-parks:" + park["gispropnum"], "name": park["signname"], "type": "Park", "lat": lat, "lon": lon,
                      "geometry": park["multipolygon"], "acres": fnum(park.get("acres")),
                      "source_url": "https://data.cityofnewyork.us/resource/enfh-gkve.json?gispropnum=" + park["gispropnum"]}
            records[record["source_id"]] = record
        if not matches:
            unresolved.append({"name": name, "type": "Park"})
    terms = sorted({max(re.findall(r"[A-Z]{3,}", unicodedata.normalize("NFKD", name.upper())), key=len, default="") for name in foods} - {""})
    def food_batch(terms):
        where = " OR ".join(f"upper(dba) like '%{term}%'" for term in terms)
        return soda("43nn-pn8j", {"$where": f"boro in ('Brooklyn','Queens') AND ({where})", "$select": "distinct camis,dba,boro,building,street,latitude,longitude"})
    restaurant_rows = [r for batch in _parallel(food_batch, [terms[i:i + 12] for i in range(0, len(terms), 12)]) for r in batch]
    for name, entries in foods.items():
        candidates = {}
        for restaurant in restaurant_rows:
            lat, lon = fnum(restaurant.get("latitude")), fnum(restaurant.get("longitude"))
            if lat is None or lon is None:
                continue
            a, b = normalized_name(name), normalized_name(restaurant["dba"])
            score = SequenceMatcher(None, a, b).ratio()
            stop = r"\b(?:restaurant|bakery|brooklyn|cafe|coffee|roasters|bar|grill|kitchen)\b"
            ca = normalized_name(re.sub(stop, "", name, flags=re.I))
            cb = normalized_name(re.sub(stop, "", restaurant["dba"], flags=re.I))
            if ca and cb and ca == cb:
                score = 1.0
            elif len(a) >= 5 and (a in b or b in a):
                score = max(score, 0.9)
            record = {"source_id": "nyc-dohmh:" + restaurant["camis"], "name": restaurant["dba"].title(), "type": "Restaurant",
                      "lat": lat, "lon": lon, "address": f"{restaurant.get('building', '')} {restaurant.get('street', '').title()}, {restaurant['boro']}, NY",
                      "source_url": "https://data.cityofnewyork.us/resource/43nn-pn8j.json?camis=" + restaurant["camis"]}
            dist = distance(record, entries)
            if score >= 0.8 and dist <= 0.8:
                candidates[record["source_id"]] = (score, -dist, record)
        ranked = sorted(candidates.values(), key=lambda x: (x[0], x[1]), reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.04 or ranked[0][1] - ranked[1][1] > 0.15):
            record = ranked[0][2]
            records[record["source_id"]] = record
        else:
            unresolved.append({"name": name, "type": "Restaurant", "reason": "No unambiguous city-record match"})
    for record in records.values():
        record["neighborhood"] = neighborhood(record["lat"], record["lon"])
        record["borough"] = "Queens" if record["source_id"].startswith("nyc-parks:Q") else None
    output = sorted(records.values(), key=lambda r: (r["type"], r["name"]))
    (cache / "amenities.json").write_text(json.dumps(output, indent=2))
    (cache / "unresolved-amenities.json").write_text(json.dumps(unresolved, indent=2))
    print(f"Resolved {len(output)} unique amenities; {len(unresolved)} names need review", flush=True)
    return output


def place_value(name, lat, lon, address=None):
    lat, lon = fnum(lat), fnum(lon)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    value = {"lat": lat, "lon": lon, "name": name}
    if address:
        value["address"] = address
    return {"place": value}


def populate_property_locations(db, cache):
    rows = _rows(db)
    properties = [r for r in rows if (r["properties"].get("Type", {}).get("select") or {}).get("name") in ("Deal", "Comp")]
    bbls = {_text(r["properties"].get("BBL")) for r in properties}
    city_path = cache / "city-lots.json"
    city = json.loads(city_path.read_text()) if city_path.exists() else pluto_rows(bbls - {""})
    city_path.write_text(json.dumps(city, indent=2))
    unresolved, changed = [], 0
    for i, row in enumerate(properties, 1):
        bbl = _text(row["properties"].get("BBL"))
        lot = city.get(bbl)
        if not lot:
            unresolved.append({"page_id": row["id"], "bbl": bbl, "reason": "No city lot record"})
            continue
        name = _text(row["properties"]["Name"])
        address = f"{lot['address'].title()}, {BORO_NAME[lot['borough']]}, NY"
        location = place_value(name, lot.get("latitude"), lot.get("longitude"), address)
        if not location:
            unresolved.append({"page_id": row["id"], "bbl": bbl, "reason": "Coordinates unknown"})
            continue
        current = row["properties"].get("Location", {}).get("place") or {}
        if any(current.get(k) != v for k, v in location["place"].items()):
            req("PATCH", f"/pages/{row['id']}", {"properties": {"Location": location}}, version=V)
            changed += 1
        if i % 20 == 0 or i == len(properties):
            print(f"Locations: checked {i}/{len(properties)}, updated {changed}", flush=True)
    (cache / "unresolved.json").write_text(json.dumps(unresolved, indent=2))
    return {"properties": len(properties), "updated": changed, "unresolved": unresolved}


def import_amenities(db, path):
    records = json.loads(path.read_text())
    existing = {_text(r["properties"].get("Source ID")): r for r in _rows(db) if _text(r["properties"].get("Source ID"))}
    ids = []
    for record in records:
        if record["type"] not in PLACE_TYPES or not record.get("source_id") or not record.get("source_url"):
            raise ValueError("Amenities require a supported Type and stable source identity")
        location = place_value(record["name"], record.get("lat"), record.get("lon"), record.get("address"))
        if not location:
            raise ValueError(f"Coordinates unknown for {record['name']}")
        props = {"Name": {"title": [{"text": {"content": record["name"]}}]},
                 "Type": {"select": {"name": record["type"]}}, "Location": location,
                 "Source ID": {"rich_text": [{"text": {"content": record["source_id"]}}]},
                 "Source URL": {"url": record["source_url"]}}
        if record.get("neighborhood"):
            props["Neighborhood"] = {"select": {"name": record["neighborhood"]}}
        if record.get("borough"):
            props["Borough"] = {"select": {"name": record["borough"]}}
        row = existing.get(record["source_id"])
        if row:
            result = req("PATCH", f"/pages/{row['id']}", {"properties": props}, version=V)
        else:
            result = req("POST", "/pages", {"parent": {"database_id": db}, "properties": props}, version=V)
            existing[record["source_id"]] = result
        ids.append(result["id"])
        print(f"Amenity: {record['type']} · {record['name']}", flush=True)
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locations", action="store_true", help="fill existing property locations from city lot coordinates")
    ap.add_argument("--amenities", type=Path, help="import sourced place records from JSON, deduped by Source ID")
    ap.add_argument("--discover", type=Path, help="resolve an existing deal-page area inventory using public city datasets")
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache" / "migrations" / "type-map")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    state = load_state()
    db = state["databases"]["properties"]
    if args.discover:
        discover_amenities(args.discover, args.cache)
    if args.locations:
        result = populate_property_locations(db, args.cache)
        (args.cache / "location-result.json").write_text(json.dumps(result, indent=2))
    if args.amenities:
        ids = import_amenities(db, args.amenities)
        (args.cache / "amenity-page-ids.json").write_text(json.dumps(ids, indent=2))


if __name__ == "__main__":
    main()
