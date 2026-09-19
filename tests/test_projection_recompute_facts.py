"""Tests for code-node recompute facts and builder cost declarations (Step 2)."""

from __future__ import annotations

import ast

import pytest

from haute._polars_operations import (
    OperationPolicy,
    OperationReceiver,
    materialising_expression_methods,
    materialising_frame_methods,
)
from haute._types import GraphNode, NodeData, NodeType
from haute.projection import (
    ReportedCall,
    _materialising_calls_in_source_order,
    code_recompute_facts,
    recompute_facts_by_node,
)


@pytest.mark.parametrize(
    ("code", "expected_cost", "expected_transparent", "expected_reason"),
    [
        ("df = df.explode('l')", "cheap", False, "opaque:explode"),
        ("df = df.shift(1)", "cheap", False, "opaque:shift"),
        ("df = df.with_columns(pl.col('x').shift(1))", "cheap", False, "opaque:shift"),
        ("df = df.with_columns(pl.col('x').sum())", "cheap", False, "opaque:sum"),
        ("df = df.group_by('k').agg(pl.len())", "costly", False, "costly:group_by"),
        ("df = df.with_columns(pl.col('x').sum().over('k'))", "costly", False, "costly:over"),
        ("df = df.pivot(on='k', index='i', values='v')", "costly", False, "costly:pivot"),
        ("df = df.with_columns(pl.col('x').sort())", "costly", False, "costly:sort"),
        (
            "df = df.with_columns(pl.col('x').map_elements(f))",
            "costly",
            False,
            "costly:map_elements",
        ),
        ("df = df.describe()", "costly", False, "unregistered_frame_method:describe"),
        ("df = (", "costly", False, "syntax_error"),
    ],
)
def test_code_node_cost_is_decided_by_registered_calls(
    code: str,
    expected_cost: str,
    expected_transparent: bool,
    expected_reason: str,
) -> None:
    facts = code_recompute_facts(code, frozenset({"quotes", "claims"}))
    assert facts.cost == expected_cost
    assert facts.slice_transparent is expected_transparent
    assert facts.reason == expected_reason


@pytest.mark.parametrize(
    ("code", "preamble", "expected_cost", "expected_reason"),
    [
        ("df = expensive_helper(df)", "", "costly", "unresolved_call:expensive_helper"),
        ("df = utils.f(df)", "import utils", "costly", "unresolved_call:f"),
        ("df = build(cfg)\ndf = df.select('x')", "", "costly", "unresolved_call:build"),
        ("n = helper(cfg['n'])", "", "costly", "unresolved_call:helper"),
        ("df = df.pipe(helper)", "", "costly", "costly:pipe"),
        ("n = int(cfg['n'])", "", "cheap", "cheap"),
        ("k = len(names)", "", "cheap", "cheap"),
        ("int = f\nn = int(df)", "", "costly", "unresolved_call:int"),
        ("s = sum(df)", "def sum(x):\n    return x", "costly", "unresolved_call:sum"),
        ("s = sum([1, 2])", "", "cheap", "cheap"),
        ("df = pl.concat([df, df])", "", "costly", "unresolved_call:pl.concat"),
    ],
)
def test_unresolved_calls_make_a_node_costly(
    code: str,
    preamble: str,
    expected_cost: str,
    expected_reason: str,
) -> None:
    facts = code_recompute_facts(code, frozenset({"quotes", "claims"}), preamble=preamble)
    assert facts.cost == expected_cost
    assert facts.reason == expected_reason


@pytest.mark.parametrize(
    ("code", "expected_cost", "expected_transparent", "expected_reason"),
    [
        (
            "df = next(iter((quotes,)))\ndf = df.group_by('k').agg(pl.len())",
            "costly",
            False,
            "costly:group_by",
        ),
        (
            "df = max(quotes, claims)\ndf = df.select('x')",
            "cheap",
            True,
            "cheap",
        ),
        (
            "n = max(1, 2)",
            "cheap",
            True,
            "cheap",
        ),
    ],
)
def test_pass_through_builtins_keep_frame_facts(
    code: str,
    expected_cost: str,
    expected_transparent: bool,
    expected_reason: str,
) -> None:
    facts = code_recompute_facts(code, frozenset({"quotes", "claims"}))
    assert facts.cost == expected_cost
    assert facts.slice_transparent is expected_transparent
    assert facts.reason == expected_reason


