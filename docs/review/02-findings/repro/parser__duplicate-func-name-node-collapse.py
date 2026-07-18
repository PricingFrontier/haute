"""Adversarial reproduction for claim `duplicate-func-name-node-collapse`.

Claim: Two top-level @pipeline.polars functions with the SAME name but
DIFFERENT bodies parse into two GraphNodes that share id == 'step'. Because
_extract_function_bodies keys bodies by func name (last write wins), BOTH
nodes carry the SECOND body; the first body's pricing logic is silently lost.
The executor then collapses both into one node via {n.id: n for n in nodes}.
codegen._error_on_name_collisions does NOT flag two identical labels.

This script asserts on the SPECIFIC WRONG VALUES:
  * len(g.nodes) == 2 but len({n.id for n in g.nodes}) == 1   (two nodes, one id)
  * BOTH nodes' config['code'] contain "222" (the second body)
  * NEITHER node's config['code'] contains "111" (the first body -> lost)
  * executor node_map collapses 2 -> 1
  * codegen does NOT raise (no loud error) when generating the file

ISOLATION: pure in-memory parse of a synthetic 4-function source string.
No project files, no rating/, no src/, no tests/ are read or written.
A tempdir project root is set only so codegen has a base to write into.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from haute import _sandbox
from haute.parser import parse_pipeline_source


# Two @pipeline.polars functions named `step` with provably distinct bodies.
# (A leading data source so the graph is well-formed and `step` has an input.)
SOURCE = '''\
import haute as pipeline


@pipeline.source
def raw(): df = pl.DataFrame({"a": [1]})


@pipeline.polars
def step(raw): return raw.with_columns(marker=pl.lit(111))


@pipeline.polars
def step(raw): return raw.with_columns(marker=pl.lit(222))
'''


def _codes(graph) -> list[str]:
    return [n.data.config.get("code", "") for n in graph.nodes]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        _sandbox.set_project_root(Path(tmp))

        graph = parse_pipeline_source(SOURCE, source_file="dup.py")

        ids = [n.id for n in graph.nodes]
        labels = [n.data.label for n in graph.nodes]
        codes = _codes(graph)

        # Keep only the two `step` nodes (ignore the `raw` source node).
        step_idx = [i for i, nid in enumerate(ids) if nid == "step"]
        step_codes = [codes[i] for i in step_idx]

        print(f"all node ids        : {ids}")
        print(f"all node labels     : {labels}")
        print(f"step node count     : {len(step_idx)}")
        print(f"step codes          : {step_codes!r}")

        # --- Core claim 1: two physical nodes, one logical id ---------------
        assert len(step_idx) == 2, (
            f"expected two 'step' nodes, got {len(step_idx)}: {ids}"
        )
        assert len({ids[i] for i in step_idx}) == 1, "step ids should collide on 'step'"

        # --- Core claim 2: BOTH bodies are the SECOND body (222); first lost.
        for i, code in enumerate(step_codes):
            assert "222" in code, (
                f"step node {i} should carry the SECOND body (222); got {code!r}"
            )
            assert "111" not in code, (
                f"step node {i} unexpectedly still carries the FIRST body (111); "
                f"got {code!r} -- if this fires the silent-loss bug is NOT present"
            )
        # Both code strings identical -> the first function's distinct logic
        # never made it into the graph at all.
        assert step_codes[0] == step_codes[1], (
            "expected both step bodies to be identical (== second body); "
            f"got {step_codes!r}"
        )

        # --- Core claim 3: executor-style collapse 2 -> 1 -------------------
        node_map = {n.id: n for n in graph.nodes}
        collapsed = node_map["step"]
        print(f"executor node_map keys : {sorted(node_map.keys())}")
        print(f"collapsed 'step' code  : {collapsed.data.config.get('code')!r}")
        assert "step" in node_map and len([k for k in node_map if k == "step"]) == 1
        # The single surviving node carries the 222 body.
        assert "222" in collapsed.data.config.get("code", "")
        assert "111" not in collapsed.data.config.get("code", "")

        # --- Core claim 4: codegen does NOT raise on identical labels -------
        # _error_on_name_collisions only flags DISTINCT labels colliding; two
        # identical 'step' labels slip through with no loud error.
        codegen_raised = None
        codegen_files = None
        try:
            from haute import codegen

            # graph_to_code_multi is the real entry point; it invokes
            # _error_on_name_collisions(...) internally (codegen.py:918).
            codegen_files = codegen.graph_to_code_multi(graph, pipeline_name="dup")
        except Exception as exc:  # noqa: BLE001 - we want to know IF it raised
            codegen_raised = exc
        print(f"codegen raised         : {codegen_raised!r}")
        if codegen_files is not None:
            main_src = next(iter(codegen_files.values()))
            print(f"codegen step defs (222): {main_src.count('222')}")
            print(f"codegen step defs (111): {main_src.count('111')}")
        # The claim is that NO loud collision error is raised for identical
        # labels. If a ParseError specifically about name collisions fires,
        # the claim's "silent" premise would be wrong.
        if codegen_raised is not None:
            from haute.errors import ParseError

            assert not (
                isinstance(codegen_raised, ParseError)
                and "sanitize" in str(codegen_raised).lower()
            ), (
                "codegen DID raise a name-collision ParseError -- the claim's "
                f"'no loud error' premise is refuted: {codegen_raised!r}"
            )

    print()
    print("REPRODUCED: duplicate 'step' funcs -> 2 nodes / 1 id; first body "
          "(111) silently lost, both carry second body (222); executor "
          "collapses to one node; no loud collision error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
