#!/usr/bin/env python3
"""Find nearby NYCHA inventory and HPD affordable-housing records; emit JSON only.

Independent of screen.py, Notion, Google, and local integration settings.
See docs/HOUSING.md for coverage, field definitions, and exit codes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

API = "https://data.cityofnewyork.us"
EARTH_MI = 3958.8
SOURCES = {
    "nycha": {
        "dataset_id": "phvi-damg", "name": "NYCHA Public Housing Developments",
        "order": "tds_num", "fields": "tds_num,developmen,borough,program,the_geom",
        "coverage": "NYCHA mapped development inventory; not proof of current ownership, management, or subsidy status.",
    },
    "hpd": {
        "dataset_id": "hg8x-zxpr", "name": "Affordable Housing Production by Building",
        "order": "project_id,building_id",
        "fields": "project_id,project_name,building_id,house_number,street_name,borough,bbl,bin,"
                  "latitude,longitude,latitude_internal,longitude_internal,project_start_date,"
                  "project_completion_date,building_completion_date,reporting_construction_type,"
                  "extended_affordability_status,all_counted_units,total_units,counted_rental_units,"
                  "counted_homeownership_units,extremely_low_income_units,very_low_income_units,"
                  "low_income_units,moderate_income_units,middle_income_units,other_income_units",
        "coverage": "HPD production records for projects begun since 2014; includes preservation and new construction. "
                    "Not a complete inventory of affordable housing or proof of current availability or ownership. "
                    "Withheld or missing locations cannot be matched by radius.",
    },
}


class SourceError(Exception):
    """A source could not provide a complete, consistent result."""


def get_json(session, path, params=None):
    for attempt in range(3):
        try:
            response = session.get(API + path, params=params, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(attempt + 1)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 2:
                raise SourceError(f"Unable to read {path}: {type(exc).__name__}") from exc
            time.sleep(attempt + 1)


def count_rows(session, dataset, where):
    rows = get_json(session, f"/resource/{dataset}.json", {"$select": "count(*) AS n", "$where": where})
    if not isinstance(rows, list) or len(rows) != 1:
        raise SourceError("Invalid source count")
    try:
        return int(rows[0]["n"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("Invalid source count") from exc


def fetch_rows(session, spec, where, page_size=1000):
    """Discard the entire source on truncation, schema drift, or mid-pull edits."""
    dataset = spec["dataset_id"]
    before = get_json(session, f"/api/views/{dataset}.json")
    if not isinstance(before, dict) or not isinstance(before.get("columns"), list):
        raise SourceError("Invalid source metadata")
    required = set(spec["fields"].split(","))
    available = {c.get("fieldName") for c in before["columns"] if isinstance(c, dict)}
    if not required <= available or before.get("rowsUpdatedAt") is None:
        raise SourceError("Source schema or update timestamp is missing")
    expected = count_rows(session, dataset, where)
    rows = []
    while len(rows) < expected:
        page = get_json(session, f"/resource/{dataset}.json", {
            "$select": spec["fields"], "$where": where, "$order": spec["order"],
            "$limit": page_size, "$offset": len(rows),
        })
        if not isinstance(page, list) or not page or not all(isinstance(r, dict) for r in page):
            raise SourceError("Source pagination ended before its reported count")
        rows.extend(page)
    after = get_json(session, f"/api/views/{dataset}.json")
    if not isinstance(after, dict) or len(rows) != expected or before["rowsUpdatedAt"] != after.get("rowsUpdatedAt"):
        raise SourceError("Source changed during the pull or returned an inconsistent count; retry")
    updated = dt.datetime.fromtimestamp(before["rowsUpdatedAt"], dt.timezone.utc).isoformat()
    return rows, updated


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def integer(value):
    value = number(value)
    return int(value) if value is not None and value.is_integer() else None


def coordinates(row):
    for lat_key, lon_key, basis in (
        ("latitude_internal", "longitude_internal", "tax_lot_centroid"),
        ("latitude", "longitude", "address_estimate"),
    ):
        lat, lon = number(row.get(lat_key)), number(row.get(lon_key))
        if lat is not None and lon is not None and 40 <= lat <= 42 and -75 <= lon <= -72:
            return lat, lon, basis
    return None


def distance_mi(lat, lon, other_lat, other_lon):
    a, b = math.radians(lat), math.radians(other_lat)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(other_lon - lon) / 2) ** 2
    return 2 * EARTH_MI * math.asin(math.sqrt(min(1, h)))


def nearest_point(geometry, lat, lon):
    """Nearest polygon point in a local projection; account for holes and components."""
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise SourceError("NYCHA record has unsupported geometry")
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    scale = math.cos(math.radians(lat))
    best = None
    for polygon in polygons:
        if not polygon:
            continue

        def inside(ring):
            result = False
            for a, b in zip(ring, ring[1:] + ring[:1]):
                if (a[1] > lat) != (b[1] > lat) and lon < a[0] + (lat - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                    result = not result
            return result

        if inside(polygon[0]) and not any(inside(ring) for ring in polygon[1:]):
            return lat, lon
        for ring in polygon:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                ax, ay = (a[0] - lon) * scale, a[1] - lat
                bx, by = (b[0] - lon) * scale, b[1] - lat
                dx, dy = bx - ax, by - ay
                t = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0
                x, y = ax + t * dx, ay + t * dy
                candidate = (x * x + y * y, lat + y, lon + x / scale)
                if best is None or candidate[0] < best[0]:
                    best = candidate
    if best is None:
        raise SourceError("NYCHA record has no polygon boundary")
    return best[1], best[2]


def bbox(lat, lon, radius):
    # Spherical circle bounds; the polygon query is a superset of the final radius.
    angle = radius / EARTH_MI
    dy = math.degrees(angle)
    dx = math.degrees(math.asin(math.sin(angle) / math.cos(math.radians(lat))))
    return lat - dy, lat + dy, lon - dx, lon + dx


def search_where(source, lat, lon, radius):
    south, north, west, east = bbox(lat, lon, radius)
    if source == "nycha":
        return f"intersects(the_geom, 'POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))')"
    return " OR ".join(
        f"({a} between {south} and {north} AND {b} between {west} and {east})"
        for a, b in (("latitude_internal", "longitude_internal"), ("latitude", "longitude"))
    )


def record_url(dataset, **params):
    return API + f"/resource/{dataset}.json?" + urlencode(params)


def normalize(source, row, lat, lon, radius):
    spec = SOURCES[source]
    if source == "nycha":
        if not row.get("tds_num") or not row.get("the_geom"):
            raise SourceError("NYCHA record lacks an identifier or boundary")
        point_lat, point_lon = nearest_point(row["the_geom"], lat, lon)
        record = {
            "source_id": "nycha:" + row["tds_num"], "category": "nycha_development",
            "name": row.get("developmen"), "borough": row.get("borough"),
            "program": row.get("program"), "geometry": row["the_geom"],
            "source_url": record_url(spec["dataset_id"], tds_num=row["tds_num"]),
        }
        basis = "nearest_development_polygon_point"
    else:
        point = coordinates(row)
        if point is None:
            return None
        point_lat, point_lon, basis = point
        if not row.get("project_id") or row.get("building_id") is None:
            raise SourceError("Located HPD record lacks a project or building identifier")
        project_id, building_id = row["project_id"], str(integer(row["building_id"]))
        if building_id == "None":
            raise SourceError("HPD building identifier is invalid")
        record = {
            "source_id": f"hpd:{project_id}:{building_id}", "category": "hpd_affordable_housing_record",
            "name": row.get("project_name"), "project_id": project_id, "building_id": building_id,
            "address": " ".join(str(row[k]) for k in ("house_number", "street_name") if row.get(k)) or None,
            "borough": row.get("borough"), "bbl": str(integer(row["bbl"])) if integer(row.get("bbl")) else None,
            "bin": str(integer(row["bin"])) if integer(row.get("bin")) else None,
            "construction_type": row.get("reporting_construction_type"),
            "project_start_date": row.get("project_start_date"),
            "project_completion_date": row.get("project_completion_date"),
            "building_completion_date": row.get("building_completion_date"),
            "extended_affordability_status": row.get("extended_affordability_status"),
            "units": {key: integer(row.get(key)) for key in (
                "all_counted_units", "total_units", "counted_rental_units", "counted_homeownership_units",
                "extremely_low_income_units", "very_low_income_units", "low_income_units",
                "moderate_income_units", "middle_income_units", "other_income_units",
            )},
            "source_url": record_url(spec["dataset_id"], project_id=project_id, building_id=building_id),
        }
    distance = distance_mi(lat, lon, point_lat, point_lon)
    if distance > radius:
        return None
    record.update({"source": source, "location": {"lat": point_lat, "lon": point_lon, "basis": basis},
                   "distance_mi": round(distance, 6), "distance_method": "straight_line"})
    return record


def collect(session, lat, lon, radius, source="all"):
    if not (math.isfinite(lat) and math.isfinite(lon) and 40 <= lat <= 42 and -75 <= lon <= -72):
        raise ValueError("Search coordinates must be in the NYC area (latitude 40–42, longitude -75–-72)")
    if not math.isfinite(radius) or not 0 < radius <= 25:
        raise ValueError("Radius must be greater than 0 and at most 25 miles")
    if source not in ("all", *SOURCES):
        raise ValueError("Unknown housing source")
    output = {
        "schema_version": "1.0", "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "query": {"lat": lat, "lon": lon, "radius_mi": radius},
        "status": "complete", "sources": {}, "records": [], "errors": [],
        "notes": ["Categories may overlap across sources; counts must not be added as unique buildings or units.",
                  "A source-complete search is not a complete housing inventory. Absence is not proof of no subsidized housing."],
    }
    for key in SOURCES if source == "all" else (source,):
        spec = SOURCES[key]
        manifest = {"dataset_id": spec["dataset_id"], "name": spec["name"],
                    "url": API + "/d/" + spec["dataset_id"], "coverage": spec["coverage"], "status": "failed"}
        output["sources"][key] = manifest
        try:
            rows, updated = fetch_rows(session, spec, search_where(key, lat, lon, radius))
            records = [record for row in rows if (record := normalize(key, row, lat, lon, radius)) is not None]
            if len({r["source_id"] for r in records}) != len(records):
                raise SourceError("Duplicate source identifiers; refusing to collapse distinct source rows")
            missing = None
            if key == "hpd":
                missing = count_rows(session, spec["dataset_id"],
                    "(latitude IS NULL OR longitude IS NULL) AND (latitude_internal IS NULL OR longitude_internal IS NULL)")
            manifest.update({"status": "complete", "data_updated_at": updated,
                             "candidate_count": len(rows), "matched_count": len(records)})
            if key == "hpd":
                manifest["citywide_records_without_coordinates"] = missing
            output["records"].extend(records)
        except (SourceError, KeyError, TypeError, ValueError, IndexError) as exc:
            output["errors"].append({"source": key, "message": str(exc)})
    failures = len(output["errors"])
    output["status"] = "failed" if failures == len(output["sources"]) else "partial" if failures else "complete"
    output["records"].sort(key=lambda r: (r["distance_mi"], r["source_id"]))
    return output


def resolve_origin(session, address=None, borough=None, bbl=None):
    if bbl:
        if not re.fullmatch(r"[1-5][0-9]{9}", bbl):
            raise ValueError("BBL must be ten digits, beginning with borough code 1–5")
        where = f"bbl={bbl}"
    else:
        if not address or not borough:
            raise ValueError("An address requires --borough MN, BX, BK, QN, or SI")
        escaped = address.strip().upper().replace("'", "''")
        where = f"upper(address)='{escaped}' AND borough='{borough}'"
    params = {"$where": where, "$select": "address,borough,bbl,latitude,longitude", "$limit": 2}
    rows = get_json(session, "/resource/64uk-42ks.json", params)
    if not isinstance(rows, list) or len(rows) != 1 or coordinates(rows[0]) is None:
        raise SourceError("PLUTO must return exactly one lot with coordinates; use --bbl or --lat/--lon")
    row = rows[0]
    lat, lon, _ = coordinates(row)
    return lat, lon, {"address": row.get("address"), "borough": row.get("borough"),
                      "bbl": str(integer(row.get("bbl"))), "source_url": record_url("64uk-42ks", **params)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", nargs="?")
    parser.add_argument("--borough", choices=["MN", "BX", "BK", "QN", "SI"])
    parser.add_argument("--bbl")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--radius-mi", type=float, default=0.5)
    parser.add_argument("--source", choices=["all", "nycha", "hpd"], default="all")
    parser.add_argument("--out", type=Path, help="also save the JSON to this file")
    args = parser.parse_args(argv)
    has_coords = args.lat is not None or args.lon is not None
    if sum((bool(args.address), bool(args.bbl), has_coords)) != 1 or has_coords and (args.lat is None or args.lon is None):
        parser.error("Provide one address, --bbl, or both --lat and --lon")
    if args.address and not args.borough:
        parser.error("An address requires --borough")
    # Validate arguments before any network request, including geocoding.
    if not math.isfinite(args.radius_mi) or not 0 < args.radius_mi <= 25:
        parser.error("--radius-mi must be greater than 0 and at most 25")
    if args.bbl and not re.fullmatch(r"[1-5][0-9]{9}", args.bbl):
        parser.error("--bbl must be ten digits, beginning with borough code 1–5")
    try:
        with requests.Session() as session:
            session.headers["User-Agent"] = "nyc-property-tools/housing/1.0"
            origin = None
            lat, lon = args.lat, args.lon
            if not has_coords:
                lat, lon, origin = resolve_origin(session, args.address, args.borough, args.bbl)
            result = collect(session, lat, lon, args.radius_mi, args.source)
            if origin:
                result["query"]["property"] = origin
    except (SourceError, ValueError) as exc:
        result = {"schema_version": "1.0", "status": "failed", "records": [],
                  "errors": [{"source": "query", "message": str(exc)}]}
    serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    return {"complete": 0, "partial": 1, "failed": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
