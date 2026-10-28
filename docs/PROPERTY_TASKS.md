# Independent property commands

Use one task when the User asks for one kind of evidence. Use `property.py` for
the complete property snapshot. The combined command keeps its existing JSON
shape so current underwriting consumers continue to work.

All six commands accept an exact address with `--borough MN|BX|BK|QN|SI`, or
`--bbl <ten-digit BBL>`. `--out <file>` saves the same JSON sent to stdout.
Progress goes to stderr. Inputs are validated before network requests.

```sh
python3 scripts/property_facts.py --bbl 3010100021
python3 scripts/sales_history.py --bbl 3010100021
python3 scripts/violations.py --bbl 3010100021
python3 scripts/permits.py --bbl 3010100021
python3 scripts/flood.py --bbl 3010100021
python3 scripts/comps.py --bbl 3010100021 --radius 0.5 --months 18 --min-amt 100000
```

Each task first resolves its property through PLUTO. It then calls only its
applicable sources, using the same reusable functions as the combined command.
The separate violation and job-query functions keep the violations command
from querying permits, and the permits command from querying violations.
The combined `dob` section still aggregates those functions into its original
summary and retains its building-footprint BIN fallback.

| Task | Task-specific JSON | Source and meaning |
| --- | --- | --- |
| `property_facts` | `pluto`, `numeric`, `corner` | City lot/building/zoning fields, source-based floor-area arithmetic, and a corner estimate. These are not a legal buildability determination. |
| `sales_history` | `deeds`, `parties`, `parties_document_id` | ACRIS deeds, newest first. Buyer/seller parties are for the latest deed only. A recorded $0 deed remains zero. An unavailable party query does not discard successfully retrieved deeds. |
| `violations` | `violations.dob`, `.ecb`, `.hpd`, plus `counts` | Raw records from each violation source, including the published dates and statuses; counts are totals, not counts of open violations. |
| `permits` | `permits.bis`, `.dob_now`, plus `counts` | Raw construction/job filings from both systems. A filing does not establish that a permit was issued, the work was completed, or the building was renovated. |
| `flood` | `flood` | Effective FEMA and NYC preliminary flood-map results. Service-success flags distinguish an unmapped point from an unavailable source; risk fields for an unavailable source are null. |
| `comps` | `candidate_comps`, `options` | Same-product-family closed-sale candidates using radius, time and amount filters. Checks DOB alteration signals on up to the first eight; those signals do not establish listing condition. Final comp selection belongs to the consuming skill. |

Shared metadata includes `schema_version: "1.0"`, `task`, UTC `retrieved_at`,
`checked`, `status`, a resolved `property` summary, `sources`, and `errors`.
Sources include the PLUTO resolution URL and applicable dataset/service URLs.
Missing source fields remain missing or null. A failed source returns null
records/counts; an empty successful query returns an empty array/count zero.

Exit **0** means the task's queries completed, **1** means some task sources
succeeded and some failed, and **2** means the task failed or input was invalid.
Successfully retrieved data is retained in a partial result. A property
resolution failure returns the schema, task, failed status and error details
without inventing an origin. Invalid command arguments use argparse's stderr
message rather than a source-result JSON document.

Only `comps.py` accepts `--radius`, `--months`, and `--min-amt`. Match these query
values to the User's requested scope. Standalone task output is evidence for an
agent; the existing Notion page writer continues to consume the complete
`property.py` JSON rather than an arbitrary single-task result.
