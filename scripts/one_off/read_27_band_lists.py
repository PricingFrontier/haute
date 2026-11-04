"""One-off conversion (READ-27): write each banding sidecar's breakpoints as a list of bands.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_27_band_lists.py <project or folder> [...]

It converts each ``config/banding/*.json`` whose breakpoints are still a map from each
band's upper edge to its label, and leaves alone each one already written as bands, so it
can run on a project in which some files are already converted, before or after merging
``main``. A file that is not a banding config is refused, naming it, and nothing is
changed. Haute keeps no copy of this conversion; the file is removed once the owner has
run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from haute._banding_config import expand_banding_config_from_sidecar
from haute._config_io import node_config_json
from haute._types import NodeType


def _is_old(config: object) -> bool | None:
    """True for breakpoints written as a map, False for bands; None for neither."""
    if not isinstance(config, dict) or not isinstance(config.get("factors", []), list):
        return None
    return any(
        isinstance(factor, dict)
        and factor.get("banding") == "breakpoints"
        and isinstance(factor.get("rules"), dict)
        for factor in config.get("factors", [])
    )


def convert(paths: list[Path]) -> list[Path]:
    """The banding sidecars under *paths* converted, all or none."""
    files = sorted(
        {
            file
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("config/banding/*.json"))
            if ".venv" not in file.parts
        }
    )
    planned: dict[Path, bytes] = {}
    for file in files:
        config = json.loads(file.read_bytes().decode("utf-8"))
        old = _is_old(config)
        if old is None:
            raise SystemExit(f"{file}: not a banding config; nothing converted.")
        if old:
            text = node_config_json(
                NodeType.BANDING, expand_banding_config_from_sidecar(config), node_label=file.stem
            )
            planned[file] = text.encode("utf-8")
    for file, data in planned.items():
        file.write_bytes(data)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} banding sidecar(s) written as bands")
