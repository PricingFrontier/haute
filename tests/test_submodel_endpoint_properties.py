"""Property-based tests for submodel endpoint conservation and flattening.

Graph/source family (ENG-T11): generated child definitions (an identity chain
behind one public input and one public output port), one or two occurrences,
and authored parent connections drawn from the legal public-port forms and the
private child-endpoint forms. Parsing conserves exactly the legal edges and
rejects any private endpoint as dangling with its authored identity in authored
order (the ENG-T07 contract); flattening a legal graph yields one qualified
runtime node per internal node per occurrence, no dangling edge, and every sink
computes the generated source rows. The negative control rewrites one legal
connection into the private form and shows the acceptance flip.
"""

from __future__ import annotations

import itertools
import textwrap
from dataclasses import dataclass
from pathlib import Path

import hypothesis
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from haute.errors import ParseError
from haute.executor import execute_graph
from haute.graph_utils import flatten_graph
from haute.parser import parse_pipeline_file
from tests._property_budget import pr_budget

_case_counter = itertools.count(1)


def _write(tmp_path: Path, name: str, code: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(code))
    return p


# ---------------------------------------------------------------------------
# Generator Construction
# ---------------------------------------------------------------------------


def _build_child_code(chain_nodes: list[str]) -> str:
    first_node = chain_nodes[0]
    last_node = chain_nodes[-1]

    node_defs: list[str] = [
        f"""
@submodel.polars
def {first_node}(source: pl.LazyFrame) -> pl.LazyFrame:
    return source
"""
    ]
    for i in range(1, len(chain_nodes)):
        prev = chain_nodes[i - 1]
        curr = chain_nodes[i]
        node_defs.append(f"""
@submodel.polars
def {curr}({prev}: pl.LazyFrame) -> pl.LazyFrame:
    return {prev}
""")

    nodes_code = "\n".join(node_defs)
    return f"""\
import polars as pl
import haute

submodel = haute.Submodel(
    "child",
    definition_id="child",
    input_ports=[
        {{
            "portId": "source",
            "label": "Source",
            "targets": [{{"nodeId": "{first_node}", "handleId": None}}],
        }}
    ],
    output_ports=[
        {{
            "portId": "result",
            "label": "Result",
            "source": {{"nodeId": "{last_node}", "handleId": None}},
        }}
    ],
)
{nodes_code}
"""


def _build_parent_code(
    child_filename: str,
    ints: list[int],
    occ_count: int,
    rendered_connects: list[str],
) -> str:
    submodel_registrations = [
        f"""
pipeline.submodel(
    {child_filename!r},
    definition_id="child",
    instance_id="submodel__a",
    alias="a",
)
"""
    ]
    # A consumer of an occurrence output names its parameter after the public
    # output port's label ("Result"), exactly as codegen emits it; that is the
    # physical edge input the executor binds after flattening.
    sinks = [
        """
@pipeline.polars
def sink_a(Result: pl.LazyFrame) -> pl.LazyFrame:
    return Result
"""
    ]
    if occ_count == 2:
        submodel_registrations.append(f"""
pipeline.submodel(
    {child_filename!r},
    definition_id="child",
    instance_id="submodel__b",
    alias="b",
    instance_of="submodel__a",
)
""")
        sinks.append("""
@pipeline.polars
def sink_b(Result: pl.LazyFrame) -> pl.LazyFrame:
    return Result
""")

    submodels_code = "\n".join(submodel_registrations)
    sinks_code = "\n".join(sinks)
    connects_code = "\n".join(rendered_connects)

    return f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("main")

@pipeline.polars
def source() -> pl.LazyFrame:
    return pl.LazyFrame({{"x": {ints!r}}})

