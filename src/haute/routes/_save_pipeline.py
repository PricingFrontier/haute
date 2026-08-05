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
fires immediately before each rename.

If any write, config step, or the sidecar raises, we restore the
snapshotted content for every file we touched and delete the files we
newly created.  The original error is re-raised unchanged so upstream
handlers see the real cause.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import HTTPException

from haute._api_input_schema import is_json_api_input_path
from haute._file_ops import Writer, atomic_write_bytes
from haute._logging import get_logger
from haute._submodel_paths import resolve_submodel_reference
from haute.graph_utils import GraphNode, NodeType, PipelineGraph, _sanitize_func_name
from haute.routes._helpers import (
    invalidate_pipeline_index,
    mark_self_write,
    save_sidecar,
    validate_safe_path,
)
from haute.schemas import SavePipelineRequest, SavePipelineResponse

logger = get_logger(component="server.pipeline.save")

# Singleton node types: at most one of each is allowed per pipeline.
_SINGLETON_NODE_TYPES: list[tuple[NodeType, str]] = [
    (NodeType.API_INPUT, "API Input"),
    (NodeType.OUTPUT, "Output"),
    (NodeType.LIVE_SWITCH, "Source Switch"),
]

# Allowlist for codegen output paths.
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
    records that path, so the watcher can skip the exact event without
    relying on the total save duration.
    """
    mark_self_write(_path)


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
        if not self._pipeline_root.is_relative_to(self._root):
            raise ValueError("pipeline_root must resolve inside project_root")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def save(
        self,
        body: SavePipelineRequest,
        *,
        delete_module_files: Sequence[str] = (),
    ) -> SavePipelineResponse:
        """Validate, generate code, write configs, and persist sidecar.

        Returns the canonical ``SavePipelineResponse``.
        Raises ``HTTPException`` on validation failures.

        Save is transactional: if any write step fails, all files we
        already touched are restored (or deleted for new files) before
        the exception propagates.  No partial save is ever left on disk.
        """
        graph = body.graph

        self._validate_singletons(graph)
        self._validate_data_io_configs(graph)
        self._validate_unique_sanitized_names(graph)
        self._validate_no_load_errors(graph)
        py_path = self._resolve_source_file(body.source_file)
        self._validate_source_file_matches_pipeline_root(py_path)
        delete_targets = [
            self._resolve_existing_module_delete_file(rel_path)
            for rel_path in delete_module_files
            if rel_path
        ]

        # Bundle 6 sub-task C — capture the pre-save view of what config
        # files haute owns, derived from the on-disk pipeline graph
        # BEFORE this save overwrites it.  This is the diff baseline
        # consumed by `_remove_stale_config_files` later in this method.
        # Files NOT in this baseline (manual edits, files from other
        # tools, residue from older haute versions) are not haute's to
        # delete — see `notes-haute/security/SECURITY.md` §3
        # "Stable-layer file ownership".
        self._prev_config_files = self._compute_disk_prev_config_files(py_path)

        touched: list[_TouchedFile] = []
        warnings: list[str] = []
        try:
            self._write_code(body, graph, py_path, touched)
            self._validate_api_inputs_have_schemas(graph, warnings)
            self._write_config_files(graph, touched)
            self._mirror_api_input_caches(graph)
            warnings.extend(
                self._write_sidecar(py_path, graph, body.sources, body.active_source, touched)
            )
            # Skip delete targets that casefold-match a file this save just
            # wrote, mirroring the stale-diff guard in
            # `_remove_stale_config_files`: after a case-only submodel rename
            # (``modules/foo.py`` → ``modules/Foo.py``) the client requests
            # deletion of the old casing, but on the case-insensitive
            # filesystems macOS and Windows default to that names the SAME
            # on-disk file as the freshly written module — unlinking it would
            # destroy the survivor. On case-SENSITIVE Linux the skip leaves
            # the old-cased file behind as harmless residue; data safety on
            # macOS/Windows wins over Linux tidiness.
            written_folded = {str(item.target.resolve()).casefold() for item in touched}
            for target in delete_targets:
                if str(target.resolve()).casefold() in written_folded:
                    continue
                self._stage_delete(target, touched)
        except BaseException:
            self._rollback(touched)
            raise

        # Stale-config removal is NOT part of the transaction: these deletions
        # are non-recoverable once committed, so run them only after every
        # write has succeeded.  If sidecar write fails mid-save, stale configs
        # remain on disk and will be cleaned up on the next successful save.
        removed = self._remove_stale_config_files(graph)

        # Final save-level self-write marker covers save-wide notifications.
        # The watcher uses per-path Writer callbacks above.
        mark_self_write()

        # The file-watcher skips self-written paths before invalidating the
        # pipeline index, so a save that renames a pipeline (or adds/removes
        # one) would leave ``_pipeline_index`` mapping the stale name → path.
        # The save path knows exactly when invalidation is required: right
        # after all files land successfully.
        invalidate_pipeline_index()

        git_sha, identity_required = self._capture_save_in_ledger(touched, removed, warnings)

        return SavePipelineResponse(
            file=str(py_path.relative_to(self._root)),
            pipeline_name=body.name,
            warnings=warnings,
            git_sha=git_sha,
            identity_required=identity_required,
        )

    def _capture_save_in_ledger(
        self,
        touched: list[_TouchedFile],
        removed: list[Path],
        warnings: list[str],
    ) -> tuple[str | None, bool]:
        """Commit this save to the clone's ledger branch, when configured.

        Returns ``(sha, identity_required)``.  ``identity_required`` is True
        only when the capture was skipped because git has no commit identity
        — a restored hosted container starts that way, and without a
        structural signal the UI can never prompt for one, so the user's work
        would silently never be version-captured.

        Additive by design: with no working branch recorded (or no git repo)
        saves behave exactly as before, and a failed capture degrades to a
        warning — the on-disk save has already succeeded, and the next
        successful capture sweeps the orphaned delta up because the ledger
        commit is computed from working-tree state, not from this call's
        bookkeeping.
        """
        from haute._git_state import read_working_branch

        working = read_working_branch(self._root)
        if working is None:
            return None, False

        rel_paths: list[str] = []
        for path in [t.target for t in touched] + removed:
            try:
                rel_paths.append(path.relative_to(self._root).as_posix())
            except ValueError:
                continue  # outside the project root — not this repo's concern
        if not rel_paths:
            return None, False

        from haute import _git

        # Same source of truth as the working-branch response's `identity_set`.
        # Checked BEFORE committing so a missing identity costs no failing
        # subprocess and yields a specific, actionable message instead of the
        # generic git-error fallback.
        name, email = _git.get_identity(self._root)
        if name is None or email is None:
            warnings.append(
                "Changes saved, but version capture needs a git identity. "
                "Set your name and email to keep version history."
            )
            return None, True

        try:
            sha = _git.commit_save(rel_paths, working, cwd=self._root)
            if sha is not None:
                # Publish to durable storage when bound; no-op otherwise.
                from haute import _project_storage

                _project_storage.enqueue_push()
            return sha, False
        except _git.GitDomainError as exc:
            # Hand-authored messages (incl. guardrails) are safe verbatim.
            warnings.append(f"Changes saved; version capture failed: {exc}")
        except _git.GitError:
            # Raw git stderr may leak paths/remotes — full detail is already
            # in the structured log from _run_git.
            warnings.append("Changes saved; version capture failed (git error — see server log).")
        return None, False

    def save_graph_transactionally(
        self,
        *,
        graph: PipelineGraph,
        name: str,
        description: str,
        preamble: str | None,
        source_file: str,
        delete_module_files: Sequence[str] = (),
    ) -> SavePipelineResponse:
        """Save an already-mutated graph through the normal save transaction.

        Submodel create/dissolve first transform the in-memory graph, then
        need exactly the same write contract as ``/pipeline/save``: path
        allowlist, rollback, config filtering, sidecar staging, and
        post-commit index invalidation.  This wrapper keeps that contract
        anchored in one service instead of duplicating file writes in the
        route.
        """
        return self.save(
            SavePipelineRequest(
                name=name,
                description=description,
                graph=graph,
                preamble=preamble,
                source_file=source_file,
                sources=graph.sources,
                active_source=graph.active_source,
                preserved_blocks=graph.preserved_blocks,
            ),
            delete_module_files=delete_module_files,
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
    def _validate_data_io_configs(graph: PipelineGraph) -> None:
        """Reject invalid provider branches before generating or writing files."""
        from haute._config_validation import validate_node_config

        graphs = [graph, *SavePipelineService._iter_embedded_submodel_graphs(graph)]
        for scoped_graph in graphs:
            for node in scoped_graph.nodes:
                if node.data.nodeType not in {NodeType.DATA_INPUT, NodeType.DATA_OUTPUT}:
                    continue
                try:
                    validate_node_config(node.data.nodeType, node.data.config)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Invalid {node.data.nodeType.value} config for "
                            f"node {node.data.label!r}: {exc}"
                        ),
                    ) from exc

    @staticmethod
    def _validate_unique_sanitized_names(graph: PipelineGraph) -> None:
        """Reject graphs where node labels sanitize to the same function name.

        Scope is GLOBAL across the root graph and every embedded submodel
        graph, matching codegen's ``_error_on_name_collisions``.  The
        load-bearing reason is runtime flattening: preview/trace/run call
        ``flatten_graph`` which inlines every submodel child into ONE
        graph keyed by ``node.id`` — and ``node.id`` round-trips to the
        sanitised function name for root and submodel nodes alike
        (``_graph_builders._build_rf_nodes``).  ``PipelineGraph.node_map``
        is a plain ``{n.id: n}`` dict, so a cross-module duplicate would
        silently shadow its twin at execution time.

        Two passes:

        * per-graph — any two nodes in the SAME graph (root, or one
          submodel) whose labels sanitise identically. Identical labels
          collide too. The same rule applies to each submodel graph so
          collisions cannot escape this guard and surface as an unhandled
          codegen ``ParseError``.
        * cross-module — a sanitised name used in more than one module.
          Structural ``SUBMODEL`` / ``SUBMODEL_PORT`` nodes are excluded
          from this pass: a submodel placeholder legally shares its label
          with one of its own children (the placeholder's runtime id is
          ``submodel__<name>``-prefixed and it never emits a ``def``).
        """
        scoped_graphs: list[tuple[str, PipelineGraph]] = [("the pipeline", graph)]
        scoped_graphs.extend(
            (f"submodel {name!r}", nested)
            for name, nested in SavePipelineService._iter_named_embedded_submodel_graphs(graph)
        )

        # Pass 1 — collisions within a single graph (root or one submodel).
        for scope, scoped_graph in scoped_graphs:
            sanitized_to_labels: dict[str, list[str]] = defaultdict(list)
            for node in scoped_graph.nodes:
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
                        f"Duplicate sanitized node names detected in {scope}. "
                        "The following node labels produce the same Python "
                        "function name:\n" + "\n".join(parts)
                    ),
                )

        # Pass 2 — collisions across modules.  Submodels execute in one
        # flattened namespace with the root graph, so a sanitised name may
        # only be used in a single module.  Nodes are assigned to modules
        # the same way codegen assigns them to files
        # (``graph_to_code_multi``): a node listed in some submodel's
        # ``childNodeIds`` belongs to that submodel even when a payload
        # also duplicates it in the parent ``nodes`` list, so such
        # duplicates must not be double-counted as a root-module use.
        structural_types = (NodeType.SUBMODEL, NodeType.SUBMODEL_PORT)
        sanitized_to_scoped: dict[str, dict[str, list[str]]] = defaultdict(dict)
        for scope, scoped_graph in scoped_graphs:
            child_ids: set[str] = set()
            for sm_meta in (scoped_graph.submodels or {}).values():
                child_ids.update(sm_meta.get("childNodeIds", []))
            for node in scoped_graph.nodes:
                if node.data.nodeType in structural_types:
                    continue
                if node.id in child_ids:
                    continue
                sanitized = _sanitize_func_name(node.data.label)
                sanitized_to_scoped[sanitized].setdefault(scope, []).append(node.data.label)

        cross_module = {
            name: scopes for name, scopes in sanitized_to_scoped.items() if len(scopes) > 1
        }
        if cross_module:
            parts = [
                f"  {name!r} <- "
                + "; ".join(f"{labels!r} in {scope}" for scope, labels in scopes.items())
                for name, scopes in sorted(cross_module.items())
            ]
            raise HTTPException(
                status_code=400,
                detail=(
                    "Duplicate sanitized node names detected across the "
                    "pipeline and its submodels. Submodels run in one "
                    "flattened namespace with the main pipeline, so each "
                    "node name may be used in only one module:\n" + "\n".join(parts)
                ),
            )

    @staticmethod
    def _iter_named_embedded_submodel_graphs(
        graph: PipelineGraph,
    ) -> Iterator[tuple[str, PipelineGraph]]:
        """Yield ``(submodel_name, embedded_graph)`` pairs, recursively.

        Nested submodels are unsupported (the parser warns and drops
        them), but recurse defensively so a crafted payload cannot smuggle
        a colliding node past the guard inside a nested graph.
        """
        for sm_name, sm_meta in (graph.submodels or {}).items():
            sm_graph_dict: Any = sm_meta.get("graph", {})
            nested = PipelineGraph.model_validate(
                {
                    "nodes": sm_graph_dict.get("nodes", []),
                    "edges": sm_graph_dict.get("edges", []),
                    "submodels": sm_graph_dict.get("submodels"),
                }
            )
            yield sm_name, nested
            for deep_name, deep_graph in SavePipelineService._iter_named_embedded_submodel_graphs(
                nested
            ):
                yield f"{sm_name}/{deep_name}", deep_graph

    @staticmethod
    def _validate_no_load_errors(graph: PipelineGraph) -> None:
        """Reject saves while any parsed node is known to be incomplete."""
        broken = [
            node.data.label
            for node in SavePipelineService._iter_nodes_recursive(graph)
            if node.data.config.get("_load_error")
        ]
        if broken:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot save while node configs failed to load or parse. "
                    "Fix these nodes before saving: " + ", ".join(sorted(broken))
                ),
            )

    @staticmethod
    def _iter_nodes_recursive(graph: PipelineGraph) -> Iterator[GraphNode]:
        yield from graph.nodes
        for nested in SavePipelineService._iter_embedded_submodel_graphs(graph):
            yield from SavePipelineService._iter_nodes_recursive(nested)

    def _resolve_source_file(self, source_file: str) -> Path:
        """Resolve and validate the main ``.py`` path."""
        if not source_file:
            raise HTTPException(
                status_code=400,
                detail="source_file is required \u2014 the frontend must track"
                " and send the original pipeline file path",
            )
        return validate_safe_path(self._root, source_file)

    def _validate_source_file_matches_pipeline_root(self, py_path: Path) -> None:
        """Reject saves whose source file does not belong to ``pipeline_root``."""
        if not py_path.is_relative_to(self._pipeline_root):
            raise HTTPException(
                status_code=400,
                detail=(
                    "source_file must be under the active pipeline directory; "
                    "refusing to write modules/config for a different pipeline."
                ),
            )

    # ------------------------------------------------------------------
    # Path allowlist
    # ------------------------------------------------------------------

    def _validate_output_rel_path(self, rel_path: str, source_file: str) -> Path:
        """Return the absolute output path for *rel_path* after safety checks.

        Accepts only two families of codegen output paths:

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

        # Reserved device names are rejected on EVERY platform, mirroring the
        # casefold collision guards: a generated ``modules/NUL.py`` (or a main
        # file named ``CON.py``) names a device, not a file, on Windows — any
        # extension, any casing. Rejecting on every platform keeps a pipeline
        # saved on Linux/macOS loadable on a Windows checkout.
        from haute._config_io import is_windows_reserved_filename

        filename = normalised.rsplit("/", 1)[-1]
        if is_windows_reserved_filename(filename):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Codegen output filename {filename!r} is a reserved "
                    "device name on Windows. Windows treats a filename whose "
                    "stem is CON, PRN, AUX, NUL, COM1-COM9 or LPT1-LPT9 (any "
                    "casing, any extension) as a device, not a file, so it "
                    "cannot be written or checked out there. The name is "
                    "rejected on every platform so a pipeline saved here "
                    "stays loadable on a Windows checkout. Rename the node "
                    "or submodel before saving."
                ),
            )

        allowed_main = (source_file or "").replace("\\", "/")

        out_path: Path | None
        is_main = bool(allowed_main) and normalised == allowed_main
        if is_main:
            out_path = (self._root / normalised).resolve()
        else:
            out_path = self._resolve_module_output_path(normalised)
        if out_path is None:
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

    def _resolve_module_output_path(self, normalised: str) -> Path | None:
        """Resolve an allowed submodel output path, or return ``None``."""
        modules_dir = (self._pipeline_root / "modules").resolve()
        if normalised.startswith(_MODULES_PREFIX) and normalised.count("/") == 1:
            out_path = (modules_dir / normalised.removeprefix(_MODULES_PREFIX)).resolve()
        else:
            out_path = (self._root / normalised).resolve()

        if not out_path.is_relative_to(self._root):
            return None
        try:
            relative_to_modules = out_path.relative_to(modules_dir)
        except ValueError:
            return None
        if len(relative_to_modules.parts) != 1 or out_path.suffix != ".py":
            return None
        return out_path

    def _resolve_module_delete_file(self, rel_path: str) -> Path:
        """Resolve a module file scheduled for deletion through the module allowlist."""
        normalised = rel_path.replace("\\", "/")
        if not normalised:
            raise HTTPException(status_code=400, detail="Submodel delete path is empty.")
        if normalised.startswith("/") or normalised.startswith("~"):
            raise HTTPException(
                status_code=400,
                detail="Submodel delete paths must be project-relative.",
            )
        if any(part == ".." for part in normalised.split("/")):
            raise HTTPException(
                status_code=400,
                detail="Submodel delete path contains a traversal segment ('..').",
            )
        target = self._resolve_module_output_path(normalised)
        if target is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Submodel delete path must resolve to a direct child of "
                    "the active pipeline's modules/ directory."
                ),
            )

        return target

    def _resolve_existing_module_delete_file(self, rel_path: str) -> Path:
        """Resolve a module deletion to the existing parser-compatible file."""
        normalised = rel_path.replace("\\", "/")
        if not normalised:
            raise HTTPException(status_code=400, detail="Submodel delete path is empty.")
        if normalised.startswith("/") or normalised.startswith("~"):
            raise HTTPException(
                status_code=400,
                detail="Submodel delete paths must be project-relative.",
            )
        if any(part == ".." for part in normalised.split("/")):
            raise HTTPException(
                status_code=400,
                detail="Submodel delete path contains a traversal segment ('..').",
            )

        target, _base = resolve_submodel_reference(
            normalised,
            pipeline_dir=self._pipeline_root,
            project_root=self._root,
        )
        if target.exists():
            if not target.is_relative_to(self._root):
                raise HTTPException(
                    status_code=400,
                    detail="Submodel delete path resolves outside the project root.",
                )
            return target
        return self._resolve_module_delete_file(normalised)

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

    def _stage_delete(self, target: Path, touched: list[_TouchedFile]) -> None:
        """Delete one file after recording enough state to restore it."""
        if not target.exists():
            return
        if not target.is_file():
            raise HTTPException(
                status_code=400,
                detail="Submodel delete target is not a file.",
            )
        touched.append(_TouchedFile(target=target, previous_bytes=target.read_bytes()))
        target.unlink()

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

        All writes pass through ``Writer`` (per-rename
        mark_self_write callback) and the allowlist gate.
        *touched* records rollback state for the surrounding save
        transaction; when ``None`` the method owns an
        internal list (used by unit tests that drive ``_write_code``
        directly without the broader save pipeline).
        """
        from haute.codegen import graph_to_code, graph_to_code_multi

        if touched is None:
            touched = []

        self._validate_source_file_matches_pipeline_root(py_path.resolve())

        if graph.submodels:
            files = graph_to_code_multi(
                graph,
                pipeline_name=body.name,
                description=body.description,
                preamble=body.preamble or "",
                source_file=body.source_file,
                preserved_blocks=body.preserved_blocks or None,
            )
        else:
            files = {
                body.source_file: graph_to_code(
                    graph,
                    pipeline_name=body.name,
                    description=body.description,
                    preamble=body.preamble or "",
                    preserved_blocks=body.preserved_blocks or None,
                )
            }
        self._write_generated_code_files(files, body.source_file, touched)

    def _write_generated_code_files(
        self,
        files: dict[str, str],
        source_file: str,
        touched: list[_TouchedFile],
    ) -> None:
        """Write generated ``.py`` files through the shared output allowlist."""
        # Resolve and collision-check every path BEFORE any write, comparing
        # casefolded for the same reason as the config-sidecar guards:
        # distinct rel-paths like ``modules/Pricing.py`` / ``modules/pricing.py``
        # are the SAME file on the case-insensitive filesystems macOS and
        # Windows default to, where the second write silently overwrites the
        # first. Rejecting on every platform keeps a pipeline saved on Linux
        # loadable on a macOS/Windows checkout.
        resolved: list[tuple[Path, str]] = []
        seen: dict[str, str] = {}
        for rel_path, code in files.items():
            out_path = self._validate_output_rel_path(rel_path, source_file)
            folded = str(out_path).casefold()
            if folded in seen:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Codegen produced duplicate output path: {seen[folded]!r} "
                        f"and {rel_path!r} name the same file on the "
                        "case-insensitive filesystems macOS and Windows "
                        "default to. Rename one of the modules before saving."
                    ),
                )
            seen[folded] = rel_path
            resolved.append((out_path, code))
        for out_path, code in resolved:
            self._stage_write(out_path, code, touched)

    # ------------------------------------------------------------------
    # JSON apiInput schema validation (no on-disk mutation)
    # ------------------------------------------------------------------

    def _validate_api_inputs_have_schemas(self, graph: PipelineGraph, warnings: list[str]) -> None:
        """Emit a non-blocking warning per JSON apiInput with no ``tables[]``.

        Per D2 / B5: empty ``tables`` is a non-blocking state. The
        pipeline can be saved without being functional; the warning is
        a navigational aid pointing the user at the next step.
        """
        for node in graph.nodes:
            if node.data.nodeType != NodeType.API_INPUT:
                continue
            cfg = node.data.config
            path = cfg.get("path", "") or ""
            if not isinstance(path, str) or not is_json_api_input_path(path):
                continue
            tables = cfg.get("tables")
            if isinstance(tables, list) and tables:
                continue
            label = node.data.label or node.id
            warnings.append(
                f"API Input node {label!r} has no tables yet. "
                "Open the node and click Infer Tables to populate the schema."
            )

    # ------------------------------------------------------------------
    # Dual-cache: mirror working/ → committed/ per API Input node
    # ------------------------------------------------------------------

    def _mirror_api_input_caches(self, graph: PipelineGraph) -> None:
        """Promote each API Input node's volatile cache to the committed layer.

        Walks every API Input node backed by a JSON or newline-delimited JSON
        data file and
        invokes :func:`haute._json_flatten.mirror_cache_to_committed`.
        Mirror semantics (test plan):

        - When working/<hash>/ exists, copy it into committed/<hash>/
          (no-op trapdoor if fingerprints already match).
        - When working/<hash>/ does NOT exist *and* this process previously
          cached the file (delete-then-save flow), remove committed/<hash>/.
        - When this process has never cached the file, do nothing — avoids
          promoting a stale on-disk working/ from a previous session.

        Mirror failures are not rolled back through ``_TouchedFile``
        because the operation is idempotent: a partial state on disk is a
        valid intermediate that the next save can repair. Logged for the
        operator to investigate.
        """
        from haute._json_flatten import mirror_cache_to_committed

        for node in graph.nodes:
            if node.data.nodeType != NodeType.API_INPUT:
                continue
            cfg = node.data.config
            path = cfg.get("path", "")
            if not isinstance(path, str) or not is_json_api_input_path(path):
                continue
            data_path = (self._root / path).resolve()
            if not data_path.is_relative_to(self._root):
                continue
            try:
                mirror_cache_to_committed(str(data_path), cfg)
            except Exception as exc:  # pragma: no cover - logged for operator
                logger.error(
                    "json_cache_mirror_failed",
                    data_path=str(data_path),
                    error=str(exc),
                )

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
        if touched is None:
            touched = []

        # `_prev_config_files` is set at the top of `save()` from the
        # on-disk graph, not rotated from the previous `_last`.  See
        # `_compute_disk_prev_config_files` for rationale.
        self._last_config_files = self._collect_node_configs_recursive(graph)
        self._protected_config_files: set[str] = set(
            self._collect_config_load_errors_recursive(graph)
        )
        # Casefolded intersection, consistent with the collision guards below:
        # a to-be-written path and a protected load-error path differing only
        # in case are the same file on the case-insensitive filesystems macOS
        # and Windows default to. (Defence in depth — the per-graph casefold
        # guard already rejects such graphs upstream.)
        protected_folded = {rel.casefold(): rel for rel in self._protected_config_files}
        conflicts: set[str] = set()
        for rel_path in self._last_config_files:
            match = protected_folded.get(rel_path.casefold())
            if match is not None:
                conflicts.update((match, rel_path))
        self._raise_config_path_conflicts(conflicts)
        for rel_path, json_content in self._last_config_files.items():
            out_path = (self._pipeline_root / rel_path).resolve()
            if not out_path.is_relative_to(self._pipeline_root):
                continue
            self._stage_write(out_path, json_content, touched)

    @staticmethod
    def _iter_embedded_submodel_graphs(graph: PipelineGraph) -> Iterator[PipelineGraph]:
        for sm_meta in (graph.submodels or {}).values():
            sm_graph_dict: Any = sm_meta.get("graph", {})
            yield PipelineGraph.model_validate(
                {
                    "nodes": sm_graph_dict.get("nodes", []),
                    "edges": sm_graph_dict.get("edges", []),
                    "submodels": sm_graph_dict.get("submodels"),
                }
            )

    @staticmethod
    def _collect_node_configs_recursive(graph: PipelineGraph) -> dict[str, str]:
        """Collect configs from the parent graph and embedded submodel graphs."""
        from haute._config_io import collect_node_configs

        SavePipelineService._validate_unique_config_paths_in_graph(graph)
        configs: dict[str, str] = dict(collect_node_configs(graph))
        for nested in SavePipelineService._iter_embedded_submodel_graphs(graph):
            SavePipelineService._merge_config_maps(
                configs,
                SavePipelineService._collect_node_configs_recursive(nested),
            )
        return configs

    @staticmethod
    def _collect_config_load_errors_recursive(graph: PipelineGraph) -> dict[str, str]:
        """Collect load-error protected config paths across submodel graphs."""
        from haute._config_io import config_load_errors

        SavePipelineService._validate_unique_config_paths_in_graph(graph)
        errors = dict(config_load_errors(graph))
        for nested in SavePipelineService._iter_embedded_submodel_graphs(graph):
            SavePipelineService._merge_config_maps(
                errors,
                SavePipelineService._collect_config_load_errors_recursive(nested),
            )
        return errors

    @staticmethod
    def _validate_unique_config_paths_in_graph(graph: PipelineGraph) -> None:
        from haute._config_io import (
            config_path_for_node,
            has_config_folder,
            is_windows_reserved_filename,
        )

        # Compare paths casefolded: labels differing only in case (``Foo`` /
        # ``foo``) sanitize to distinct Python identifiers, but their sidecars
        # (``Foo.json`` / ``foo.json``) are the SAME file on the
        # case-insensitive filesystems macOS and Windows default to, where the
        # second write silently overwrites the first. Rejecting the collision
        # on every platform keeps a pipeline saved on Linux loadable on a
        # macOS/Windows checkout.
        #
        # Reserved device names get the same all-platform treatment: a label
        # like ``CON`` sanitizes to a valid identifier, but its sidecar
        # ``CON.json`` names the console device, not a file, on Windows —
        # regardless of extension. Rejecting on every platform keeps a
        # pipeline saved on Linux/macOS loadable on a Windows checkout.
        seen: dict[str, str] = {}
        duplicates: set[str] = set()
        reserved: set[str] = set()
        for node in graph.nodes:
            nt = node.data.nodeType
            if not has_config_folder(nt):
                continue
            if node.data.config.get("instanceOf"):
                continue
            func_name = _sanitize_func_name(node.data.label)
            rel_path = config_path_for_node(nt, func_name).as_posix()
            if is_windows_reserved_filename(f"{func_name}.json"):
                reserved.add(rel_path)
            folded = rel_path.casefold()
            if folded in seen:
                duplicates.update((seen[folded], rel_path))
            else:
                seen[folded] = rel_path
        SavePipelineService._raise_reserved_device_filenames(reserved)
        SavePipelineService._raise_config_path_conflicts(duplicates)

    @staticmethod
    def _merge_config_maps(target: dict[str, str], incoming: dict[str, str]) -> None:
        # Casefolded comparison for the same reason as
        # ``_validate_unique_config_paths_in_graph``: a parent node and a
        # submodel child whose sidecar paths differ only in case would
        # silently clobber each other on a case-insensitive filesystem.
        duplicates: set[str] = set()
        folded_target = {key.casefold(): key for key in target}
        for rel_path, content in incoming.items():
            folded = rel_path.casefold()
            if folded in folded_target:
                duplicates.update((folded_target[folded], rel_path))
                continue
            target[rel_path] = content
            folded_target[folded] = rel_path
        SavePipelineService._raise_config_path_conflicts(duplicates)

    @staticmethod
    def _raise_reserved_device_filenames(paths: set[str]) -> None:
        if not paths:
            return
        formatted = ", ".join(repr(path) for path in sorted(paths))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Filename is a reserved device name on Windows: {formatted}. "
                "Windows treats a filename whose stem is CON, PRN, AUX, NUL, "
                "COM1-COM9 or LPT1-LPT9 (any casing, any extension) as a "
                "device, not a file, so it cannot be written or checked out "
                "there. The name is rejected on every platform so a pipeline "
                "saved here stays loadable on a Windows checkout. Rename the "
                "node before saving."
            ),
        )

    @staticmethod
    def _raise_config_path_conflicts(paths: set[str]) -> None:
        if not paths:
            return
        formatted = ", ".join(repr(path) for path in sorted(paths))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Duplicate config sidecar path detected: {formatted}. "
                "Sidecar filenames are compared case-insensitively, because "
                "case-insensitive filesystems (macOS, Windows) treat names "
                "differing only in case as the same file. Rename one of the "
                "nodes before saving."
            ),
        )

    def _remove_stale_config_files(self, graph: PipelineGraph) -> list[Path]:
        """Delete config JSON files that THIS pipeline previously owned but no longer needs.

        Only removes files in the diff (prev - current).  Prev is the
        set of configs the on-disk pipeline graph referenced before this
        save ran (computed at the top of `save()` via
        `_compute_disk_prev_config_files`).  This preserves files that
        haute did not write — manual edits, files from other tools, or
        residue from older haute versions we don't recognise — per the
        Bundle 6 trust model (`notes-haute/security/SECURITY.md` §3
        "Stable-layer file ownership").

        Before Bundle 6 sub-task C, a missing `_prev_config_files`
        triggered a full-scan fallback that deleted any unknown JSON in
        every `config/<type>/` folder — actively violating the trust
        model.  That fallback is gone; the safe answer when we can't
        compute prev (no .py yet, .py unparseable) is to delete nothing
        and let the user clean up via the file tree if desired.

        Returns the absolute paths of every file removed, so deletions
        ride the same ledger commit as the writes they accompany.
        """
        prev = getattr(self, "_prev_config_files", {}) or {}
        current = getattr(self, "_last_config_files", {})
        protected: set[str] = getattr(self, "_protected_config_files", set())
        removed: list[Path] = []

        # Compute the stale diff casefolded, mirroring the collision guards in
        # `_validate_unique_config_paths_in_graph` / `_merge_config_maps`:
        # after a case-only node rename (``Foo`` → ``FOO``), prev's
        # ``Foo.json`` and current's ``FOO.json`` are the SAME on-disk file on
        # the case-insensitive filesystems macOS and Windows default to — this
        # save has just rewritten it in place, so unlinking the prev casing
        # would delete the freshly written survivor. Protected paths get the
        # same treatment: a prev path differing only in case from a protected
        # one names the same file there too. Excluding casefold matches on
        # every platform means a case-only rename on case-SENSITIVE Linux
        # leaves the old-cased file behind as harmless residue; data safety on
        # macOS/Windows wins over Linux tidiness.
        keep_folded = {rel.casefold() for rel in current}
        keep_folded.update(rel.casefold() for rel in protected)
        stale = {rel for rel in prev if rel.casefold() not in keep_folded}
        if not stale:
            return removed

        for rel in stale:
            stale_path = (self._pipeline_root / rel).resolve()
            if not stale_path.is_relative_to(self._pipeline_root):
                continue
            if stale_path.is_file():
                stale_path.unlink()
                removed.append(stale_path)
                logger.info("stale_config_removed", path=rel)
            folder = stale_path.parent
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()

        config_dir = self._pipeline_root / "config"
        if config_dir.is_dir() and not any(config_dir.iterdir()):
            config_dir.rmdir()
        return removed

    def _compute_disk_prev_config_files(self, py_path: Path) -> dict[str, str]:
        """Return the rel-path → JSON map for configs the on-disk graph
        references, used as the stale-cleanup diff baseline.

        Bundle 6 sub-task C — this is THE source of truth for "what
        does haute currently own on disk".  It's computed at the top of
        `save()` before any writes start, so it captures the graph
        haute previously persisted (rather than the new graph the save
        is about to write).

        Returns an empty mapping when:
          - the .py file doesn't exist yet (truly first save of a brand
            new pipeline — nothing on disk to own);
          - the .py file can't be parsed (mid-edit corruption, encoding
            issue, etc. — we have no evidence of ownership, so the
            safe answer is "delete nothing").

        Both cases produce ``stale = {} - current = {}``, so
        `_remove_stale_config_files` deletes nothing.  Hand-added
        configs, configs from other tools, and configs from older
        haute versions are preserved by virtue of never appearing in
        any parsed graph's reference set.
        """
        from haute.routes._helpers import parse_pipeline_to_graph

        if not py_path.is_file():
            return {}
        try:
            disk_graph = parse_pipeline_to_graph(py_path)
        except Exception as exc:
            logger.warning(
                "stale_cleanup_baseline_unavailable",
                path=str(py_path),
                error=str(exc),
                detail=(
                    "on-disk pipeline could not be parsed; stale-config "
                    "cleanup will preserve all unknown files this save"
                ),
            )
            return {}
        try:
            return self._collect_node_configs_recursive(disk_graph)
        except HTTPException as exc:
            logger.warning(
                "stale_cleanup_baseline_unavailable",
                path=str(py_path),
                error=str(exc.detail),
                detail=(
                    "on-disk pipeline has ambiguous config ownership; "
                    "stale-config cleanup will preserve all unknown files this save"
                ),
            )
            return {}

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
        ``save_sidecar`` (sanitized-name collisions).
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
