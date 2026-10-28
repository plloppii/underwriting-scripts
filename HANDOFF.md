# Handoff: Noah's NYC underwriting system

Last updated 2026-10-28. Audience: an agent or person taking over maintenance. Noah Pan is the owner; he is new to real-estate
jargon, so everything he reads must be plain sentences, and he decides pursue or pass himself.

## What it does

Noah texts an address, an ask and a strategy word (Flip, Rent or Build) to his assistant, Instinct. The agent follows the
instructions in this repository (read from a fresh clone every job, since its workspace is not persistent), runs
`scripts/screen.py` for the data stages, finishes the page by hand, and keeps one row per building in the Notion Properties
database. A separate project, `~/nyc-zr` (github.com/plloppii/nyc-zr), holds the NYC Zoning Resolution as markdown with a
searchable index and a CLI; pages here cite its sections by official URL, and nothing in that project depends on this one.

## Where things live

This repo, `~/notion-underwriting` (github.com/plloppii/notion-underwriting, private; Instinct has read access and clones it;
`.venv/`, `.cache/` are local):

| Path | Purpose |
| --- | --- |
| `AGENTS.md` | Entry point for any agent: reading order, the job in one screen, the four hard rules. |
| `docs/PLAYBOOK.md` | The method, agent-neutral. Section 1 has the division of labor between the script and the agent. |
| `docs/TEMPLATE.md` | Deal page and comp page shapes, with the budgets table at the end. |
| `docs/SOURCES.md` | Every data source with its API recipe, the browser-only few, link patterns. |
| `docs/NOTION.md` | The shared Properties database, fields, native map views, payloads and page-writing rules. |
| `docs/agents/INSTINCT.md` | What is specific to Instinct: ephemeral workspace, Python 3.10, vault key, saved routine rents-for-address. A new agent gets its own file here. |
| `scripts/bootstrap.sh` | Clone or pull both repos under `$UW_HOME` (default `$HOME`), pip install, `screen.py --check`, zr tool check. Idempotent. |
| `scripts/screen.py` | Stages 0 to 3: Open Data pulls, flood zone from two map services, envelope, flags, ACRIS comps with a renovation signal from DOB jobs, two maps, Notion write. Progress lines with elapsed seconds. Python 3.10+. |
| `scripts/notion_deals.py` | Creates, migrates and backfills the Properties database. Idempotent; run after schema changes. |
| `scripts/notion_api.py` | Shared Notion helpers: paced `req()`, markdown to blocks, `load_state()` / `save_state()`. |
| `data/notion_state.json` | Database ids; the two archived instruction pages under `pages_archived`. |
| `.cache/` | OpenStreetMap tiles and matplotlib's cache; safe to delete. |

Zoning, in `~/nyc-zr`: `python3 scripts/zr.py search|get|define|district|history` over the full Zoning Resolution text
(SQLite full-text index, built on first use from the JSONL in the repo; plain Python, nothing to install; verified on 3.10 on
2026-10-21). Use it for any zoning question instead of fetching zr.planning.nyc.gov. Its Notion mirror was retired on 2026-10-21.

Notion, all under the "Real Estate" page `60a139f3-f569-40cf-9976-8d0c40dae1c5`:

| Page or database | Id | Notes |
| --- | --- | --- |
| Properties database | `3fa9d8ce-4a74-8115-95f5-c286b2ef5beb` | State keys `databases.properties` and the historical `deals`. |
| Archive | `3fb9d8ce-4a74-8194-9562-faee98b3dbeb` | Old templates. Never read from it. |

