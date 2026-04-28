"""Config file I/O: read/write node config JSON sidecar files.

Each pipeline node's declarative config (everything except user code in
the function body) is stored in a JSON file under
``config/<type_folder>/<node_name>.json``.  The decorator references it::

    @pipeline.banding(config="config/banding/optimiser_banding.json")

This module provides:

- Path conventions (folder ↔ NodeType mappings)
- Read / write helpers
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
from haute._io import read_user_text
from haute._logging import get_logger
from haute._rating_step_config import (
    compact_rating_step_config_for_sidecar,
    expand_rating_step_config_from_sidecar,
)
from haute._types import NodeType, PipelineGraph

logger = get_logger(component="config_io")

# ---------------------------------------------------------------------------
# Folder ↔ NodeType mappings
# ---------------------------------------------------------------------------

NODE_TYPE_TO_FOLDER: dict[NodeType, str] = {
    NodeType.API_INPUT: "quote_input",
    NodeType.DATA_SOURCE: "data_source",
    NodeType.LIVE_SWITCH: "source_switch",
    NodeType.MODEL_SCORE: "model_scoring",
    NodeType.BANDING: "banding",
    NodeType.RATING_STEP: "rating_step",
    NodeType.OUTPUT: "quote_response",
    NodeType.DATA_SINK: "data_sink",
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


def _load_json_object(path: Path) -> dict[str, Any]:
    """Load a config JSON object, rejecting duplicate keys."""

    def object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    loaded = json.loads(read_user_text(path), object_pairs_hook=object_pairs_hook)
    if not isinstance(loaded, dict):
        raise ValueError("Node config JSON must contain an object")
    return loaded


def _config_node_type_from_path(path: Path) -> NodeType | None:
    return FOLDER_TO_NODE_TYPE.get(path.parent.name)


def _normalise_loaded_config(config: dict[str, Any], node_type: NodeType | None) -> dict[str, Any]:
    if node_type == NodeType.BANDING:
        return expand_banding_config_from_sidecar(config)
    if node_type == NodeType.RATING_STEP:
        return expand_rating_step_config_from_sidecar(config)
    return config


def _prepare_config_for_sidecar(node_type: NodeType, config: dict[str, Any]) -> dict[str, Any]:
    filtered = {k: v for k, v in config.items() if k not in _CODE_KEYS and not k.startswith("_")}
    filtered = cast(dict[str, Any], _strip_internal_keys(filtered))
    if node_type == NodeType.BANDING:
        return compact_banding_config_for_sidecar(filtered)
    if node_type == NodeType.RATING_STEP:
        return compact_rating_step_config_for_sidecar(filtered)
    return filtered


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


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


def save_node_config(
    node_type: NodeType,
    node_name: str,
    config: dict[str, Any],
    base_dir: Path,
) -> Path:
    """Write a node's config to its JSON file.

    Returns the **relative** path (for use in the decorator).
    Code keys are excluded — they stay in the ``.py`` function body.
    """
    rel_path = config_path_for_node(node_type, node_name)
    abs_path = base_dir / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    filtered = _prepare_config_for_sidecar(node_type, config)
    abs_path.write_text(json.dumps(filtered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("config_saved", path=str(rel_path), node_type=node_type.value)
    return rel_path


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
        filtered = _prepare_config_for_sidecar(nt, node.data.config)
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
