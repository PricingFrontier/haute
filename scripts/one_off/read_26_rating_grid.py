"""One-off conversion (READ-26): write each rating sidecar's tables as a grid.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_26_rating_grid.py <project or folder> [...]

It converts each ``config/rating_step/*.json`` still in the row-array form (a table with
``entries``) and leaves alone each one already written as a grid, so it can run on a project
in which some files are already converted, before or after merging ``main``. A file in
neither form is refused, naming it, and nothing is changed. Haute's own code keeps no copy of
this conversion (specs/README.md: nothing migrates an old form); the file is removed once the
owner has run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from haute._config_io import node_config_json
from haute._rating_step_config import normalise_rating_step_config
from haute._types import NodeType


def _form(config: object) -> str:
    """``rows`` for a sidecar written as a grid, ``entries`` for the row-array form."""
    if not isinstance(config, dict):
        return "neither"
    tables = config.get("tables")
    if not isinstance(tables, list):
        return "rows"
    if any(isinstance(table, dict) and "entries" in table for table in tables):
        if any(
            isinstance(table, dict) and ("rows" in table or "columns" in table) for table in tables
        ):
            return "neither"
        return "entries"
    return "rows"


def convert(paths: list[Path]) -> list[Path]:
    """The sidecars under *paths* converted, all or none: a file in neither form refuses
    the whole run before anything is written."""
    files = sorted(
        {
            file
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("config/rating_step/*.json"))
            if ".venv" not in file.parts
        }
    )
    planned: dict[Path, bytes] = {}
    for file in files:
        config = json.loads(file.read_bytes().decode("utf-8"))
        form = _form(config)
        if form == "neither":
            raise SystemExit(f"{file}: neither a grid nor the row-array form; nothing converted.")
        if form == "entries":
            text = node_config_json(
                NodeType.RATING_STEP, normalise_rating_step_config(config), node_label=file.stem
            )
            planned[file] = text.encode("utf-8")
    for file, data in planned.items():
        file.write_bytes(data)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} rating sidecar(s) written as grids")
