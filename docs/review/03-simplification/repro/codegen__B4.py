"""Isolated reproduction for bug B4.

Claim: ``_rewrite_single_return_source`` (src/haute/codegen.py:348-365) locates the
single passthrough return line with the heuristic::

    line.startswith("    return ") and not line.startswith("    return pl.")

The ``not startswith('    return pl.')`` clause is a brittle proxy for "this is the
passthrough return of a source variable". It only excludes Polars-expression returns,
so ANY OTHER line in the generated body that begins with ``    return `` (4-space
indent) is mis-counted as a passthrough return.

The optimiserApply node docstring is built from the user-controlled ``description``
field via ``_sanitize_description`` (src/haute/_codegen_builders.py:131-182), which
preserves embedded newlines VERBATIM (it only escapes backslashes and double-quotes
and prepends a leading newline). Therefore a description containing a line whose text
is ``    return X`` (indented, not ``pl.``-prefixed) lands inside the emitted docstring
as a physical source line (``    return X`` followed by the closing triple-quote) that
ALSO satisfies the heuristic.

Result: ``_rewrite_single_return_source`` sees TWO matching lines, ``len(return_lines)
!= 1`` is True, and it raises ``HauteError: optimiserApply codegen expected exactly one
passthrough return line.`` for an otherwise-valid config -- a SPURIOUS codegen error that
blocks save/codegen of a legitimate graph.

This is reachable TODAY through the user-facing description field; it does NOT require
the hypothetical "future template change" the claim also mentions.

Strict: this repro touches NO project files. It imports the public-ish internal
``_node_to_code`` and constructs synthetic in-memory GraphNode objects only.

Run:  uv run python review/03-simplification/repro/codegen__B4.py
"""

from __future__ import annotations

from haute._types import GraphNode, NodeData, NodeType
from haute.codegen import _node_to_code
from haute.errors import HauteError


def _make_optimiser_apply_node(description: str) -> GraphNode:
    """An optimiserApply node whose ratebook wiring forces the rewrite to run.

    optimiser_mode == "ratebook" deliberately bypasses the
    ``sourceType in {run, registered} and optimiser_mode != "ratebook"`` early
    return in ``_optimiser_apply_ratebook_return_source`` so that
    ``_rewrite_single_return_source`` IS invoked (otherwise the heuristic is
    never exercised).
    """
    return GraphNode(
        id="apply_node",
        data=NodeData(
            label="Apply Optimiser",
            description=description,
            nodeType=NodeType.OPTIMISER_APPLY,
            config={
                "ratebook_input": "rb_node",       # must match a connected source id
                "optimiser_mode": "ratebook",      # bypass the MLflow-unresolved skip
                "sourceType": "run",
                "artifact_path": "model/ratebook",
            },
        ),
    )


# Two upstream inputs: index 0 = data passthrough, index 1 = ratebook ("rb_node").
SOURCE_NAMES = ["data_src", "ratebook_src"]
SOURCE_IDS = ["data_node", "rb_node"]


def control_single_line_description() -> str:
    """CONTROL: a plain one-line description -> exactly one return line -> OK."""
    node = _make_optimiser_apply_node("apply the optimiser ratebook")
    code = _node_to_code(node, source_names=SOURCE_NAMES, source_ids=SOURCE_IDS)
    # Rewrite must have selected the ratebook input (index 1).
    assert "    return ratebook_src" in code, code
    return code


def trigger_multiline_description() -> tuple[bool, str]:
    """TRIGGER: a description containing an indented ``return`` line.

    The 4-space-indented ``    return me_from_docstring`` line is interpolated
    verbatim into the docstring and is wrongly counted as a passthrough return.
    """
    # NB: the leading "\n" is not required for the bug, but makes the emitted
    # docstring obviously multi-line. The load-bearing part is the SECOND line
    # beginning with four spaces + "return ".
    description = "summary line\n    return me_from_docstring"
    node = _make_optimiser_apply_node(description)
    try:
        code = _node_to_code(node, source_names=SOURCE_NAMES, source_ids=SOURCE_IDS)
    except HauteError as exc:
        return True, f"{type(exc).__name__}: {exc} | context={getattr(exc, 'context', {})}"
    return False, code


def main() -> int:
    print("=== B4 reproduction: brittle return-line heuristic in "
          "_rewrite_single_return_source ===\n")

    # 1. Establish the heuristic works for the normal case.
    ctrl = control_single_line_description()
    print("[CONTROL] plain single-line description -> codegen OK; "
          "rewrite selected the ratebook input:")
    for ln in ctrl.splitlines():
        print("    | " + ln)
    print()

    # 2. The miscount: a valid graph whose ONLY difference is the description text.
    raised, payload = trigger_multiline_description()
    print("[TRIGGER] description = 'summary line\\n    return me_from_docstring'")
    if raised:
        print("    -> _node_to_code raised on an otherwise-valid config:")
        print("       " + payload)
    else:
        print("    -> NO error raised; emitted code was:")
        for ln in payload.splitlines():
            print("    | " + ln)

    print()
    # The bug is confirmed iff the only-description-changed graph now FAILS codegen.
    assert raised, (
        "EXPECTED a spurious HauteError from the mis-counted return line, "
        "but codegen succeeded -> claim B4 would be REFUTED."
    )
    assert "expected exactly one passthrough return line" in payload, payload
    assert "return_line_count" in payload and "2" in payload, payload

    print("RESULT: B4 CONFIRMED. A legitimate optimiserApply graph fails codegen with")
    print("        'expected exactly one passthrough return line' (return_line_count=2)")
    print("        solely because its user-supplied description contains an indented")
    print("        'return ...' line that the heuristic mis-counts as a passthrough return.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
