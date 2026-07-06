"""Adversarial repro for claim:
'executor-path-resolution-no-enforce-project-root'

Claim: the executor resolves pipeline config file paths with
resolve_runtime_file_path(..., prefer='project') and enforce_project_root
defaulting False, so an ABSOLUTE config path that escapes the project root is
returned unchecked and subsequently READ by read_data_source / scan_*, unlike
the HTTP route layer which passes enforce_project_root=True.

This script asserts on SPECIFIC wrong VALUES / behaviours (expected vs actual),
not merely that 'something raised'. It is fully isolated: all disk I/O uses
tempfile, the project root is set via haute._sandbox.set_project_root, and no
rating/, src/, or tests/ file is touched.

Sinks proven:
  A) resolve_runtime_file_path asymmetry: default (enforce=False) returns the
     out-of-root absolute path verbatim; enforce=True raises ValueError.
  B) canonical_dataframe_execution_graph (the EXECUTOR's path-rewrite step)
     passes an out-of-root absolute dataSource path through unchanged.
  C) read_data_source (the EXECUTOR's read step, via read_source ->
     _validate_source_path) ACTUALLY READS an out-of-root absolute file -
     proving there is NO project-root containment on the execution read path.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def main() -> int:
    import haute._sandbox as sandbox
    from haute._io import _validate_source_path, read_data_source
    from haute._path_resolution import resolve_runtime_file_path
    from haute.execution import canonical_dataframe_execution_graph
    from haute.graph_utils import PipelineGraph

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as outside_tmp:
        project_root = Path(proj_tmp).resolve()
        outside_root = Path(outside_tmp).resolve()
        # Sanity: the two temp roots are genuinely disjoint (no accidental nesting).
        assert not outside_root.is_relative_to(project_root), "temp roots overlapped"

        # A "secret" file outside the project root - the stand-in for /etc/passwd.
        secret = outside_root / "secret.csv"
        secret.write_text("col_a,col_b\nSECRET_VALUE,42\n", encoding="utf-8")
        secret_abs = str(secret)

        # The pipeline source file lives inside the project root.
        source_file = project_root / "pipe.py"
        source_file.write_text("# pipeline\n", encoding="utf-8")

        sandbox.set_project_root(project_root)

        # ------------------------------------------------------------------
        # Sink A: resolve_runtime_file_path behavioural asymmetry.
        # ------------------------------------------------------------------
        # Default call (mirrors execution.py:178-184 / 405-413): no
        # enforce_project_root kwarg, prefer='project'.
        resolved_default = resolve_runtime_file_path(
            secret_abs,
            source_file=str(source_file),
            prefer="project",
        )
        if Path(resolved_default) != secret:
            failures.append(
                "A: expected default resolve to return the out-of-root path "
                f"{secret!s}, got {resolved_default!s}"
            )
        else:
            print(f"[A] default resolve returned out-of-root path verbatim: {resolved_default}")

        # Route-layer call (mirrors json_cache.py / pipeline.py): enforce=True
        # MUST raise ValueError.
        try:
            resolve_runtime_file_path(
                secret_abs,
                source_file=str(source_file),
                prefer="project",
                enforce_project_root=True,
            )
            failures.append(
                "A: enforce_project_root=True should have raised ValueError for "
                f"out-of-root path {secret_abs}, but it returned normally"
            )
        except ValueError as exc:
            print(f"[A] enforce_project_root=True raised as expected: {exc}")

        # ------------------------------------------------------------------
        # Sink B: the executor's path-rewrite step (canonical_dataframe_
        # execution_graph) passes the out-of-root absolute path through.
        # ------------------------------------------------------------------
        graph_json = {
            "nodes": [
                {
                    "id": "src1",
                    "type": "dataSource",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "nodeType": "dataSource",
                        "label": "src",
                        "config": {
                            "sourceType": "flat_file",
                            "path": secret_abs,
                        },
                    },
                }
            ],
            "edges": [],
            "source_file": str(source_file),
        }
        graph = PipelineGraph.model_validate(graph_json)
        canonical = canonical_dataframe_execution_graph(graph)
        canon_path = canonical.nodes[0].data.config.get("path")
        # On an absolute path resolve() is idempotent, so the canonical config
        # still points at the out-of-root secret - the executor never rejects it.
        if Path(str(canon_path)).resolve() != secret:
            failures.append(
                "B: expected canonical graph dataSource path to remain the "
                f"out-of-root file {secret!s}, got {canon_path!s}"
            )
        else:
            print(f"[B] executor canonical graph kept out-of-root path: {canon_path}")

        # ------------------------------------------------------------------
        # Sink C: _validate_source_path admits the absolute out-of-root path,
        # and read_data_source ACTUALLY READS it (no containment on read).
        # ------------------------------------------------------------------
        # _validate_source_path only blocks scheme:// and '..' components.
        validated = _validate_source_path(secret_abs)
        if Path(validated) != secret:
            failures.append(
                f"C: _validate_source_path rejected/changed out-of-root abs path: {validated!s}"
            )
        else:
            print(f"[C] _validate_source_path admitted out-of-root abs path: {validated}")

        # The real proof: read the data through the executor's source reader,
        # using exactly the config the canonical graph produced.
        lf = read_data_source(canonical.nodes[0].data.config)
        df = lf.collect()
        read_values = df.get_column("col_a").to_list() if "col_a" in df.columns else []
        if read_values != ["SECRET_VALUE"]:
            failures.append(
                "C: read_data_source did NOT read the out-of-root secret; "
                f"expected ['SECRET_VALUE'], got {read_values!r}"
            )
        else:
            print(
                "[C] read_data_source READ the out-of-root file content "
                f"(col_a={read_values!r}) - arbitrary-file read confirmed"
            )

        # ------------------------------------------------------------------
        # Sink D: route-level asymmetry. The pipeline/explore execution routes
        # call _validate_runtime_input_paths (enforce_project_root=True) and
        # would REJECT this exact graph with HTTP 403. The train and optimiser
        # execution routes do NOT call it - they execute body.graph directly.
        # ------------------------------------------------------------------
        from fastapi import HTTPException

        from haute.routes.pipeline import _validate_runtime_input_paths

        # The guarded routes reject the out-of-root graph.
        rejected = False
        try:
            _validate_runtime_input_paths(graph)
        except HTTPException as exc:
            rejected = exc.status_code == 403
            print(f"[D] pipeline/explore guard rejects out-of-root graph: HTTP {exc.status_code}")
        if not rejected:
            failures.append(
                "D: _validate_runtime_input_paths should reject the out-of-root "
                "graph with HTTP 403 (proving the guard exists on guarded routes)"
            )

        # The train/optimiser route modules do NOT carry that guard. Read their
        # source text (NOT executing them) and assert the guard call is absent,
        # while the pipeline route module DOES contain it.
        import haute.routes.modelling as modelling_mod
        import haute.routes.optimiser as optimiser_mod
        import haute.routes.pipeline as pipeline_mod

        modelling_src = Path(modelling_mod.__file__).read_text(encoding="utf-8")
        optimiser_src = Path(optimiser_mod.__file__).read_text(encoding="utf-8")
        pipeline_src = Path(pipeline_mod.__file__).read_text(encoding="utf-8")
        guard = "_validate_runtime_input_paths("
        if guard in modelling_src:
            failures.append("D: expected modelling.py to LACK the path guard, but it is present")
        if guard in optimiser_src:
            failures.append("D: expected optimiser.py to LACK the path guard, but it is present")
        if guard not in pipeline_src:
            failures.append("D: expected pipeline.py to CONTAIN the path guard, but it is absent")
        if not failures:
            print(
                "[D] modelling.py and optimiser.py LACK _validate_runtime_input_paths; "
                "pipeline.py HAS it -> route-level containment asymmetry confirmed"
            )

    print()
    if failures:
        print("REPRO RESULT: claim NOT supported - assertions failed:")
        for f in failures:
            print("  - " + f)
        return 1

    print("REPRO RESULT: CLAIM SUPPORTED at the function/read level.")
    print("  - resolve_runtime_file_path defaults enforce_project_root=False;")
    print("  - the executor's path-rewrite keeps out-of-root absolute paths;")
    print("  - read_source/_validate_source_path apply NO project-root containment")
    print("    for absolute paths, so the file IS read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
