"""One-off conversion (READ-28): constants that only formulas read become global constants.

Run once on a project, in a commit of its own, after ``read_22_polars_code.py``:

    uv run python scripts/one_off/read_28_promote_constants.py <project> [...]

A Constant node joined to every row of one stepped Polars node's input, for that node's
formulas alone, costs a Constant, a cross join, their ``connect()`` calls, a sidecar and
two positions. Such a node is promoted: its constants become global constants, which the
formulas read as ``global_constants.<name>``, and the node and its join go. It is promoted
only when all of these hold, else it is left and the failed condition reported:

- the Constant's one consumer is a cross join with no keys, whose one consumer is a
  stepped Polars node that starts from it;
- in that node every reference to a constant column is a column in a formula (a
  with_column step's arithmetic, values and functions), outside any group-by, window or
  condition;
- no step writes a constant column, and no code step follows the join;
- while a constant column is in the frame, no step brings rows or values into it without
  naming it: no concat, join, cast, fill-null, pivot or unpivot, so each row's constant
  value is the Constant's own wherever a formula reads it;
- no constant column reaches the node's output: a later step selects it out or drops it;
- each constant's name is a valid global constant name that no global constant has.

A global constant keeps the value's runtime type: a value that reads as a number is a
number, as the Constant node runs it, and any other is text. After the change every node
must give the same output columns, types and values as before, or the project's files are
put back and the node reported. Haute keeps no copy of this conversion; the file is
removed once the owner has run it.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from haute._config_io import global_constants_json
from haute._project_context import standalone_project_scope
from haute._types import GLOBAL_CONSTANTS_FILE, GlobalConstant, NodeType
from haute.codegen import graph_to_code
from haute.graph_utils import GraphEdge, GraphNode, PipelineGraph

#: Step kinds that can bring rows, or values, into a column without naming it: appended or
#: joined rows hold their own value there, and a cast, a fill or a reshape changes it.
_REACHING_KINDS = frozenset({"concat", "join", "cast", "fill_null", "pivot", "unpivot"})

#: The expression types a formula is made of, where a column and a scalar behave alike.
FORMULA_TYPES = frozenset({"binary", "operand", "function"})


class NotPromotedError(Exception):
    """A Constant node the conversion leaves, and why."""


def _constants(node: GraphNode) -> dict[str, Any]:
    return {
        entry["name"]: entry.get("value", "")
        for entry in node.data.config.get("values", [])
        if entry.get("name")
    }


def _global(name: str, value: Any) -> GlobalConstant:
    """*value* as the Constant node runs it: a number when it reads as one, else text."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return GlobalConstant(name=name, type="text", value=str(value))
    return GlobalConstant(name=name, type="float", value=number)


def _occurrences(value: Any, names: set[str], path: tuple[Any, ...] = ()) -> Iterator[tuple]:
    """Each place in *value* a string of *names* sits, by its path."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("id", "kind", "type", "op", "operator", "text", "comment"):
                continue
            yield from _occurrences(item, names, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _occurrences(item, names, (*path, index))
    elif isinstance(value, str) and value in names:
        yield path


def _in_formula(step: dict[str, Any], path: tuple[Any, ...]) -> bool:
    """Whether *path* in *step* is a column operand inside a formula step's expression."""
    if step["kind"] != "with_column" or not path or path[0] != "expr" or path[-1] != "name":
        return False
    node: Any = step
    for key in path[:-1]:
        node = node[key]
        if isinstance(node, dict) and "type" in node and node["type"] not in FORMULA_TYPES:
            return False
    return isinstance(node, dict) and node.get("kind") == "column"


