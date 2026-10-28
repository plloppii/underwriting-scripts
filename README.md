# underwriting-scripts

Independent Python tools for NYC property records and nearby places. Each command
returns JSON for a User, agent or another application to interpret. The GitHub repository and Python project are named `underwriting-scripts`.

Underwriting instructions, templates, comp-review evidence, page composition,
and Notion writes live in the sibling
[notion-agentic-skills](https://github.com/plloppii/notion-agentic-skills) repository.
These data tools do not read Notion, private action settings or a repository `.env`.

## Setup

Python 3.10+ and `requests` are required:

```sh
python3 -m pip install -r requirements.txt
python3 scripts/property.py --check
```

## Independent JSON commands

| Command | Result |
| --- | --- |
| `scripts/property.py` or `scripts/screen.py` | PLUTO facts, deeds/parties, DOB/ECB/HPD facts, flood checks and candidate closed sales. |
| `scripts/property_facts.py` | Just the property/lot/zoning facts, numeric estimates and corner geometry. |
| `scripts/sales_history.py` | Just recorded deeds and the latest deed's buyer/seller parties. |
| `scripts/violations.py` | Just DOB, ECB and HPD violation records. |
| `scripts/permits.py` | Just BIS and DOB NOW construction/job filings. |
| `scripts/flood.py` | Just the effective and preliminary flood-map checks. |
| `scripts/comps.py` | Just candidate closed sales and alteration signals on the first eight. |
| `scripts/restaurants.py` | Nearby city inspection-record businesses, deduplicated by CAMIS. |
| `scripts/train_stations.py` | MTA subway station records with GTFS IDs and routes. |
| `scripts/parks.py` | NYC Parks properties and nearest-polygon distances. |
| `scripts/housing_developments.py` | Distinct NYCHA inventory and HPD affordable-housing production records. |

```sh
python3 scripts/property.py --bbl 3010100021 --radius 0.5 --months 18 --out /tmp/property.json
python3 scripts/violations.py --bbl 3010100021
python3 scripts/comps.py --bbl 3010100021 --radius 0.5 --months 18
python3 scripts/restaurants.py --bbl 3010100021 --radius-mi 0.5
python3 scripts/train_stations.py --lat 40.67 --lon -73.99 --radius-mi 0.5
python3 scripts/parks.py "278A 9 STREET" --borough BK --radius-mi 1
python3 scripts/housing_developments.py --bbl 3010100021 --radius-mi 1
```

An address requires its borough; a BBL avoids address-spelling ambiguity. Place
commands also accept coordinates. The property command's `--no-comps` skips
comps but still fetches its other sections; use `property_facts.py` for just lot
and zoning facts. `--out` saves the same JSON printed to stdout; progress uses
stderr. No place search runs implicitly as part of a property screen.

The six individual property commands share the fetching implementation with
the combined command, and do not run unrelated tasks. Their
[JSON interfaces](docs/PROPERTY_TASKS.md) describe records, partial failures and
the compatibility of the combined output.

Exit 0 means the selected queries succeeded, 1 means a supported multi-source
query is partial, and 2 means failure or invalid input. Inspect `status` and
`errors`; zero records is different from an unreachable source. Missing values
stay unknown. [Housing coverage](docs/HOUSING.md) and
[place contracts](docs/PLACES.md) describe the source-specific limitations.

Source/API recipes are in [docs/SOURCES.md](docs/SOURCES.md). Search radii,
windows and sale-price cutoffs are query parameters. The consuming skill gets
its active defaults from the requesting person's private workspace settings.
The JSON contains source facts and calculations, not a final investment verdict.

`property_sources.py` owns the reusable public-data implementation. The optional
`google_food.py` helper accepts exported Google Places key variables for clients
that explicitly request that source; public directory searches require no key.
No tool writes to Notion or needs a Notion token.

## Checks

```sh
python3 -m unittest discover -s tests -v
```

Keep API snapshots and logs under gitignored `.cache/runs/`. Personal destination
IDs, comp-review decisions and reusable owner directories belong in private
runtime storage managed by the consuming skill, outside this repository.
