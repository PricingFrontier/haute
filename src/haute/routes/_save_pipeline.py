"""Service layer for the save-pipeline endpoint.

Encapsulates graph validation, code generation, config-file management,
sidecar persistence, and (future) broadcast notifications so that the
route handler stays thin.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from fastapi import HTTPException

from haute._file_ops import atomic_write_text
from haute._logging import get_logger
from haute.graph_utils import NodeType, PipelineGraph, _sanitize_func_name
from haute.routes._helpers import mark_self_write, save_sidecar, validate_safe_path
from haute.schemas import SavePipelineRequest, SavePipelineResponse

logger = get_logger(component="server.pipeline.save")

# Singleton node types: at most one of each is allowed per pipeline.
_SINGLETON_NODE_TYPES: list[tuple[NodeType, str]] = [
    (NodeType.API_INPUT, "API Input"),
    (NodeType.OUTPUT, "Output"),
    (NodeType.LIVE_SWITCH, "Source Switch"),
]


class SavePipelineService:
    """Orchestrates every side-effect of saving a pipeline graph.

    Parameters
    ----------
    project_root:
        Absolute path to the project working directory (``Path.cwd()``
        at startup).  All file I/O is sandboxed under this directory.
    pipeline_root:
        Directory that contains the pipeline file and its ``config/``
        subfolder.  Defaults to *project_root* when not given.
    """

    def __init__(self, project_root: Path, pipeline_root: Path | None = None) -> None:
        self._root = project_root
        self._pipeline_root = pipeline_root or project_root

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def save(self, body: SavePipelineRequest) -> SavePipelineResponse:
        """Validate, generate code, write configs, and persist sidecar.

        Returns the canonical ``SavePipelineResponse``.
        Raises ``HTTPException`` on validation failures.
        """
        graph = body.graph

        self._validate_singletons(graph)
        self._validate_unique_sanitized_names(graph)
        py_path = self._resolve_source_file(body.source_file)

        self._write_code(body, graph, py_path)
        self._infer_flatten_schemas(graph)
        self._write_config_files(graph)
        self._remove_stale_config_files(graph)
        self._write_sidecar(py_path, graph, body.sources, body.active_source)

        # Mark self-write AFTER all files are written so the cooldown
        # window covers the file-watcher events for all written files.
        mark_self_write()

        return SavePipelineResponse(
            file=str(py_path.relative_to(self._root)),
            pipeline_name=body.name,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_singletons(graph: PipelineGraph) -> None:
        """Ensure singleton node types appear at most once."""
        for singleton_type, label in _SINGLETON_NODE_TYPES:
            count = sum(1 for n in graph.nodes if n.data.nodeType == singleton_type)
            if count > 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"Only one {label} node is allowed per pipeline (found {count}).",
                )

    @staticmethod
    def _validate_unique_sanitized_names(graph: PipelineGraph) -> None:
        """Reject graphs where distinct node labels sanitize to the same function name."""
        sanitized_to_labels: dict[str, list[str]] = defaultdict(list)
        for node in graph.nodes:
            sanitized = _sanitize_func_name(node.data.label)
            sanitized_to_labels[sanitized].append(node.data.label)

        collisions = {
            name: labels for name, labels in sanitized_to_labels.items() if len(labels) > 1
        }
        if collisions:
            parts = [f"  {name!r} <- {labels!r}" for name, labels in sorted(collisions.items())]
            raise HTTPException(
                status_code=400,
                detail=(
                    "Duplicate sanitized node names detected. "
                    "The following node labels produce the same Python "
                    "function name:\n" + "\n".join(parts)
                ),
            )

    def _resolve_source_file(self, source_file: str) -> Path:
        """Resolve and validate the main ``.py`` path."""
        if not source_file:
            raise HTTPException(
                status_code=400,
                detail="source_file is required \u2014 the frontend must track"
                " and send the original pipeline file path",
            )
        return validate_safe_path(self._root, source_file)

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    def _write_code(
        self,
        body: SavePipelineRequest,
        graph: PipelineGraph,
        py_path: Path,
    ) -> None:
        """Generate and write the ``.py`` file(s)."""
        from haute.codegen import graph_to_code, graph_to_code_multi

        if graph.submodels:
            files = graph_to_code_multi(
                graph,
                pipeline_name=body.name,
                description=body.description,
                preamble=body.preamble or "",
                source_file=body.source_file,
                preserved_blocks=body.preserved_blocks or None,
            )
            for rel_path, code in files.items():
                out_path = (self._root / rel_path).resolve()
                if not out_path.is_relative_to(self._root):
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(code)
        else:
            code = graph_to_code(
                graph,
                pipeline_name=body.name,
                description=body.description,
                preamble=body.preamble or "",
                preserved_blocks=body.preserved_blocks or None,
            )
            atomic_write_text(py_path, code)

    # ------------------------------------------------------------------
    # JSON flatten schema inference
    # ------------------------------------------------------------------

    def _infer_flatten_schemas(self, graph: PipelineGraph) -> None:
        """Auto-infer ``flattenSchema`` for API-input nodes backed by JSON files."""
        from haute._json_flatten import infer_schema, load_samples

        for node in graph.nodes:
            if node.data.nodeType != NodeType.API_INPUT:
                continue
            cfg = node.data.config
            path = cfg.get("path", "")
            if not path.endswith((".json", ".jsonl")):
                continue
            if cfg.get("flattenSchema"):
                continue
            data_path = (self._root / path).resolve()
            if data_path.is_file() and data_path.is_relative_to(self._root):
                samples = load_samples(data_path)
                if samples:
                    cfg["flattenSchema"] = infer_schema(samples)

    # ------------------------------------------------------------------
    # Config file I/O
    # ------------------------------------------------------------------

    def _write_config_files(self, graph: PipelineGraph) -> None:
        """Write per-node config JSON sidecar files."""
        from haute._config_io import collect_node_configs, config_load_errors

        self._prev_config_files = getattr(self, "_last_config_files", None)
        self._last_config_files = collect_node_configs(graph)
        self._protected_config_files: set[str] = set(config_load_errors(graph))
        for rel_path, json_content in self._last_config_files.items():
            out_path = (self._pipeline_root / rel_path).resolve()
            if not out_path.is_relative_to(self._pipeline_root):
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json_content)

    def _remove_stale_config_files(self, graph: PipelineGraph) -> None:
        """Delete config JSON files that THIS pipeline previously owned but no longer needs.

        Only removes files in the diff (prev - current) to avoid destroying
        other pipelines' configs in multi-pipeline projects.
        """
        prev = getattr(self, "_prev_config_files", None)
        current = getattr(self, "_last_config_files", {})

        protected: set[str] = getattr(self, "_protected_config_files", set())

        if prev is None:
            # First save — fall back to full-scan cleanup so pre-existing
            # stale files from manual edits or other tools are removed.
            from haute._config_io import NODE_TYPE_TO_FOLDER

            config_dir = self._pipeline_root / "config"
            if not config_dir.is_dir():
                return
            for folder in NODE_TYPE_TO_FOLDER.values():
                folder_path = config_dir / folder
                if not folder_path.is_dir():
                    continue
                for json_file in folder_path.glob("*.json"):
                    rel = json_file.relative_to(self._pipeline_root).as_posix()
                    if rel not in current and rel not in protected:
                        json_file.unlink()
                        logger.info("stale_config_removed", path=rel)
                if not any(folder_path.iterdir()):
                    folder_path.rmdir()
            if config_dir.is_dir() and not any(config_dir.iterdir()):
                config_dir.rmdir()
            return

        stale = set(prev) - set(current) - protected
        if not stale:
            return

        for rel in stale:
            stale_path = (self._pipeline_root / rel).resolve()
            if not stale_path.is_relative_to(self._pipeline_root):
                continue
            if stale_path.is_file():
                stale_path.unlink()
                logger.info("stale_config_removed", path=rel)
            folder = stale_path.parent
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()

        config_dir = self._pipeline_root / "config"
        if config_dir.is_dir() and not any(config_dir.iterdir()):
            config_dir.rmdir()

    # ------------------------------------------------------------------
    # Sidecar persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _write_sidecar(
        py_path: Path,
        graph: PipelineGraph,
        sources: list[str],
        active_source: str,
    ) -> None:
        """Persist node positions and source state to ``.haute.json``."""
        graph.sources = sources
        graph.active_source = active_source
        save_sidecar(py_path, graph)
