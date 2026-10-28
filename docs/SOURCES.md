# Sources

Every source the method uses, what it answers and how to reach it: by API wherever one exists, and in a browser only where none does. `scripts/screen.py` implements every row that names a function. Example values use 63 Irving Place, Brooklyn (BBL 3019840005).

## NYC Open Data

All calls are `GET https://data.cityofnewyork.us/resource/<dataset>.json` with SoQL parameters; no token is needed at this volume. Null fields are omitted from the JSON, so a missing key means not set. PLUTO boroughs are MN BX BK QN SI; borough codes elsewhere are 1 to 5. Dates are `'2024-01-01T00:00:00.000'`.

| Fact | Dataset | Query and fields | Function |
| --- | --- | --- | --- |
| BBL, block, lot, lat/long, lot front and depth, lot area, lot type; zoning district, overlay, special district; historic district, landmark; building class, year, floors, units, gross area, built FAR; residential FAR; E-designation | PLUTO `64uk-42ks` | `$where=upper(address)='63 IRVING PLACE' AND borough='BK'` or `$where=bbl=3019840005`. lottype 3 corner, 4 through, 5 interior; irrlotcode; zonedist1-4, overlay1-2, spdist1-3, splitzone; histdist and landmark absent when none; bldgclass, yearbuilt, numfloors, unitsres, unitstotal, bldgarea (DOF gross area, its own basis), builtfar, residfar; edesignum present only with an E-designation (hazardous materials, air or noise conditions to clear before new construction) | `pluto_by_address`, `pluto_by_bbl` |
| Owner, deeds, prices, parties | ACRIS legals `8h5j-fqxa`, master `bnx9-e6tj`, parties `636b-3b5g` | Legals `$where=borough='3' AND block='1984' AND lot='5'` gives document_id values; master `$where=document_id in ('id1','id2') AND doc_type='DEED'` gives document_date and document_amt; parties by document_id, party_type 1 seller, 2 buyer. document_amt under 100000 is not a market sale | `acris_deeds`, `acris_parties` |
| BIN | DOB/HPD records below, then NYC building-footprint centroids `u9wf-3gbt` | `$where=base_bbl='3010100021'&$select=bin,base_bbl`; reject unassigned borough-only BINs such as 3000000. PAD `bc8t-ecyu` is a downloadable ZIP, not a queryable SoQL table | `dob_facts` |
| DOB and ECB violations | DOB `3h2n-5cm9`, ECB `6bgk-3dad` | DOB `$where=boro='3' AND block='01984' AND lot='00005'` (block 5 digits, lot 5 digits); ECB the same with the lot 4 digits. Open versus dismissed from violation_category and ecb_violation_status; sum balance_due | `dob_facts` |
| DOB jobs, both systems | BIS jobs `ic3t-wcy2`, DOB NOW `w9ak-ipjd` | `$where=bbl='3019840005'`. BIS: job__, job_type (NB, A1, A2, A3, DM), job_status_descrp, pre__filing_date, proposed_dwelling_units, proposed_zoning_sqft. DOB NOW: job_filing_number, job_type, filing_status, filing_date, approved_date, signoff_date, proposed_dwelling_units, total_construction_floor_area | `dob_facts` |
| Renovation signal on a comp | the same two datasets, by the comp's bbl | BIS `job_type in ('A1','A2')`, DOB NOW `job_type like 'Alteration%'`; a job filed between 36 months and 6 months before the deed, not withdrawn or expired, means the seller did permitted work before selling. Fills the comps table's Listing column as "renovation signal: Alteration job filed May 2024, letter of completion issued" for the listing pass to confirm; unpermitted work leaves no signal | `renovation_signal` |
| HPD violations and registration | HPD violations `wvxf-dwi5`; Multiple Dwelling Registrations `tesw-yqqr` | `$where=bbl='3019840005' AND violationstatus='Open'`, count by class A, B, C. A registrationid on a violation, or a row in `tesw-yqqr` by block and lot (lastregistrationdate, registrationenddate), means the building is registered | `dob_facts` (violations) |
| Neighborhood name and outlines | NTA 2020 `9nt8-h7nd` | `$where=intersects(the_geom, 'POLYGON((lon lat, ...))')`; ntaname, the_geom | `nta_polygons` |
| Parks | `enfh-gkve` | `$where=intersects(multipolygon, 'POLYGON((...))') AND typecategory in (...)`; signname, typecategory, acres, multipolygon | `area_layers` |
| Shared restaurant identities and coordinates | DOHMH inspection records `43nn-pn8j` | Match existing area names to dba and nearby coordinates; dedupe by camis. Keep public business name, address, latitude and longitude. Ambiguous matches remain unresolved; no Google ratings are copied | `map_database.discover_amenities` |
| Comp candidates | PLUTO, then ACRIS | PLUTO `$where=latitude between 40.676 and 40.690 AND longitude between -73.968 and -73.950 AND bldgclass like 'V%'` for land, `'A%'`, `'B%'`, `'C%'` for houses and walk-ups. Legals one query per candidate block (`block='3424' AND lot in (...)`), six queries in flight at once; master with `doc_type='DEED' AND document_date >= ... AND document_amt > 100000`; join back by block and lot for area and class. A half-mile search touches about 130 blocks and takes under a minute | `find_comps`, `acris_deeds` |
| Assessed value, exemptions, tax class | Property Valuation and Assessment Data `yjxr-fw8i` | by bble (BBL); fullval, avtot, extot, exmptcl, taxclass, owner. The tax bill itself is not here (see Browser only) | not in the script |

