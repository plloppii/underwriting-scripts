# Page template (v5)

Two page shapes: the deal page and the lighter comp page. Square brackets are instructions and never appear on a page. The method is `PLAYBOOK.md`; the Notion mechanics are `NOTION.md`. Nothing on a page is for Noah to fill in. Omit any section that has nothing to say, without explanation. Never write notes addressed to Noah or to yourself.

---

## Deal page

[IMAGE: the front of the property, uploaded as a file: the listing's lead photo, else Street View, else the links line begins "No photo · "]

[Listing](https://www.zillow.com/homedetails/63-Irving-Pl-Brooklyn-NY-11238/112504346_zpid/) · [StreetEasy](https://streeteasy.com/building/63-irving-place-brooklyn) · [ZoLa](https://zola.planning.nyc.gov/l/lot/3/1984/5) · [BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=3393676) · [ACRIS](https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?borough=3&block=1984&lot=5) · [DOF](https://propertyinformationportal.nyc.gov/parcels/parcel/3019840005)

[Each word is itself the link (these point at 63 Irving Place); no bare URL anywhere on the page. With no live listing the first word is "Off market", linked to the property's Zillow or StreetEasy page.]

Investigate: vacant 12.75 × 53 ft R6B interior lot in Clinton Hill that builds a 1,352 sf one- or two-family house. The ask is $370 per buildable sf; the one R6B lot of similar size sold at $223. A survey showing the lot sits within 100 ft of the Putnam corner removes the rear yard; failing that, the 1961 lot depth gives a 10 ft yard.

[Three sentences of plain prose, no labels: the verdict word and what it is; the ask against the best analog; the one fact that decides it. `PLAYBOOK.md`, section 4.]

### Floor plan

[Only if the listing has one. Image, no text.]

### Details

| Field | Finding | Source |
| --- | --- | --- |
| Ask | $499,999 · listed 2026-09-02 · 43 days on market | [listing] |
| Lot | 3-01984-0005 · BIN 3393676 · 12.75 × 53 ft, 676 sf, interior | [ZoLa] |
| Zoning | R6B · no overlay · no special district · not historic | [ZoLa] |
| Existing building | Vacant land · or: three-family walk-up · 3 units · built 1910 · 2 floors · 2,320 sf gross | [ZoLa] |
| Buildable | 676 × 2.0 = 1,352 sf · 0 built · 1,352 unused | [ZR 23-22](https://zr.planning.nyc.gov/article-ii/chapter-3/23-22) |
| Owner · last deed | Minta Gillespie Walker Living Trust · deed 2012-11-12 · $0, non-market | [ACRIS] |
| Violations · jobs | none open (DOB, ECB, HPD) · jobs: minor alteration, permit issued 2005 | [BIS], [HPD] |
| Taxes · rent regulation | $ per year · regulation signal · [only when the lot has units] | [DOF] |
| Flood | zone AE, high risk · flood insurance required · base flood elevation 10 ft · [only when a zone is mapped, effective or 2015 preliminary] | [FEMA] |

[These Field names; 9 rows max; 12 words per Finding. Building classes and DOB codes in words, never codes. Every Source is a linked label to the lot-specific page. An estimate says so in the cell ("about 63 ft, survey to confirm"). Delete rows that do not apply.]

### Zoning

Buildable: 676 sf × FAR 2.0 = 1,352 sf ([ZR 23-22](https://zr.planning.nyc.gov/article-ii/chapter-3/23-22))

Rear yard, corner exemption: no rear yard is required within 100 ft of a street corner; the whole lot sits about 63 to 93 ft from the Putnam corner, estimated from lot centroids. A survey proves it. ([ZR 23-344](https://zr.planning.nyc.gov/article-ii/chapter-3/23-344))

Rear yard, shallow lot: a lot under 95 ft deep loses 6 in of rear yard per foot short, floor 10 ft; 30 − 0.5 × (95 − 53) = 10 ft. Proof of the lot's depth on December 15, 1961 is required. ([ZR 23-342](https://zr.planning.nyc.gov/article-ii/chapter-3/23-342))

Lot size: 12.75 ft wide and 676 sf, under the 18 ft and 1,700 sf minimums, but exempt as a zoning lot that existed on December 5, 2024 and has not shrunk since. ([ZR 23-10](https://zr.planning.nyc.gov/article-ii/chapter-3/23-10))

[The buildable line, then up to 3 rules of up to 3 lines each: only rules specific to this lot, each with the number for this lot, what proves it, and the official link labeled "ZR 23-344". Rules that apply to every lot in the district do not go here. Quoted text goes in Research notes.]

### Area

[Default: link to the native Neighborhood map and a short list of nearby shared places. Reuse the existing directory;
no new restaurant search or image rendering on an ordinary run. The image layout below is for an explicit --map-images export.]

[The area map (`PLAYBOOK.md`, section 5), then in this order: one caption line, the pin list as numbered blocks grouped by type with each line colored like its pins, and one sentence only for a negative (historic district or flood zone). No new-building projects, no sentence on development pace.]

Map: OpenStreetMap tiles © OpenStreetMap contributors · NYC 2020 neighborhood boundaries · MTA stations · Google Places · NYC Parks · checked 2026-10-16 · straight-line miles

1. Halsey St (L) · subway · 0.20 mi [blue, as are all train lines]
2. Wilson Av (L) · subway · 0.31 mi
3. Saraghina · restaurant · 4.6 (1,200 ratings) · 0.15 mi [red, as are all food lines]
4. Irving Square Park · park, 2.4 acres · 0.22 mi [green]

### Rents

[Only when the building has units and StreetEasy has history for it. One row per unit, asking rents from past listings, never called a rent roll. With no history the section is absent and the row's Flags says "No rent history".]

| Unit | Beds/baths | Last asking rent | Listed | StreetEasy |
| --- | --- | --- | --- | --- |
| 1 | 2 / 1 | $2,950 | March 2025 | [unit page] |
| 2 | 2 / 1 | $3,100 | August 2026 | [building page, when the unit has no page] |

### Purchase comps

[Default: use the native Comps map in the shared database. The table and linked property rows remain on the deal page;
the generated map image below is an explicit legacy export.]

[The comps map: same basemap and frame, comps numbered in table order, filled pin for a closed sale, hollow for a listing, no text in the image. Then every qualifying comp, up to 8 rows, nearest first: closed ACRIS deeds only, as-is sales of the same product family. Then one sentence naming the best analogs against the ask and any lot that sold twice. No implied-value range.]

| # | Address | Closed | Price | Zoning | Area · basis | $/sf | Listing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | [address, linked to its ACRIS page] · 0.21 mi | date | $ | R6B | sf · buildable / building / marketed | $ | $1.25M list · 41 days · [link], or "not listed"; the script pre-fills "renovation signal: …" from DOB jobs until the listing pass replaces it |

Best analogs: #7 148 Madison Street at $223 per buildable sf; the ask is $370. The other rows are different districts or two to seven times the size.

### Exit comps

[Only when Strategy is set. Same table shape, up to 8 rows, one sentence; the evidence per strategy is in `PLAYBOOK.md`, section 4.]

### Open items

| Missing fact | Moves the deal how | Evidence needed | Who |
| --- | --- | --- | --- |
| [fact] | [effect] | [document or record] | [Noah / architect / attorney] |

[6 rows max. Actual rents, legal unit count, title and the survey are the usual rows.]

Run log: [2026-10-16 · stages 0-3 · created]

▸ Research notes [a toggle block, collapsed, never a heading; no budget: rule quotes, precedents, BSA cases, long source notes, the map code as a code block]

### Noah's notes

[Noah's. Nothing automated edits this heading or anything below it.]

---

## Comp page

[For a row with Type Comp. 150 words excluding tables. No maps, no zoning block, no comps of its own.]

[IMAGE: the lead photo from the sold listing; failing that, Street View; failing both, the links line begins "No photo · "]

[Sold listing](https://www.zillow.com/homes/700-Monroe-St-Brooklyn-NY-11221_rb/) · [StreetEasy](https://streeteasy.com/building/700-monroe-street-brooklyn) · [ZoLa](https://zola.planning.nyc.gov/l/lot/3/1642/20) · [ACRIS](https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?borough=3&block=1642&lot=20)

Converted two-family, 19.92 × 40 ft on a 20 × 100 R6B lot in Bedford-Stuyvesant; sold for $1,990,000 in July 2026, $1,248 per sf of building, after a gut renovation, having sold as-is for $600,000 in December 2025. Listed at $1,995,000, 4 beds 3 baths, 38 days on market.

[One or two sentences: what it is, what it sold for and when, per sf on its basis, what the listing said about its condition, beds and baths, days on market.]

### Details

| Field | Finding | Source |
| --- | --- | --- |
| Lot | 3-01642-0020 · 20 × 100 ft, 2,000 sf, interior | [ZoLa] |
| Existing building | converted two-family · 2 units · built 1899 · 2 floors · 1,594 sf gross | [ZoLa] |
| Zoning | R6B · 3,984 sf allowed | [ZoLa] |
| Sales | $1,990,000 · 2026-07-30 · prior $600,000 · 2025-12-03 | [ACRIS] |
| Listing | $1,995,000 list · 4 / 3 · 38 days · renovated | [listing] |

### Rents

[Only when StreetEasy has history. Same table as the deal page.]

### Noah's notes

[Noah's. Never edited.]

---

## Budgets

| Section of the page | Budget |
| --- | --- |
| Top paragraph | 3 sentences, no labels |
| Details table | 9 rows; Finding cell 12 words; Field, Finding, Source only |
| Zoning | 1 buildable line + up to 3 rule entries of 3 lines each |
| Area | map + 1 caption line + grouped pin list (about 10) + 1 sentence only for a negative |
| Rents | 1 table, one row per unit, up to 8 rows; absent when there is no history |
| Purchase comps | map + up to 8 rows with the Listing column + 1 sentence |
| Exit comps | up to 8 rows + 1 sentence, only with a strategy |
| Open items | 6 rows |
| Run log | 1 line per run |
| Noah's notes | his; never edited |
| Comp page | photo, links, 1 to 2 sentences, Details table of 5 rows, Rents table if any; 150 words excluding tables |
| Whole deal page excluding tables, the pin list, Research notes and Noah's notes | 450 words |
