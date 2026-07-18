"""V020 reproduction — exec/codegen divergence for the CONSTANT node type.

Claim: `_gen_constant` (src/haute/_codegen_builders.py:572-603) builds the
LazyFrame dict literal with `name = v.get("name", "col")` and never skips
entries whose `name` is empty/missing. The executor `_build_constant`
(src/haute/_builders.py:610-630) skips empty/missing names (`if not name:
continue`) and the contract helper `_constant_columns` (_builders.py:604-607)
keeps only truthy names. Therefore, for a node whose `values` contains an entry
with empty or missing `name`, the standalone GENERATED pipeline body produces
extra columns that (1) the in-canvas executor never produces, and (2) the
generated file's OWN decorator-declared contract omits.

This repro drives the REAL codegen (`haute.codegen._node_to_code`) and the REAL
executor (`haute.executor._build_node_fn`), then asserts on the specific wrong
VALUES/columns — not merely that "something raised".

Isolation: no rating/, src/, tests/, or real project files are read or written.
A tempdir is set as the project root so any config-path logic stays sandboxed.
"""

from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

import polars as pl

import haute  # noqa: F401  (ensures package import side effects)
from haute._sandbox import set_project_root


# ---------------------------------------------------------------------------
# Build a minimal GraphNode entirely in memory.
# ---------------------------------------------------------------------------
def _make_constant_node(values):
    from haute.graph_utils import GraphNode

    return GraphNode.model_validate(
        {
            "id": "n1",
            "data": {
                "label": "myconst",
                "nodeType": "constant",
                "config": {"values": values},
            },
        }
    )


def _exec_generated_body(generated_code: str) -> pl.DataFrame:
    """Execute the generated node function body and collect its output.

    We compile the real generated source in a namespace where
    ``pipeline.constant(...)`` is a transparent pass-through decorator that
    returns the wrapped function unchanged. That isolates and runs exactly the
    body literal that codegen emitted (``return pl.LazyFrame({...})``), so the
    columns/values we observe are precisely what the standalone file produces.
    """

    class _PassThroughPipeline:
        def constant(self, *dargs, **dkwargs):
            def _decorator(fn):
                return fn

            return _decorator

    ns: dict = {"pl": pl, "pipeline": _PassThroughPipeline()}
    exec(compile(generated_code, "<generated>", "exec"), ns)
    # The generated function name is the sanitised label.
    fn = ns["myconst"]
    return fn().collect()


def _declared_outputs_from_contract(generated_code: str) -> list[str]:
    """Parse the ``contract={...}`` decorator kwarg's declared outputs."""
    tree = ast.parse(generated_code)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    assert func.decorator_list, "generated function has no decorator"
    call = func.decorator_list[0]
    assert isinstance(call, ast.Call)
    for kw in call.keywords:
        if kw.arg == "contract":
            contract_obj = ast.literal_eval(kw.value)
            assert isinstance(contract_obj, dict), contract_obj
            return list(contract_obj["outputs"])
    raise AssertionError("no contract= kwarg found in generated decorator")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="v020_"))
    set_project_root(tmp)

    from haute.codegen import _node_to_code
    from haute.executor import _build_node_fn

    # Exact values from the V020 evidence: a missing-name entry, an
    # empty-name entry, and a normal entry.
    values = [
        {"value": "5"},  # name MISSING
        {"name": "", "value": "7"},  # name EMPTY
        {"name": "ok", "value": "1"},  # name OK
    ]
    node = _make_constant_node(values)

    # --- REAL codegen path -------------------------------------------------
    generated = _node_to_code(node, source_names=[], source_ids=[])
    print("----- GENERATED CODE -----")
    print(generated)
    print("--------------------------")

    declared_outputs = _declared_outputs_from_contract(generated)
    gen_df = _exec_generated_body(generated)
    gen_cols = list(gen_df.columns)

    # --- REAL executor path ------------------------------------------------
    _, exec_fn, _ = _build_node_fn(node, source_names=[], source=None, node_map=None)
    exec_df = exec_fn().collect()
    exec_cols = list(exec_df.columns)

    print(f"declared contract outputs : {declared_outputs}")
    print(f"generated body columns    : {gen_cols}")
    print(f"executor columns          : {exec_cols}")
    print(f"generated body row(values): {gen_df.to_dicts()}")
    print(f"executor row(values)      : {exec_df.to_dicts()}")

    # ----------------------------------------------------------------------
    # Assertions pinning the SPECIFIC wrong behaviour.
    # ----------------------------------------------------------------------

    # 1. The executor (in-canvas truth, pinned by
    #    tests/test_builder_edge_cases.py::test_value_with_empty_name_skipped)
    #    produces ONLY the truthy-named column.
    assert exec_cols == ["ok"], f"executor expected ['ok'], got {exec_cols}"
    assert exec_df.to_dicts() == [{"ok": 1.0}], exec_df.to_dicts()

    # 2. The declared contract on the SAME generated file lists only 'ok'.
    assert declared_outputs == ["ok"], (
        f"declared contract outputs expected ['ok'], got {declared_outputs}"
    )

    # 3. BUG: the generated body emits THREE columns, including the
    #    missing-name default 'col' and the empty-name '' column — i.e. extra
    #    columns the executor never produces and the contract never declares.
    assert gen_cols == ["col", "", "ok"], (
        f"generated body expected ['col', '', 'ok'] (the bug), got {gen_cols}"
    )

    # 4. Divergence #1 — generated body columns != executor columns.
    assert gen_cols != exec_cols, "expected codegen/executor column divergence"
    extra_vs_executor = sorted(set(gen_cols) - set(exec_cols))
    assert extra_vs_executor == ["", "col"], extra_vs_executor

    # 5. Divergence #2 — internal contradiction: generated body produces
    #    columns its OWN declared contract omits.
    undeclared_in_body = sorted(set(gen_cols) - set(declared_outputs))
    assert undeclared_in_body == ["", "col"], undeclared_in_body

    # 6. Values also differ: the generated frame carries 5.0 and 7.0 under
    #    'col'/'' that simply do not exist in the executor output.
    assert gen_df.to_dicts() == [{"col": 5.0, "": 7.0, "ok": 1.0}], gen_df.to_dicts()

    print()
    print("V020 REPRODUCED: codegen body produces columns {'col',''} that the")
    print("executor omits AND that the generated file's own contract omits.")


if __name__ == "__main__":
    main()
