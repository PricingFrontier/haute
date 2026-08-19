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
from pathlib import Path
from typing import Any, cast

from haute._banding_config import (
    compact_banding_config_for_sidecar,
    expand_banding_config_from_sidecar,
)
from haute._graph_utils import _sanitize_func_name
from haute._logging import get_logger
from haute._rating_step_config import (
    normalise_rating_step_config,
)
from haute._types import NodeType, PipelineGraph

logger = get_logger(component="config_io")

# ---------------------------------------------------------------------------
# Folder ↔ NodeType mappings
# ---------------------------------------------------------------------------

NODE_TYPE_TO_FOLDER: dict[NodeType, str] = {
    NodeType.API_INPUT: "quote_input",
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
_CODE_KEYS: frozenset[str] = frozenset({"code"})


def _strip_internal_keys(obj: Any) -> Any:
    """Recursively strip keys starting with ``_`` from dicts.

    Frontend-only state (e.g. ``_prevRules``, ``_id``) may be nested inside
    arrays of objects (like ``factors[].rules[]``).  A top-level-only filter
    misses these; this function walks the full structure.
    """
    if isinstance(obj, dict):
        return {
            k: _strip_internal_keys(v)
            for k, v in obj.items()
            if not isinstance(k, str) or not k.startswith("_")
        }
    if isinstance(obj, list):
        return [_strip_internal_keys(item) for item in obj]
    return obj


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


def _load_json_object(path: Path) -> dict[str, Any]:
    """Load a config JSON object, rejecting duplicate keys."""
    # Config JSON remains strict UTF-8: unlike Python source, a leading BOM is
    # invalid JSON and must surface as authored corruption instead of being
    # silently consumed by the user-source reader.
    text = path.read_text(encoding="utf-8", errors="replace")
    loaded = json.loads(text, object_pairs_hook=reject_duplicate_keys_hook)
    if not isinstance(loaded, dict):
        raise ValueError("Node config JSON must contain an object")
    return loaded


def _config_node_type_from_path(path: Path) -> NodeType | None:
    return FOLDER_TO_NODE_TYPE.get(path.parent.name)


def _normalise_loaded_config(config: dict[str, Any], node_type: NodeType | None) -> dict[str, Any]:
    if node_type == NodeType.BANDING:
        return expand_banding_config_from_sidecar(config)
    if node_type == NodeType.RATING_STEP:
        return normalise_rating_step_config(config)
    return config


def _prepare_config_for_sidecar(node_type: NodeType, config: dict[str, Any]) -> dict[str, Any]:
    # Drop user-code keys and internal `_*` keys before the typed allowlist.
    filtered = {k: v for k, v in config.items() if k not in _CODE_KEYS and not k.startswith("_")}
    filtered = cast(dict[str, Any], _strip_internal_keys(filtered))

    # Persist only fields declared by the current node config TypedDict.
    # Unknown fields are logged and omitted so UI save failures are visible
    # without corrupting the sidecar.
    from haute._config_validation import VALID_KEYS

    allowed = VALID_KEYS.get(node_type)
    if allowed is not None:
        dropped = sorted(k for k in filtered if k not in allowed)
        if dropped:
            logger.warning(
                "config_keys_dropped_at_write",
                node_type=node_type.value,
                keys=dropped,
            )
            filtered = {k: v for k, v in filtered.items() if k in allowed}

    if node_type == NodeType.BANDING:
        return compact_banding_config_for_sidecar(filtered)
    if node_type == NodeType.RATING_STEP:
        return normalise_rating_step_config(filtered)
    return filtered


def _remap_config_ids_for_saved_graph(
    node_type: NodeType,
    config: dict[str, Any],
    saved_node_id_by_graph_id: dict[str, str],
    *,
    node_label: str | None = None,
) -> dict[str, Any]:
    """Translate GUI node ids in config to ids produced by parsing saved Python."""
    if node_type != NodeType.OPTIMISER_APPLY:
        return config

    ratebook_input = config.get("ratebook_input")
    if not isinstance(ratebook_input, str) or not ratebook_input:
        return config
    saved_id = saved_node_id_by_graph_id.get(ratebook_input)
    if saved_id is None:
        # The configured upstream node is no longer in the graph (deleted,
        # renamed, or the GUI never persisted it).  Surface this so the
        # user can re-pick the ratebook source instead of the apply node
        # silently falling back to the first connected input at runtime.
        logger.warning(
            "ratebook_input_remap_unresolved",
            ratebook_input=ratebook_input,
            node_label=node_label,
        )
        return config
    return {**config, "ratebook_input": saved_id}


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


def has_config_folder(node_type: NodeType) -> bool:
    """Whether this node type stores config in an external JSON file."""
    return node_type in NODE_TYPE_TO_FOLDER


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
    rel = Path("config") / folder / f"{node_name}.json"
    if base_dir:
        abs_path = (base_dir / rel).resolve()
        config_root = (base_dir / "config").resolve()
        if not abs_path.is_relative_to(config_root):
            raise ValueError(f"Config path for {node_name!r} escapes config directory")
        return abs_path
    return rel


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------


def load_node_config(
    config_path: str | Path,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Load a node's config from its JSON file.

    *config_path* can be relative (resolved against *base_dir*) or absolute.

    Raises ``ValueError`` if the resolved path escapes *base_dir*.
    """
    p = Path(config_path)
    if not p.is_absolute() and base_dir:
        p = base_dir / p
    resolved = p.resolve()
    # Validate path stays within project directory
    if base_dir:
        root = base_dir.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Config path {config_path!r} resolves outside project root")
    config = _load_json_object(resolved)
    return _normalise_loaded_config(config, _config_node_type_from_path(resolved))


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
            except (json.JSONDecodeError, OSError, ValueError) as exc:
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
    Instance nodes are skipped (they reference an original).
    Nodes whose config failed to load (``_load_error`` marker) are skipped
    so the original file on disk is preserved.
    """
    configs: dict[str, str] = {}
    saved_node_id_by_graph_id = {
        node.id: _sanitize_func_name(node.data.label) for node in graph.nodes
    }
    for node in graph.nodes:
        nt = node.data.nodeType
        if not has_config_folder(nt):
            continue
        if node.data.config.get("instanceOf"):
            continue
        if node.data.config.get("_load_error"):
            continue
        func_name = _sanitize_func_name(node.data.label)
        rel_path = config_path_for_node(nt, func_name).as_posix()
        config = _remap_config_ids_for_saved_graph(
            nt,
            node.data.config,
            saved_node_id_by_graph_id,
            node_label=node.data.label,
        )
        filtered = _prepare_config_for_sidecar(nt, config)
        configs[rel_path] = json.dumps(filtered, indent=2, ensure_ascii=False) + "\n"
    return configs


def config_load_errors(graph: PipelineGraph) -> dict[str, str]:
    """Return relative config paths for nodes whose config failed to load.

    Used by the save service to protect these files from stale-file cleanup.
    """
    errors: dict[str, str] = {}
    for node in graph.nodes:
        nt = node.data.nodeType
        if not has_config_folder(nt):
            continue
        err = node.data.config.get("_load_error")
        if not err:
            continue
        func_name = _sanitize_func_name(node.data.label)
        try:
            rel_path = config_path_for_node(nt, func_name).as_posix()
        except ValueError:
            continue
        errors[rel_path] = str(err)
    return errors
