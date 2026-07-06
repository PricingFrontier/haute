"""Adversarial repro for claim: regex-fallback-drops-all-submodels.

Claim: when a pipeline main file references submodels via
`pipeline.submodel("modules/sub.py")` AND contains a syntax error anywhere,
`parse_pipeline_source` catches the SyntaxError and delegates to
`fallback_parse`, which has NO submodel handling. The returned graph has
`submodels=None` and none of the submodel child nodes -> silent structural
loss of the entire submodel subgraph.

Strategy:
  1. BASELINE (healthy): valid main.py + valid modules/sub.py. Confirm the
     healthy AST path DOES populate `submodels` and DOES include the child
     node. This proves the project layout is correct and the divergence in
     step 2 is caused specifically by the fallback path.
  2. BROKEN: identical main.py but with one unclosed paren in a node body.
     Assert `submodels is None` and the child node id is ABSENT.

Isolation: all disk I/O via tempfile; project root set via
haute._sandbox.set_project_root(tmp); no real project files touched.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import haute._sandbox as _sandbox
from haute.parser import parse_pipeline_file

MAIN_TEMPLATE = '''\
"""Demo pipeline."""

pipeline = __import__("types").SimpleNamespace()


@pipeline.polars
def root_node(df):
    """Root node."""
    return df{maybe_broken}


pipeline.submodel("modules/sub.py")
'''

SUB_SRC = '''\
"""Sub model."""


@submodel.polars
def child_node(df):
    """A submodel child node."""
    return df
'''


def _node_ids(graph) -> list[str]:
    ids: list[str] = []
    for n in graph.nodes:
        # GraphNode pydantic models expose .id
        ids.append(getattr(n, "id", None) or getattr(getattr(n, "data", None), "id", None))
    return [i for i in ids if i]


def _write_project(tmp: Path, *, broken: bool) -> Path:
    modules = tmp / "modules"
    modules.mkdir(parents=True, exist_ok=True)
    # A project-root marker so get_project_root resolves tmp as the root.
    (tmp / "haute.toml").write_text("[project]\n", encoding="utf-8")
    (modules / "sub.py").write_text(SUB_SRC, encoding="utf-8")
    main = tmp / "main.py"
    # Unclosed paren in the node body => SyntaxError for the whole file.
    main.write_text(
        MAIN_TEMPLATE.format(maybe_broken=".select((" if broken else ""),
        encoding="utf-8",
    )
    return main


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        _sandbox.set_project_root(tmp)

        # --- 1. BASELINE: healthy main.py -> submodels merged ---------------
        healthy_main = _write_project(tmp / "healthy", broken=False)
        g_ok = parse_pipeline_file(healthy_main, flatten=True)
        ok_ids = _node_ids(g_ok)
        print(f"[baseline] node ids        = {ok_ids}")
        print(f"[baseline] submodels       = {g_ok.submodels!r}")
        print(f"[baseline] warning         = {g_ok.warning!r}")

        baseline_has_child = "child_node" in ok_ids
        print(f"[baseline] child present?  = {baseline_has_child}")
        # Sanity: the healthy path must include the submodel child, otherwise
        # the layout is wrong and the test below would be meaningless.
        assert baseline_has_child, (
            "SETUP FAILURE: healthy path did not include submodel child_node; "
            f"got ids={ok_ids}. Repro layout is wrong, claim not exercised."
        )

        # --- 2. BROKEN: syntax error -> fallback path -----------------------
        broken_main = _write_project(tmp / "broken", broken=True)
        g_bad = parse_pipeline_file(broken_main, flatten=True)
        bad_ids = _node_ids(g_bad)
        print(f"[broken]   node ids        = {bad_ids}")
        print(f"[broken]   submodels       = {g_bad.submodels!r}")
        print(f"[broken]   warning         = {g_bad.warning!r}")

        # The main-file node should still be recovered by the regex fallback.
        assert "root_node" in bad_ids, (
            "Expected regex fallback to recover main-file node 'root_node'; "
            f"got {bad_ids}. (If absent, fallback recovered nothing -> setup issue.)"
        )

        # THE BUG: submodels silently dropped + child node absent.
        child_absent = "child_node" not in bad_ids
        submodels_none = g_bad.submodels is None

        print()
        print(f"EXPECTED (no silent loss): submodels populated + child_node present")
        print(f"ACTUAL  : submodels={g_bad.submodels!r}, child_node present={not child_absent}")

        assert submodels_none, (
            "Claim REFUTED: fallback graph has submodels populated "
            f"({g_bad.submodels!r}) -- no silent loss."
        )
        assert child_absent, (
            "Claim REFUTED: fallback graph still contains 'child_node' -- "
            "submodel nodes were NOT dropped."
        )

        print()
        print("CLAIM CONFIRMED: regex fallback silently dropped the submodel")
        print("subgraph (submodels=None, child_node absent) even though the")
        print("identical healthy file merged it correctly.")


if __name__ == "__main__":
    main()
