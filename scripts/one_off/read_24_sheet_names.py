"""One-off conversion (READ-31, READ-24): write sheets plainly, their parts known by title and
number, not by id.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_24_sheet_names.py <project or folder> [...]

For each ``workbench/sheets/<name>.json`` and its ``<name>.layout.json`` it:
- turns a Collection's ``spacers`` into a ``null`` in ``fields`` at each gap's place
  (READ-31);
- turns a Table's or Collection's ``column_looks`` into an object holding the field and its
  settings, in the field's place in ``fields`` (READ-31);
- turns a ``match`` that is a number, on a ``show_when`` or a group, into ``at_least``
  (READ-31);
- drops the sheet's, each section's and each component's ``id``;
- gives each component a ``number``, 1, 2, 3 ... in its section's order;
- keys the layout's sections by their titles and their components by their numbers.

A sheet already written so is left alone, so the script can run on a project in which some
sheets are converted. A sheet without its layout file, a layout that does not place every
section and component of its sheet, or two sections on one sheet titled alike is refused,
naming the file, and nothing is changed. Haute keeps no copy of this conversion; the file is
removed once the owner has run it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from haute._workbench_folder import render_folder_file
from haute._workbench_folder_files import LayoutFile, SheetFile


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


def _converted(sheet: dict[str, Any]) -> bool:
    sections = sheet.get("sections", [])
    return "id" not in sheet and all(
        "id" not in section
        and all("id" not in c and "number" in c for c in section.get("components", []))
        for section in sections
    )


def _pair(sheet_file: Path) -> tuple[bytes, bytes | None] | None:
    """*sheet_file* and its layout written plainly and by title and number (the layout None
    when it is unchanged); None if both are already so."""
    layout_file = sheet_file.with_name(sheet_file.name[: -len(".json")] + ".layout.json")
    sheet = json.loads(sheet_file.read_bytes().decode("utf-8"))
    if not isinstance(sheet, dict) or "sections" not in sheet:
        raise SystemExit(f"{sheet_file}: not a sheet; nothing converted.")
    sheet, plainer = _sheet(sheet)
    if _converted(sheet):
        return None if not plainer else (render_folder_file(SheetFile.model_validate(sheet)), None)
    if not layout_file.is_file():
        raise SystemExit(f"{sheet_file}: no {layout_file.name} beside it; nothing converted.")
    layout = json.loads(layout_file.read_bytes().decode("utf-8"))
    titles = [section["title"] for section in sheet["sections"]]
    twice = next((title for title in titles if titles.count(title) > 1), None)
    if twice is not None:
        raise SystemExit(
            f"{sheet_file}: two sections are titled {twice!r}; retitle one, then run this "
            "again. Nothing converted."
        )
    placed: dict[str, Any] = {}
    for section in sheet["sections"]:
        boxes = layout["sections"].get(section.get("id"))
        if boxes is None:
            raise SystemExit(
                f"{layout_file}: no place for the section {section['title']!r}; nothing converted."
            )
        components: dict[str, Any] = {}
        for number, component in enumerate(section["components"], start=1):
            box = boxes["components"].get(component.get("id"))
            if box is None:
                raise SystemExit(
                    f"{layout_file}: no place for component {component.get('id')!r} in the "
                    f"section {section['title']!r}; nothing converted."
                )
            component.pop("id", None)
            component["number"] = number
            components[str(number)] = box
        section.pop("id", None)
        placed[section["title"]] = {**boxes, "components": components}
    sheet.pop("id", None)
    layout["sections"] = placed
    try:
        return (
            render_folder_file(SheetFile.model_validate(sheet)),
            render_folder_file(LayoutFile.model_validate(layout)),
        )
    except ValidationError as error:
        raise SystemExit(
            f"{sheet_file}: not a sheet this conversion knows ({error}); nothing converted."
        ) from None


def convert(paths: list[Path]) -> list[Path]:
    """The sheets under *paths* converted, with their layouts, all or none."""
    files = sorted(
        {
            file
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("workbench/sheets/*.json"))
            if not file.name.endswith(".layout.json") and ".venv" not in file.parts
        }
    )
    planned: dict[Path, tuple[bytes, bytes | None]] = {}
    for file in files:
        pair = _pair(file)
        if pair is not None:
            planned[file] = pair
    for file, (sheet, layout) in planned.items():
        file.write_bytes(sheet)
        if layout is not None:
            file.with_name(file.name[: -len(".json")] + ".layout.json").write_bytes(layout)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} sheet(s) written plainly, their parts known by title and number")