Archived (in Notion's trash, restorable): "Underwriting Playbook (for Instinct)" `3fa9d8ce-4a74-81e6-8885-d87cd7451488` and
"Underwriting Template v5" `3fa9d8ce-4a74-8145-b45f-e2f540b5000c`, on 2026-10-21 when the instructions moved here for good; the
old Comps database `3fa9d8ce-4a74-81c2-846c-c1e399a85f9c` (merged into Properties on 2026-10-16); the ZR Sections and
Definitions databases and "README for agents" (`3f99d8ce-4a74-8153-9aab-c2d09cb6ca5b`, `3f99d8ce-4a74-8124-8759-d991c0770239`,
`3fa9d8ce-4a74-8119-be68-f1820e9b355d`), retired 2026-10-21.

## Access

- Notion: internal integration token in `NOTION_API_TOKEN` or `NOTION_TOKEN`. `scripts/notion_api.req(method, path, body,
  version)`; database work uses version `2022-06-28`. Never print the token.
- Google Maps Platform key: `GOOGLE_MAPS_API_KEY` in the environment for the script; Instinct keeps it in its vault as the
  login entry "Google Places API key". Optional. Never write it on a page or in a message.
- GitHub: github.com/plloppii/notion-underwriting, Noah's personal account (SSH alias `github-personal`; never the work account).
  Instinct has read access and clones over HTTPS.
- Open Data needs no key. Neither do the two flood map services: FEMA's National Flood Hazard Layer (hazards.fema.gov, ArcGIS
  REST layer 28, the effective maps that set insurance; the host resets some connections, so the script retries) and NYC
  Emergency Management's copy of the 2015 preliminary maps (services1.arcgis.com, PFIRM layer 0, what the Building Code uses for
  new construction). Tiles come from tile.openstreetmap.org with a User-Agent header.
- Python: 3.10 or newer. `requests` required; `matplotlib` and `pillow` optional (maps). Shapely was dropped on 2026-10-21 in
  favor of a few lines of GeoJSON math (`_rings`, `contains`, `rep_point` in screen.py). `uv sync`, `pip install -r
  requirements.txt` or `sh scripts/bootstrap.sh`.

## Data model: the Properties database

One row per property or amenity. Type is Deal, Comp, Restaurant, Park or Subway station; the old Role field was renamed in
place. Properties are keyed by BBL; amenities by Source ID. Location powers native map views. Property-only fields stay
empty on amenities. Fields and ownership are in `docs/NOTION.md`. Strategy is Flip, Rent, Build or Not set for property work.

## Flow of one job

1. Bootstrap: clone if missing, `sh scripts/bootstrap.sh` (pull both repos, install, check).
2. `python3 scripts/screen.py "<address>" --borough BK --ask <n> --strategy <word> --listing <url> --notion`: Stage 0 facts
   (PLUTO, ACRIS, DOB, ECB, HPD, the two flood maps, E-designation), Stage 1 envelope and flags with ZR citations, Stage 2
   classifiers and strategy inference, Stage 3 area map with pin list and ACRIS-only purchase comps with comp rows (the
   table's Listing column pre-filled with "renovation signal: …" where DOB alteration jobs were filed in the three years
   before the deed and at least six months ahead of it), a draft top paragraph, open items and run log; the row and page body
   are written above "Noah's notes". About one to three minutes end to end; the ACRIS comp search runs six queries at once.
3. By hand: photo and floor plan from the listing, the listing's facts, the DOF tax bill row, HPD registration when units are in
   question, the listing pass on each comp (Listing column, Beds/baths and Listing URL on comp rows), the Rents table, exit
   comps by strategy, then sentences 1 and 3 of the top paragraph, the final open items, Rules that mattered, a Run log line.
4. Never touch "Noah's notes" or anything below it. No returns, costs or price ladders.

A re-run of the script rebuilds everything above "Noah's notes" and drops the hand-added parts; the playbook tells the agent to
re-run only when facts or comps need refreshing.

## Rules that are easy to break

- The word PLUTO never appears on a page; the lot record is called ZoLa and linked to the lot's ZoLa page.
- Links are labels, never bare URLs. Template placeholders must be real URLs or Notion rejects the publish.
- Plain language: no coined words, codes spelled out, process state in database fields, not page text.
- Noah wants a discussion and an explicit go before anything changes in Notion or the scripts.
- Deleting a database field deletes its values everywhere; carry values over first (see `migrate()` for how Lot was done).
- The instructions live in this repo, not Notion, and the four documents under `docs/` are agent-neutral: anything specific to
  one agent goes in `docs/agents/`. Edit the markdown, commit, push; the agent picks it up on its next bootstrap.

