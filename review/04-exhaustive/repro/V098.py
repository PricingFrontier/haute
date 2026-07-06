"""Isolated reproduction for V098.

Claim: ``SavePipelineService._mirror_api_input_caches`` resolves each apiInput
data file as ``data_path = (self._root / path).resolve()`` — i.e. PROJECT ROOT
ONLY. The cache *build* path (``routes/json_cache.py::_resolve_data_path``)
resolves the SAME logical apiInput path via ``resolve_runtime_file_path(...,
pipeline_dir=pipeline_dir(), project_root=cwd, prefer='project',
enforce_project_root=True)``, which FALLS BACK to the pipeline-relative
location when the project-root candidate does not exist, and marks THAT
resolved path session-consulted via ``_mark_working_consulted``.

For a nested pipeline (``pipeline_root != project_root`` — a first-class
supported config: ``pipeline.py`` line 373 constructs the save service with
``pipeline_root=pipeline_dir()``) whose apiInput references a data file
relatively (``data/quotes.json``) that physically lives UNDER the pipeline
directory (``<pipeline_dir>/data/quotes.json``) and NOT under the project root,
the two resolutions diverge:

    build  -> <pipeline_dir>/data/quotes.json   (marked consulted, working/ built here)
    mirror -> <project_root>/data/quotes.json   (different path -> different _path_hash)

Different path -> different ``_path_hash`` -> ``_is_working_consulted`` returns
False for the mirror's hash -> ``mirror_cache_to_committed`` short-circuits to a
no-op (returns False). The working cache the editor built is NEVER promoted to
committed/, silently (no warning). A fresh server / deploy then serves a
stale-or-missing committed/ while the editor shows the fresh working/.

This repro drives the REAL code paths:
  * ``routes.json_cache._resolve_data_path``  (build-time resolution + consult)
  * ``_json_shred.build_per_port_cache``      (writes a real working/ cache)
  * ``SavePipelineService._mirror_api_input_caches`` (save-time mirror)

It ASSERTS on the specific wrong behaviour: after a save, ``committed/`` for the
data file the editor actually cached is absent (no-op), and we further pin the
mechanism by showing ``mirror_cache_to_committed`` returns True for the
build-resolved path but False for the project-root path the mirror used.

ISOLATION: all disk I/O is under a Python tempfile dir; cwd is chdir'd into that
temp project; ``haute._sandbox.set_project_root`` is pointed at it; the
``pipeline_dir`` lru_cache and the module-level consulted-hash set are reset.
No rating/, src/, tests/, or real project files are read or written.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

# Make the in-repo source importable without touching project data files.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))


def _col(name: str, path: str) -> dict:
    return {
        "name": name,
        "path": path,
        "type": "int",
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


def _root_cfg(*cols: dict) -> dict:
    return {
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": list(cols),
            }
        ]
    }


def _api_input_graph(rel_path: str, cfg: dict):
    """Build a minimal PipelineGraph with a single JSON apiInput node."""
    from haute.graph_utils import PipelineGraph

    return PipelineGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "api",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "quotes",
                        "nodeType": "apiInput",
                        "config": {
                            "path": rel_path,
                            "contract": "opaque",
                            "tables": cfg["tables"],
                        },
                    },
                }
            ],
            "edges": [],
        }
    )


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp).resolve()

        # Nested pipeline directory: <project_root>/nested/  (pipeline_root != project_root)
        nested = project_root / "nested"
        (nested / "data").mkdir(parents=True)

        # haute.toml declares the active pipeline lives in the nested dir, so
        # pipeline_dir() resolves to <project_root>/nested.
        (project_root / "haute.toml").write_text(
            '[project]\npipeline = "nested/pipe.py"\n', encoding="utf-8"
        )
        # The pipeline source must exist + be under pipeline_root for the save
        # service's own validations; we only call _mirror_api_input_caches here,
        # but create it anyway so the project is coherent.
        (nested / "pipe.py").write_text("import haute\n", encoding="utf-8")

        # The data file lives ONLY at the pipeline-relative location, NOT at the
        # project-root location. This is the nested-pipeline scenario.
        data_file = nested / "data" / "quotes.json"
        data_file.write_bytes(b'[{"id": 1}, {"id": 2}]')
        project_root_candidate = project_root / "data" / "quotes.json"
        assert not project_root_candidate.exists(), (
            "precondition: data/quotes.json must NOT exist at the project root"
        )

        old_cwd = Path.cwd()
        os.chdir(project_root)
        try:
            import haute._sandbox as _sandbox
            from haute._json_flatten import (
                _clear_session,
                _is_working_consulted,
                _json_cache_dir,
                _mark_working_consulted,
                _path_hash,
                mirror_cache_to_committed,
            )
            from haute._json_shred import build_per_port_cache
            from haute.routes import _helpers
            from haute.routes._save_pipeline import SavePipelineService

            _sandbox.set_project_root(project_root)
            # pipeline_dir() is lru_cache'd on the process; reset so it reads our
            # temp haute.toml rather than any earlier value.
            _helpers.pipeline_dir.cache_clear()
            # Fresh "process": empty session-consulted set.
            _clear_session()

            pipeline_dir = _helpers.pipeline_dir()
            assert pipeline_dir == nested, (
                f"precondition: pipeline_dir() should be {nested}, got {pipeline_dir}"
            )

            cfg = _root_cfg(_col("id", "$[*].id"))
            rel_path = "data/quotes.json"

            # ---- BUILD-TIME RESOLUTION (exactly what /api/json-cache/build does) ----
            from haute.routes.json_cache import _resolve_data_path

            build_resolved = _resolve_data_path(rel_path)
            # It must fall back to the pipeline-relative location (project-root
            # candidate doesn't exist).
            assert Path(build_resolved).resolve() == data_file.resolve(), (
                f"precondition: build resolution should land on the pipeline-relative "
                f"file {data_file}, got {build_resolved}"
            )

            # Build a REAL working/ cache at the build-resolved path and mark it
            # consulted — this is what build_json_cache does on a successful build.
            working_dir = _json_cache_dir(build_resolved, "working")
            build_per_port_cache(
                data_path=build_resolved, v2_config=cfg, cache_dir=working_dir
            )
            _mark_working_consulted(build_resolved)
            assert working_dir.exists(), "precondition: working/ cache must have been built"

            # ---- SAVE-TIME MIRROR RESOLUTION (what _mirror_api_input_caches does) ----
            svc = SavePipelineService(project_root=Path.cwd(), pipeline_root=pipeline_dir)
            mirror_resolved = (svc._root / rel_path).resolve()

            committed_dir_build = _json_cache_dir(build_resolved, "committed")
            committed_dir_mirror = _json_cache_dir(mirror_resolved, "committed")

            graph = _api_input_graph(rel_path, cfg)

            # Run the actual save-time mirror step.
            svc._mirror_api_input_caches(graph)

            # ----- Diagnostics -----
            print("--- V098 nested-pipeline cache-mirror divergence ---")
            print(f"  project_root                 = {project_root}")
            print(f"  pipeline_dir()               = {pipeline_dir}")
            print(f"  build-resolved data_path     = {build_resolved}")
            print(f"  mirror-resolved data_path    = {mirror_resolved}")
            print(f"  build  _path_hash            = {_path_hash(build_resolved)}")
            print(f"  mirror _path_hash            = {_path_hash(mirror_resolved)}")
            print(f"  consulted(build_resolved)    = {_is_working_consulted(build_resolved)}")
            print(f"  consulted(mirror_resolved)   = {_is_working_consulted(mirror_resolved)}")
            print(f"  working/ (build hash) exists = {working_dir.exists()}")
            print(
                f"  committed/ (build hash) exists after save = {committed_dir_build.exists()}"
            )
            print(
                f"  committed/ (mirror hash) exists after save = {committed_dir_mirror.exists()}"
            )

            # ----- Core assertions on the WRONG behaviour -----

            # 1. The two resolutions genuinely diverge (the root cause).
            if Path(build_resolved).resolve() == mirror_resolved:
                failures.append(
                    "build and mirror resolved to the SAME path — divergence absent; "
                    "the scenario did not trigger the bug mechanism."
                )

            # 2. The save mirror was a silent no-op: committed/ for the data file
            #    the editor actually cached (build-resolved hash) was NOT created.
            #    THIS is the user-visible wrong outcome — the deployed pipeline
            #    keeps serving a stale/missing committed/ cache.
            if committed_dir_build.exists():
                failures.append(
                    "committed/ for the build-resolved (editor-cached) path EXISTS after "
                    "save — the mirror promoted it, so the bug is NOT present."
                )
            else:
                print(
                    "  REPRODUCED: save left committed/ ABSENT for the editor-cached file "
                    "(silent mirror no-op)."
                )

            # 3. Pin the mechanism: a mirror against the path the editor actually
            #    cached WOULD promote (returns True / creates committed/), proving
            #    the no-op was caused purely by the project-root-only resolution
            #    choosing the wrong cache key.
            promoted = mirror_cache_to_committed(build_resolved)
            if not promoted or not committed_dir_build.exists():
                failures.append(
                    "control: mirroring the build-resolved path did NOT promote "
                    f"(returned {promoted}, committed exists={committed_dir_build.exists()}); "
                    "could not isolate the resolution mismatch as the cause."
                )
            else:
                print(
                    "  CONTROL: mirror_cache_to_committed(build_resolved) promoted "
                    "(returns True) — confirms the working/ cache was promotable and the "
                    "save no-op was caused solely by the project-root-only path resolution."
                )

        finally:
            os.chdir(old_cwd)

    print()
    if failures:
        print("REPRO RESULT: claim NOT reproduced as predicted")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("REPRO RESULT: BUG REPRODUCED — for a nested pipeline whose apiInput data file")
    print("lives under the pipeline dir, the build marks <pipeline_dir>/data/quotes.json")
    print("consulted while the save mirror resolves <project_root>/data/quotes.json; the")
    print("hash mismatch makes mirror_cache_to_committed a silent no-op, so committed/ is")
    print("never populated and a fresh server serves a stale/missing cache.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # pragma: no cover - surface unexpected harness errors
        traceback.print_exc()
        raise SystemExit(2)
