#!/usr/bin/env python3
"""Fetch one NYC property's public records and candidate sales as JSON.

No Notion access, page formatting, strategy decisions, or neighborhood searches.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
import property_sources as data
from housing_developments import resolve_origin
from property_tasks import numeric_facts


def collect(args):
    result = {"schema_version": "1.0", "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "checked": dt.date.today().isoformat(), "status": "complete", "errors": [],
              "options": {"radius": args.radius, "months": args.months, "min_amt": args.min_amt}}
    lat, lon, origin = resolve_origin(data.SESSION, args.address, args.borough, args.bbl)
    p = data.pluto_by_bbl(origin["bbl"])
    if not p:
        raise ValueError("The resolved PLUTO lot is no longer available")
    p["bbl"] = data.norm_bbl(p["bbl"])
    result["pluto"] = p
    result["origin"] = origin
    result["provenance"] = {"pluto": origin["source_url"], "acris": "https://data.cityofnewyork.us/d/bnx9-e6tj",
                            "dob": "https://data.cityofnewyork.us/d/ic3t-wcy2", "hpd": "https://data.cityofnewyork.us/d/wvxf-dwi5",
                            "effective_flood": data.FEMA_NFHL, "preliminary_flood": data.NYC_PFIRM}
    boro, block, lot = data.BORO_CODE[p["borough"]], str(int(p["block"])), str(int(p["lot"]))
    def stage(name, callback):
        data.say(name)
        try:
            value = callback()
            result[name] = value
            return value
        except Exception as exc:
            result[name] = None
            result["errors"].append({"source": name, "message": f"{type(exc).__name__}: {exc}"})
    stage("corner", lambda: data.near_corner_estimate(p))
    stage("dob", lambda: data.dob_facts(boro, block, lot, p["bbl"]))
    flood = stage("flood", lambda: data.flood_zone(lat, lon))
    if flood and not (flood.get("effective_ok") and flood.get("preliminary_ok")):
        result["errors"].append({"source": "flood", "message": "At least one flood service could not be checked"})
    deeds = stage("deeds", lambda: sorted(data.acris_deeds(boro, [(block, lot)], None), key=lambda d: d["date"], reverse=True))
    if deeds:
        stage("parties", lambda: data.acris_parties(deeds[0]["document_id"]))
    else:
        result["parties"] = {}
    if not args.no_comps:
        comps = stage("candidate_comps", lambda: data.find_comps(p, args.radius, args.months, args.min_amt))
        if comps:
            stage("renovation_signals", lambda: data.renovation_signal(comps[:8]))
    else:
        result["candidate_comps"] = []
    result["numeric"] = numeric_facts(p)
    result["status"] = "partial" if result["errors"] else "complete"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", nargs="?")
    parser.add_argument("--borough", choices=list(data.BORO_CODE))
    parser.add_argument("--bbl")
    parser.add_argument("--radius", type=float, default=0.5, help="candidate sales radius in miles")
    parser.add_argument("--months", type=int, default=18)
    parser.add_argument("--min-amt", type=float, default=100000)
    parser.add_argument("--no-comps", action="store_true", help="skip comps; still fetch the other property sections")
    parser.add_argument("--out", type=Path, help="also save JSON to a file")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps({"python": sys.version.split()[0], "output": "JSON", "dependencies": ["requests"]}))
        return 0
    if bool(args.address) == bool(args.bbl):
        parser.error("Provide one address or --bbl")
    if not math.isfinite(args.radius) or not 0 < args.radius <= 25 or not 1 <= args.months <= 120:
        parser.error("Radius must be 0–25 miles (exclusive of zero); months must be 1–120")
    if not math.isfinite(args.min_amt) or args.min_amt < 0:
        parser.error("--min-amt must be a nonnegative finite number")
    try:
        result = collect(args)
    except Exception as exc:
        result = {"schema_version": "1.0", "status": "failed", "errors": [{"source": "property", "message": str(exc)}]}
    serialized = json.dumps(result, indent=2, default=str, allow_nan=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    return {"complete": 0, "partial": 1, "failed": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
