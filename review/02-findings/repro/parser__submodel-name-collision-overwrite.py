"""Adversarial repro: submodel name-collision silently overwrites a subgraph.

CLAIM under test (submodel-name-collision-overwrite):
    Two distinct submodel files that both declare ``haute.Submodel("shared")``
    (same name) collide in ``parse_pipeline_source``. The code only logs
    ``logger.warning("submodel_name_collision")`` and then *unconditionally*
    overwrites ``submodel_graphs[sm_name]`` / ``submodel_files[sm_name]``.
    The first submodel's nodes/edges therefore vanish from the merged
    (flattened) graph, recorded only in a log line.

This script builds a fully isolated tmp Haute project (tempfile only — it
NEVER touches src/, tests/, rating/, or any real project file), with:
  - modules/subA.py -> haute.Submodel("shared") containing node ``a_node``
  - modules/subB.py -> haute.Submodel("shared") containing node ``b_node``
  - main.py referencing BOTH via pipeline.submodel(...)

It then calls the real public entry point ``parse_pipeline_file(main,
flatten=True)`` and ASSERTS on the specific wrong behaviour: exactly one of
the two child nodes survives (the second, ``b_node``), and the first
(``a_node``) is silently gone.

Run:
    uv run python review/02-findings/repro/parser__submodel-name-collision-overwrite.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import haute._sandbox as _sandbox
from haute.parser import parse_pipeline_file

SUBMODEL_A = '''\
import polars as pl
import haute

submodel = haute.Submodel("shared", description="Submodel A")

@submodel.polars
def a_node(df: pl.LazyFrame) -> pl.LazyFrame:
    """Node defined only in submodel A."""
    return df.with_columns(pl.lit(1.0).alias("a_value"))
'''

SUBMODEL_B = '''\
import polars as pl
import haute

submodel = haute.Submodel("shared", description="Submodel B")

@submodel.polars
def b_node(df: pl.LazyFrame) -> pl.LazyFrame:
    """Node defined only in submodel B."""
    return df.with_columns(pl.lit(2.0).alias("b_value"))
'''

MAIN_PIPELINE = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("main", description="References two same-named submodels")

@pipeline.polars
def root(df: pl.LazyFrame) -> pl.LazyFrame:
    """Root node in the main pipeline."""
    return df.with_columns(pl.lit(0.0).alias("root_value"))

pipeline.submodel("modules/subA.py")
pipeline.submodel("modules/subB.py")
'''


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()

        # Minimal Haute project: haute.toml + a .git marker so get_project_root
        # accepts this directory as the project root.
        (root / "haute.toml").write_text(
            '[project]\npipeline = "main.py"\n', encoding="utf-8"
        )
        (root / ".git").mkdir()  # .git dir marker is enough for _has_git

        modules = root / "modules"
        modules.mkdir()
        (modules / "subA.py").write_text(SUBMODEL_A, encoding="utf-8")
        (modules / "subB.py").write_text(SUBMODEL_B, encoding="utf-8")

        main_file = root / "main.py"
        main_file.write_text(MAIN_PIPELINE, encoding="utf-8")

        # Keep any project-path validation scoped to the tmp dir.
        _sandbox.set_project_root(root)

        graph = parse_pipeline_file(main_file, flatten=True)

        node_ids = {n.id for n in graph.nodes}
        print(f"flattened node_ids = {sorted(node_ids)}")

        # Sanity: the two child nodes have DISTINCT ids derived from their
        # function names, so a loss of one is cleanly observable.
        assert "a_node" != "b_node"

        # Sanity: the main-pipeline node is present (parse path actually ran).
        assert "root" in node_ids, (
            f"setup sanity failed: 'root' missing from {sorted(node_ids)} — "
            "parse path did not run as expected"
        )

        a_present = "a_node" in node_ids
        b_present = "b_node" in node_ids

        # --- The bug assertion -------------------------------------------------
        # If both submodels were correctly merged, BOTH a_node and b_node would
        # be present. The claim is that the second (subB/'shared') silently
        # overwrites the first, so a_node is LOST and only b_node survives.
        if a_present and b_present:
            print(
                "REFUTED: both a_node and b_node survived — no silent overwrite."
            )
            return 1

        # Predicted wrong behaviour: exactly the SECOND submodel's node survives.
        assert b_present and not a_present, (
            "Unexpected outcome: expected ONLY b_node to survive (second "
            f"submodel wins the name collision), got a_node={a_present}, "
            f"b_node={b_present}. node_ids={sorted(node_ids)}"
        )

        print(
            "REPRODUCED: submodel name collision silently dropped 'a_node'. "
            "Expected {a_node, b_node, root}; got "
            f"{sorted(node_ids)}. The first submodel's subgraph was lost with "
            "only a 'submodel_name_collision' warning."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
