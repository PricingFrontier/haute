"""One-off conversion (READ-28): write each Constant sidecar's values as a map.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_28_constant_maps.py <project or folder> [...]

It converts each ``config/constant/*.json`` whose ``values`` are still rows of
``{"name", "value"}`` into a map from name to value, a number where it reads back to its
text, and leaves alone each one already written as a map, so it can run on a project in
which some files are already converted, before or after merging ``main``. A file in neither
form is refused, naming it, and nothing is changed. Haute keeps no copy of this conversion;
the file is removed once the owner has run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from haute._config_io import node_config_json
from haute._types import NodeType


def convert(paths: list[Path]) -> list[Path]:
    """The Constant sidecars under *paths* converted, all or none."""
    files = sorted(
        {
            file
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("config/constant/*.json"))
            if ".venv" not in file.parts
        }
    )
    planned: dict[Path, bytes] = {}
    for file in files:
        config = json.loads(file.read_bytes().decode("utf-8"))
        values = config.get("values") if isinstance(config, dict) else None
        if isinstance(values, dict):
            continue
        if not isinstance(values, list):
            raise SystemExit(f"{file}: not a Constant config; nothing converted.")
        text = node_config_json(NodeType.CONSTANT, config, node_label=file.stem)
        planned[file] = text.encode("utf-8")
    for file, data in planned.items():
        file.write_bytes(data)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} Constant sidecar(s) written as maps")