@pytest.mark.parametrize(
    ("code", "expected_cost", "expected_reason"),
    [
        (
            "df = next(map(expensive_helper, (quotes,)))",
            "costly",
            "unresolved_callback:expensive_helper",
        ),
        (
            "ordered = sorted(frames, key=size_of)",
            "costly",
            "unresolved_callback:size_of",
        ),
        (
            "out = list(map(lambda f: f.group_by('k').agg(pl.len()), frames))",
            "costly",
            "costly:group_by",
        ),
        (
            "names2 = list(map(str, names))",
            "cheap",
            "cheap",
        ),
        (
            "s = sorted(names, key=len)",
            "cheap",
            "cheap",
        ),
    ],
)
def test_callbacks_to_pass_through_builtins_are_classified(
    code: str,
    expected_cost: str,
    expected_reason: str,
) -> None:
    facts = code_recompute_facts(code, frozenset({"quotes", "claims"}))
    assert facts.cost == expected_cost
    assert facts.reason == expected_reason


@pytest.mark.parametrize(
    ("code", "preamble", "expected_transparent", "expected_reason"),
    [
        ("df = df.select(pl.col('x'))", "", True, "cheap"),
        (
            "df = df.with_columns(pl.when(pl.col('x') > 0).then(1).otherwise(0).alias('y'))",
            "",
            True,
            "cheap",
        ),
        ("df = df.select(pl.all())", "", True, "cheap"),
        ("df = df.select(pl.exclude('x'))", "", True, "cheap"),
        ("df = df.rename({'a': 'b'}).cast({'b': pl.Int64})", "", True, "cheap"),
        ("df = df.filter(pl.col('x') > 0)", "", False, "opaque:filter"),
        ("df = df.drop_nulls()", "", False, "opaque:drop_nulls"),
        ("df = df.select(pl.first('x'))", "", False, "opaque:first"),
        ("df = df.with_columns(pl.all('flag'))", "", False, "opaque:all"),
        ("df = df.with_columns(pl.col('l').list.explode())", "", False, "opaque:explode"),
        ("df = df.select(cs.numeric())", "import polars.selectors as cs", True, "cheap"),
        ("df = df.select(pl.selectors.numeric())", "", True, "cheap"),
        ("df = df.unnest('s')", "", True, "cheap"),
    ],
)
def test_code_node_slice_transparency(
    code: str,
    preamble: str,
    expected_transparent: bool,
    expected_reason: str,
) -> None:
    facts = code_recompute_facts(code, frozenset({"quotes", "claims"}), preamble=preamble)
    assert facts.cost == "cheap"
    assert facts.slice_transparent is expected_transparent
    assert facts.reason == expected_reason


@pytest.mark.parametrize(
    ("code", "expected_full_input"),
    [
        ("df = df.sort('x')", True),
        ("df = df.unique()", True),
        ("df = df.with_columns(pl.col('x').rank())", True),
        ("df = df.explode('l')", False),
        ("df = df.with_columns(pl.col('x').map_elements(f))", False),
        ("df = expensive_helper(df)", False),
    ],
)
def test_full_input_work_is_reported(code: str, expected_full_input: bool) -> None:
    facts = code_recompute_facts(code, frozenset({"quotes", "claims"}))
    assert facts.full_input_work is expected_full_input