## Other APIs, no key

| Fact | Service | Call | Function |
| --- | --- | --- | --- |
| Flood zone on the effective maps, which set flood insurance | FEMA National Flood Hazard Layer, ArcGIS REST, layer 28 | `GET https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query?geometry=<lon>,<lat>&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE&returnGeometry=false&f=json`, longitude first. FLD_ZONE is X, AE, VE, A, AO or AH; SFHA_TF T means insurance is required; STATIC_BFE is the base flood elevation in feet, -9999 none; ZONE_SUBTY "0.2 PCT ANNUAL CHANCE FLOOD HAZARD" is shaded X, "AREA OF MINIMAL FLOOD HAZARD" unshaded X. The host drops some connections: retry | `flood_zone` |
| Flood zone on the 2015 preliminary maps, which the Building Code uses for the design flood elevation of new construction | NYC Emergency Management PFIRM, ArcGIS REST, layer 0 | The same parameters against `https://services1.arcgis.com/8cuieNI8NbqQZQVJ/arcgis/rest/services/PFIRM/FeatureServer/0/query` with `outFields=FLD_ZONE,SFHA_TF,STATIC_BFE`. An empty features list means outside the preliminary floodplain | `flood_zone` |
| Subway stations | `https://data.ny.gov/resource/39hk-dx4f.json` | `$where=gtfs_latitude::number between .. AND gtfs_longitude::number between ..`; stop_name, daytime_routes, gtfs_latitude, gtfs_longitude | `area_layers` |
| Map tiles | OpenStreetMap | `https://tile.openstreetmap.org/16/{x}/{y}.png`, one request per tile with a User-Agent header, cached in `.cache/tiles`. Credit "© OpenStreetMap contributors" in the caption | `Basemap` |
| Zoning text | the nyc-zr CLI, `~/nyc-zr/scripts/zr.py` | `get 23-344`, `get 23-30 --children`, `search "rear yard corner lot" --district R6B --json`, `define "corner lot"`. Plain Python 3.10 or newer; the index builds itself on first run. Never fetch zr.planning.nyc.gov | `PLAYBOOK.md`, section 3 |

Flood results on the page: effective SFHA_TF T gives the Flood row ("zone AE, high risk · flood insurance required · base flood elevation 10 ft"), the "Flood zone" flag on the row, and an open item for insurance cost and design elevation that carries the preliminary zone and elevation. A preliminary zone alone gives "zone X now, AE on 2015 preliminary maps · no insurance required yet", the flag and the open item. Shaded X gives "zone X shaded, moderate risk · no insurance required" and no flag. Unshaded X with no preliminary zone gives nothing. A service that is unreachable gives no row, an open item, never a guess. The Source link is FEMA's own map search for the address, labeled FEMA (pattern below).

## Google, with the key

Normal screens reuse native place records rather than calling Places. Google nearby search runs only with `--refresh-area`.
The permanent directory uses independent city records; the live Google response is not the shared directory.

