# Agent instructions

This repository owns independent NYC data tools and their JSON contracts.
Underwriting methodology, page templates, comp-review evidence and all Notion
writes belong to `notion-agentic-skills`; begin there for a full deal workflow.

- Read `README.md`, `docs/SOURCES.md`, and the applicable `docs/PLACES.md` or
  `docs/HOUSING.md` before changing a source adapter.
- Refer to the person using these tools as **User**. Keep personal names and
  person-specific settings out of tracked content.
- Keep each data command independently runnable. Emit one JSON document on
  stdout, with progress/errors on stderr or in explicit JSON error fields.
- Data collection must not import Notion helpers, read private settings, load a
  repository `.env`, render a page, infer a user's strategy or write to Notion.
- A missing source field is Unknown, never a substituted zero or guess. Retain
  real zero values. Preserve source identifiers, source dates and provenance.
- Paginate complete results, apply the requested geographic radius after a
  bounding-box query, and report source failures distinctly from empty results.
- Nearby restaurants, trains, parks and housing are separate commands. A
  property fetch does not run them implicitly.
- Private destination IDs, reviews, settings, credentials and deployment state
  stay outside Git. Local API results/logs go under `.cache/runs/`.
- Use the personal `github-personal` SSH identity for
  `plloppii/underwriting-scripts`. Do not modify global Git settings.
- Use `.venv/bin/python` locally. Run the offline test suite and meaningful live
  read-only queries when a source or JSON interface changes. Never execute a
  consuming skill or write to its external applications while testing data tools.
