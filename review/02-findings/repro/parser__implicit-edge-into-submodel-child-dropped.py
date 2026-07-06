"""Adversarial repro: implicit param-name edge main-file node -> submodel child.

Claim: an implicit param-name edge from a main-file node `root_node` into a
submodel child `child_in(root_node)` is silently dropped from the parsed graph
(both flatten=True executed DAG and flatten=False GUI hierarchy), because
merge_submodels only reconstructs cross-boundary edges from the *explicit*
pipeline.connect(...) list and classify_ports only sees explicit connects.

We assert the SPECIFIC wrong values:
  * flatten=True  -> there is NO (root_node, child_in) edge in the flat graph
  * flatten=False -> submodel placeholder inputPorts == [] and there is NO
                     root_node -> submodel__sub edge

As a CONTROL we then add an explicit pipeline.connect("root_node", "child_in")
and show the edge IS reconstructed -- proving the gap is specifically about
implicit param-name DI, not a general parse failure.

Isolation: pure tempfile; writes only inside a TemporaryDirectory that we also
register as the project root via haute._sandbox.set_project_root. No real
project files (rating/, src/, tests/) are read or written.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from haute._sandbox import set_project_root
from haute.parser import parse_pipeline_file

MAIN_PY = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("main")


@pipeline.polars
def root_node(df):
    return df


pipeline.submodel("modules/sub.py")
'''

# Submodel child whose *parameter* is named `root_node` -> matches the
# main-file node name, so param-name DI implies edge root_node -> child_in.
SUB_PY = '''\
import polars as pl
import haute

submodel = haute.Submodel("sub")


@submodel.polars
def child_in(root_node):
    return root_node
'''

# Control variant: identical, but main.py adds an explicit connect().
MAIN_PY_EXPLICIT = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("main")


@pipeline.polars
def root_node(df):
    return df


pipeline.submodel("modules/sub.py")
pipeline.connect("root_node", "child_in")
'''


def _make_project(tmp: Path, main_src: str) -> Path:
    """Lay down a minimal Haute project; return the main.py path."""
    (tmp / "haute.toml").write_text('[project]\npipeline = "main.py"\n')
    # get_project_root requires a .git entry at or above haute.toml.
    (tmp / ".git").mkdir()
    (tmp / "modules").mkdir()
    main_path = tmp / "main.py"
    main_path.write_text(main_src)
    (tmp / "modules" / "sub.py").write_text(SUB_PY)
    return main_path


def _has_edge(graph, src: str, tgt: str) -> bool:
    return any(e.source == src and e.target == tgt for e in graph.edges)


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        set_project_root(tmp)
        main_path = _make_project(tmp, MAIN_PY)

        # --- flatten=True : the executed DAG -------------------------------
        flat = parse_pipeline_file(main_path, flatten=True)
        flat_edges = [(e.source, e.target) for e in flat.edges]
        flat_node_ids = {n.id for n in flat.nodes}
        implicit_present_flat = _has_edge(flat, "root_node", "child_in")

        print(f"[flatten=True] node_ids = {sorted(flat_node_ids)}")
        print(f"[flatten=True] edges    = {flat_edges}")
        print(f"[flatten=True] has (root_node->child_in) edge = {implicit_present_flat}")

        # child_in must be present as a node (it is a real submodel node), but
        # the implicit input edge feeding it must have been dropped per the claim.
        if "child_in" not in flat_node_ids:
            failures.append(
                "SETUP: child_in node missing from flattened graph; "
                "submodel did not parse as expected"
            )
        # The BUG assertion: the implicit edge is absent.
        if implicit_present_flat:
            failures.append(
                "REFUTED(flatten): (root_node->child_in) edge IS present in the "
                "flat graph -- implicit edge was reconstructed after all"
            )

        # --- flatten=False : the GUI hierarchy -----------------------------
        hier = parse_pipeline_file(main_path, flatten=False)
        sm_meta = (hier.submodels or {}).get("sub", {})
        input_ports = sm_meta.get("inputPorts", None)
        sm_node = next((n for n in hier.nodes if n.id == "submodel__sub"), None)
        sm_node_input_ports = (
            sm_node.data.config.get("inputPorts") if sm_node is not None else None
        )
        boundary_edge_present = _has_edge(hier, "root_node", "submodel__sub")

        print(f"[flatten=False] submodels keys = {list((hier.submodels or {}).keys())}")
        print(f"[flatten=False] meta inputPorts = {input_ports}")
        print(f"[flatten=False] placeholder inputPorts = {sm_node_input_ports}")
        print(
            f"[flatten=False] has root_node->submodel__sub edge = {boundary_edge_present}"
        )

        if sm_node is None:
            failures.append("SETUP: submodel__sub placeholder node missing")
        # The BUG assertions: inputPorts empty and no boundary edge.
        if input_ports:
            failures.append(
                f"REFUTED(hier.meta): inputPorts non-empty: {input_ports!r}"
            )
        if sm_node_input_ports:
            failures.append(
                f"REFUTED(hier.node): placeholder inputPorts non-empty: "
                f"{sm_node_input_ports!r}"
            )
        if boundary_edge_present:
            failures.append(
                "REFUTED(hier.edge): root_node->submodel__sub edge IS present"
            )

        # --- CONTROL: explicit connect SHOULD reconstruct the edge ---------
        main_path.write_text(MAIN_PY_EXPLICIT)
        flat_ctrl = parse_pipeline_file(main_path, flatten=True)
        ctrl_present = _has_edge(flat_ctrl, "root_node", "child_in")
        hier_ctrl = parse_pipeline_file(main_path, flatten=False)
        ctrl_ports = (hier_ctrl.submodels or {}).get("sub", {}).get("inputPorts", [])
        print(
            f"[control explicit connect] flat has (root_node->child_in) = {ctrl_present}; "
            f"hier inputPorts = {ctrl_ports}"
        )
        if not ctrl_present:
            failures.append(
                "CONTROL FAILED: explicit connect did NOT produce the edge -- "
                "the difference is not isolated to implicit DI"
            )
        if "child_in" not in ctrl_ports:
            failures.append(
                "CONTROL FAILED: explicit connect did NOT classify child_in as an "
                "input port"
            )

    if failures:
        print("\nRESULT: NOT REPRODUCED / refuted -------------------------")
        for f in failures:
            print("  - " + f)
        return 1

    print("\nRESULT: REPRODUCED ----------------------------------------")
    print(
        "  Implicit param-name edge root_node->child_in is DROPPED in both "
        "flatten=True and flatten=False, while an explicit connect() for the "
        "same wiring IS honoured. Submodel child silently loses its input."
    )
    return 0


def _run_pricing_demonstration() -> None:
    """Optional: show downstream executor behaviour difference, if importable.

    Kept best-effort and guarded -- the core verdict rests on the structural
    assertions above. We do NOT fail the repro on environment issues here.
    """
    # Intentionally left as a no-op placeholder to keep the repro hermetic;
    # the structural graph assertions already establish the dropped edge.
    return None


if __name__ == "__main__":
    # Run in-process; print the interpreter for provenance.
    print(f"python = {sys.executable}")
    print(f"cwd    = {Path.cwd()}")
    _ = subprocess  # silence unused-import lints if any
    sys.exit(main())