## Current state

### Shared map database, 2026-10-28

- Notion MCP reconnected to Noah Pan's Notion. Role was renamed to Type with its original property ID; all 23 deals and
  157 comps retained their page IDs, fields and relationships. Location, Source ID and Source URL were added.
- All 180 property locations were populated from NYC lot coordinates. Property page bodies and Noah's notes were untouched.
- Native views: All records, Deals map, Comps map, Neighborhood map and Amenities. Deals map is verified to return only
  the 23 deals; map quick filters allow neighborhood selection. Each map displays at most 100 pins at once.
- The Properties table retains a property-only comparison view. There are 298 located records in total: 180 original
  properties plus 56 city-matched restaurants/cafes, 25 parks and 37 subway stations. Amenity Source IDs are unique.
- Full property comparison against the pre-migration snapshot verified every original field and relation, with Role mapped
  to Type by the same property ID. The shared-directory dry run for 278A 9 Street completed in 55 seconds, with no Places
  search and no map-image rendering. The regression suite passes 23 checks, including shared-directory reuse.
- `scripts/map_database.py` imports sourced amenities by stable IDs and safely resumes location filling. Existing area
  entries were matched to MTA, NYC Parks and DOHMH restaurant records; Google ratings are not copied into the directory.
  Unmatched or unsuitable entries remain in their original pages and in `.cache/migrations/type-map/unresolved-amenities.json`.
- Normal screens reuse the directory and skip Places searches and PNG rendering. `--refresh-area` and `--map-images` are
  explicit options. `data/amenities.json` is the portable directory; Notion writes read current amenity records from Notion.
- The Notion client retries transient 5xx errors for idempotent reads/updates. It does not blindly retry page creation on a 5xx.

### Update: 2026-10-28, 278A 9 Street feedback

- Instinct's run took 83 seconds (about 60 on ACRIS), then about 12 minutes of manual finish and 4 minutes of corrections.
  The outstanding work is mostly listing review, not script runtime.
- Fixed missing BIN/BIS links for buildings with no DOB/HPD activity: query NYC building-footprint centroids by `base_bbl`.
  PAD `bc8t-ecyu` is a downloadable ZIP and cannot be queried as previously documented. The fallback returns BIN 3022256
  for 278A 9 Street (3010100021). Missing BIN remains unknown with an open item.
- Food pins now require a restaurant/cafe/bakery primary type; a secondary cafe inside a supermarket or bookstore is excluded.
  Park distances and pins use the nearest polygon edge, labeled as such. Map pins use output-scale spacing and leaders.
- Added `scripts/listing_media.py`: automatic floor-plan extraction from a rendered listing through local Chrome DevTools,
  or saved rendered HTML; explicit `--floor-plan` fallback, bounded image decoding and conservative cropping. No plain
  request to a listing page and no workaround for site blocks. Browser/image packages are optional at runtime.
- Added `scripts/comp_review.py` and `data/reviews/3010100021.json`, preserving Instinct's listing pass with its provenance.
  Reviews are sale-specific (BBL + date); renovated/new sales leave purchase comps. The full candidate set and city data
  are saved in each screen JSON. `--refresh` redraws the table/map without another ACRIS search; `--notion-refresh` updates
  only affected sections after fresh city queries. Both limited Notion modes preserve hand-finished facts and Noah's notes.
- Fresh local run on 278A 9 Street: 71 seconds total, about 36 seconds on the ACRIS comp search; 111 candidates, five selected
  purchase comps, three renovated sales withheld, automatic floor-plan retrieval, and Prospect Park 0.68 mi to edge.
  A second fresh run with Noah's `GOOGLE_PLACES_API_KEY` from his local zsh configuration took 63 seconds (51 on the ACRIS
  comp search). The live food filter returned Chela, Bison & Bourbon, Terrace Restaurant & Bakery, Table 87 and Masalawala
  & Sons; no supermarkets or bookstores. That run's browser was unavailable; the saved rendered listing HTML supports
  floor-plan extraction without repeating city queries. The original browser extraction was verified in the first run.
