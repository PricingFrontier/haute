"""Adversarial repro for claim:
   optimiserApply-standalone-passthrough-no-apply

Hypothesis under test
---------------------
An optimiserApply node with sourceType='run', run_id set, ratebook_input
pointing at the 2nd input, and optimiser_mode NOT yet 'ratebook':

  (A) Codegen emits a *pure passthrough* body `return <first input>` for the
      standalone file (the ratebook rewrite is SKIPPED because the artifact mode
      is unresolved), and that body never loads the artifact / never applies the
      optimiser.

  (B) The canvas/preview executor, by contrast, loads the artifact and (when
      artifact mode == 'ratebook') applies the ratebook to the 2nd input,
      producing a __optimiser_version__ column + optimised values.

  => standalone pipeline.run() output == raw input frame (un-optimised),
     while the canvas preview shows optimised prices. Divergence.

This script proves the divergence WITHOUT any real MLflow/model machinery:
  * Part 1 inspects the literal generated source for the exact scenario node.
  * Part 2 runs a real Pipeline whose optimiserApply body is the generated
    passthrough and asserts the output has NO __optimiser_version__ column and
    is value-identical to the raw input.
  * Part 3 contrasts _select_optimiser_apply_input, the executor-side selector,
    which (under artifact mode=='ratebook') routes to the 2nd input — the input
    the standalone passthrough silently ignores.

ISOLATION: all disk I/O via tempfile; project root pointed at the tmp dir; no
read/write of rating/, src/, tests/, or any real project file.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl

import haute._sandbox as _sandbox
from haute._types import GraphNode, NodeData, NodeType
from haute.codegen import _node_to_code
from haute._builders import _select_optimiser_apply_input


def _make_node(config: dict) -> GraphNode:
    return GraphNode(
        id="apply_node",
        data=NodeData(
            label="Apply Optimiser",
            description="apply",
            nodeType=NodeType.OPTIMISER_APPLY,
            config=config,
        ),
    )


def part1_codegen_is_passthrough() -> str:
    """Return the generated optimiserApply source for the claimed scenario."""
    # sourceType='run', run_id set, ratebook_input -> 2nd input ('rb_node'),
    # optimiser_mode NOT 'ratebook' (unresolved by the picker).
    node = _make_node(
        {
            "sourceType": "run",
            "run_id": "abc",
            "ratebook_input": "rb_node",
            # optimiser_mode intentionally absent (== unresolved / not 'ratebook')
            "version_column": "__optimiser_version__",
            "optimised_value_column": "selected (price)",
        }
    )
    code = _node_to_code(
        node,
        source_names=["data_src", "ratebook_src"],
        source_ids=["data_node", "rb_node"],
    )
    return code


def part2_standalone_run_does_not_apply() -> None:
    """Build a Pipeline mirroring the generated standalone file and run it.

    The optimiserApply body is the generated passthrough (`return src`).
    Assert the run output is value-identical to the raw input and carries
    NO __optimiser_version__ column => optimiser never applied standalone.
    """
    from haute.pipeline import Pipeline

    p = Pipeline("standalone_repro")

    # Raw input the user feeds the optimiserApply node (the "first" input).
    raw = pl.DataFrame(
        {
            "quote_id": [1, 2, 3],
            "selected (price)": [100.0, 200.0, 300.0],
        }
    )

    @p.data_source
    def src() -> pl.LazyFrame:
        return raw.lazy()

    # This is EXACTLY the body codegen emits for the scenario node: a pure
    # passthrough of the first input. (sourceType/run_id/ratebook_input live in
    # the decorator config, but the standalone body never consults them.)
    @p.optimiser_apply(
        sourceType="run",
        run_id="abc",
        ratebook_input="rb",
        version_column="__optimiser_version__",
        optimised_value_column="selected (price)",
    )
    def apply_optimiser(src: pl.LazyFrame) -> pl.LazyFrame:
        return src

    p.connect("src", "apply_optimiser")

    out = p.run()
    if isinstance(out, pl.LazyFrame):
        out = out.collect()

    # ---- Assertions on the SPECIFIC wrong behaviour --------------------------
    # 1) The standalone output is value-identical to the raw, un-optimised input.
    assert out.equals(raw), (
        "EXPECTED standalone optimiserApply to be a pure passthrough "
        f"(out == raw). Got out=\n{out}\nraw=\n{raw}"
    )
    # 2) No optimiser version column was produced => optimiser NOT applied.
    assert "__optimiser_version__" not in out.columns, (
        "standalone output unexpectedly carries __optimiser_version__ "
        f"=> optimiser WAS applied. columns={out.columns}"
    )
    print("PART2: standalone p.run() output == raw input; "
          "no __optimiser_version__ column => optimiser NOT applied.")


def part3_executor_would_apply_to_second_input() -> None:
    """Show the executor-side selector routes to the 2nd input under ratebook
    mode — the input the standalone passthrough silently ignores."""
    data_df = pl.LazyFrame({"quote_id": [1, 2], "marker": ["data", "data"]})
    rb_df = pl.LazyFrame({"quote_id": [1, 2], "marker": ["ratebook", "ratebook"]})

    selected = _select_optimiser_apply_input(
        dfs=(data_df, rb_df),
        artifact={"mode": "ratebook"},
        ratebook_input="rb_node",
        source_names=["data_src", "ratebook_src"],
        source_ids=["data_node", "rb_node"],
    )
    sel = selected.collect()
    # Executor selects dfs[1] (the ratebook input) — index 1, not 0.
    assert sel["marker"].to_list() == ["ratebook", "ratebook"], (
        "executor selector should route to the 2nd (ratebook) input; "
        f"got {sel}"
    )
    print("PART3: executor _select_optimiser_apply_input -> dfs[1] (ratebook "
          "input) under artifact mode=='ratebook'.")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        _sandbox.set_project_root(Path(tmp))

        code = part1_codegen_is_passthrough()
        print("=== Generated optimiserApply source (scenario node) ===")
        print(code)
        print("=======================================================")

        # The generated body must be the pure first-input passthrough.
        assert "return data_src" in code, (
            "EXPECTED generated body `return data_src` (pure passthrough of the "
            f"FIRST input). Generated code was:\n{code}"
        )
        # It must NOT have been rewritten to return the ratebook (2nd) input,
        # and must NOT contain any apply/score call.
        assert "return ratebook_src" not in code, (
            "ratebook rewrite unexpectedly fired (artifact mode is unresolved); "
            f"code:\n{code}"
        )
        # Precise call-site tokens that would indicate the body actually loads
        # the artifact / applies the optimiser. (We deliberately avoid the bare
        # substring "apply_" here: it appears in the *decorator* name
        # `optimiser_apply` and the *function* name `Apply_Optimiser`, neither
        # of which is an apply CALL. We assert on the function BODY only.)
        body = code.split("-> pl.LazyFrame:", 1)[1]
        for forbidden in (
            "load_optimiser_artifact",
            "load_mlflow_optimiser_artifact",
            "_dispatch_apply",
            "_select_optimiser_apply_input",
            "score_from_config",
            "apply_banding_from_config",
            "apply_rating_step_from_config",
            "apply_ratebook",
            "apply_online",
        ):
            assert forbidden not in body, (
                f"standalone body unexpectedly references {forbidden!r} "
                f"=> it would apply the optimiser; body:\n{body}"
            )
        print("PART1: generated standalone body is `return data_src` with NO "
              "artifact load / apply call.")

        part2_standalone_run_does_not_apply()
        part3_executor_would_apply_to_second_input()

    print("\nRESULT: REPRODUCED — codegen emits a pure passthrough for the "
          "scenario, standalone p.run() never applies the optimiser, while the "
          "executor selector would apply to the 2nd input. Canvas (optimised) "
          "vs standalone (un-optimised) diverge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
