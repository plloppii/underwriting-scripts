# Codex

Local maintenance notes, in addition to `AGENTS.md`. Property facts and workflow rules remain in the four core documents.

- Use the repository's `.venv/bin/python`; the system interpreter may not have the map packages. `uv sync` installs the
  declared packages without requiring pip in the virtual environment.
- Noah configured `GOOGLE_PLACES_API_KEY` in his local `~/.zshrc`. A noninteractive command may not inherit it. Launch a
  zsh interactive subprocess and export the variable by name before running the script; never print or copy its value.
  This key is used for Places only. `GOOGLE_MAPS_API_KEY` is the separate Street View key and fallback for Places.
- A local Chrome debugging session on port 9222 supports rendered-listing floor plans. The screen opens and closes its own
  tab. Saved rendered HTML or an explicit verified image can be used if the browser is unavailable.
- Local screen output, logs and pre-update Notion snapshots belong under `.cache/runs/`; they are gitignored. Persist
  reusable sale-condition evidence in `data/reviews/<BBL>.json`, never integration credentials.
- For an existing hand-finished page, use the limited Notion refresh documented in `docs/NOTION.md`. Verify that the manual
  details, opening paragraph, original photo and the original Noah's notes block IDs and content survive the update.
