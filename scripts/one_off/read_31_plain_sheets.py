"""One-off conversion (READ-31): write each sheet's gaps, conditions and looks plainly.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_31_plain_sheets.py <project or folder> [...]

In each ``workbench/sheets/*.json`` (not the ``.layout.json`` files) it turns:
- a Collection's ``spacers`` into a ``null`` in ``fields`` at each gap's place;
- a Table's or Collection's ``column_looks`` into an object holding the field and its
  settings, in the field's place in ``fields``;
- a ``match`` that is a number, on a ``show_when`` or a group, into ``at_least``.

A sheet already written so is left alone, so the script can run on a project in which some
sheets are converted, before or after merging ``main``. A file that is not a sheet is
refused, naming it, and nothing is changed. Haute keeps no copy of this conversion; the file
is removed once the owner has run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from haute._workbench_folder import render_folder_file
from haute._workbench_folder_files import SheetFile


def _rule(rule: Any) -> tuple[Any, bool]:
    """*rule* (a show_when or a group) with a numeric match as at_least; whether it changed."""
    if not isinstance(rule, dict):
        return rule, False
    changed = False
    match = rule.get("match")
    if isinstance(match, int) and not isinstance(match, bool):
        rule = {("at_least" if key == "match" else key): value for key, value in rule.items()}
        changed = True
    conditions = rule.get("conditions")
    if isinstance(conditions, list):
        converted = []
        for condition in conditions:
            if isinstance(condition, dict) and "conditions" in condition:
                condition, inner = _rule(condition)
                changed |= inner
            converted.append(condition)
        rule = {**rule, "conditions": converted}
    return rule, changed


def _component(component: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    if "show_when" in component:
        rule, changed = _rule(component["show_when"])
        component = {**component, "show_when": rule}
    looks = {look["column"]: look for look in component.get("column_looks", [])}
    if looks:
        for look in looks.values():
            if "show_when" in look:
                look["show_when"] = _rule(look["show_when"])[0]
    gaps = list(component.get("spacers", []))
    if not looks and not gaps:
        return component, changed
    fields: list[Any] = []
    for index, field in enumerate(component.get("fields", [])):
        while gaps and gaps[0] == index:
            fields.append(None)
            gaps.pop(0)
        look = looks.get(field)
        fields.append(
            field
            if look is None
            else {"field": field, **{k: v for k, v in look.items() if k != "column"}}
        )
    fields.extend(None for _ in gaps)
    converted = {
        key: value for key, value in component.items() if key not in ("spacers", "column_looks")
    }
    converted["fields"] = fields
    return converted, True


def _sheet(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    if "show_when" in data:
        data["show_when"], changed = _rule(data["show_when"])
    for section in data.get("sections", []):
        if "show_when" in section:
            section["show_when"], inner = _rule(section["show_when"])
            changed |= inner
        components = []
        for component in section.get("components", []):
            component, inner = _component(component)
            changed |= inner
            components.append(component)
        section["components"] = components
    return data, changed


def convert(paths: list[Path]) -> list[Path]:
    """The sheets under *paths* converted, all or none."""
    files = sorted(
        {
            file
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("workbench/sheets/*.json"))
            if not file.name.endswith(".layout.json") and ".venv" not in file.parts
        }
    )
    planned: dict[Path, bytes] = {}
    for file in files:
        data = json.loads(file.read_bytes().decode("utf-8"))
        if not isinstance(data, dict) or "sections" not in data:
            raise SystemExit(f"{file}: not a sheet; nothing converted.")
        converted, changed = _sheet(data)
        if changed:
            planned[file] = render_folder_file(SheetFile.model_validate(converted))
    for file, data in planned.items():
        file.write_bytes(data)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} sheet(s) written plainly")
