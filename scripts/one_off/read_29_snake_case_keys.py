"""One-off conversion (READ-29): write every key Haute's schemas own in snake_case.

Run once on a project, in a commit of its own:

    uv run python scripts/one_off/read_29_snake_case_keys.py <project or folder> [...]

It reads every pipeline and submodel file under the paths and converts:

- each node config sidecar a decorator names (``config="..."``), held to the node type the
  decorator gives: a key is renamed only where that type's config model declares the new
  key, so a column, level, input name, factor, source or constant the analyst named keeps
  its name, whatever its casing;
- a rating table's dtype descriptors (``timeUnit``, ``timeZone``) and every Polars step's
  keys, in a sidecar and in the pipeline's ``.haute.json`` (an unfinished step list);
- a decorator's camelCase keywords in the Python file itself: the transform's
  ``inputMapping=``, and any other its node type declares in snake_case (a hand-written
  Edge Join's ``leftOn=``).

A file already in snake_case is left byte for byte as it is, so the conversion can run on a
project in which some files are converted, before or after merging ``main``. Each converted
sidecar is written as Haute saves it, and held to its config model on the way: a file the
conversion cannot bring to the current form refuses the whole run, naming it, before
anything is written. Haute's own code keeps no copy of this conversion (specs/README.md:
nothing migrates an old form); the file is removed once the owner has run it.
"""

from __future__ import annotations

import ast
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any, NotRequired, Required, Union

from typing_extensions import is_typeddict

from haute._config_io import (
    config_from_sidecar,
    node_config_json,
    reject_duplicate_keys_hook,
)
from haute._config_validation import SNAKE_CASE_KEYS
from haute._json_files import render_json
from haute._node_config_models import NODE_CONFIG_MODELS
from haute._sidecar import SidecarModel
from haute._types import DECORATOR_TO_NODE_TYPE, NodeType

STEP_KEYS = {
    key: SNAKE_CASE_KEYS[key]
    for key in (
        "orderBy",
        "nullsLast",
        "numberType",
        "variableName",
        "valueName",
        "leftOn",
        "rightOn",
        "maintainOrder",
    )
}
DESCRIPTOR_KEYS = {key: SNAKE_CASE_KEYS[key] for key in ("timeUnit", "timeZone")}
SKIPPED_PARTS = {".venv", "node_modules", ".git"}


def _unwrap(hint: Any) -> list[Any]:
    """The concrete types *hint* may be: unions flattened, ``Required``/``Annotated`` unwrapped."""
    origin = typing.get_origin(hint)
    if origin in (Required, NotRequired, typing.Annotated):
        return _unwrap(typing.get_args(hint)[0])
    if origin in (Union, types.UnionType):
        return [part for arg in typing.get_args(hint) for part in _unwrap(arg)]
    return [hint]


def _declared_keys(hint: Any) -> dict[str, Any] | None:
    """The keys a record shape among *hint*'s types declares, each with its type."""
    declared: dict[str, Any] = {}
    found = False
    for part in _unwrap(hint):
        if is_typeddict(part):
            found = True
            declared.update(typing.get_type_hints(part, include_extras=True))
        elif isinstance(getattr(part, "model_fields", None), dict):
            found = True
            for name, info in part.model_fields.items():
                declared[info.alias or name] = info.annotation
    return declared if found else None


def _container_item(hint: Any, container: type) -> Any | None:
    """The item type of a ``list[X]`` or the value type of a ``dict[str, X]`` among *hint*'s."""
    for part in _unwrap(hint):
        if typing.get_origin(part) is container:
            args = typing.get_args(part)
            return args[-1] if args else Any
    return None


class KeyCollisionError(ValueError):
    """A renamed key the record already holds under its new name: keeping either value
    would discard the other, so the conversion refuses instead."""


def _put(record: dict[str, Any], key: str, item: Any) -> None:
    if key in record:
        raise KeyCollisionError(f"the key {key!r} is written twice, once in its camelCase form")
    record[key] = item


def _steps(value: Any) -> Any:
    """A step tree with every step key renamed: steps hold no map keyed by the analyst."""
    if isinstance(value, list):
        return [_steps(item) for item in value]
    if isinstance(value, dict):
        renamed: dict[str, Any] = {}
        for key, item in value.items():
            _put(renamed, STEP_KEYS[key] if key in STEP_KEYS else key, _steps(item))
        return renamed
    return value


