#!/bin/sh
# Install the independent data tools; no Notion integration or workflow setup.
set -eu
tools_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python_runtime="${PYTHON:-python3}"
"$python_runtime" -m pip install -r "$tools_root/requirements.txt"
"$python_runtime" "$tools_root/scripts/screen.py" --check