- `GOOGLE_PLACES_API_KEY` is now supported directly and takes precedence for Places; `GOOGLE_MAPS_API_KEY` remains its
  fallback and the Street View key. Local gitignored `.env` integration settings are also read without executing shell code.
- Regression checks: `python3 -m unittest discover -s tests -v` (19 checks passing).
- The limited live Notion refresh completed in 32 seconds after local rendering. Verified against a pre-update snapshot:
  the original photo, opening paragraph, listing/tax Details table, and Noah's notes block IDs/content were preserved;
  Area and the cropped floor plan were replaced, and the purchase table/map/relation now contain the same five comps.
  This verifies the limited update path; the older full-rebuild update path remains a separate unverified path.

### Earlier state

- 2026-10-21 (evening): first live job by Instinct from a fresh sandbox: 236 Schaefer Street, Bushwick (BBL 3034300020),
  Investigate, Strategy Not set, 18 minutes end to end of which the script took about 8. Its feedback led to this commit: comp
  ACRIS links were broken because the lot-dimension string overwrote the lot number in each comp (now `lot_dims`); the ACRIS
  legals queries run six at a time (comps stage 128 s to about 40 s here); every progress line carries the seconds since
  start and stdout is line-buffered; a renovation signal from DOB alteration jobs pre-fills the comps table's Listing column
  (it caught two of the three gut renovations Instinct found by hand on Schaefer; the third had no permits); the docs now say
  to find the live listing by address when Noah sends none, and `agents/INSTINCT.md` says to run the screen under `nohup`
  with a log because its tool stops waiting after 120 seconds.
- 2026-10-21 (later): the script is the default path for the agent; docs split into AGENTS.md plus PLAYBOOK, TEMPLATE, SOURCES,
  NOTION and agents/INSTINCT; the two Notion instruction pages archived and `scripts/notion_underwriting.py` (which kept them)
  removed; Python 3.10 supported and verified by dry runs with the full dependency set and with `requests` alone; shapely
  dropped; `bootstrap.sh` and `screen.py --check` added.
- 2026-10-21: instructions moved to GitHub. Flood zone comes from FEMA's effective maps and the city's 2015 preliminary maps by
  API (`flood_zone()` in screen.py, SOURCES.md); the Flood Hazard Mapper is no longer used.
- 2026-10-21: zoning citations moved from Notion rows to the nyc-zr CLI and official zr.planning.nyc.gov URLs. The 14 deal
  rows' Rules that mattered were carried over as text and the ZR links on their pages rewritten to official URLs.
- 2026-10-17: 50 rows: 5 deals (63 Irving Place, 1241 Putnam Avenue, 324 Weirfield Street, 706 Monroe Street, 1135 Decatur Street)
  and 45 comps. 63 Irving needs a rerun under Template v5; Putnam and Decatur still show the retired new-buildings map group.
- Verified live on 2026-10-21 by Instinct's Schaefer run: `bootstrap.sh` on a fresh sandbox with Python 3.10, the Google
  Places layer with the vault key, and a new page written with `--notion`. Still untested: screen.py's in-place page update
  path against live Notion; Street View (Schaefer had a listing photo); the Flood row and flag on a live Notion page (dry runs
  verified them for 85 Dikeman Street, zone AE on both maps, 10 ft effective and 12 ft preliminary; Schaefer is zone X); the
  renovation signal on a live page.
- Open with Noah: whether "projects" on the map meant NYCHA public housing; floor plan beside the photo in a two-column layout
  (proposed, not built); Condition field tied to his viewing notes (parked); cost basis and returns once he has contractor
  quotes; adding the DOF assessment dataset (`yjxr-fw8i`) and HPD registrations (`tesw-yqqr`) to the script.
- Related but separate: the iMessage showings bridge, spec at `~/showings-bridge/HANDOFF.md`.
