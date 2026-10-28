# Maintenance handoff

The split is now explicit: this repository owns independent NYC JSON data tools;
`notion-agentic-skills` owns reusable workflow instructions, underwriting policy,
page composition, media, comp-review evidence and Notion adapters.

The former `screen.py --notion` workflow moved to
`notion-agentic-skills/scripts/underwriting/workflow.py`. The data `screen.py`
now emits property JSON only; `property.py` is its equivalent entry point.
Restaurants, subway stations, parks and housing have separate commands. The repository and Python project are named `underwriting-scripts`. GitHub redirects the previous repository URL, and the old local checkout path is a compatibility symlink for existing artifacts.

User-specific Notion state, the amenity directory and saved comp reviews were
removed from tracked data and retained in private runtime storage. The consuming
skill resolves the requesting person’s current Private Agent Settings before
writing. Shared instruction bodies reconcile two ways in that repository;
private settings are excluded from its snapshot and reconciliation mappings.

Use `README.md` for commands and `docs/SOURCES.md`, `docs/PLACES.md` and
`docs/HOUSING.md` for source coverage. The old playbook/template/output filenames
are migration pointers, not duplicated instruction authorities. Local pre-split
artifacts are retained outside Git, and previous implementation history remains
in Git.
