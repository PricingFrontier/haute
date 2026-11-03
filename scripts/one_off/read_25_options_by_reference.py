"""One-off conversion (READ-25): keep a sourced column's allowed values only as a reference.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_25_options_by_reference.py <project or folder> [...]

In each ``workbench/schema/*.json`` it drops the ``options`` a column with an
``options_source`` carries, a copy of its params file's values: reading the form fills them
from the file. A schema file holding no such copy is left alone, so the script can run on a
project in which some files are converted, before or after merging ``main``. A file that is
not a schema file is refused, naming it, and nothing is changed. Haute keeps no copy of this
conversion; the file is removed once the owner has run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from haute._json_files import ANY, at, render_json_bytes


def convert(paths: list[Path]) -> list[Path]:
    """The schema files under *paths* converted, all or none."""
    files = sorted(
        {
            file
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("workbench/schema/*.json"))
            if ".venv" not in file.parts
        }
    )
    planned: dict[Path, bytes] = {}
    for file in files:
        data = json.loads(file.read_bytes().decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("columns"), list):
            raise SystemExit(f"{file}: not a schema file; nothing converted.")
        changed = False
        for column in data["columns"]:
            if isinstance(column, dict) and "options_source" in column and "options" in column:
                del column["options"]
                changed = True
        if changed:
            planned[file] = render_json_bytes(data, one_line=at(("columns", ANY)))
    for file, data in planned.items():
        file.write_bytes(data)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} schema file(s) now keep sourced values as a reference")