_BOUNDARY_WALK_SNIPPETS = (
    ("df = src.unique(subset=['k']).reverse()", frozenset({"src"})),
    ("df = src.reverse().unique(subset=['k'])", frozenset({"src"})),
    ("df = src.sort('a').unique(subset=['k']).reverse()", frozenset({"src"})),
    ("df = src.with_columns(pl.col('p').shift(1).alias('lag'))", frozenset({"src"})),
    (
        "df = src.with_columns(\n"
        "    pl.col('p').diff().alias('d'),\n"
        "    pl.col('p').pct_change().alias('pc'),\n"
        ")",
        frozenset({"src"}),
    ),
    ("f = pl.col('p').diff\ndf = src.with_columns(f())", frozenset({"src"})),
    (
        "def delta(expr):\n    return expr.diff()\ndf = src.with_columns(delta(pl.col('p')))",
        frozenset({"src"}),
    ),
    (
        "change = lambda expr: expr.pct_change()\ndf = src.with_columns(change(pl.col('p')))",
        frozenset({"src"}),
    ),
    (
        "def window(expr):\n"
        "    return expr.over('k')\n"
        "df = src.with_columns(window(pl.col('p').sum()))",
        frozenset({"src"}),
    ),
    (
        "value = src if flag else pl.col('p')\ndf = src.with_columns(value.diff())",
        frozenset({"src"}),
    ),
    (
        "value = pl.col('l').list if flag else pl.col('p')\n"
        "df = src.with_columns(value.sort(), value.diff())",
        frozenset({"src"}),
    ),
    (
        "df = src.with_columns(pl.col('l').list.shift(1), pl.col('l').list.diff())",
        frozenset({"src"}),
    ),
    ("df = src.with_columns(pl.col('a').arr.shift(1))", frozenset({"src"})),
    ("f = pl.col('l').list.diff\ndf = src.with_columns(f())", frozenset({"src"})),
    ("items = pl.col('l').list\ndf = src.with_columns(items.diff())", frozenset({"src"})),
    (
        "items = pl.col('l').list\nlag = items.shift\ndf = src.with_columns(lag(1))",
        frozenset({"src"}),
    ),
    ("df = src.with_columns((pl.col('a') * 2).sort())", frozenset({"src"})),
    ("df = (src * 2).sort('a')", frozenset({"src"})),
    ("df = left.join(right.sort('a'), on='k')", frozenset({"left", "right"})),
    ("df = left.join(right.unique(subset=['k']), on='k')", frozenset({"left", "right"})),
    ("df = left.sort('a').join(right.unique(subset=['k']), on='k')", frozenset({"left", "right"})),
    ("df = src.with_columns(pl.selectors.numeric().over('k'))", frozenset({"src"})),
)


@pytest.mark.parametrize(("code", "input_names"), _BOUNDARY_WALK_SNIPPETS)
def test_walk_report_all_matches_boundary_walk(code: str, input_names: frozenset[str]) -> None:
    tree = ast.parse(code)
    boundary_calls = _materialising_calls_in_source_order(
        tree,
        input_names,
        materialising_frame_methods(),
        materialising_expression_methods(),
    )
    boundary_names = [call[3] for call in boundary_calls]

    report: list[ReportedCall] = []
    _materialising_calls_in_source_order(
        tree,
        input_names,
        materialising_frame_methods(),
        materialising_expression_methods(),
        report=report,
    )
    reported_boundary_names = [
        call.name
        for call in report
        if call.category == "registered"
        and any(
            e.policy is OperationPolicy.MATERIALISATION_BOUNDARY
            and e.receiver is not OperationReceiver.NAMESPACE
            for e in call.entries
        )
    ]
    assert boundary_names == reported_boundary_names


def test_code_bearing_nodes_combine_declaration_with_code_facts() -> None:
    nodes = {
        "se": GraphNode(
            id="se",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(label="se", nodeType=NodeType.SCENARIO_EXPANDER, config={}),
        ),
        "exp": GraphNode(
            id="exp",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(
                label="exp",
                nodeType=NodeType.EXPLORE,
                config={"code": "df = df.group_by('k').agg(pl.len())"},
            ),
        ),
        "ef_no_code": GraphNode(
            id="ef_no_code",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(label="ef_no_code", nodeType=NodeType.EXTERNAL_FILE, config={}),
        ),
        "ef_with_code": GraphNode(
            id="ef_with_code",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(
                label="ef_with_code",
                nodeType=NodeType.EXTERNAL_FILE,
                config={"code": "df = helper(df)"},
            ),
        ),
        "rs": GraphNode(
            id="rs",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(label="rs", nodeType=NodeType.RATING_STEP, config={}),
        ),
        "di_no_code": GraphNode(
            id="di_no_code",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(label="di_no_code", nodeType=NodeType.DATA_INPUT, config={}),
        ),
        "di_with_code": GraphNode(
            id="di_with_code",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(
                label="di_with_code",
                nodeType=NodeType.DATA_INPUT,
                config={"code": "df = df.unique()"},
            ),
        ),
    }
    order = ["se", "exp", "ef_no_code", "ef_with_code", "rs", "di_no_code", "di_with_code"]

    facts = recompute_facts_by_node(order, nodes, relevant_edges=())

    # Scenario Expander -> cheap, not transparent
    assert facts["se"].cost == "cheap"
    assert facts["se"].slice_transparent is False

    # Explore with group_by -> costly
    assert facts["exp"].cost == "costly"
    assert facts["exp"].reason == "costly:group_by"

    # External File without code -> cheap and transparent
    assert facts["ef_no_code"].cost == "cheap"
    assert facts["ef_no_code"].slice_transparent is True
    assert facts["ef_no_code"].reason == "blank_code"

    # External File with helper(df) -> costly
    assert facts["ef_with_code"].cost == "costly"
    assert facts["ef_with_code"].reason == "unresolved_call:helper"

    # Rating Step -> costly builder:ratingStep
    assert facts["rs"].cost == "costly"
    assert facts["rs"].slice_transparent is False
    assert facts["rs"].reason == "builder:ratingStep"

    # Data Input without code is absent from the mapping
    assert "di_no_code" not in facts

    # Data Input with post-load code df = df.unique() -> costly
    assert facts["di_with_code"].cost == "costly"
    assert facts["di_with_code"].reason == "costly:unique"


