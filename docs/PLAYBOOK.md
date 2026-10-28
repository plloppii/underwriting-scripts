# Underwriting playbook

The method for one property. Read it from your checkout at the start of every job, after `git pull`. This file is instructions and never page content; nothing here is copied onto a property page. The page follows `TEMPLATE.md`, the data comes from `SOURCES.md`, the output goes where `NOTION.md` says. Revised 2026-10-21. Noah owns the defaults in section 7.

## 0. Rules that override everything

1. A property page holds facts, numbers, maps and a verdict. No methodology, definitions, formulas, source recipes or process text on a page, and no notes addressed to Noah or to yourself.
2. The budgets in `TEMPLATE.md` win. There are no status tags. The Source column carries provenance; an estimate says so inside the Finding in five words or fewer ("about 63 ft, survey to confirm"). What does not fit is dropped or goes under Research notes.
3. A zoning rule on a page is at most three lines: one plain sentence, the number for this lot, what proves it, and a link to the rule's official page on zr.planning.nyc.gov labeled with its number ("ZR 23-344"). Quoted text goes under Research notes. Rules that apply to every lot in the district (height, coverage, standard yards, density) are not written on pages.
4. A blank is Unknown, not zero. Never invent rents, costs, cap rates, zoning bonuses or legal vacancy. Leave it Unknown and add an open item.
5. Asking price is not a closed price. Tax or PLUTO area is not legal area. An asking rent from a past listing is not a rent roll. Existing is not proposed. An estimate is not an approval. A conflict is two numbers, each with its source, never an average.
6. Only sections that apply go on the page; omit the rest without explanation. No placeholder text such as "pending" or "not checked".
7. Do not contact brokers, lenders, tenants or sellers, order paid reports, submit financing or make offers.
8. Stop only at a hard gate: no lawful path to the use, possession legally blocked, or title that cannot close. Whether the price is good enough is Noah's decision. You do not compute returns, profit or a price ladder.
9. Nothing on a page is for Noah to fill in. His only input is "Noah's call" on the row. Never edit the "Noah's notes" heading or anything below it.
10. Strategy comes from Noah: Flip, Rent or Build (section 4). Infer one only when exactly one fits. Otherwise Strategy stays "Not set" and the job ends after purchase comps.
11. Links are labels; a bare URL never appears on a page. The city's lot record is called ZoLa on the page. PLUTO is the dataset name for queries and never appears on a page.

## 1. One job: trigger, scope, division of labor

**Trigger.** Noah sends an address and borough, usually a strategy word (Flip, Rent, Build), the asking price and a listing link. When he sends only the address, find the live listing yourself first (section 2) and take the ask from it. One property per job. A later message about the same property re-runs only the stages it touches.

**Dedupe.** The screen resolves the BBL and finds the row by BBL in the Properties database (`NOTION.md`). A Comp row becomes the deal: Type set to Deal, page filled the rest of the way. A Deal row is updated in place above "Noah's notes", with a line added to its Run log. No row: one is created. Never a second row for the same BBL.

**Scope.** Stages 0 to 3 and the top paragraph on every job. Exit comps, and the rents work that depends on a strategy, run only when Strategy is set. Nothing about returns, costs or price ladders.

**The script does the API half.** Run `scripts/screen.py` first with Noah's ask, strategy and listing link and `--notion`, then do the rest by hand. On a re-run the script rebuilds everything above "Noah's notes", so what you added by hand is gone and must be added again: re-run it only when the facts or comps need refreshing, otherwise edit the page directly.

For an already finished page, use `--notion-refresh` on a fresh screen to update maps, comp tables and relations, and the
floor plan while preserving listing facts, taxes, photo, rents, the opening paragraph and Noah's notes. For a comp-only
change, use `--refresh <saved-screen.json> --review <review.json> --notion`; city records are reused and retain their original
checked date. Both limited updates require the existing section headings and Noah's notes boundary. Ordinary fresh
`--notion` remains a full rebuild.