def _check(
    graph: PipelineGraph, constant: GraphNode
) -> tuple[GraphNode, GraphNode, GraphEdge, dict[str, Any]]:
    """The join, the stepped node and the base edge of *constant*'s promotion, and its
    constants; raises :class:`NotPromotedError` naming the first condition that fails."""
    edges_from = {node.id: [e for e in graph.edges if e.source == node.id] for node in graph.nodes}
    nodes = {node.id: node for node in graph.nodes}
    consumers = edges_from[constant.id]
    if len(consumers) != 1:
        raise NotPromotedError(f"it has {len(consumers)} consumers, not one")
    join = nodes[consumers[0].target]
    config = join.data.config
    if join.data.nodeType != NodeType.EDGE_JOIN or config.get("how") != "cross":
        raise NotPromotedError(f"its consumer {join.id} is not a cross join")
    if any(config.get(key) for key in ("on", "leftOn", "rightOn")):
        raise NotPromotedError(f"its join {join.id} is keyed")
    into = [edge for edge in graph.edges if edge.target == join.id]
    base = [edge for edge in into if edge.source != constant.id]
    if len(into) != 2 or len(base) != 1:
        raise NotPromotedError(f"its join {join.id} does not join it to one other input")
    after = edges_from[join.id]
    if len(after) != 1:
        raise NotPromotedError(f"its join {join.id} has {len(after)} consumers, not one")
    stepped = nodes[after[0].target]
    steps = stepped.data.config.get("steps")
    if stepped.data.nodeType != NodeType.POLARS or not isinstance(steps, list):
        raise NotPromotedError(f"its join feeds {stepped.id}, which is no stepped Polars node")
    if not steps or steps[0].get("kind") != "source" or steps[0].get("input") != join.id:
        raise NotPromotedError(f"{stepped.id} does not start from its join {join.id}")
    values = _constants(constant)
    names = set(values)
    taken = {held.name for held in graph.global_constants}
    for name in sorted(names):
        if name in taken:
            raise NotPromotedError(f"the global constant {name!r} is taken")
        try:
            _global(name, values[name])
        except ValueError as error:
            raise NotPromotedError(f"{name!r} is no valid global constant name: {error}") from None
    live = set(names)
    for number, step in enumerate(steps[1:], start=2):
        kind = step["kind"]
        if kind == "free_code":
            raise NotPromotedError(f"{stepped.id}'s step {number} is code, which Haute cannot read")
        if kind in _REACHING_KINDS and live:
            raise NotPromotedError(
                f"{stepped.id}'s step {number} ({kind}) can bring rows or values in "
                f"{sorted(live)[0]!r}"
            )
        if kind == "with_column" and step.get("name") in names:
            raise NotPromotedError(
                f"{stepped.id}'s step {number} writes the column {step['name']!r}"
            )
        if kind == "rename" and names & {entry.get("to") for entry in step.get("renames", [])}:
            raise NotPromotedError(f"{stepped.id}'s step {number} renames a column to a constant's")
        for path in _occurrences(step, names):
            if kind == "drop" and path[:1] == ("columns",):
                continue
            if _in_formula(step, path):
                continue
            where = "an aggregation" if kind in ("group_by", "pivot") else "no formula"
            if kind == "with_column" and step["expr"].get("type") == "window":
                where = "an aggregation"
            raise NotPromotedError(f"{stepped.id}'s step {number} reads a constant in {where}")
        if kind == "select":
            if step.get("dtypes"):
                raise NotPromotedError(f"{stepped.id}'s step {number} selects columns by type")
            live &= set(step.get("columns", []))
        elif kind == "drop":
            live -= set(step.get("columns", []))
    if live:
        raise NotPromotedError(
            f"the constant column {sorted(live)[0]!r} reaches {stepped.id}'s output"
        )
    return join, stepped, base[0], values


def _formula_constants(step: dict[str, Any], names: set[str]) -> dict[str, Any]:
    """*step* reading each constant of *names* as a global constant."""
    rewritten = copy.deepcopy(step)
    for path in _occurrences(step, names):
        if _in_formula(step, path):
            operand: Any = rewritten
            for key in path[:-1]:
                operand = operand[key]
            operand["kind"] = "constant"
    return rewritten


