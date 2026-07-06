"""Isolated reproduction for V042.

Claim: ``_extract_decorated_nodes`` (src/haute/_graph_builders.py:73) builds
``param_names`` from ``stmt.args.args`` ONLY. Python's ``ast.arguments`` splits
parameters into ``posonlyargs`` (before ``/``), ``args``
(positional-or-keyword), ``kwonlyargs`` (after ``*``), ``vararg`` and ``kwarg``.

Consequences predicted by the finding:
  (A) A ``@pipeline.polars`` node ``def transform(claims, *, adjustments): ...``
      where ``adjustments`` names an upstream node loses its implicit
      ``(adjustments, transform)`` edge in ``_build_edges``.
  (B) Same for a positional-only parameter (``def node(a, /, b): ...``): ``a``
      is invisible, so an ``(a, node)`` edge is dropped.
  (C) A ``@pipeline.live_switch`` node's ``config['inputs']`` (set to
      ``param_names`` in _config_builder.py:108) omits the kw-only branch,
      corrupting the live-switch input list.

This repro parses fully-synthetic in-memory pipeline source via the public
``parse_pipeline_source`` entrypoint (NO disk I/O, NO real project files) and
asserts on the SPECIFIC wrong values (a missing edge / a short inputs list),
not merely that "something raised".
"""

from __future__ import annotations

from haute.parser import parse_pipeline_source

# ---------------------------------------------------------------------------
# (A) kw-only parameter on a transform node  ->  dropped implicit edge
# ---------------------------------------------------------------------------
SRC_KWONLY = '''
from haute import pipeline


@pipeline.polars
def claims(df):
    return df


@pipeline.polars
def adjustments(df):
    return df


@pipeline.polars
def transform(claims, *, adjustments):
    df = claims
    return df
'''


# ---------------------------------------------------------------------------
# (B) positional-only parameter on a transform node  ->  dropped implicit edge
# ---------------------------------------------------------------------------
SRC_POSONLY = '''
from haute import pipeline


@pipeline.polars
def upstream(df):
    return df


@pipeline.polars
def downstream(upstream, /, other):
    df = upstream
    return df


@pipeline.polars
def other(df):
    return df
'''


# ---------------------------------------------------------------------------
# (C) positional-only FIRST parameter on a transform node  ->  the codegen
#     ``df = <param>`` alias line is NOT recognised as boilerplate (because the
#     param is absent from param_names) and survives as spurious "user code".
#
#     _finalise_polars strips a leading ``df = <param>`` ONLY when
#     ``<param> in param_names``.  A positional-only first param is not in
#     param_names, so the alias leaks into config['code'].
# ---------------------------------------------------------------------------
SRC_ALIAS = '''
from haute import pipeline


@pipeline.polars
def claims(df):
    return df


@pipeline.polars
def enrich(claims, /):
    df = claims
    df = df.filter(pl.col("x") > 0)
    return df
'''


def _edge_pairs(graph) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges}


def main() -> None:
    failures: list[str] = []

    # ---- (A) kw-only edge drop ------------------------------------------------
    g = parse_pipeline_source(SRC_KWONLY, source_file="kwonly.py")
    pairs = _edge_pairs(g)
    transform_node = next(n for n in g.nodes if n.id == "transform")
    print("[A] transform node implicit-edge param_names (config inputs n/a):")
    print("    edges:", sorted(pairs))
    # The positional-or-keyword edge (claims -> transform) IS present.
    assert ("claims", "transform") in pairs, (
        "sanity: the positional-or-keyword 'claims' edge should exist"
    )
    # The kw-only edge (adjustments -> transform) is the one under test.
    expected_kwonly_edge = ("adjustments", "transform")
    if expected_kwonly_edge not in pairs:
        failures.append(
            f"[A] EXPECTED implicit edge {expected_kwonly_edge} from kw-only "
            f"param, but it is MISSING. edges={sorted(pairs)}"
        )

    # ---- (B) positional-only edge drop ---------------------------------------
    g2 = parse_pipeline_source(SRC_POSONLY, source_file="posonly.py")
    pairs2 = _edge_pairs(g2)
    print("[B] posonly edges:", sorted(pairs2))
    # 'other' is positional-or-keyword -> its edge IS present.
    assert ("other", "downstream") in pairs2, (
        "sanity: positional-or-keyword 'other' edge should exist"
    )
    expected_posonly_edge = ("upstream", "downstream")
    if expected_posonly_edge not in pairs2:
        failures.append(
            f"[B] EXPECTED implicit edge {expected_posonly_edge} from "
            f"positional-only param, but it is MISSING. edges={sorted(pairs2)}"
        )

    # ---- (C) positional-only first param -> codegen alias leaks into code ----
    g3 = parse_pipeline_source(SRC_ALIAS, source_file="alias.py")
    enrich_node = next(n for n in g3.nodes if n.id == "enrich")
    code = enrich_node.data.config.get("code", "")
    print("[C] enrich config['code']:", repr(code))
    # The codegen alias 'df = claims' should have been stripped (claims is the
    # sole upstream input). Because 'claims' is positional-only it is missing
    # from param_names, so _finalise_polars cannot recognise/strip the alias.
    if code.splitlines() and code.splitlines()[0].strip() == "df = claims":
        failures.append(
            "[C] codegen alias 'df = claims' leaked into config['code'] "
            f"because the positional-only param is absent from param_names. "
            f"code={code!r}"
        )
    # Also: the (claims -> enrich) edge is dropped (claims is posonly).
    pairs3 = _edge_pairs(g3)
    print("[C] alias-case edges:", sorted(pairs3))
    if ("claims", "enrich") not in pairs3:
        failures.append(
            f"[C] EXPECTED implicit edge ('claims', 'enrich') from "
            f"positional-only param, but it is MISSING. edges={sorted(pairs3)}"
        )

    print()
    if failures:
        print("REPRODUCED — finding holds. Failures:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("NOT reproduced — all expected edges/inputs were present.")


if __name__ == "__main__":
    main()
