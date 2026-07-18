"""Adversarial repro for claim 'nested-submodel-call-silently-ignored'.

Claim: a ``pipeline.submodel('modules/b.py')`` call placed INSIDE a submodel
file (modules/a.py) is silently ignored. parse_submodel_source parses the
submodel's own decorated nodes/connects but never calls extract_submodel_calls
/ merge_submodels, so b's node is absent from the flattened graph -- with no
warning and no RecursionError.

Isolation: all disk I/O via tempfile; project root pinned via
haute._sandbox.set_project_root(tmp). No real project files are touched.

This script ASSERTS the specific wrong behaviour (b_node absent while a_node
present, and parsing completes without raising), so a clean exit == reproduced.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path


# Submodel A: defines a_node AND contains a nested submodel reference that uses
# the exact ``pipeline.submodel(...)`` form that extract_submodel_calls matches.
SUBMODEL_A = '''\
import polars as pl
import haute

submodel = haute.Submodel("sub_a", description="Submodel A")

@submodel.polars
def a_node(df: pl.LazyFrame) -> pl.LazyFrame:
    """A node."""
    return df.with_columns(pl.lit(1).alias("a"))

# Nested composition: A intends to pull in B's nodes.
pipeline.submodel("modules/b.py")
'''

# Submodel B: defines b_node. If nesting worked, b_node would appear in the
# flattened parent graph.
SUBMODEL_B = '''\
import polars as pl
import haute

submodel = haute.Submodel("sub_b", description="Submodel B")

@submodel.polars
def b_node(df: pl.LazyFrame) -> pl.LazyFrame:
    """B node."""
    return df.with_columns(pl.lit(2).alias("b"))
'''

# Main pipeline: references submodel A only.
MAIN = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("main", description="Main pipeline")

@pipeline.polars
def root(df: pl.LazyFrame) -> pl.LazyFrame:
    """Root node."""
    return df

pipeline.submodel("modules/a.py")
'''


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        import haute._sandbox as sandbox

        sandbox.set_project_root(tmp)

        modules = tmp / "modules"
        modules.mkdir()
        (modules / "a.py").write_text(SUBMODEL_A, encoding="utf-8")
        (modules / "b.py").write_text(SUBMODEL_B, encoding="utf-8")
        main_path = tmp / "main.py"
        main_path.write_text(MAIN, encoding="utf-8")

        from haute.parser import parse_pipeline_file

        # The claim says parsing completes WITHOUT a RecursionError / any error.
        try:
            graph = parse_pipeline_file(main_path, flatten=True)
        except RecursionError:
            print("UNEXPECTED: RecursionError raised -> nested submodels DO recurse")
            print("CLAIM REFUTED (recursion happens; not a silent drop)")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"UNEXPECTED: parsing raised {type(exc).__name__}: {exc}")
            traceback.print_exc()
            print("CLAIM NOT REPRODUCED (parse failed for an unrelated reason)")
            return 1

        node_ids = {n.id for n in graph.nodes}
        warning = getattr(graph, "warning", None)

        print(f"flattened node ids = {sorted(node_ids)}")
        print(f"graph.warning      = {warning!r}")

        # 1. The direct submodel (A) must have been flattened in: a_node present.
        if "a_node" not in node_ids:
            print("SETUP PROBLEM: a_node missing -> direct submodel flattening "
                  "did not work; cannot isolate the nested-drop behaviour")
            return 1

        # 2. The NESTED submodel (B) node must be ABSENT -- this is the bug.
        if "b_node" in node_ids:
            print("b_node IS present -> nested submodel WAS merged")
            print("CLAIM REFUTED (nested submodels are supported)")
            return 1

        # 3. There must be NO warning mentioning the dropped nested submodel
        #    (silent failure, per the claim). If a warning surfaced it, the
        #    'silent' characterisation would be wrong.
        warned_about_b = bool(warning) and ("b.py" in str(warning) or "nested" in str(warning).lower())
        if warned_about_b:
            print("A warning surfaced the dropped nested submodel -> not silent")
            print("CLAIM PARTIALLY REFUTED (drop is reported, not silent)")
            return 1

        print()
        print("REPRODUCED: nested pipeline.submodel('modules/b.py') inside a "
              "submodel file was silently dropped.")
        print("  expected (if supported): {'root','a_node','b_node'}")
        print(f"  actual                 : {sorted(node_ids)}")
        print("  b_node absent, no error, no RecursionError, no warning.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
