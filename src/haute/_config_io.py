"""Config file I/O: read node config JSON sidecars and collect save payloads.

Each pipeline node's declarative config (everything except user code in
the function body) is stored in a JSON file under
``config/<type_folder>/<node_name>.json``.  The decorator references it::

    @pipeline.banding(config="config/banding/optimiser_banding.json")

This module provides:

- Path conventions (folder ↔ NodeType mappings)
- Read helpers
- ``collect_node_configs`` for generating all config files from a graph
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from haute._api_input_schema import columns_for_sidecar, columns_from_sidecar
from haute._banding_config import (
    bands_read_from_sidecar,
    compact_banding_config_for_sidecar,
    expand_banding_config_from_sidecar,
)
from haute._constant_config import constants_for_sidecar, constants_from_sidecar
from haute._graph_utils import _sanitize_func_name
from haute._io import utf8_decode_failure
from haute._json_files import ANY, OneLine, at, render_json
from haute._logging import get_logger
from haute._polars_steps import STEPPED_NODE_TYPES
from haute._rating_step_config import (
    compact_rating_config_for_sidecar,
    expand_rating_config_from_sidecar,
    normalise_rating_step_config,
    refuse_levels_equal_under_factor_dtypes,
)
from haute._sandbox import contained_path, is_contained
from haute._types import (
    GLOBAL_CONSTANTS_FILE,
    GlobalConstant,
    GraphNode,
    NodeType,
    PipelineGraph,
)
from haute.errors import ConfigError, PathOutsideProjectError

logger = get_logger(component="config_io")

# ---------------------------------------------------------------------------
# Folder ↔ NodeType mappings
# ---------------------------------------------------------------------------

NODE_TYPE_TO_FOLDER: dict[NodeType, str] = {
    NodeType.API_INPUT: "quote_input",
    NodeType.WORKBENCH_INPUT: "workbench_input",
    NodeType.WORKBENCH_OUTPUT: "workbench_output",
    NodeType.DATA_INPUT: "data_input",
    NodeType.DATA_OUTPUT: "data_output",
    NodeType.LIVE_SWITCH: "source_switch",
    NodeType.MODEL_SCORE: "model_scoring",
    NodeType.BANDING: "banding",
    NodeType.RATING_STEP: "rating_step",
    NodeType.OUTPUT: "quote_response",
    NodeType.EXTERNAL_FILE: "load_file",
    NodeType.MODELLING: "model_training",
    NodeType.OPTIMISER: "optimisation",
    NodeType.OPTIMISER_APPLY: "apply_optimisation",
    NodeType.SCENARIO_EXPANDER: "expander",
    NodeType.CONSTANT: "constant",
}

FOLDER_TO_NODE_TYPE: dict[str, NodeType] = {v: k for k, v in NODE_TYPE_TO_FOLDER.items()}

# Keys that live in the .py function body, NOT in the JSON config file.


def reject_duplicate_keys_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that raises ``ValueError`` on a repeated key.

    Shared by every funnel that materialises a disk-resident config JSON
    into an in-memory dict, so they agree on the same file: keeping the
    last duplicate silently (stdlib/orjson default) hides a corrupted or
    hand-edited config behind a plausible-looking value. The JSON cache
    read path (``routes/json_cache.py::_read_v2_config``) reuses this hook
    so it rejects duplicates identically to :func:`_load_json_object`.
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


#: The reader node configs are read through while one is installed: editor
#: recovery installs its load's byte capture, so the revision authenticates the
#: config bytes the document was built from.
_NODE_CONFIG_READER: ContextVar[Callable[[Path], bytes] | None] = ContextVar(
    "haute_node_config_reader", default=None
)


@contextmanager
def node_configs_read_through(read_bytes: Callable[[Path], bytes]) -> Iterator[None]:
    """Read every node config through *read_bytes* for the length of the block."""
    token = _NODE_CONFIG_READER.set(read_bytes)
    try:
        yield
    finally:
        _NODE_CONFIG_READER.reset(token)


def _load_json_object(path: Path) -> dict[str, Any]:
    """Load a config JSON object, rejecting duplicate keys.

    Config JSON is strict UTF-8: a byte that is not is a ``ConfigError`` naming
    the file and the byte, because a config decoded with replacement characters
    would be written over the author's bytes by the next save. Unlike Python
    source, a leading BOM is invalid JSON and surfaces as authored corruption.
    """
    reader = _NODE_CONFIG_READER.get()
    raw = reader(path) if reader is not None else path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid UTF-8: {utf8_decode_failure(raw, exc)}. Save the file as UTF-8."
        ) from exc
    # Each failure names the file: a run outside the editor reports it as raised,
    # with no parser around it to say which sidecar it read. The types are kept,
    # because the parser words a file that is not JSON apart from bad content.
    try:
        loaded = json.loads(text, object_pairs_hook=reject_duplicate_keys_hook)
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(f"{path}: {exc.msg}", exc.doc, exc.pos) from exc
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: Node config JSON must contain an object")
    return loaded


def _config_node_type_from_path(path: Path) -> NodeType | None:
    return FOLDER_TO_NODE_TYPE.get(path.parent.name)


def _normalise_loaded_config(config: dict[str, Any], node_type: NodeType | None) -> dict[str, Any]:
    if node_type is None:
        return config
    from haute._config_validation import reject_form_given_keys, reject_removed_config_keys

    reject_removed_config_keys(node_type, config)
    reject_form_given_keys(node_type, config)
    return config_from_sidecar(node_type, config)


def config_from_sidecar(node_type: NodeType, config: dict[str, Any]) -> dict[str, Any]:
    """A sidecar's settings as the editor and the engine hold them, its file format decoded:
    rating tables from grids, banding from band lists, constants from a map, and a setting
    left out at its default given it again (READ-10, 26, 27, 28, 42). A file in a form before
    one of those is refused. Nothing else is checked, so Recover can read a sidecar whose
    keys the contract no longer takes."""
    # A setting a sidecar leaves out at its default reads as that default (READ-10), so
    # the config is the one the node had before it was written.
    for key, default in _SIDECAR_DEFAULTS.get(node_type, {}).items():
        if key not in config:
            config = {**config, key: deepcopy(default)}
    if node_type == NodeType.BANDING:
        # A sidecar writes breakpoints as a list of bands (READ-27).
        return expand_banding_config_from_sidecar(bands_read_from_sidecar(config))
    if node_type == NodeType.CONSTANT:
        # A sidecar writes the constants as a map from name to value (READ-28).
        return constants_from_sidecar(config)
    if node_type == NodeType.RATING_STEP:
        # A sidecar writes each table as a grid (READ-26); the editor and the engine read rows.
        return normalise_rating_step_config(expand_rating_config_from_sidecar(config))
    if node_type == NodeType.API_INPUT:
        # A column leaves out the settings inference gives it (READ-42).
        return columns_from_sidecar(config)
    return config


def _prepare_config_for_sidecar(
    node_type: NodeType,
    config: dict[str, Any],
    *,
    node_label: str,
) -> dict[str, Any]:
    from haute._config_validation import CODE_CONFIG_KEYS, validate_node_config

    # Save validation has already refused a config its model refuses; the write
    # boundary refuses it too, so no path drops a user key or writes a malformed
    # value and continues.
    validate_node_config(node_type, config, require_complete=False, node_label=node_label)

    # Nested mappings may contain arbitrary column names or user records, and
    # type-specific serializers own their nested records. The contract
    # annotation lives on the decorator; a sidecar copy goes stale.
    # A stepped node's code is the only copy of its steps (READ-44).
    in_code = CODE_CONFIG_KEYS | ({"steps"} if node_type in STEPPED_NODE_TYPES else set())
    filtered = {k: deepcopy(v) for k, v in config.items() if k not in in_code and k != "contract"}

    if node_type == NodeType.BANDING:
        return compact_banding_config_for_sidecar(filtered)
    if node_type == NodeType.CONSTANT:
        return constants_for_sidecar(filtered)
    if node_type == NodeType.RATING_STEP:
        normalised = normalise_rating_step_config(filtered)
        refuse_levels_equal_under_factor_dtypes(normalised)
        return compact_rating_config_for_sidecar(normalised)
    if node_type == NodeType.API_INPUT:
        return columns_for_sidecar(filtered)
    return filtered


#: Each node type's settings a sidecar leaves out at their default: the loader reads the
#: key's absence as that value (READ-10).
_SIDECAR_DEFAULTS: dict[NodeType, dict[str, Any]] = {
    NodeType.RATING_STEP: {"combined_outputs": []},
    NodeType.DATA_INPUT: {"arguments": {}},
    NodeType.DATA_OUTPUT: {"arguments": {}},
}

#: A rating table's keys in the order its sidecar writes them; others follow as given.
_RATING_TABLE_KEYS = (
    "name",
    "factors",
    "factor_dtypes",
    "output_column",
    "on_missing",
    "default_value",
    "columns",
    "rows",
)

#: No place in a file: the printer's own rule alone.
_NO_PLACE: OneLine = at()


#: Each node type's records written on one line each: a rating table's factors, its columns
#: and each row of its grid (READ-26), a banding factor's bands (READ-27), an API Input
#: column and a Quote Response mapping row (READ-42). Keyed by the node type, so another
#: type's ``tables`` or ``factors`` keeps one item a line.
_SIDECAR_ONE_LINE: dict[NodeType, OneLine] = {
    NodeType.RATING_STEP: at(
        ("tables", ANY, "factors"),
        ("tables", ANY, "columns"),
        ("tables", ANY, "rows", ANY),
    ),
    NodeType.BANDING: at(("factors", ANY, "rules", ANY)),
    NodeType.API_INPUT: at(("tables", ANY, "columns", ANY)),
    NodeType.OUTPUT: at(("output_mapping", ANY)),
}


#: Each node type's maps of numbers written one item a line: a Constant's values (READ-28).
_SIDECAR_VERTICAL: dict[NodeType, OneLine] = {NodeType.CONSTANT: at(("values",))}


def _ordered(record: Mapping[str, Any], order: Sequence[str]) -> dict[str, Any]:
    """*record* with the keys *order* names first, in that order, the rest as given."""
    first = {key: record[key] for key in order if key in record}
    return {**first, **{key: value for key, value in record.items() if key not in first}}


def _rating_table_record(table: Any) -> Any:
    """A rating table in its sidecar's key order, its grid last."""
    return _ordered(table, _RATING_TABLE_KEYS) if isinstance(table, dict) else table