def _descriptor(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    renamed: dict[str, Any] = {}
    for key, item in value.items():
        _put(renamed, DESCRIPTOR_KEYS[key] if key in DESCRIPTOR_KEYS else key, item)
    return renamed


def _descriptors(value: Any) -> Any:
    """A rating table's ``factor_dtypes``: each factor (the analyst's) to its descriptor."""
    if not isinstance(value, dict):
        return value
    return {factor: _descriptor(descriptor) for factor, descriptor in value.items()}


def rename(value: Any, hint: Any) -> Any:
    """*value* with each camelCase key renamed where the shape *hint* declares its new name."""
    if isinstance(value, dict):
        declared = _declared_keys(hint)
        if declared is not None:
            renamed: dict[str, Any] = {}
            for key, item in value.items():
                new = SNAKE_CASE_KEYS.get(key)
                if new is not None and new in declared and key not in declared:
                    key = new
                if key == "steps":
                    item = _steps(item)
                elif key == "factor_dtypes":
                    item = _descriptors(item)
                elif key in declared:
                    item = rename(item, declared[key])
                _put(renamed, key, item)
            return renamed
        mapped = _container_item(hint, dict)
        if mapped is not None:
            # A map keyed by the analyst: its keys stay; its values follow their shape.
            return {key: rename(item, mapped) for key, item in value.items()}
        return value
    if isinstance(value, list):
        item_hint = _container_item(hint, list)
        if item_hint is not None:
            return [rename(item, item_hint) for item in value]
    return value


def _pipeline_directory(tree: ast.Module, path: Path) -> Path:
    """The directory the file's ``config=`` paths resolve against, as the parser reads it:
    the file's own, climbed by a submodel definition's ``Submodel(..., pipeline_dir=...)``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "Submodel":
            continue
        for keyword in node.keywords:
            if keyword.arg != "pipeline_dir":
                continue
            if not isinstance(keyword.value, ast.Constant) or not isinstance(
                keyword.value.value, str
            ):
                raise SystemExit(f"{path}: pipeline_dir= is not a string; nothing converted.")
            return (path.parent / keyword.value.value).resolve()
    return path.parent


def _decorator_configs(
    source: str, path: Path
) -> tuple[dict[Path, NodeType], list[tuple[int, int, str]]]:
    """Each sidecar the file's decorators name, with its node type, and each camelCase
    keyword its node type declares in snake_case (its line, column and name), such as a
    transform's ``inputMapping=`` or a hand-written Edge Join's ``leftOn=``."""
    tree = ast.parse(source, filename=str(path))
    sidecars: dict[Path, NodeType] = {}
    keywords: list[tuple[int, int, str]] = []
    base = _pipeline_directory(tree, path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            node_type = DECORATOR_TO_NODE_TYPE.get(decorator.func.attr)
            declared = NODE_CONFIG_MODELS[node_type].keys if node_type else frozenset()
            # A ``**options`` argument has no name, so it is never a spelling of a key.
            written = {keyword.arg for keyword in decorator.keywords if keyword.arg is not None}
            for keyword in decorator.keywords:
                if keyword.arg in SNAKE_CASE_KEYS and SNAKE_CASE_KEYS[keyword.arg] in written:
                    raise SystemExit(
                        f"{path}:{keyword.lineno}: {keyword.arg}= and "
                        f"{SNAKE_CASE_KEYS[keyword.arg]}= on one decorator; nothing converted."
                    )
                if keyword.arg is not None and SNAKE_CASE_KEYS.get(keyword.arg) in declared:
                    keywords.append((keyword.lineno, keyword.col_offset, keyword.arg))
                if (
                    keyword.arg == "config"
                    and node_type is not None
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    sidecar = (base / keyword.value.value).resolve()
                    known = sidecars.get(sidecar)
                    if known is not None and known != node_type:
                        raise SystemExit(
                            f"{path}: {keyword.value.value} is named by a {known.value} and a "
                            f"{node_type.value} decorator; nothing converted."
                        )
                    sidecars[sidecar] = node_type
    return sidecars, keywords


def _renamed_keywords(source: str, starts: list[tuple[int, int, str]], path: Path) -> str:
    lines = source.split("\n")
    # From the right of each line, so a rename on it moves no column still to come.
    for line, column, old in sorted(starts, reverse=True):
        text = lines[line - 1]
        if text[column : column + len(old)] != old:
            raise SystemExit(f"{path}:{line}: cannot place the {old}= keyword.")
        lines[line - 1] = text[:column] + SNAKE_CASE_KEYS[old] + text[column + len(old) :]
    return "\n".join(lines)


def _read_json(path: Path) -> Any:
    """*path*'s JSON, refused when a key repeats: the last value would hide the first."""
    try:
        return json.loads(
            path.read_bytes().decode("utf-8"), object_pairs_hook=reject_duplicate_keys_hook
        )
    except ValueError as exc:
        raise SystemExit(f"{path}: {exc}; nothing converted.") from exc


def _sidecar_text(path: Path, node_type: NodeType) -> bytes | None:
    """The sidecar converted, or ``None`` when it holds no camelCase key."""
    config = _read_json(path)
    if not isinstance(config, dict):
        raise SystemExit(f"{path}: not a JSON object; nothing converted.")
    try:
        converted = rename(config, NODE_CONFIG_MODELS[node_type].shape)
    except KeyCollisionError as exc:
        raise SystemExit(f"{path}: {exc}; nothing converted.") from exc
    if converted == config and list(converted) == list(config):
        return None
    try:
        text = node_config_json(
            node_type, config_from_sidecar(node_type, converted), node_label=path.stem
        )
    except Exception as exc:
        raise SystemExit(f"{path}: {exc}; nothing converted.") from exc
    return text.encode("utf-8")


def _pipeline_sidecar_text(path: Path) -> bytes | None:
    """The pipeline's ``.haute.json`` with its unfinished steps' keys renamed, or ``None``."""
    data = _read_json(path)
    unfinished = data.get("unfinished_steps") if isinstance(data, dict) else None
    if not isinstance(unfinished, dict):
        return None
    try:
        converted = {node: _steps(steps) for node, steps in unfinished.items()}
    except KeyCollisionError as exc:
        raise SystemExit(f"{path}: {exc}; nothing converted.") from exc
    if converted == unfinished:
        return None
    model = SidecarModel.model_validate({**data, "unfinished_steps": converted})
    return render_json(model.model_dump(mode="json", exclude_defaults=True)).encode("utf-8")


def convert(paths: list[Path]) -> list[Path]:
    """The files under *paths* converted, all or none: a file the conversion cannot bring
    to the current form refuses the whole run before anything is written."""
    sources = sorted(
        {
            file.resolve()
            for path in paths
            for file in ([path] if path.is_file() else path.rglob("*.py"))
            if not SKIPPED_PARTS.intersection(file.parts) and file.suffix == ".py"
        }
    )
    planned: dict[Path, bytes] = {}
    sidecars: dict[Path, NodeType] = {}
    for source_path in sources:
        source = source_path.read_bytes().decode("utf-8")
        named, keywords = _decorator_configs(source, source_path)
        for sidecar, node_type in named.items():
            if sidecars.setdefault(sidecar, node_type) != node_type:
                raise SystemExit(f"{sidecar}: named by two node types; nothing converted.")
        if keywords:
            planned[source_path] = _renamed_keywords(source, keywords, source_path).encode("utf-8")
        pipeline_sidecar = source_path.with_suffix(".haute.json")
        if pipeline_sidecar.is_file():
            text = _pipeline_sidecar_text(pipeline_sidecar)
            if text is not None:
                planned[pipeline_sidecar] = text
    for sidecar, node_type in sorted(sidecars.items()):
        if not sidecar.is_file():
            # A sidecar left unread would stay in the old form, refused on the next load.
            raise SystemExit(f"{sidecar}: named by a decorator but not found; nothing converted.")
        text = _sidecar_text(sidecar, node_type)
        if text is not None:
            planned[sidecar] = text
    for file, data in planned.items():
        file.write_bytes(data)
    return list(planned)


if __name__ == "__main__":
    converted = convert([Path(arg) for arg in sys.argv[1:]])
    for file in converted:
        print(f"converted {file}")
    print(f"{len(converted)} file(s) written with snake_case keys")
