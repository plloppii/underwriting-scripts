# Place JSON contracts

The independent `restaurants.py`, `train_stations.py` and `parks.py` commands use
`place_sources.py`. They accept a BBL, exact address plus borough, or latitude
and longitude; `--radius-mi` defaults to 0.5 and accepts up to 25 miles. Those
CLI defaults are convenience query values; a consuming skill passes the owner's
active private preferences explicitly.

Each result has `schema_version`, `status`, `category`, `retrieved_at`, `query`,
`source`, `records` and `errors`. Each record includes a stable `source_id`,
`name`, `type`, coordinates, `location_basis`, straight-line `distance_mi`,
`distance_method`, borough and `source_url`. Pagination uses ordered pages,
a count check and dataset edit timestamps; a failed source returns no partial
place directory masquerading as a complete result.

| Script | Official source | Identity and qualifications |
| --- | --- | --- |
| Restaurants | [DOHMH Restaurant Inspection Results](https://data.cityofnewyork.us/d/43nn-pn8j) | `nyc-dohmh:<CAMIS>`. Uses the newest returned inspection per business. The directory does not establish that a restaurant is currently open. Historical locations may remain in the source. Cuisine and inspection date remain source fields; Google ratings are absent. |
| Train stations | [MTA Subway Stations](https://data.ny.gov/d/39hk-dx4f) | `mta:<GTFS stop ID>`. Routes, complex ID and the source ADA value are retained. These are subway records, not commuter rail, entrances or real-time service. |
| Parks | [NYC Parks Properties](https://data.cityofnewyork.us/d/enfh-gkve) | `nyc-parks:<property number>`. Retains published polygons and acres, excludes retired records, and measures to the nearest polygon point. This is not a verified entrance or a walking route. |

All bounding-box candidates are filtered by the final requested radius.
Polygon holes and separate components participate in the nearest-point
calculation. If the origin lies within a park, its polygon distance is zero.
No script queries an owner’s Notion directory. A consuming agent decides which
records to present or import, deduplicates by Source ID and preserves provenance.