def node_config_json(node_type: NodeType, config: dict[str, Any], *, node_label: str) -> str:
    """The canonical sidecar text of one node's config: what every save path writes.

    The record's own keys are sorted, so one configuration has one form whatever
    order its keys arrived in, and a setting at the default its loader reads for an
    absent key is left out. A rating table's keys and entries follow their kind's
    order, each entry on one line; other nested user mappings keep their order,
    since they are data (:mod:`haute._json_files`).
    """
    prepared = _prepare_config_for_sidecar(node_type, config, node_label=node_label)
    defaults = _SIDECAR_DEFAULTS.get(node_type, {})
    canonical = {
        key: prepared[key]
        for key in sorted(prepared)
        if not (key in defaults and prepared[key] == defaults[key])
    }
    if node_type == NodeType.RATING_STEP and isinstance(canonical.get("tables"), list):
        canonical["tables"] = [_rating_table_record(table) for table in canonical["tables"]]
    return render_json(
        canonical,
        one_line=_SIDECAR_ONE_LINE.get(node_type, _NO_PLACE),
        vertical=_SIDECAR_VERTICAL.get(node_type, _NO_PLACE),
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Windows reserves the DOS device names below: a filename whose stem before
# the FIRST dot matches one of them, case-insensitively and with ANY
# extension, denotes the device rather than a file (``CON.json`` is the
# console, ``NUL.py`` is the null device). Creating such a file fails or
# silently aliases the device on any Windows checkout.
_WINDOWS_RESERVED_DEVICE_STEMS: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def is_windows_reserved_filename(filename: str) -> bool:
    """Whether *filename* names a reserved DOS device on Windows.

    The stem before the FIRST dot is compared casefolded against the
    reserved set (CON, PRN, AUX, NUL, COM1-COM9, LPT1-LPT9) — the
    extension is irrelevant, so ``CON.json`` and ``nul.tar.gz`` are both
    reserved while ``CONTRACT.json`` and ``COM10.json`` are not.

    Windows additionally strips trailing dots and spaces when resolving
    names; sanitized node names cannot carry those, but the check strips
    them anyway so the predicate is robust for any caller.

    Used by the save-time guards (``routes/_save_pipeline.py``,
    ``routes/submodel.py``) that reject these filenames on EVERY
    platform, mirroring the portability rationale of the casefold
    collision guards: rejecting on every platform keeps a pipeline saved
    on Linux/macOS loadable on a Windows checkout.
    """
    stem = filename.rstrip(" .").split(".", 1)[0].rstrip(" .")
    return stem.casefold() in _WINDOWS_RESERVED_DEVICE_STEMS


@cache
def _palette_defaults() -> dict[str, dict[str, Any]]:
    return cast(
        dict[str, dict[str, Any]],
        json.loads(Path(__file__).with_name("node_defaults.json").read_text(encoding="utf-8")),
    )


def palette_default_config(node_type: NodeType) -> dict[str, Any]:
    """A fresh copy of the config the editor palette gives a new *node_type* node.

    ``node_defaults.json`` is the one source the palette, config recovery and
    the assistant share; every node type has an entry.
    """
    return deepcopy(_palette_defaults()[node_type.value])


def has_config_folder(node_type: NodeType) -> bool:
    """Whether this node type requires an external JSON config file."""
    return node_type in NODE_TYPE_TO_FOLDER


def node_emits_sidecar(node: GraphNode) -> bool:
    """Whether saving ``node`` writes a JSON sidecar: every node of a type with a
    config folder does. A transform has none: its steps live in its code (READ-22)."""
    return has_config_folder(node.data.nodeType)


def config_path_for_node(
    node_type: NodeType,
    node_name: str,
    base_dir: Path | None = None,
) -> Path:
    """Build the config file path for a node.

    Returns a relative path like ``config/banding/optimiser_banding.json``.
    If *base_dir* is provided, returns an absolute path.

    Raises ``ValueError`` if *node_name* contains path separators or ``..``
    that would escape the config directory.
    """
    folder = NODE_TYPE_TO_FOLDER.get(node_type)
    if folder is None:
        raise ValueError(f"No config folder for node type {node_type!r}")
    # Sanitize node_name to prevent path traversal
    if ".." in node_name or "/" in node_name or "\\" in node_name:
        raise ValueError(
            f"Invalid node name {node_name!r}: must not contain path separators or '..'"
        )
    in_config = Path(folder) / f"{node_name}.json"
    if base_dir:
        try:
            return contained_path(base_dir / "config", in_config)
        except PathOutsideProjectError:
            raise ValueError(f"Config path for {node_name!r} escapes config directory") from None
    return Path("config") / in_config


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------


def load_node_config(
    config_path: str | Path,
    base_dir: Path | None = None,
    *,
    node_type: NodeType | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Load a node's config from its JSON file, held to its node type's config model.

    *config_path* can be relative (resolved against *base_dir*) or absolute. The
    node type is *node_type*, else the one the file's folder names; a standalone
    run's helper names it, since a sidecar may sit in any folder, and the config
    is held to the model as a run's is (``at_run``). The parser passes
    ``validate=False``: it holds the config it assembles from the file to the
    whole model once, after its steps and contract are reconciled.

    Raises ``ValueError`` if the resolved path escapes *base_dir*.
    """
    p = Path(config_path)
    if not p.is_absolute() and base_dir:
        p = base_dir / p
    resolved = p.resolve()
    if base_dir and not is_contained(base_dir, resolved):
        raise ValueError(f"Config path {config_path!r} resolves outside project root")
    config = _load_json_object(resolved)
    kind = node_type if node_type is not None else _config_node_type_from_path(resolved)
    if kind in STEPPED_NODE_TYPES and "steps" in config:
        raise ConfigError(
            f"{resolved} holds the node's steps, the form before its code was their only "
            'copy: remove "steps" from it. The steps are rebuilt from the function\'s body.',
            config_path=str(resolved),
            removed_config_keys=["steps"],
        )
    loaded = _normalise_loaded_config(config, kind)
    if validate and kind is not None:
        from haute._config_validation import validate_node_config

        validate_node_config(kind, loaded, require_complete=False, at_run=True)
    return loaded


def remove_config_file(
    node_type: NodeType,
    node_name: str,
    base_dir: Path,
) -> bool:
    """Remove a node's config file.  Returns *True* if removed."""
    try:
        rel_path = config_path_for_node(node_type, node_name)
    except ValueError:
        return False
    abs_path = base_dir / rel_path
    if abs_path.is_file():
        abs_path.unlink()
        logger.info("config_removed", path=str(rel_path))
        return True
    return False


def find_config_by_func_name(
    func_name: str,
    base_dir: Path,
) -> tuple[dict[str, Any], NodeType] | None:
    """Scan config folders for a JSON file matching *func_name*.

    Used as a recovery path when the ``config=`` path in a ``.py`` file is
    mangled by Windows backslash escape interpretation (e.g. ``\\b`` →
    backspace).  The function name is always a valid Python identifier and
    unaffected by path escapes, so we can reconstruct the correct file.

    Returns ``(config_dict, node_type)`` on success, or ``None``.
    """
    # Reject func_name with path separators to prevent traversal
    if ".." in func_name or "/" in func_name or "\\" in func_name:
        logger.warning("config_recovery_rejected", func=func_name, reason="path traversal")
        return None
    for folder, node_type in FOLDER_TO_NODE_TYPE.items():
        candidate = base_dir / "config" / folder / f"{func_name}.json"
        if candidate.is_file():
            try:
                config = _normalise_loaded_config(_load_json_object(candidate), node_type)
            except (json.JSONDecodeError, OSError, ValueError, ConfigError) as exc:
                logger.warning(
                    "config_recovery_failed",
                    func=func_name,
                    path=str(candidate),
                    error=str(exc),
                )
                return None
            logger.info("config_recovered", func=func_name, path=str(candidate))
            return config, node_type
    return None


# ---------------------------------------------------------------------------
# Graph-level helpers
# ---------------------------------------------------------------------------


def collect_node_configs(graph: PipelineGraph) -> dict[str, str]:
    """Extract config files for all nodes in a graph.

    Returns a dict mapping relative path (e.g.
    ``"config/banding/optimiser_banding.json"``) to JSON content string.

    Nodes without a config folder (transforms, submodels) are skipped.
    """
    configs: dict[str, str] = {}
    for node in graph.nodes:
        nt = node.data.nodeType
        if not node_emits_sidecar(node):
            continue
        func_name = _sanitize_func_name(node.data.label)
        rel_path = config_path_for_node(nt, func_name).as_posix()
        configs[rel_path] = node_config_json(nt, node.data.config, node_label=node.data.label)
    return configs


# ---------------------------------------------------------------------------
# Global constants file
# ---------------------------------------------------------------------------

_GLOBAL_CONSTANT_KEY_ORDER = ("name", "type", "value", "by_source")


def _constant_validation_message(exc: ValidationError) -> str:
    """The first validation failure of one constant entry, as one readable clause."""
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in error["loc"])
    if error["type"] == "extra_forbidden":
        return f"unknown key {location!r}"
    message = str(error["msg"]).removeprefix("Value error, ")
    return f"{location}: {message}" if location else message


def parse_global_constants(
    raw: bytes,
    *,
    source: str = GLOBAL_CONSTANTS_FILE,
) -> list[GlobalConstant]:
    """Validate the bytes of a global constants file.

    The file holds one object whose only key, ``constants``, lists the
    entries in display order. Every failure is a ``ConfigError`` naming
    *source* and, for an entry, its position, its name when it has one, and
    the field. The bytes are strict UTF-8, as node config JSON is.
    """
    try:
        loaded = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys_hook)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"{source} must hold a JSON object with a 'constants' list.",
        )
    unknown = sorted(set(loaded) - {"constants"})
    if unknown:
        raise ConfigError(
            f"{source} has unknown key(s) {unknown!r}; its only key is 'constants'.",
        )
    entries = loaded.get("constants")
    if not isinstance(entries, list):
        raise ConfigError(f"{source} must have a 'constants' list.")
    constants: list[GlobalConstant] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        name = entry.get("name") if isinstance(entry, dict) else None
        label = f"constant {index}" + (f" ({name!r})" if isinstance(name, str) else "")
        if not isinstance(entry, dict):
            raise ConfigError(f"{source} {label} must be a JSON object.")
        try:
            constant = GlobalConstant.model_validate(entry)
        except ValidationError as exc:
            raise ConfigError(
                f"{source} {label}: {_constant_validation_message(exc)}",
            ) from exc
        if constant.name in seen:
            raise ConfigError(
                f"{source} {label}: the name {constant.name!r} is already defined.",
            )
        seen.add(constant.name)
        constants.append(constant)
    return constants


def global_constants_json(constants: Sequence[GlobalConstant]) -> str:
    """Serialise *constants* as the canonical file, in order, as node configs are written."""
    entries = []
    for constant in constants:
        record = constant.model_dump(exclude_none=True)
        entries.append({key: record[key] for key in _GLOBAL_CONSTANT_KEY_ORDER if key in record})
    return render_json({"constants": entries})
