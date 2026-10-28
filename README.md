# notion-underwriting

Noah's NYC property underwriting system: the instruction set an agent follows to screen one property, the page it produces,
the Notion **Properties** database that holds every deal and comp, and the script that does the data stages. Zoning text
lives in a sibling project, `~/nyc-zr` (github.com/plloppii/nyc-zr): the NYC Zoning Resolution as searchable markdown with a
CLI; pages here link to the official section URLs.

Start with `AGENTS.md` if you are the agent doing the work, `HANDOFF.md` if you are maintaining the system. The instructions
live in this repository and nowhere else; the Notion pages that used to hold them were archived on 2026-10-21.

## Layout

| Path | Purpose |
| --- | --- |
| `AGENTS.md` | Entry point for any agent: what to read, the job in one screen, the four hard rules. |
| `docs/PLAYBOOK.md` | The method: rules, Stages 0 to 4, what the script does versus what the agent does by hand. Agent-neutral. |
| `docs/TEMPLATE.md` | Deal page and comp page shapes, with budgets and real example links. |
| `docs/SOURCES.md` | Every data source: the API recipes, the browser-only few, link patterns. |
| `docs/NOTION.md` | The output adapter: the Properties database, its fields and payloads, page-writing rules. |
| `docs/agents/INSTINCT.md` | Notes specific to Noah's assistant Instinct: ephemeral workspace, keys, saved routine. |
| `scripts/bootstrap.sh` | Clone or pull this repo and nyc-zr, install the packages, print a check. Idempotent. |
| `scripts/screen.py` | Property facts, flood checks, envelope, ACRIS comps, native map locations and shared nearby places; optional PNG export. |
| `scripts/map_database.py` | Fill native property locations and import reusable city-sourced amenities by Source ID. |
| `scripts/notion_deals.py` | Creates, migrates and backfills the Properties database. Run after schema changes only. |
| `scripts/notion_api.py` | Shared Notion helpers: paced requests, markdown to blocks, state file. |
| `data/notion_state.json` | Database ids, and the ids of the archived instruction pages. |
| `HANDOFF.md` | Architecture, ids, rules and current state, for a maintainer. |

## Setup

Python 3.10 or newer. `requests` is required; `matplotlib` and `pillow` draw the two maps and are optional (without them the
screen still runs and the page gets the pin list without the images).

```sh
sh scripts/bootstrap.sh                   # or: uv sync, or pip install -r requirements.txt
export NOTION_TOKEN=...                   # internal integration shared with the Real Estate page (NOTION_API_TOKEN also works)
export GOOGLE_MAPS_API_KEY=...            # optional: Street View photo, restaurants and cafes with ratings
python3 scripts/screen.py --check
```

The screen also reads these integration settings from the repo's gitignored `.env` file; exported environment values win.
`GOOGLE_PLACES_API_KEY` is accepted for Places and takes precedence there; `GOOGLE_MAPS_API_KEY` is the fallback for Places
and the key used for Street View.

## Commands

The database now holds deals, comps and amenities together, with one **Type** field and native **Location** pins. Role was
renamed in place; the original records and relationships are retained. Use the Deals map, Comps map and Neighborhood map tabs.
Normal screens reuse shared place records and skip Google Places searches and PNG rendering. Use `--refresh-area` to fetch
area sources again, or `--map-images` for a legacy image export. `data/amenities.json` is the portable, city-sourced directory;
Google ratings are not stored in that directory. `scripts/map_database.py` fills existing property locations and imports
amenities by their stable Source ID.

```sh
python3 scripts/screen.py "63 Irving Place" --borough BK --ask 499999 --strategy Build --listing <url> --out $TMPDIR/irving   # local .md and .json
python3 scripts/screen.py --bbl 3019840005 --ask 499999 --strategy Build --notion                                            # write the row, comp rows and page body
python3 scripts/notion_deals.py                                                                                               # bring the database to the current schema
```