def promote(graph: PipelineGraph) -> tuple[PipelineGraph, list[str], list[str]]:
    """*graph* with each Constant node that qualifies promoted; the promoted nodes, and a
    line for each left, naming why."""
    promoted: list[str] = []
    left: list[str] = []
    for constant in [node for node in graph.nodes if node.data.nodeType == NodeType.CONSTANT]:
        try:
            join, stepped, base, values = _check(graph, constant)
        except NotPromotedError as reason:
            left.append(f"{constant.id} stays: {reason}")
            continue
        names = set(values)
        steps = [
            {**_formula_constants(step, names), "input": base.source}
            if step["kind"] == "source"
            else _formula_constants(step, names)
            for step in stepped.data.config["steps"]
        ]
        steps = [
            {**step, "columns": [c for c in step["columns"] if c not in names]}
            if step["kind"] == "drop"
            else step
            for step in steps
        ]
        steps = [s for s in steps if s["kind"] != "drop" or s["columns"] or s.get("dtypes")]
        gone = {constant.id, join.id}
        nodes = [
            stepped.with_config({**stepped.data.config, "steps": steps})
            if node.id == stepped.id
            else node
            for node in graph.nodes
            if node.id not in gone
        ]
        edges = [e for e in graph.edges if e.source not in gone and e.target not in gone]
        edges.append(
            GraphEdge(id=f"e_{base.source}_{stepped.id}", source=base.source, target=stepped.id)
        )
        constants = [*graph.global_constants, *(_global(n, v) for n, v in values.items())]
        graph = graph.model_copy(
            update={"nodes": nodes, "edges": edges, "global_constants": constants}
        )
        promoted.append(constant.id)
    return graph, promoted, left


def _outputs(graph: PipelineGraph) -> dict[str, Any]:
    from haute.executor import execute_graph

    return {
        node_id: (result.status, result.columns, result.preview)
        for node_id, result in execute_graph(graph).items()
    }


def convert(pipeline: Path) -> list[str]:
    """*pipeline*'s Constant nodes promoted where they qualify, its files written, every node
    checked to give the same outputs; what was done and left. It runs in the project
    holding *pipeline*."""
    with standalone_project_scope(pipeline):
        return _convert(pipeline)


def _convert(pipeline: Path) -> list[str]:
    from haute.parser import parse_pipeline_file

    before = parse_pipeline_file(pipeline)
    after, promoted, left = promote(before)
    if not promoted:
        return left
    old, new = _outputs(before), _outputs(after)
    changed = sorted(node_id for node_id, result in new.items() if old.get(node_id) != result)
    if changed:
        return [*left, f"nothing promoted: {', '.join(changed)} would give other outputs"]
    source = pipeline.read_bytes().decode("utf-8")
    code = graph_to_code(
        after,
        pipeline_name=after.pipeline_name or "main",
        description=after.pipeline_description or "",
        preamble=after.preamble or "",
        preserved_blocks=after.preserved_blocks or None,
        existing_source=source,
    )
    pipeline.write_bytes(code.encode("utf-8"))
    (pipeline.parent / GLOBAL_CONSTANTS_FILE).parent.mkdir(parents=True, exist_ok=True)
    (pipeline.parent / GLOBAL_CONSTANTS_FILE).write_bytes(
        global_constants_json(after.global_constants).encode("utf-8")
    )
    for name in promoted:
        sidecar = pipeline.parent / "config" / "constant" / f"{name}.json"
        if sidecar.is_file():
            sidecar.unlink()
    return [*(f"{name} promoted to global constants" for name in promoted), *left]


if __name__ == "__main__":
    import tomllib

    for project in (Path(arg) for arg in sys.argv[1:]):
        settings = tomllib.loads((project / "haute.toml").read_text(encoding="utf-8"))
        for line in convert(project / settings["project"]["pipeline"]):
            print(f"{project}: {line}")