| Stage | The script | You, by hand |
| --- | --- | --- |
| 0 Identify | Twelve facts from PLUTO, ACRIS, DOB, ECB, HPD and the two flood maps; the links line; the Details rows; the flags; the Flood row and the E-designation | The listing's facts (ask, marketed area, beds and baths, days on market, stated taxes and rents); the photo and floor plan; the tax bill row from the DOF portal; HPD registration and I-cards when the unit count is in question |
| 1 Envelope | Max zoning floor area, unused rights, the Zoning entries for near-corner, shallow, narrow, irregular and historic lots, cited by official URL | Any other lot-specific rule, read with the zr tool |
| 2 Classify | The three classifiers, strategy inference, the verdict and a draft top paragraph | Sentences 1 and 3 of the top paragraph, rewritten once you know the listing and the rents |
| 3 Area and comps | Native map locations and shared nearby-place list; ACRIS purchase comps, with the Listing column pre-filled where DOB jobs signal renovation; one Comp row per comp building | The listing pass (Listing column, Beds/baths and Listing URL), moving renovated sales to exit comps; Rents; exit comps |
| 4 Close | A draft Open items table, the row fields, a Run log line | The final open items; Rules that mattered for anything you cited; your own Run log line |

## 2. Stage 0: Identify

The script fills the twelve facts (`SOURCES.md` lists each with its query). You add what only a web page gives:

- **From the live listing**, all tagged Broker: asking price, marketed area, beds and baths, floor plan, photos, days on market, stated taxes and rents. Marketed area is its own basis and never replaces PLUTO area or legal area. The listing is the one Noah sent; when he sent none, search the address in the browser on Redfin, then Zillow, then Compass, then StreetEasy, and use the first page that shows an active listing with an asking price. StreetEasy often has no live listing for a small Brooklyn house while Redfin does.
- **Photo.** The first block of the page is the front of this property, from a listing of this property, live or sold: save the lead exterior photo and upload it as a file so it survives the listing going down. An image that does not show this building's exterior, or that carries text, a logo or advertising, is not a photo. A floor plan, when the listing has one, is cropped and uploaded under the Floor plan heading. No usable listing photo: Google Street View with the Google key, only when its metadata call says OK. Neither: the links line begins "No photo · ".
- **Taxes and rent regulation**, when the lot has units: the DOF Property Information Portal for the annual bill, exemptions and arrears. A "Rent Stabilization" fee line on the bill is the best public signal that units are regulated. The HCR building list is a lead, not proof, in both directions.
- **HPD registration and I-cards**, only when the legal unit count is in question, from HPD Online.
- **Links line.** Listing (the live listing, wherever it was found; with none anywhere, "Off market" linked to the property's Zillow or StreetEasy page), StreetEasy (the building page, kept only if it loads), ZoLa, BIS, ACRIS, DOF. Every Source cell in a table links the same way to the lot-specific page, never a search page. URL patterns are in `SOURCES.md`.

## 3. Stage 1: Envelope

Formulas, with the citation that goes on the page:

- Max zoning floor area = lot area × residfar. Cite ZR 23-22 (R6 to R12) or 23-21 (R1 to R5). The affordable-housing FAR is an upside line only when the lot could plausibly take 5 or more units.
- Unused rights = max ZFA minus PLUTO bldgarea, which is gross, so a screen only. Unused ratio U = bldgarea ÷ max ZFA; a vacant lot is U = 0.
- Height: ZR 23-432 (R6 to R12), the 23-42 series (R1 to R5). Coverage: 23-362 (R6 to R12), 23-361 (R1 to R5). Rear yard 23-342. Side yards 23-335, front yards 23-322, lot minimums 23-12, density 23-52 (R6 to R12).
- Flags, from PLUTO geometry: Corner (lottype 3). Through (lottype 4). Shallow (lotdepth under 95 ft: a ZR 23-342(b) candidate, 1961 history must be proven). Near corner (any part within 100 ft of an intersection: 23-344(a) no rear yard and 23-363(b) full coverage candidate; the survey decides). Narrow or small (lotfront under 18 ft or lotarea under 1,700 sf: below the 23-12 minimums, exempt under 23-10 as a zoning lot that existed on December 5, 2024, so a one-line Zoning entry, not a gate). Irregular (irrlotcode true: do not use front × depth).

Read any rule with the zr tool, never a web fetch of zr.planning.nyc.gov:

```sh
python3 ~/nyc-zr/scripts/zr.py get 23-344                                      # the section as markdown, with its official URL and last-amended date
python3 ~/nyc-zr/scripts/zr.py get 23-30 --children                            # a whole group; --parents adds the sections above it
python3 ~/nyc-zr/scripts/zr.py search "rear yard corner lot" --district R6B --json
python3 ~/nyc-zr/scripts/zr.py define "corner lot"
```

Output: the Zoning block, lot-specific numbers only, one entry of up to three lines per rule that applies to this lot. Flags are never listed as tags on the page; each becomes a finding in words or a Zoning entry.

Gate: hard blocks only. If the intended use is not permitted, possession is legally blocked or title cannot close, start the top paragraph with "Pass:" and the reason, set Verdict to Pass, and stop. If U is at least 0.8 and the units are occupied and regulated and the building is in a historic district, say so in the top paragraph (income only) and continue.

## 4. Stage 2: Classify, then strategy

Three classifiers describe what the property allows. They shape sentence 1 and the price basis of the comps; there is no field for them on the row.

| Classifier | Value | Meaning |
| --- | --- | --- |
| A. Unused ratio U | 0, or under 0.4 | Development or enlargement site |
| | 0.4 to 0.8 | Enlargement possible; the economics are renovation |
| | over 0.8 | Renovation or income; land value is irrelevant |
| B. Demolition freedom | histdist or landmark set | No demolition, LPC review on exterior work; development only if the lot is vacant |
| | neither | Demolition and a new building are options |
| C. Possession | unitsres over 0 and any of: rent-stabilization fee on the bill, HCR hit, 6 or more units built before 1974 | Income in place; lawful rents, no vacancy assumption |
| | otherwise | Vacant or free-market delivery plausible; confirm with the listing and leases |

Say what classifier A means in sentence 1, in words: a development site, a building with room to grow, a renovation, or an income building with tenants in place. Land is priced per buildable sf; a building per sf. Product by size when the lot can be built on: under 2,500 sf of ZFA is a one- or two-family house; 2,500 to 6,000 sf a two- to four-family; over 6,000 sf or 5 or more units a small condo or rental building, which brings elevator, egress and 23-52 density questions.

| Strategy | Meaning | Exit evidence |
| --- | --- | --- |
| Flip | Buy as-is, renovate, sell | Closed sales whose listing said renovated, gut renovated or new |
| Rent | Buy, renovate as needed, hold | The building's Rents table and the rents of renovated comps |
| Build | Vacant or badly underbuilt lot (U under 0.4, no landmark), new construction | Land sales per buildable sf and new-construction sales |

Without Noah's word: a vacant lot is Build; an occupied building with regulated tenants is Rent; anything else is "Not set", the job ends after purchase comps, and the page carries no note about it.

**Top paragraph**: three sentences of plain prose, no labels, directly under the links line. Sentence 1: "Investigate:" or "Pass:", then what it is and, with a strategy, what the strategy would make of it. Sentence 2: the ask against the best analog, per buildable sf for land, per sf for a building. Sentence 3: the one fact that decides it. Pass means a hard gate blocks every path; Investigate means a path exists and sentence 3 names what decides it. Pursue is written only by Noah. Do not rank, recommend or grade beyond that.

## 5. Stage 3: Area, comps and rents

**Comp rules.** Closed within 18 months, within half a mile, same product family, from ACRIS deeds only: never the DOF rolling sales file or any other sales list, so comps stay comparable across pages. Purchase comps are as-is sales; a sale whose listing says renovated, gut renovated or new is an exit comp and never sits in the purchase table. Listings and in-contract deals are asks, not comps: hollow pin, no Last sale. Widen distance or time only when needed and say so in the sentence under the table. Price basis: land is price ÷ (lotarea × residfar) as $/buildable sf; houses are price ÷ bldgarea as $/PLUTO sf unless a marketed area is known and labeled; five or more units, $/unit. The script finds and tabulates these and draws the comps map; the recipe is in `SOURCES.md` for a single lookup.

**Listing pass**, once per closed comp, up to the eight on the page: find the sold listing on Redfin first, then Zillow, then Compass. Beds and baths and the listing URL go on the comp's row; list price and days on market go only in the table's Listing column as "$1.25M list · 41 days · [link]". Nothing on any of the three: "not listed", which usually means an off-market sale. A Listing cell the script filled with "renovation signal: …" means DOB alteration jobs were filed in the three years before the sale and at least six months ahead of it: check that comp first, and when its listing said renovated, gut renovated or new, move the sale to exit comps and drop it from the purchase table and the comps map. The signal misses work done without permits, so the pass still covers every comp; replace the signal text with the listing facts once you have them. One lookup per comp, a few seconds apart. A comp row that already has a Listing URL was found in an earlier job: open that URL instead of searching again. If a site rate-limits you, stop using it for the rest of the job and say so in the Run log line.

Persist the listing pass in `data/reviews/<BBL>.json`, or pass a file with `--review`; each sale needs its BBL and closing
date, condition (`unknown`, `as_is`, `renovated`, `new`, `exclude`), listing URL and supporting evidence. Keep unknown values
null. The screen saves a review template beside its JSON output and automatically loads an existing BBL review on future
fresh runs. `renovated` and `new` cannot reappear as purchase comps; they produce an exit table only when the strategy and
evidence allow one. `include_unreviewed: false` keeps the reviewed selection without adding unreviewed replacements.
Refresh the saved screen after updating the file so table order, map numbers and comp relations agree.

**Comp rows.** Every comp is a row with Type Comp, one per building, keyed by BBL (`NOTION.md`). The latest sale is Last sale price and date; the one before is Prior sale. Two sales within three years are a renovation pair; name it in the sentence under the table. A comp's page is the light template: photo from the sold listing, links line, one or two sentences, a Details table of at most five rows, Rents if StreetEasy has history, and the Noah's notes heading. A tracked deal that later records a deed is the same row: Last sale filled, Verdict set to Closed by others.

**Sentence under the table.** Name the best analogs (same district, one-third to three times the subject's size) against the ask, and any lot that sold twice. No implied-value range.

**Exit comps**, only with a strategy: same table shape, up to eight rows, one sentence, evidence per the strategy table above. Under Rent, cite the renovated comps' rents per unit from their Rents tables.

**Rents**, whenever the building has units: asking rents from StreetEasy's listing history, one row per unit (Unit, Beds/baths, Last asking rent, Listed as month and year, a "unit page" or "building page" link). These are asking rents from past listings, not a rent roll; actual rents stay an open item resolved by leases. No history: the section is absent and the row's Flags gets "No rent history".

**Maps and reusable area data.** Use the native Notion map views of Properties. Each record has Type and Location; properties
and nearby restaurants, parks and subway stations share the database. Normal runs reuse the shared place directory and do
not repeat Google Places searches or render map images. In local dry runs the portable directory is `data/amenities.json`;
Notion writes read the current place records from the database. The Area section carries a map link, sources and distances.
Restaurant entries are directory facts, without copied Google ratings. Park distances remain to the nearest polygon edge.
An area with missing coverage is Unknown; refresh it explicitly with `--refresh-area` when needed. `--map-images` is an explicit
legacy export. The source geometry and the old image style remain available in the script for that export.

## 6. Stage 4: Close

- Finish the top paragraph: sentence 3 names the one fact that decides the deal.
- Open items, at most six rows: Missing fact, how it moves the deal, evidence needed, who resolves it. Actual rents, legal unit count, title and the survey are the usual rows.
- Row: Strategy, Verdict, Flags, Comps used, Rules that mattered (`NOTION.md`). Append one line to Run log: date, stages run, what changed.
- Anything over a budget goes under "Research notes", a collapsed toggle near the bottom with no budget. The map code goes there as a code block, never as a file path.
- Last sale price and date on every row come from the latest ACRIS deed even when the amount is $0 (write 0); Prior sale is the deed before it.
- Never edit "Noah's notes" or anything below it.

## 7. Noah's defaults

Used unless Noah gives a different number for a deal. Cost, return and financing assumptions are deliberately absent until he has a basis for them.

| Assumption | Default |
| --- | --- |
| Comps scope | Closed within 18 months, half a mile, same product family; widen only when needed and say so |
| Exit comps | Only with a strategy: Flip renovated sales, Rent renovated comps with their rents, Build land and new construction |
| Rents | Asking rents from StreetEasy history; actual rents are an open item |