Listing floor plans are read automatically from a rendered listing when Chrome is already running with remote debugging
on localhost:9222. The script uses its own tab and closes it afterward. With no browser, or a blocked listing, use
`--listing-html <saved-rendered.html>` or `--floor-plan <image path or URL>`. Pillow validates and crops the image before upload.
The rest of the screen runs when browser support is unavailable.

Each run also saves the full API snapshot and a `<address>-review.json` template. After the listing pass, save decisions in
`data/reviews/<BBL>.json` (automatically loaded on fresh runs), or pass another file with `--review`. Each decision is keyed
by BBL **and sale date**, with a source URL and evidence; `renovated` and `new` leave purchase comps, and `exclude` drops an
unsuitable sale. `unknown` stays unknown. Set `include_unreviewed` to false to retain only the sales covered by the review
instead of filling the table with additional unreviewed candidates. A DOB alteration signal alone never proves condition.

```sh
python3 scripts/screen.py --refresh /tmp/irving/63-irving-place.json --review data/reviews/3019840005.json --out /tmp/irving-reviewed
python3 scripts/screen.py --refresh /tmp/irving/63-irving-place.json --review data/reviews/3019840005.json --out /tmp/irving-reviewed --notion
```

Refresh reuses the saved public records and updates the table and map together. With `--notion`, it replaces Area, Purchase
comps, applicable Exit comps and the floor plan, updates the comp relation, and appends a Run log line. Existing listing
facts, taxes, photo, verdict paragraph, rents, and Noah's notes remain intact. `--notion-refresh` selects that same limited
Notion update after a fresh API run. Ordinary fresh `--notion` still rebuilds everything above Noah's notes.

Run the regression checks with `python3 -m unittest discover -s tests -v`.

Notion, under Noah's "Real Estate" page: the Properties database is https://www.notion.so/3fa9d8ce4a74811595f5c286b2ef5beb.

## How it works

Noah sends an address, an ask and a strategy word (Flip, Rent or Build). The agent bootstraps a workspace, runs the screen,
and finishes by hand what no API reaches: the photo, the listing's facts, the tax bill, the listing pass on each comp, rents
and exit comps. Strategy is inferred only when exactly one fits (a vacant lot is Build); otherwise the row says "Not set" and
the job stops after purchase comps. A deal page opens with the property photo, a links line and three plain sentences, then a
details table, a zoning block with only the lot-specific rules (linked to the official zr.planning.nyc.gov pages), the area
map with a color-matched pin list, a rents table from StreetEasy history, purchase comps from ACRIS deeds with a listing pass,
exit comps only with a strategy, open items, a run log, a Research notes toggle and a "Noah's notes" heading that nothing
automated edits. Returns, costs and price ladders are deliberately absent: Noah has no cost basis yet.

The Properties database has one row per property or nearby place. Property types are Deal and Comp; amenity types are
Restaurant, Park and Subway station. Properties use BBL and amenities use Source ID. Every located record has a native
Location value. The latest deed sits on property rows as Last sale price and date with the one before in Prior sale; a deal links its comps
through Comps used and names the ZR sections it cited in Rules that mattered. The row holds only what is not already on the
page; current fields and their ownership are listed in `docs/NOTION.md`.

`screen.py` pulls PLUTO, ACRIS deeds and parties, DOB and ECB violations, BIS and DOB NOW jobs and HPD violations, and the
flood zone from FEMA's effective maps and the city's copy of the 2015 preliminary maps (both ArcGIS REST, no key); computes
max zoning floor area and lot flags; finds closed same-class sales within a radius and window (six ACRIS queries at a time)
and marks the ones with DOB alteration jobs filed before the sale as a renovation signal for the listing pass; reuses the shared
area directory and writes native Location values for interactive database maps. `--map-images` explicitly renders legacy
OpenStreetMap PNGs; supplied listing images use the Notion file-upload API. The screen writes the
deal row, comp rows and page body above "Noah's notes". It never writes Pursue.
