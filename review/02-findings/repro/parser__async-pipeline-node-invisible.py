"""Adversarial repro: async @pipeline node functions silently dropped.

CLAIM: `_extract_function_bodies` (src/haute/_ast_helpers.py:270) and
`_extract_decorated_nodes` (src/haute/_graph_builders.py:59) test only
`isinstance(..., ast.FunctionDef)`, never `ast.AsyncFunctionDef`.  A file
mixing a normal `@pipeline.polars def a(df)` with an
`@pipeline.polars async def b(a)` therefore parses to node ids ['a'] only:
'b' is dropped silently with NO exception, and the implied a->b edge is gone.

This violates the project's fail-loud mandate (CLAUDE.md): the node's pricing
logic vanishes from the graph, and a GUI save (graph -> code) would delete it
from the file.

ISOLATION: pure in-memory source string passed to parse_pipeline_source.
No disk I/O, no reads/writes of rating/, src/, tests/, or any real project
file.  We assert on the SPECIFIC wrong value (node-id list and edge count),
not merely that "something raised".
"""

from __future__ import annotations

import ast

from haute._ast_helpers import _extract_function_bodies
from haute.parser import parse_pipeline_source

# A perfectly valid Python module: one sync polars node + one ASYNC polars node
# whose only parameter ("first") names the sync node, implying a first->second
# edge.  Nothing here is a syntax error, so the regex fallback is NOT engaged.
SOURCE = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("demo")


@pipeline.polars
def first(df):
    return df.with_columns(pl.lit(1).alias("one"))


@pipeline.polars
async def second(first):
    return first.with_columns(pl.lit(2).alias("two"))
'''


def main() -> None:
    # Sanity: the source is valid Python and `second` really is an async def
    # decorated with @pipeline.polars (i.e. the user wrote a legitimate node).
    tree = ast.parse(SOURCE)
    async_defs = [n for n in ast.iter_child_nodes(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert [n.name for n in async_defs] == ["second"], (
        f"setup precondition failed: expected one async def 'second', got "
        f"{[n.name for n in async_defs]}"
    )

    # --- Direct unit-level confirmation on _extract_function_bodies ----------
    bodies = _extract_function_bodies(SOURCE, tree=tree)
    assert "first" in bodies, f"setup precondition failed: 'first' body missing: {bodies.keys()}"
    # The bug: the async node's body is never extracted.
    assert "second" not in bodies, (
        "EXPECTED-BUG NOT PRESENT: _extract_function_bodies returned a body for "
        "the async node 'second' (claim would be refuted)."
    )

    # --- End-to-end confirmation through the public parser --------------------
    graph = parse_pipeline_source(SOURCE)
    node_ids = [n.id for n in graph.nodes]
    edge_count = len(graph.edges)

    print(f"node_ids   = {node_ids}")
    print(f"edge_count = {edge_count}")

    # The async node is gone entirely, with no exception raised.
    assert node_ids == ["first"], (
        f"EXPECTED node_ids == ['first'] (async 'second' silently dropped), "
        f"got {node_ids}"
    )
    # The implied first->second edge vanished with the node.
    assert edge_count == 0, (
        f"EXPECTED edge_count == 0 (the first->second edge vanished with the "
        f"dropped node), got {edge_count}"
    )

    print(
        "REPRODUCED: async @pipeline.polars node 'second' is silently absent "
        "from the parsed graph (no error raised); its edge is gone too."
    )


if __name__ == "__main__":
    main()
