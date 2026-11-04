"""One-off conversion (READ-23): know a workbench table and its columns by their names.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_23_schema_names.py <project or folder> [...]

In each ``workbench/schema/<table>.json`` but ``quote_details.json`` it drops the table's
``id`` and each column's ``id``, and writes the file as Haute writes it. The sheets, the
samples and ``quote_details.json`` already name tables and columns by name and are left
alone.

A file already written so is left alone, so the script can run on a project in which some
files are converted. A file that is not a table's, or a table holding two columns whose
names differ only in case, is refused, naming the file, and nothing is changed. Haute keeps
no copy of this conversion; the file is removed once the owner has run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from haute._workbench_folder import render_folder_file
from haute._workbench_folder_files import TableFile


def _converted(table: dict[str, Any]) -> bool:
    return "id" not in table and all("id" not in column for column in table["columns"])


def _table(path: Path) -> bytes | None:
    """*path*'s table without ids, as Haute writes it; None when it holds none."""
    data = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("columns"), list):
        raise SystemExit(f"{path}: not a table's file; nothing converted.")
    if _converted(data):
        return None
    data.pop("id", None)
    for column in data["columns"]:
        column.pop("id", None)
    try:
        return render_folder_file(TableFile.model_validate(data))
    except ValidationError as error:
        raise SystemExit(f"{path}: {error}; nothing converted.") from None


def convert(paths: list[Path]) -> list[Path]:
    """The table files under *paths* converted, all or none."""
    files = sorted(
        {
            file
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("workbench/schema/*.json"))
            if file.name != "quote_details.json" and ".venv" not in file.parts
        }
    )
    planned = {file: data for file in files if (data := _table(file)) is not None}
    for file, data in planned.items():
        file.write_bytes(data)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} table file(s) now name their table and columns only")
