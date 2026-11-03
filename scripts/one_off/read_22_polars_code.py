"""One-off conversion (READ-22): a Polars transform's steps live only in its code.

Run once on a project, in a commit of its own, after the other one-off scripts:

    uv run python scripts/one_off/read_22_polars_code.py <project> [...]

In each pipeline file it turns ``@pipeline.polars(config="config/polars/<node>.json")``
into ``@pipeline.polars``, the file's column settings (``selected_columns``,
``column_renames``) written as decorator keywords, and deletes the file. Haute then rebuilds
the steps from the body each time it loads it. A node whose steps cannot be rendered (an
unfinished step) keeps its placeholder body, and its step list moves into the pipeline's
``.haute.json`` under ``unfinished_steps``.

Each converted node's rebuilt steps are compared with the steps the file held. A node
whose steps differ is reported: its code stays as it is, and the statements Haute does not
recognise load as code steps. A file that no longer parses after the change is put back,
and nothing of it is changed. Haute keeps no copy of this conversion; the file is removed
once the owner has run it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from haute._json_files import render_json
from haute._polars_steps import PolarsStepError, render_node_steps
from haute._polars_steps_lift import canonical_steps
from haute._types import NodeType
from haute.parser import parse_pipeline_file

DECORATOR = re.compile(r'@pipeline\.polars\(config="(?P<path>config/polars/[^"]+\.json)"\)')
COLUMN_SETTINGS = ("selected_columns", "column_renames")


def _decorator(settings: dict[str, Any]) -> str:
    given = [f"{key}={settings[key]!r}" for key in COLUMN_SETTINGS if settings.get(key)]
    return f"@pipeline.polars({', '.join(given)})" if given else "@pipeline.polars"


def _renders(steps: list[dict[str, Any]]) -> bool:
    try:
        render_node_steps(NodeType.POLARS, steps)
    except PolarsStepError:
        return False
    return True


def _convert(pipeline: Path) -> list[str]:
    """*pipeline*'s Polars sidecars moved into its code; what to report."""
    source = pipeline.read_bytes().decode("utf-8")
    found = list(DECORATOR.finditer(source))
    if not found:
        return []
    base = pipeline.parent
    held: dict[str, dict[str, Any]] = {}
    converted = source
    for match in reversed(found):
        settings = json.loads((base / match["path"]).read_bytes().decode("utf-8"))
        node = Path(match["path"]).stem
        held[node] = settings
        converted = converted[: match.start()] + _decorator(settings) + converted[match.end() :]
    unfinished = {
        node: settings["steps"]
        for node, settings in held.items()
        if isinstance(settings.get("steps"), list) and not _renders(settings["steps"])
    }
    sidecar_path = pipeline.with_suffix(".haute.json")
    sidecar_before = sidecar_path.read_bytes() if sidecar_path.is_file() else None
    pipeline.write_bytes(converted.encode("utf-8"))
    try:
        graph = parse_pipeline_file(pipeline)
    except Exception as error:  # noqa: BLE001 - put the file back, whatever stopped it
        pipeline.write_bytes(source.encode("utf-8"))
        raise SystemExit(
            f"{pipeline}: does not parse once converted ({error}); put back."
        ) from None
    report: list[str] = []
    nodes = {node.id: node for node in graph.nodes}
    for name, settings in held.items():
        steps = settings.get("steps")
        if name in unfinished or not isinstance(steps, list):
            continue
        node = nodes.get(name)
        rebuilt = None if node is None else node.data.config.get("steps")
        if rebuilt != canonical_steps(NodeType.POLARS, steps):
            report.append(f"{pipeline}: {name}'s steps rebuild otherwise; its code is kept")
    if unfinished:
        sidecar = json.loads(sidecar_before.decode("utf-8")) if sidecar_before else {}
        sidecar["unfinished_steps"] = {
            **sidecar.get("unfinished_steps", {}),
            **dict(sorted(unfinished.items())),
        }
        sidecar_path.write_bytes(render_json(sidecar).encode("utf-8"))
    for match in found:
        (base / match["path"]).unlink()
    folder = base / "config" / "polars"
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
    return report


def convert(projects: list[Path]) -> tuple[list[Path], list[str]]:
    pipelines = sorted(
        {
            file
            for project in projects
            for file in project.rglob("*.py")
            if ".venv" not in file.parts and DECORATOR.search(file.read_bytes().decode("utf-8"))
        }
    )
    report: list[str] = []
    for pipeline in pipelines:
        report.extend(_convert(pipeline))
    return pipelines, report


if __name__ == "__main__":
    converted, reported = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    for line in reported:
        print(line)
    print(f"{len(converted)} pipeline file(s) keep their Polars steps in code alone")
