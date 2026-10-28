# Public and affordable housing JSON tool

`scripts/housing_developments.py` is an independent data tool. It reads official
NYC APIs and returns JSON; it requires only Python 3.10+ and `requests`. It does
not import the underwriting screen, read integration credentials or Notion
records, render maps, or write to Notion. An agent chooses how to interpret and
present its results.

## Usage

```sh
python3 scripts/housing_developments.py --bbl 3010100021 --radius-mi 1
python3 scripts/housing_developments.py "278A 9 STREET" --borough BK
python3 scripts/housing_developments.py --lat 40.67 --lon -73.99 --radius-mi 0.5
python3 scripts/housing_developments.py --bbl 3010100021 --source nycha
python3 scripts/housing_developments.py --bbl 3010100021 --source hpd --out /tmp/housing.json
```

Choose one origin: an address plus borough (`MN`, `BX`, `BK`, `QN`, `SI`), a
ten-digit BBL, or coordinates. Address resolution uses an exact PLUTO address
match and rejects ambiguous results; use the BBL when an address differs from
the city's spelling. Search coordinates must be in the NYC area. The default
radius is 0.5 miles; the maximum is 25 miles. Both housing sources run unless
`--source` selects one. JSON always goes to stdout; `--out` also saves that
same document to the specified file.

## Official sources and coverage

| Category | Source | What the result establishes |
| --- | --- | --- |
| `nycha_development` | [NYCHA Public Housing Developments, phvi-damg](https://data.cityofnewyork.us/Housing-Development/NYCHA-Public-Housing-Developments/phvi-damg) | A development in NYCHA's mapped inventory, its name, program, borough and published boundary. Inclusion does not establish current ownership, management, or subsidy status. |
| `hpd_affordable_housing_record` | [HPD Affordable Housing Production by Building, hg8x-zxpr](https://data.cityofnewyork.us/Housing-Development/Affordable-Housing-Production-by-Building/hg8x-zxpr) | A building/project record counted toward the city's housing plans since 2014; includes new construction and preservation. It is not a complete inventory of affordable housing. |

HPD's [source overview](https://www.nyc.gov/site/hpd/about/open-data.page) and
the field descriptions in its [dataset metadata](https://data.cityofnewyork.us/api/views/hg8x-zxpr.json)
explain the production records. In particular:

- `project_start_date` records loan/regulatory closing or notice of intent to
  market affordable units. It is not a verified construction start.
- Project and building completion dates remain separate. A blank completion
  date remains `null`; the tool does not infer an opening date or occupancy.
- `units.all_counted_units` is the building's affordable units counted toward
  the housing plan. `units.total_units` includes affordable and market-rate
  units. Missing source counts remain `null`, while reported zero remains `0`.
- These are production records, not current vacancy, rent, or eligibility data.
  A record does not mean the government owns the building, that every unit is
  affordable, or that an apartment is available.
- Some locations are withheld or absent. The source manifest reports
  `citywide_records_without_coordinates`, a citywide count, **not** a count of
  missing records near the search origin. The tool does not reconstruct withheld
  locations. Geographic results cannot include records without usable coordinates.

There may be overlap between the two sources. HPD can also have different
projects for the same physical building. The tool preserves this evidence;
do not sum returned records or unit counts as unique buildings or households.
Broader coverage of older subsidies, state/federal programs, or future proposals
would require additional sources.

## Geographic meaning

NYCHA candidates are selected by polygon intersection with a bounding box and
then filtered to the requested circle using distance to the nearest development
polygon point. Distance is zero when the origin is inside the development.
Polygon holes and disconnected components are respected. The returned
`location` is the point used for that distance, **not** a verified street
entrance or a permanent development centroid. The published geometry is retained.

HPD uses the published internal tax-lot centroid when available, otherwise its
address-based coordinate estimate. Both coordinate pairs are searched for
candidates, then the preferred pair is used for the final radius filter.
`location.basis` identifies the selected coordinate method.

All distances are straight-line miles, not walking or driving distances. Polygon
nearest points use a local planar approximation, followed by haversine distance.

## JSON contract

The top-level fields are:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1.0`; version of this output contract. |
| `retrieved_at` | UTC retrieval time. |
| `query` | Origin coordinates and radius; resolved property/PLUTO source when applicable. |
| `status` | `complete`, `partial`, or `failed`. Complete refers to the selected source queries, not total housing coverage. |
| `sources` | Source IDs, URLs, coverage statements, dataset update times, query candidate counts, matched counts and completion status. |
| `records` | Nearby source records, sorted by distance then source ID. |
| `errors` | Failed sources and their error messages. |
| `notes` | Coverage and overlap qualifications for consumers. |

Every record includes `source_id`, `source`, `category`, `name`, `borough`,
`source_url`, `location`, `distance_mi` and `distance_method`. NYCHA source IDs
are `nycha:<TDS number>`. HPD IDs are `hpd:<project ID>:<building ID>`; this
identifies a building/project record, not a globally unique physical building.
HPD records also carry its address, BBL, BIN, construction type, dates and units.
Identifiers and labels describe the published source and may need verification.

Pagination is ordered and checked against the query's reported row count. Dataset
update timestamps are checked before and after retrieval. A changed source,
missing schema, duplicate source identifiers, truncated pagination, or failed
request marks that source failed and discards all its records. Successfully
retrieved sources can still return results with `status: partial`.

Exit codes: **0** all selected sources succeeded (possibly zero matches), **1**
some sources failed, **2** all sources failed or the query could not be resolved.
Source/query failures emit JSON; invalid CLI arguments use argparse's stderr
message and exit 2. A resolution failure returns `schema_version`, `status`,
`records`, and `errors`, without a source manifest. Network retries are bounded.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Offline checks cover polygon edges/holes, radius filtering, coordinate fallback,
null values, independent project identities, pagination, source changes,
partial failures, argument validation, and JSON stdout. Live outputs and logs
belong under the gitignored `.cache/runs/` directory.