The Google Maps Platform key is restricted to the Street View Static API and Places API (New) and quota-capped. The script reads it from `GOOGLE_MAPS_API_KEY`; a separate `GOOGLE_PLACES_API_KEY` takes precedence for Places only. Where an agent keeps it is in its file under `agents/`. Never on a page, in a code block, in Run log, in Research notes or in a message. Without the corresponding key the Street View photo or food layer is omitted, with a clause in the map caption for missing food.

| Fact | Call | Function |
| --- | --- | --- |
| Street View photo, only when no listing photo exists | `GET https://maps.googleapis.com/maps/api/streetview/metadata?location=<lat>,<lon>&key=<key>`; continue only when status is OK, then `GET https://maps.googleapis.com/maps/api/streetview?size=640x400&location=<lat>,<lon>&fov=70&key=<key>` | `fetch_photo` |
| Restaurants and cafes for the area map | `POST https://places.googleapis.com/v1/places:searchNearby`, key in the `X-Goog-Api-Key` header, includedTypes restaurant, cafe, bakery, a 800 m circle; retain primary types restaurant, *_restaurant, cafe, bakery, coffee_shop or tea_house, rejecting retail stores with secondary food types; the five best by rating weighted by review count | `places_food` |

## Browser only

No API exists for these. Take only what is listed.

Floor-plan extraction uses the rendered listing through an existing local Chrome DevTools session (localhost:9222), or a
saved rendered HTML file with `--listing-html`. It selects images explicitly labeled as floor plans in the DOM or structured
media, validates the image bytes and crops separated branding bands when drawing boundaries are clear. A description merely
mentioning a floor plan does not qualify other images. `--floor-plan` supplies a verified image directly. A blocked listing
is recorded in local output without bypassing the block; the rest of the run continues. Visually check the extracted plan.

Park distances and pins use the nearest point on the park polygon, including multipart parks and interior holes, labeled
"mi to edge". This is a straight-line edge distance; an entrance or walking route requires separate evidence. Displaced map
pins have leader lines to their actual coordinates, and spacing accounts for the rendered marker diameter.

| Source | What to take | Notes |
| --- | --- | --- |
| The live listing: the one Noah sent, or, when he sent none, the address searched on Redfin, then Zillow, then Compass, then StreetEasy | Ask, marketed area, beds and baths, days on market, stated taxes and rents; the lead exterior photo; the floor plan | Everything tagged Broker. The photo and floor plan are uploaded as files (`NOTION.md`). These sites block plain fetches; use the browser |
| Redfin, then Zillow, then Compass | For each closed comp: the sold listing's URL, beds and baths, list price, days on market, condition words | One lookup per comp, a few seconds apart; stop on a rate limit and say so in Run log. Start with any comp whose Listing cell says "renovation signal" |
| StreetEasy | The building page, to confirm it loads before it goes on the links line; the listing history for the Rents table | Asking rents from past listings, never a rent roll |
| DOF Property Information Portal | Annual bill, exemptions, arrears, and whether a "Rent Stabilization" fee line appears | Assessed values and exemption codes are also on Open Data (`yjxr-fw8i`); the bill and the fee line are not |
| HPD Online | Registration and I-cards, only when the legal unit count is in question | The registration itself is on Open Data (`tesw-yqqr`) |
| HCR rent-stabilized building list | Whether the building appears | A lead, not proof, in either direction |

Never: the Flood Hazard Mapper or any map viewer; a web fetch of zr.planning.nyc.gov; ACRIS document images (they return 403; link, do not read); the DOF rolling sales file or any other sales list; the Notion "Archive" page.

## Link patterns

Every link on a page is a label on a lot-specific URL, never a search page.

| Label | URL |
| --- | --- |
| ZoLa | `https://zola.planning.nyc.gov/l/lot/<boro 1-5>/<block>/<lot>` |
| BIS | `https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=<bin>` |
| ACRIS | `https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?borough=<boro>&block=<block>&lot=<lot>` |
| DOF | `https://propertyinformationportal.nyc.gov/parcels/parcel/<bbl>` |
| HPD | `https://hpdonline.nyc.gov/hpdonline/building/<building id>/overview`, or the building page reached on HPD Online |
| StreetEasy | `https://streeteasy.com/building/<house>-<street>-<borough>` |
| FEMA | `https://msc.fema.gov/portal/search?AddressQuery=<address>, <borough>, NY` |
| ZR 23-344 | the official URL the zr tool prints, `https://zr.planning.nyc.gov/article-ii/chapter-3/23-344` |