def test_recompute_facts_by_node_raises_when_node_declares_no_recompute_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._registry import NODE_REGISTRY

    entry = NODE_REGISTRY[NodeType.OUTPUT]
    monkeypatch.setattr(entry, "recompute_cost", None)
    nodes = {
        "out": GraphNode(
            id="out",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(label="out", nodeType=NodeType.OUTPUT, config={}),
        ),
    }
    with pytest.raises(RuntimeError, match="NodeType 'output' declares no recompute cost"):
        recompute_facts_by_node(["out"], nodes, relevant_edges=())


@pytest.mark.parametrize(
    (
        "code",
        "preamble",
        "expected_cost",
        "expected_transparent",
        "expected_full_input",
        "expected_reason",
    ),
    [
        (
            "df = df.select(cs.numeric().rank())",
            "import polars.selectors as cs",
            "costly",
            False,
            True,
            "costly:rank",
        ),
        (
            "df = df.with_columns(cs.numeric().sum().over('k'))",
            "import polars.selectors as cs",
            "costly",
            False,
            True,
            "costly:over",
        ),
        (
            "df = df.select(cs.numeric())",
            "import polars.selectors as cs",
            "cheap",
            True,
            False,
            "cheap",
        ),
        (
            "df = df.select(pl.selectors.numeric().rank())",
            "",
            "costly",
            False,
            True,
            "costly:rank",
        ),
        (
            "df = df.with_columns(pl.selectors.numeric().sum().over('k'))",
            "",
            "costly",
            False,
            True,
            "costly:over",
        ),
        (
            "df = df.select(pl.selectors.numeric())",
            "",
            "cheap",
            True,
            False,
            "cheap",
        ),
    ],
)
def test_selector_expression_recompute_facts(
    code: str,
    preamble: str,
    expected_cost: str,
    expected_transparent: bool,
    expected_full_input: bool,
    expected_reason: str,
) -> None:
    facts = code_recompute_facts(code, frozenset({"quotes", "claims"}), preamble=preamble)
    assert facts.cost == expected_cost
    assert facts.slice_transparent is expected_transparent
    assert facts.full_input_work is expected_full_input
    assert facts.reason == expected_reason


def test_costly_builder_keeps_code_full_input_work() -> None:
    nodes = {
        "ms_with_code": GraphNode(
            id="ms_with_code",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(
                label="ms_with_code",
                nodeType=NodeType.MODEL_SCORE,
                config={"code": 'df = df.sort("prediction")'},
            ),
        ),
        "ms_no_code": GraphNode(
            id="ms_no_code",
            type="custom",
            position={"x": 0, "y": 0},
            data=NodeData(
                label="ms_no_code",
                nodeType=NodeType.MODEL_SCORE,
                config={},
            ),
        ),
    }
    facts = recompute_facts_by_node(
        ["ms_with_code", "ms_no_code"],
        nodes,
        relevant_edges=(),
    )
    assert facts["ms_with_code"].cost == "costly"
    assert facts["ms_with_code"].slice_transparent is False
    assert facts["ms_with_code"].full_input_work is True
    assert facts["ms_with_code"].reason == "builder:modelScore"

    assert facts["ms_no_code"].cost == "costly"
    assert facts["ms_no_code"].slice_transparent is False
    assert facts["ms_no_code"].full_input_work is False
    assert facts["ms_no_code"].reason == "builder:modelScore"
