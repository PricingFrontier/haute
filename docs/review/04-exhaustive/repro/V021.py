"""Reproduction for V021.

Claim: ``_gen_constant`` (codegen side, ``src/haute/_codegen_builders.py``)
crashes with ``AttributeError: 'NoneType' object has no attribute 'replace'``
when a constant ``values`` entry has ``value=None`` OR ``name=None``, even
though the *executor* side (``_build_constant`` in ``src/haute/_builders.py``)
handles both gracefully. Net effect: a constant node that runs fine in the
canvas hard-crashes Save Pipeline / deploy (the codegen path).

Mechanism (codegen, lines 583-596):
    name = v.get("name", "col")   # key present with value None -> returns None
    val  = v.get("value", "")     # key present with value None -> returns None
    try:
        num = float(val)          # float(None) -> TypeError
        ...
    except (ValueError, TypeError):
        data_pairs.append(f"{_safe_str(name)}: [{_safe_str(val)}]")
                                  # _safe_str(None) -> None.replace(...) -> AttributeError

Mechanism (executor, lines 617-625): a None name is falsy -> ``if not name:
continue`` skips it; a None value -> ``float(None)`` TypeError -> ``data[name]
= [None]`` which Polars accepts. So the executor accepts exactly the input the
codegen rejects.

ISOLATION: everything is built in-memory from synthetic pydantic models. No
disk I/O, no reads/writes of rating/, src/, tests/, or any real project file.
We assert on the SPECIFIC behaviour: codegen raises the predicted
AttributeError on input that the executor evaluates without error.
"""

from __future__ import annotations

import polars as pl

from haute._types import GraphNode, NodeData, NodeType
from haute.codegen import _node_to_code

failures: list[str] = []


def make_constant_node(values: list[dict]) -> GraphNode:
    """Build a synthetic CONSTANT GraphNode with the given values config.

    NodeData.config is dict[str, Any] with no runtime validation, so the
    raw {"name": None, ...} entries pass through untouched to the builders.
    """
    return GraphNode(
        id="n_const",
        data=NodeData(
            label="my_constant",
            description="",
            nodeType=NodeType.CONSTANT,
            config={"values": values},
        ),
    )


def exec_constant(values: list[dict]) -> pl.DataFrame:
    """Drive the REAL executor builder for CONSTANT and collect the result."""
    from haute._builders import NodeBuildContext, _build_constant

    node = make_constant_node(values)
    ctx = NodeBuildContext(
        node=node,
        source_names=[],
        source_ids=[],
        target_handles=None,
        row_limit=None,
        node_map=None,
        orig_source_names=None,
        preamble_ns=None,
        source=None,
    )
    _name, fn, _ = _build_constant(ctx)
    return fn().collect()


def check_case(name: str, values: list[dict], crashing_field: str) -> None:
    """Assert codegen raises the predicted AttributeError while exec succeeds."""
    node = make_constant_node(values)

    # --- codegen side: expect AttributeError 'NoneType' ... 'replace' ---
    codegen_error: BaseException | None = None
    try:
        code = _node_to_code(node, source_names=[])
        print(f"[no-crash] {name}: codegen returned code (no exception)")
        print("      generated snippet:")
        print("      " + code.replace("\n", "\n      ")[:400])
    except AttributeError as exc:  # the predicted bug
        codegen_error = exc
    except BaseException as exc:  # noqa: BLE001 — any OTHER error is NOT the claim
        codegen_error = exc

    is_predicted_crash = (
        isinstance(codegen_error, AttributeError)
        and "replace" in str(codegen_error)
        and "NoneType" in str(codegen_error)
    )

    # --- executor side: expect graceful success on the SAME config ---
    exec_ok = False
    exec_detail = ""
    try:
        df = exec_constant(values)
        exec_ok = True
        exec_detail = f"columns={df.columns} row0={df.row(0) if df.height else None}"
    except BaseException as exc:  # noqa: BLE001
        exec_detail = f"executor RAISED {type(exc).__name__}: {exc}"

    status = "BUG" if (is_predicted_crash and exec_ok) else "ok"
    print(f"[{status}] {name}  (crashing field = {crashing_field})")
    print(f"      codegen  -> {type(codegen_error).__name__ if codegen_error else None}: "
          f"{codegen_error}")
    print(f"      executor -> ok={exec_ok}  {exec_detail}")

    if not is_predicted_crash:
        failures.append(
            f"{name}: codegen did NOT raise the predicted "
            f"AttributeError('NoneType' ... 'replace'); got {codegen_error!r}"
        )
    if not exec_ok:
        failures.append(
            f"{name}: executor did NOT succeed on the same config "
            f"({exec_detail}) -- asymmetry not demonstrated"
        )


# ---------------------------------------------------------------------------
# (1) value=None : float(None) -> TypeError -> _safe_str(None) crashes codegen.
#     Executor: data['x'] = [None]  (supported, pinned by
#     tests/test_builder_edge_cases.py::test_none_value_kept_as_string).
# ---------------------------------------------------------------------------
check_case(
    name="value_is_None  {'name':'x','value':None}",
    values=[{"name": "x", "value": None}],
    crashing_field="value",
)

# ---------------------------------------------------------------------------
# (2) name=None : v.get('name','col') returns None (key present) ->
#     _safe_str(None) crashes codegen even though val coerces fine.
#     Executor: 'if not name: continue' skips it -> falls back to {'constant':[0]}.
# ---------------------------------------------------------------------------
check_case(
    name="name_is_None   {'name':None,'value':'5'}",
    values=[{"name": None, "value": "5"}],
    crashing_field="name",
)

print()
if failures:
    print("REPRO RESULT: NOT fully reproduced -- discrepancies:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
else:
    print("REPRO RESULT: REPRODUCED -- codegen (_gen_constant via _node_to_code) raises")
    print("AttributeError('NoneType' object has no attribute 'replace') on a constant")
    print("values entry with value=None or name=None, while the executor (_build_constant)")
    print("evaluates the identical config without error. Asymmetric hard-crash confirmed.")
