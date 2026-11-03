"""One rule binding a node's parameters to its inputs (READ-01).

The parser and a standalone :class:`~haute.pipeline.Pipeline` run both bind
every node through :func:`bind_node_inputs`, so ``haute run``, a plain
``import main`` and a deployed scorer wire a node alike. A node's positional
parameters name the inputs it reads:

- a Polars node, a declaration and an External File: every positional
  parameter names one input (through the node's ``inputMapping``, when it
  declares one);
- a frame hook (Rating Step, Model Score, Scenario Expander, Explore) takes
  as ``df`` the frame its decorator makes from its first input, so that input
  is named by no parameter, and the parameters after ``df`` name the others;
- a Data Input hook takes as ``df`` the frame its decorator loads: it is a
  source, and ``df`` names no input.

The inputs are a node's incoming connections. One that a ``connect()`` call
states is *stated*; one inferred from a parameter naming its source node, or
received through a submodel's input port, is not. Four shapes are refused,
naming the node and the parameter: a parameter that names no input, an input
no parameter names and the decorator does not take, a parameter that names an
input no ``connect()`` states while a stated input goes unnamed, and stated
inputs whose ``connect()`` calls come in another order than the parameters
read them. An input with a target port (an Edge Join's ``base`` and ``join``,
a Workbench Output's table) binds by its port, so its call's order is free.

An input's name is its executable name, which spells a non-ASCII letter as
``_x<hex>_``. A Polars node's or External File's parameter is a variable its
code reads, so it names its input exactly; any other node's parameter only
names the input, so ``données`` names the input ``donn_xe9_es`` too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from haute._graph_utils import _sanitize_func_name
from haute._types import NodeType
from haute.errors import HauteError, ParseError

__all__ = ["NodeInput", "bind_node_inputs", "input_parameters"]

#: Node types whose ``df``-first function is a hook on the decorator's frame.
_FRAME_HOOK_TYPES: frozenset[NodeType] = frozenset(
    {NodeType.RATING_STEP, NodeType.MODEL_SCORE, NodeType.SCENARIO_EXPANDER, NodeType.EXPLORE}
)

#: Node types whose code reads each input under its parameter's name.
_CODE_READS_PARAMETERS: frozenset[NodeType] = frozenset({NodeType.POLARS, NodeType.EXTERNAL_FILE})

_REMEDIATION = (
    "Name each parameter after the node or table connected to it, in the order of the "
    "node's connect() calls, or declare inputMapping={logical: connected} on the decorator."
)


@dataclass(frozen=True, slots=True)
class NodeInput:
    """One incoming connection of a node.

    *name* is the name a parameter gives it: its source node's name, a
    Workbench or Quote Input's table, or a submodel port's name. *stated* is
    whether a ``connect()`` call in the node's own file states it.
    """

    name: str
    target_port: str | None = None
    stated: bool = True


def input_parameters(
    node_type: NodeType | str, positional: Sequence[str]
) -> tuple[list[str], bool]:
    """The parameters that name *node_type*'s inputs, and whether its decorator takes the first.

    A Data Input or frame hook's ``df`` names no input; a frame hook's
    decorator takes its first input to make ``df``.
    """
    params = list(positional)
    if params[:1] != ["df"]:
        return params, False
    if node_type == NodeType.DATA_INPUT:
        return params[1:], False
    if node_type in _FRAME_HOOK_TYPES:
        return params[1:], True
    return params, False


def _listed(noun: str, names: Sequence[str]) -> str:
    """``parameter 'a'`` or ``parameters 'a', 'b'``."""
    return (noun if len(names) == 1 else f"{noun}s") + " " + ", ".join(map(repr, names))


def _resolve(param: str, input_mapping: Mapping[str, str] | None) -> str:
    if input_mapping is None:
        return param
    resolved = input_mapping.get(param, param)
    return resolved if isinstance(resolved, str) else param


def bind_node_inputs(
    *,
    node_id: str,
    node_type: NodeType | str,
    positional: Sequence[str],
    inputs: Sequence[NodeInput],
    input_mapping: Mapping[str, str] | None = None,
    source_file: str | None = None,
    error: type[HauteError] = ParseError,
) -> list[int]:
    """The indices of *inputs* in the order the node's parameters read them.

    *inputs* are in connection order: the node's ``connect()`` calls in file
    order, then the inputs that are not stated. A frame hook's first input
    comes first. Raises *error* naming the node and its parameters when they
    do not bind (see the module docstring).
    """
    naming, takes_first = input_parameters(node_type, positional)
    claimed: dict[int, str] = {}
    read: list[int] = []
    unbound: list[str] = []
    exact = node_type in _CODE_READS_PARAMETERS
    for param in naming:
        name = _resolve(param, input_mapping)
        names = {name} if exact else {name, _sanitize_func_name(name)}
        index = next(
            (
                position
                for position, candidate in enumerate(inputs)
                if candidate.name in names and position not in claimed
            ),
            None,
        )
        if index is None:
            unbound.append(param)
            continue
        claimed[index] = param
        read.append(index)

    unclaimed = [position for position in range(len(inputs)) if position not in claimed]
    taken: list[int] = []
    takes_named: list[str] = []
    if takes_first and inputs:
        if unclaimed:
            taken = [unclaimed.pop(0)]
        else:
            # Every input is named, leaving the decorator none to make df from.
            takes_named = [claimed[0]]
    unconsumed = [inputs[position].name for position in unclaimed]
    unstated_params = [claimed[position] for position in read if not inputs[position].stated]
    stated_unnamed = [inputs[position].name for position in unclaimed if inputs[position].stated]
    disagreeing = [
        {"parameter": param, "connected": name}
        for param, name in zip(unstated_params, stated_unnamed, strict=False)
    ]

    order = taken + read
    positional_inputs = [
        position
        for position, candidate in enumerate(inputs)
        if candidate.stated and candidate.target_port is None
    ]
    expected = [position for position in order if position in positional_inputs]
    out_of_order = not (unbound or unconsumed or takes_named) and expected != positional_inputs

    if not (unbound or unconsumed or takes_named or out_of_order):
        return order

    reasons = [
        f"parameter {item['parameter']!r} names an input no connect() states, "
        f"while connect() connects {item['connected']!r}"
        for item in disagreeing
    ]
    if unbound:
        reasons.append(
            _listed("parameter", unbound)
            + (" names" if len(unbound) == 1 else " name")
            + " no input connected to it"
        )
    disagreeing_inputs = {item["connected"] for item in disagreeing}
    unnamed = [name for name in unconsumed if name not in disagreeing_inputs]
    if unnamed:
        reasons.append(
            _listed("input", unnamed)
            + (" is" if len(unnamed) == 1 else " are")
            + " connected but no parameter names "
            + ("it" if len(unnamed) == 1 else "them")
        )
    if takes_named:
        reasons.append(
            f"parameter {takes_named[0]!r} names the first input, which the decorator takes "
            "as df; leave it unnamed"
        )
    if out_of_order:
        reasons.append(
            "its connect() calls come in the order "
            + ", ".join(repr(inputs[position].name) for position in positional_inputs)
            + ", but its parameters read "
            + ", ".join(repr(inputs[position].name) for position in expected)
        )
    located = {} if source_file is None else {"source_file": source_file}
    raise error(
        f"The parameters of node {node_id!r} do not match its inputs: " + "; ".join(reasons) + ".",
        node_id=node_id,
        unbound_parameters=[
            *unbound,
            *(item["parameter"] for item in disagreeing),
            *takes_named,
        ],
        unconsumed_inputs=unconsumed,
        disagreeing_parameters=disagreeing,
        connected_inputs=[candidate.name for candidate in inputs],
        parameter_order=[inputs[position].name for position in expected],
        connect_order=[inputs[position].name for position in positional_inputs],
        remediation=_REMEDIATION,
        **located,
    )
