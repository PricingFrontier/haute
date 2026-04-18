"""Service layer for the save-pipeline endpoint.

Encapsulates graph validation, code generation, config-file management,
sidecar persistence, and (future) broadcast notifications so that the
route handler stays thin.

Transactional semantics
-----------------------
Save is an all-or-nothing commit.  Every intended write is staged first:
existing files are snapshotted (bytes) and new files are recorded so
they can be deleted on rollback.  Writes land through
:class:`haute._file_ops.Writer` so the file-watcher's self-write callback
fires immediately before each rename (item #7 — no 2-second cooldown).

If any write, config step, or the sidecar raises, we restore the
snapshotted content for every file we touched and delete the files we
newly created.  The original error is re-raised unchanged so upstream
handlers see the real cause.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException

from haute._file_ops import Writer, atomic_write_bytes
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

# Allowlist for codegen output paths (item #12 — path traversal hardening).
# Module files must live directly under ``modules/`` (no nested escapes).
_MODULES_PREFIX = "modules/"


class _TouchedFile(NamedTuple):
    """One file committed during a save, with enough state to undo it.

    ``previous_bytes`` is ``None`` when the file did not exist before —
    rollback deletes rather than restores.  ``target`` is absolute.
    """

    target: Path
    previous_bytes: bytes | None


def _mark_self_write_cb(_path: Path) -> None:
    """Writer callback — signals the file-watcher for each rename.

    Writer passes the path it is about to rename; our self-write tracker
    records a timestamp, so the watcher sees a coherent event sequence
    regardless of how long the overall save takes.
    """
    mark_self_write()


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
        self._root = project_root.resolve()
        self._pipeline_root = (pipeline_root or project_root).resolve()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def save(self, body: SavePipelineRequest) -> SavePipelineResponse:
        """Validate, generate code, write configs, and persist sidecar.

        Returns the canonical ``SavePipelineResponse``.
        Raises ``HTTPException`` on validation failures.

        Save is transactional: if any write step fails, all files we
        already touched are restored (or deleted for new files) before
        the exception propagates.  No partial save is ever left on disk.
        """
        graph = body.graph

        self._validate_singletons(graph)
        self._validate_unique_sanitized_names(graph)
        py_path = self._resolve_source_file(body.source_file)

        touched: list[_TouchedFile] = []
        warnings: list[str] = []
        try:
            self._write_code(body, graph, py_path, touched)
            self._infer_flatten_schemas(graph)
            self._write_config_files(graph, touched)
            warnings.extend(
                self._write_sidecar(
                    py_path, graph, body.sources, body.active_source, touched
                )
            )
        except BaseException:
            self._rollback(touched)
            raise

        # Stale-config removal is NOT part of the transaction: these deletions
        # are non-recoverable once committed, so run them only after every
        # write has succeeded.  If sidecar write fails mid-save, stale configs
        # remain on disk and will be cleaned up on the next successful save.
        self._remove_stale_config_files(graph)

        # Final save-level self-write marker keeps the existing behaviour
        # for callers that wait on the cooldown rather than per-file
        # callbacks.  Item #7's per-rename callbacks fire inside Writer.
        mark_self_write()

        return SavePipelineResponse(
            file=str(py_path.relative_to(self._root)),
            pipeline_name=body.name,
            warnings=warnings,
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
    # Path allowlist — item #12
    # ------------------------------------------------------------------

    def _validate_output_rel_path(self, rel_path: str, source_file: str) -> Path:
        """Return the absolute output path for *rel_path* after safety checks.

        Accepts only two families of codegen output paths (item #12):

        * The main pipeline file, matching *source_file* exactly
          (normalised POSIX form).
        * Module files under ``modules/`` (no nested escapes — the
          normalised path must still start with ``modules/``).

        Any other shape — absolute path, traversal (``..``), alternative
        prefixes, symlink-escape candidates — raises HTTP 400 and the
        surrounding save transaction rolls back.  We fail loudly rather
        than silently skipping so a malformed codegen output cannot
        produce a half-saved pipeline.
        """
        if not rel_path:
            raise HTTPException(
                status_code=400,
                detail="Codegen output path is empty; refusing to save.",
            )

        # Windows paths often come back with mixed separators.  Normalise
        # BEFORE any resolution so traversal components (``..``) are
        # detected on the raw string.
        normalised = rel_path.replace("\\", "/")

        # Reject absolute paths and traversal up front — these can never
        # correspond to a valid allowlist target.
        if normalised.startswith("/") or normalised.startswith("~"):
            raise HTTPException(
                status_code=400,
                detail="Codegen output paths must be project-relative.",
            )
        # Any ``..`` component anywhere is forbidden; a crafted codegen
        # output must not be able to escape even when combined with a
        # legitimate-looking prefix.
        if any(part == ".." for part in normalised.split("/")):
            raise HTTPException(
                status_code=400,
                detail="Codegen output path contains a traversal segment ('..').",
            )

        allowed_main = (source_file or "").replace("\\", "/")

        is_main = bool(allowed_main) and normalised == allowed_main
        is_module = normalised.startswith(_MODULES_PREFIX) and normalised.count("/") == 1
        if not (is_main or is_module):
            logger.warning(
                "save_reject_output_path",
                rel_path=rel_path,
                allowed_main=allowed_main,
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "Codegen produced an output path outside the allowed set "
                    "(main pipeline file or 'modules/<name>.py')."
                ),
            )

        out_path = (self._root / normalised).resolve()
        # Defence in depth: even after the prefix check, the resolved path
        # must still sit under the project root.  A symlink inside
        # ``modules/`` pointing outside the repo would bypass the string
        # check but fail here.
        if not out_path.is_relative_to(self._root):
            logger.warning(
                "save_reject_output_path_resolve",
                rel_path=rel_path,
                resolved=str(out_path),
            )
            raise HTTPException(
                status_code=400,
                detail="Codegen output path resolves outside the project root.",
            )
        return out_path

    # ------------------------------------------------------------------
    # Writes — route every disk write through Writer for self-write safety
    # ------------------------------------------------------------------

    def _stage_write(
        self,
        out_path: Path,
        code: str,
        touched: list[_TouchedFile],
    ) -> None:
        """Commit one file through Writer, recording rollback state first.

        Writer's mark_self_write callback fires immediately before the
        rename so the file-watcher sees a coherent self-write event for
        every file, not just the last one of a long save.
        """
        # Snapshot the previous state BEFORE writing so a failed rename
        # is recoverable.  ``previous_bytes is None`` means the file did
        # not exist and rollback should delete it.
        previous_bytes: bytes | None = None
        if out_path.exists():
            previous_bytes = out_path.read_bytes()
        touched.append(_TouchedFile(target=out_path, previous_bytes=previous_bytes))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with Writer(out_path, mark_self_write=_mark_self_write_cb) as w:
            w.write_text(code)

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    def _write_code(
        self,
        body: SavePipelineRequest,
        graph: PipelineGraph,
        py_path: Path,
        touched: list[_TouchedFile] | None = None,
    ) -> None:
        """Generate and write the ``.py`` file(s).

        All writes pass through ``Writer`` (item #7 — per-rename
        mark_self_write callback) and the allowlist gate (item #12).
        *touched* records rollback state for the surrounding save
        transaction (item #50); when ``None`` the method owns an
        internal list (used by unit tests that drive ``_write_code``
        directly without the broader save pipeline).
        """
        from haute.codegen import graph_to_code, graph_to_code_multi

        if touched is None:
            touched = []

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
                out_path = self._validate_output_rel_path(rel_path, body.source_file)
                self._stage_write(out_path, code, touched)
        else:
            code = graph_to_code(
                graph,
                pipeline_name=body.name,
                description=body.description,
                preamble=body.preamble or "",
                preserved_blocks=body.preserved_blocks or None,
            )
            self._stage_write(py_path, code, touched)

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

    def _write_config_files(
        self,
        graph: PipelineGraph,
        touched: list[_TouchedFile] | None = None,
    ) -> None:
        """Write per-node config JSON sidecar files.

        *touched* records rollback state when called from the
        transactional save path; unit tests can omit it and an internal
        list is used.
        """
        from haute._config_io import collect_node_configs, config_load_errors

        if touched is None:
            touched = []

        self._prev_config_files = getattr(self, "_last_config_files", None)
        self._last_config_files = collect_node_configs(graph)
        self._protected_config_files: set[str] = set(config_load_errors(graph))
        for rel_path, json_content in self._last_config_files.items():
            out_path = (self._pipeline_root / rel_path).resolve()
            if not out_path.is_relative_to(self._pipeline_root):
                continue
            self._stage_write(out_path, json_content, touched)

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
        touched: list[_TouchedFile] | None = None,
    ) -> list[str]:
        """Persist node positions and source state to ``.haute.json``.

        Returns the list of non-fatal warnings emitted by
        ``save_sidecar`` (sanitized-name collisions — item #51).
        *touched* records rollback state when called from the
        transactional save path; unit tests can omit it.
        """
        graph.sources = sources
        graph.active_source = active_source
        sidecar_path = py_path.with_suffix(".haute.json")
        if touched is not None:
            # Snapshot for rollback: the transactional save needs to
            # restore the previous sidecar bytes if a later step fails.
            previous_bytes: bytes | None = None
            if sidecar_path.exists():
                previous_bytes = sidecar_path.read_bytes()
            touched.append(_TouchedFile(target=sidecar_path, previous_bytes=previous_bytes))
        return save_sidecar(py_path, graph)

    # ------------------------------------------------------------------
    # Rollback — invoked by ``save`` when any step fails
    # ------------------------------------------------------------------

    @staticmethod
    def _rollback(touched: list[_TouchedFile]) -> None:
        """Undo every file write recorded in *touched*.

        For files that did not exist before the save (``previous_bytes
        is None``) we delete the target.  For files we overwrote we
        restore the original bytes atomically.  Best-effort: if a
        rollback step itself fails we log and continue so the remaining
        files still have a chance to recover — losing *part* of a
        rollback is strictly better than losing all of it.
        """
        # Undo in reverse order of writes so a file we created is
        # removed before any directory cleanup later in the list.
        for entry in reversed(touched):
            target = entry.target
            try:
                if entry.previous_bytes is None:
                    if target.is_file():
                        target.unlink()
                else:
                    atomic_write_bytes(target, entry.previous_bytes)
            except OSError as exc:
                logger.error(
                    "save_rollback_failed",
                    target=str(target),
                    error=str(exc),
                )