{sinks_code}
{submodels_code}
{connects_code}
"""


@dataclass(frozen=True)
class _ConnInfo:
    connect_stmt: str
    identity: tuple[str, str, str | None, str | None]
    is_private: bool
    dangling_detail: dict[str, str | None] | None
    legal_parsed_edge: tuple[str, str, str | None, str | None] | None


def _resolve_connection(raw: tuple, chain_nodes: list[str], occ_count: int) -> _ConnInfo:
    tag = raw[0]
    if tag == "legal_in":
        alias = raw[1]
        inst_id = f"submodel__{alias}"
        return _ConnInfo(
            connect_stmt=f'pipeline.connect("source", "{alias}", target_port="source")',
            identity=("source", alias, None, "source"),
            is_private=False,
            dangling_detail=None,
            legal_parsed_edge=("source", inst_id, None, "in__source"),
        )
    if tag == "legal_out":
        alias = raw[1]
        inst_id = f"submodel__{alias}"
        sink_name = f"sink_{alias}"
        return _ConnInfo(
            connect_stmt=f'pipeline.connect("{alias}", "{sink_name}", source_port="result")',
            identity=(alias, sink_name, "result", None),
            is_private=False,
            dangling_detail=None,
            legal_parsed_edge=(inst_id, sink_name, "out__result", None),
        )
    if tag == "private_source_child":
        node_idx = raw[1] % len(chain_nodes)
        target_node = chain_nodes[node_idx]
        return _ConnInfo(
            connect_stmt=f'pipeline.connect("source", "{target_node}")',
            identity=("source", target_node, None, None),
            is_private=True,
            dangling_detail={
                "source": "source",
                "target": target_node,
                "source_handle": None,
                "target_handle": None,
            },
            legal_parsed_edge=None,
        )
    if tag == "private_child_sink":
        node_idx = raw[1] % len(chain_nodes)
        source_node = chain_nodes[node_idx]
        alias = raw[2]
        sink_name = f"sink_{alias}"
        return _ConnInfo(
            connect_stmt=f'pipeline.connect("{source_node}", "{sink_name}")',
            identity=(source_node, sink_name, None, None),
            is_private=True,
            dangling_detail={
                "source": source_node,
                "target": sink_name,
                "source_handle": None,
                "target_handle": None,
            },
            legal_parsed_edge=None,
        )
    if tag == "private_child_child":
        idx1 = raw[1] % len(chain_nodes)
        idx2 = raw[2] % len(chain_nodes)
        node1 = chain_nodes[idx1]
        node2 = chain_nodes[idx2]
        return _ConnInfo(
            connect_stmt=f'pipeline.connect("{node1}", "{node2}")',
            identity=(node1, node2, None, None),
            is_private=True,
            dangling_detail={
                "source": node1,
                "target": node2,
                "source_handle": None,
                "target_handle": None,
            },
            legal_parsed_edge=None,
        )
    raise ValueError(f"Unknown connection tag: {tag}")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_raw_conn_strategy = st.one_of(
    st.tuples(st.just("legal_in"), st.sampled_from(["a", "b"])),
    st.tuples(st.just("legal_out"), st.sampled_from(["a", "b"])),
    st.tuples(st.just("private_source_child"), st.integers(min_value=0, max_value=2)),
    st.tuples(
        st.just("private_child_sink"),
        st.integers(min_value=0, max_value=2),
        st.sampled_from(["a", "b"]),
    ),
    st.tuples(
        st.just("private_child_child"),
        st.integers(min_value=0, max_value=2),
        st.integers(min_value=0, max_value=2),
    ),
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pr_budget(40)
@example(
    chain_name_prefix="transform",
    chain_len=1,
    ints=[1],
    occ_count=1,
    raw_conns=[
        ("private_source_child", 0),
        ("private_child_sink", 0, "a"),
    ],
)
@given(
    chain_name_prefix=st.sampled_from(["n", "transform"]),
    chain_len=st.integers(min_value=1, max_value=3),
    ints=st.lists(st.integers(min_value=-9, max_value=9), min_size=1, max_size=5),
    occ_count=st.integers(min_value=1, max_value=2),
    raw_conns=st.lists(_raw_conn_strategy, min_size=1, max_size=4),
)
def test_private_child_endpoints_are_rejected_with_exactly_the_authored_edges(
    tmp_path: Path,
    chain_name_prefix: str,
    chain_len: int,
    ints: list[int],
    occ_count: int,
    raw_conns: list[tuple],
) -> None:
    case_dir = tmp_path / f"case_{next(_case_counter)}"
    case_dir.mkdir(parents=True, exist_ok=True)

    if chain_name_prefix == "transform" and chain_len == 1:
        chain_nodes = ["transform"]
    else:
        chain_nodes = [f"{chain_name_prefix}{i}" for i in range(1, chain_len + 1)]

    # Filter raw connections to valid occurrences
    valid_aliases = {"a"} if occ_count == 1 else {"a", "b"}
    filtered_raw: list[tuple] = []
    for rc in raw_conns:
        if rc[0] in ("legal_in", "legal_out") and rc[1] not in valid_aliases:
            continue
        if rc[0] == "private_child_sink" and rc[2] not in valid_aliases:
            continue
        filtered_raw.append(rc)
    if not filtered_raw:
        filtered_raw = [("legal_in", "a")]

    # Deduplicate connections in authored order
    seen_identities: set[tuple[str, str, str | None, str | None]] = set()
    conns: list[_ConnInfo] = []
    for rc in filtered_raw:
        info = _resolve_connection(rc, chain_nodes, occ_count)
        if info.identity not in seen_identities:
            seen_identities.add(info.identity)
            conns.append(info)

    child_path = _write(case_dir, "child.py", _build_child_code(chain_nodes))
    rendered_connects = [info.connect_stmt for info in conns]
    parent_path = _write(
        case_dir,
        "main.py",
        _build_parent_code(child_path.name, ints, occ_count, rendered_connects),
    )

    private_conns = [info for info in conns if info.is_private]
    if private_conns:
        expected_dangling = [
            info.dangling_detail for info in private_conns if info.dangling_detail is not None
        ]
        with pytest.raises(ParseError, match="dangling") as exc_info:
            parse_pipeline_file(parent_path)
        assert exc_info.value.context.get("dangling_edges") == expected_dangling
    else:
        parsed = parse_pipeline_file(parent_path)
        expected_edges = {
            info.legal_parsed_edge for info in conns if info.legal_parsed_edge is not None
        }
        actual_edges = {
            (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)
            for edge in parsed.edges
        }
        assert actual_edges == expected_edges


@pr_budget(25)
@example(
    chain_name_prefix="n",
    chain_len=1,
    ints=[0],
    occ_count=1,
)
@given(
    chain_name_prefix=st.sampled_from(["n", "transform"]),
    chain_len=st.integers(min_value=1, max_value=3),
    ints=st.lists(st.integers(min_value=-9, max_value=9), min_size=1, max_size=5),
    occ_count=st.integers(min_value=1, max_value=2),
)
def test_flatten_conserves_rows_through_public_ports(
    tmp_path: Path,
    chain_name_prefix: str,
    chain_len: int,
    ints: list[int],
    occ_count: int,
) -> None:
    case_dir = tmp_path / f"case_{next(_case_counter)}"
    case_dir.mkdir(parents=True, exist_ok=True)

    if chain_name_prefix == "transform" and chain_len == 1:
        chain_nodes = ["transform"]
    else:
        chain_nodes = [f"{chain_name_prefix}{i}" for i in range(1, chain_len + 1)]

    child_path = _write(case_dir, "child.py", _build_child_code(chain_nodes))

    # Wired: source -> occurrence -> sink for every occurrence
    legal_connects = [
        'pipeline.connect("source", "a", target_port="source")',
        'pipeline.connect("a", "sink_a", source_port="result")',
    ]
    aliases = ["a"]
    if occ_count == 2:
        legal_connects.extend(
            [
                'pipeline.connect("source", "b", target_port="source")',
                'pipeline.connect("b", "sink_b", source_port="result")',
            ]
        )
        aliases.append("b")

    parent_path = _write(
        case_dir,
        "main.py",
        _build_parent_code(child_path.name, ints, occ_count, legal_connects),
    )

    parsed = parse_pipeline_file(parent_path)
    flattened = flatten_graph(parsed)

    # Qualified runtime node count: one per internal child node per occurrence
    expected_runtime_count = len(chain_nodes) * occ_count
    runtime_nodes = [node for node in flattened.nodes if node.id.startswith("submodel_runtime/")]
    assert len(runtime_nodes) == expected_runtime_count

    # No dangling edge: every edge endpoint is a node id in the flattened graph
    all_node_ids = {node.id for node in flattened.nodes}
    for edge in flattened.edges:
        assert edge.source in all_node_ids
        assert edge.target in all_node_ids

    # Execute graph to each sink and verify preview rows
    expected_rows = [{"x": v} for v in ints]
    for alias in aliases:
        sink_id = f"sink_{alias}"
        results = execute_graph(flattened, target_node_id=sink_id)
        assert results[sink_id].status == "ok"
        assert results[sink_id].preview == expected_rows


def test_one_private_mutation_of_a_legal_graph_flips_acceptance(
    tmp_path: Path,
) -> None:
    counter = itertools.count(1)

    # Strategy for legal graph configuration + which wire to mutate
    strategy = st.tuples(
        st.sampled_from(["n", "transform"]),
        st.integers(min_value=1, max_value=3),
        st.lists(st.integers(min_value=-9, max_value=9), min_size=1, max_size=5),
        st.integers(min_value=1, max_value=2),
        st.integers(min_value=0, max_value=3),  # wire index to mutate
        st.integers(min_value=0, max_value=2),  # internal child node index
    )

    def predicate(params: tuple) -> bool:
        prefix, chain_len, ints, occ_count, wire_choice, child_choice = params
        case_dir = tmp_path / f"case_{next(counter)}"
        case_dir.mkdir(parents=True, exist_ok=True)

        if prefix == "transform" and chain_len == 1:
            chain_nodes = ["transform"]
        else:
            chain_nodes = [f"{prefix}{i}" for i in range(1, chain_len + 1)]

        mutated_child = chain_nodes[child_choice % len(chain_nodes)]
        child_path = _write(case_dir, "child.py", _build_child_code(chain_nodes))

        legal_connects = [
            'pipeline.connect("source", "a", target_port="source")',
            'pipeline.connect("a", "sink_a", source_port="result")',
        ]
        if occ_count == 2:
            legal_connects.extend(
                [
                    'pipeline.connect("source", "b", target_port="source")',
                    'pipeline.connect("b", "sink_b", source_port="result")',
                ]
            )

        # Step 1: Verify legal graph parses successfully
        legal_parent = _write(
            case_dir,
            "main_legal.py",
            _build_parent_code(child_path.name, ints, occ_count, legal_connects),
        )
        try:
            parsed_legal = parse_pipeline_file(legal_parent)
            if parsed_legal is None:
                return False
        except ParseError:
            return False

        # Step 2: Rewrite exactly one authored legal connection to private form
        chosen_wire = wire_choice % len(legal_connects)
        mutated_connects = list(legal_connects)
        if chosen_wire == 0:
            # Rewrite source -> a to source -> internal child
            mutated_connects[0] = f'pipeline.connect("source", "{mutated_child}")'
            expected_dangling = {
                "source": "source",
                "target": mutated_child,
                "source_handle": None,
                "target_handle": None,
            }
        elif chosen_wire == 1:
            # Rewrite a -> sink_a to internal child -> sink_a
            mutated_connects[1] = f'pipeline.connect("{mutated_child}", "sink_a")'
            expected_dangling = {
                "source": mutated_child,
                "target": "sink_a",
                "source_handle": None,
                "target_handle": None,
            }
        elif chosen_wire == 2:
            # Rewrite source -> b to source -> internal child
            mutated_connects[2] = f'pipeline.connect("source", "{mutated_child}")'
            expected_dangling = {
                "source": "source",
                "target": mutated_child,
                "source_handle": None,
                "target_handle": None,
            }
        else:
            # Rewrite b -> sink_b to internal child -> sink_b
            mutated_connects[3] = f'pipeline.connect("{mutated_child}", "sink_b")'
            expected_dangling = {
                "source": mutated_child,
                "target": "sink_b",
                "source_handle": None,
                "target_handle": None,
            }

        mutated_parent = _write(
            case_dir,
            "main_mutated.py",
            _build_parent_code(child_path.name, ints, occ_count, mutated_connects),
        )

        try:
            parse_pipeline_file(mutated_parent)
            return False
        except ParseError as exc:
            if "dangling" in str(exc) and exc.context.get("dangling_edges") == [expected_dangling]:
                return True
            return False

    found = hypothesis.find(
        strategy,
        predicate,
        settings=pr_budget(25),
    )
    # The found example flips from acceptance to the exact dangling rejection
    # (the predicate is re-run so the assertion stands on its own).
    assert predicate(found), found
