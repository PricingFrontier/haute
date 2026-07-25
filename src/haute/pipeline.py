"""Pipeline and node decorator for haute."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self, cast

import polars as pl

from haute._edge_join import (
    execute_edge_join,
    normalise_edge_join_decorator_kwargs,
    resolve_edge_join_role_indices,
)
from haute._graph_utils import _edge_id
from haute._logging import get_logger
from haute._types import GraphEdge, NodeType
from haute.errors import ConfigError, ExecutionError
from haute.graph_utils import topo_sort_ids

logger = get_logger(component="pipeline")


@dataclass
class Node:
    """A single step in a pipeline."""

    name: str
    description: str
    fn: Callable
    is_source: bool
    config: dict = field(default_factory=dict)
    _input_arity: _InputArity = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._input_arity = _inspect_input_arity(
            self.fn,
            is_source=self.is_source,
            node_name=self.name,
        )

    @property
    def is_deploy_input(self) -> bool:
        """Whether this node is the live API input seeded by :meth:`Pipeline.score`.

        True when the node was registered with the ``@registry.api_input``
        decorator (so ``config['_node_type'] == NodeType.API_INPUT``) or was
        explicitly flagged with ``api_input=True``.  The decorator alone is
        sufficient — a user must not have to *also* pass ``api_input=True``
        for the node to be recognised as the deploy seed.
        """
        if self.config.get("_node_type") == NodeType.API_INPUT:
            return True
        return bool(self.config.get("api_input"))

    @property
    def is_live_switch(self) -> bool:
        """Whether this node is a live/batch switch."""
        return bool(self.config.get("live_switch"))

    @property
    def n_inputs(self) -> int:
        """Minimum number of positional DataFrame inputs the function requires."""
        if self.is_source:
            return 0
        return self.input_arity.min_inputs

    @property
    def input_arity(self) -> _InputArity:
        """Positional DataFrame arity for this node function.

        Pipeline edges are positional DataFrame inputs. Keyword-only config
        parameters and positional parameters with defaults should not require
        extra edges.
        """
        return self._input_arity

    def __call__(self, *dfs: pl.DataFrame) -> pl.DataFrame:
        if self.is_source:
            result: pl.DataFrame = self.fn()
            return result
        arity = self.input_arity
        if len(dfs) == 0:
            raise ValueError(
                f"Node '{self.name}' expects {arity.describe()} input(s) but received none"
            )
        # The number of wired inputs must fit the function's positional arity.
        # Keyword-only parameters are configuration, not edge slots; optional
        # positional parameters may be supplied by extra edges or left to their
        # defaults. Fewer required inputs or too many inputs are fail-loud.
        if not arity.accepts(len(dfs)):
            raise ExecutionError(
                f"Node '{self.name}' accepts {arity.describe()} input(s) but "
                f"{len(dfs)} edge(s) are wired to it. Connect "
                f"{arity.describe()} input(s) with pipeline.connect(...).",
                node=self.name,
                expected=arity.describe(),
                received=len(dfs),
            )
        result = self.fn(*dfs)
        return result


@dataclass(frozen=True)
class _InputArity:
    min_inputs: int
    max_inputs: int | None

    def accepts(self, received: int) -> bool:
        if received < self.min_inputs:
            return False
        return self.max_inputs is None or received <= self.max_inputs

    def describe(self) -> str:
        if self.max_inputs is None:
            return f"at least {self.min_inputs}"
        if self.min_inputs == self.max_inputs:
            return str(self.min_inputs)
        return f"{self.min_inputs}-{self.max_inputs}"


def _inspect_input_arity(
    fn: Callable,
    *,
    is_source: bool,
    node_name: str,
) -> _InputArity:
    """Inspect and freeze the supported positional-input signature once."""
    if is_source:
        return _InputArity(min_inputs=0, max_inputs=0)
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        raise ExecutionError(
            f"Node '{node_name}' has a callable signature that cannot be inspected.",
            node=node_name,
        ) from exc

    positional: list[inspect.Parameter] = []
    has_varargs = False
    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            positional.append(parameter)
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            has_varargs = True
    min_inputs = sum(1 for parameter in positional if parameter.default is inspect.Parameter.empty)
    return _InputArity(
        min_inputs=min_inputs,
        max_inputs=None if has_varargs else len(positional),
    )


@dataclass(frozen=True)
class RegisteredEdge:
    """Internal edge registration including optional port metadata."""

    source: str
    target: str
    source_port: str | None = None
    target_port: str | None = None


def _validate_port(value: str | None, name: str) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a non-empty string or None")
    if value == "":
        raise ValueError(f"{name} must be a non-empty string or None")


class NodeRegistry:
    """Base class for Pipeline and Submodel — shared node/edge registration.

    Provides the ``@registry.node`` decorator, ``connect()`` for wiring
    edges, and read-only ``nodes`` / ``edges`` properties.
    """

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._nodes: list[Node] = []
        self._node_map: dict[str, Node] = {}
        self._edges: list[RegisteredEdge] = []
        self._submodel_files: list[str] = []

    def _register_node(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Internal decorator to register a function as a node.

        Type-specific public decorators (``transform``, ``data_input``, etc.)
        delegate to this method.
        """

        def _register(f: Callable) -> Callable:
            sig = inspect.signature(f)
            params = [p for p in sig.parameters.values() if p.name != "self"]
            is_source = len(params) == 0

            if f.__name__ in self._node_map:
                raise ValueError(
                    f"Duplicate node name '{f.__name__}'. Each node must have a "
                    "unique function name; rename one of the two functions."
                )

            n = Node(
                name=f.__name__,
                description=(f.__doc__ or "").strip(),
                fn=f,
                is_source=is_source,
                config=config,
            )
            self._nodes.append(n)
            self._node_map[n.name] = n
            return f

        if fn is not None:
            return _register(fn)
        return _register

    # -- Type-specific decorators -------------------------------------------

    def api_input(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for API-input nodes."""
        return self._register_node(fn, _node_type=NodeType.API_INPUT, **config)

    def data_input(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for data-input nodes (native-polars-width inputs)."""
        return self._register_node(fn, _node_type=NodeType.DATA_INPUT, **config)

    def data_output(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for data-output nodes (native-polars-width outputs)."""
        return self._register_node(fn, _node_type=NodeType.DATA_OUTPUT, **config)

    def polars(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for polars nodes."""
        return self._register_node(fn, _node_type=NodeType.POLARS, **config)

    def edge_join(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for edge-join nodes."""
        normalised_config = normalise_edge_join_decorator_kwargs(config)
        return self._register_node(fn, _node_type=NodeType.EDGE_JOIN, **normalised_config)

    def _apply_edge_join(
        self,
        node_name: str,
        base: pl.LazyFrame | pl.DataFrame,
        join: pl.LazyFrame | pl.DataFrame,
    ) -> pl.LazyFrame | pl.DataFrame:
        """Apply an edge-join node's registered config to two frames."""
        node = self._node_map.get(node_name)
        if node is None:
            raise ConfigError("edgeJoin runtime node is not registered.", node=node_name)
        if node.config.get("_node_type") != NodeType.EDGE_JOIN:
            raise ConfigError(
                "edgeJoin runtime node must be registered as an edgeJoin.",
                node=node_name,
                node_type=node.config.get("_node_type"),
            )
        return execute_edge_join(base, join, node.config, collect_eager=True)

    def model_score(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for model-score nodes."""
        return self._register_node(fn, _node_type=NodeType.MODEL_SCORE, **config)

    def banding(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for banding nodes."""
        return self._register_node(fn, _node_type=NodeType.BANDING, **config)

    def rating_step(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for rating-step nodes."""
        return self._register_node(fn, _node_type=NodeType.RATING_STEP, **config)

    def output(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for output nodes."""
        return self._register_node(fn, _node_type=NodeType.OUTPUT, **config)

    def explore(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for explore nodes."""
        return self._register_node(fn, _node_type=NodeType.EXPLORE, **config)

    def external_file(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for external-file nodes."""
        return self._register_node(fn, _node_type=NodeType.EXTERNAL_FILE, **config)

    def live_switch(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for live-switch nodes."""
        return self._register_node(fn, _node_type=NodeType.LIVE_SWITCH, **config)

    def modelling(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for modelling (training) nodes."""
        return self._register_node(fn, _node_type=NodeType.MODELLING, **config)

    def optimiser(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for optimiser nodes."""
        return self._register_node(fn, _node_type=NodeType.OPTIMISER, **config)

    def scenario_expander(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for scenario-expander nodes."""
        return self._register_node(fn, _node_type=NodeType.SCENARIO_EXPANDER, **config)

    def optimiser_apply(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for optimiser-apply nodes."""
        return self._register_node(fn, _node_type=NodeType.OPTIMISER_APPLY, **config)

    def constant(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for constant nodes."""
        return self._register_node(fn, _node_type=NodeType.CONSTANT, **config)

    def instance(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for instance nodes (registered as ``polars``).

        ``instanceOf``/``inputMapping`` are resolved by the graph executor and
        code generator, which bake the referenced node's logic into a concrete
        node.  The standalone :meth:`Pipeline.run`/:meth:`Pipeline.score`
        executor cannot resolve those references and will raise loudly rather
        than silently ignore them; provide a real function body if you invoke
        the pipeline object directly.
        """
        return self._register_node(
            fn,
            _node_type=NodeType.POLARS,
            **{**config, "_instance": True},
        )

    def connect(
        self,
        source: str,
        target: str,
        *,
        source_port: str | None = None,
        target_port: str | None = None,
    ) -> Self:
        """Declare an edge: source node's output feeds into target node.

        ``source_port`` names the output port on the source node for
        multi-port nodes; ``target_port`` names the input port on the
        target node (e.g. an edge-join's "base"/"join"). ``None`` (the
        default) means the single default port. Codegen emits these
        keywords for port-aware edges.

        Can be chained: ``registry.connect("a", "b").connect("b", "c")``
        """
        if source not in self._node_map:
            raise ValueError(
                f"Source node '{source}' not found in pipeline. Known nodes: {list(self._node_map)}"
            )
        if target not in self._node_map:
            raise ValueError(
                f"Target node '{target}' not found in pipeline. Known nodes: {list(self._node_map)}"
            )
        _validate_port(source_port, "source_port")
        _validate_port(target_port, "target_port")
        self._edges.append(
            RegisteredEdge(
                source=source,
                target=target,
                source_port=source_port,
                target_port=target_port,
            )
        )
        return self

    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes)

    @property
    def edges(self) -> list[tuple[str, str]]:
        return [(edge.source, edge.target) for edge in self._edges]

    @property
    def edge_ports(self) -> list[str | None]:
        """Source port for each edge, parallel to :attr:`edges` (None = default output)."""
        return [edge.source_port for edge in self._edges]


class Pipeline(NodeRegistry):
    """A haute pricing pipeline - a DAG of decorated nodes.

    Nodes are functions. Edges define data flow: the output DataFrame
    of the source node is passed as the input to the target node.

    Usage:
        pipeline = Pipeline("main")

        # Folder-backed types (data_input, external_file, …) reference a
        # JSON sidecar rather than inline kwargs, matching the scaffold and
        # what the parser accepts:
        @pipeline.data_input(config="config/data_input/read_data.json")
        def read_data() -> pl.DataFrame: ...

        @pipeline.polars
        def transform(df: pl.DataFrame) -> pl.DataFrame: ...

        @pipeline.output
        def result(df: pl.DataFrame) -> pl.DataFrame: ...

        pipeline.connect("read_data", "transform").connect("transform", "result")
        result = pipeline.run()
    """

    def _topo_order(self) -> list[Node]:
        """Return nodes in topological order based on edges."""
        if not self._edges:
            # No explicit edges - fall back to registration order
            return list(self._nodes)

        node_ids = [n.name for n in self._nodes]
        edges = [
            GraphEdge(
                id=_edge_id(edge.source, edge.target, edge.source_port, edge.target_port),
                source=edge.source,
                target=edge.target,
                sourceHandle=edge.source_port,
                targetHandle=edge.target_port,
            )
            for edge in self._edges
        ]
        sorted_ids = topo_sort_ids(node_ids, edges)

        if len(sorted_ids) != len(self._nodes):
            missing = {n.name for n in self._nodes} - set(sorted_ids)
            raise ValueError(f"Cycle detected or disconnected nodes: {missing}")

        return [self._node_map[name] for name in sorted_ids if name in self._node_map]

    def _get_inputs(self, node_name: str) -> list[str]:
        """Get the names of all nodes that feed into this node."""
        return [edge.source for edge in self._get_input_edges(node_name)]

    def _get_input_edges(self, node_name: str) -> list[RegisteredEdge]:
        """Get inbound edges in the runtime argument order for *node_name*."""
        incoming = [edge for edge in self._edges if edge.target == node_name]
        node = self._node_map.get(node_name)
        if node is None or node.config.get("_node_type") != NodeType.EDGE_JOIN or not incoming:
            return incoming
        source_ids = [edge.source for edge in incoming]
        target_handles = [edge.target_port for edge in incoming]
        base_index, join_index = resolve_edge_join_role_indices(
            node.config,
            source_ids,
            target_handles,
        )
        return [incoming[base_index], incoming[join_index]]

    def _resolve_output_node(self, order: list[Node]) -> Node:
        """Return the node whose result :meth:`run`/:meth:`score` should return.

        The returned frame is resolved *explicitly*, never as "whichever
        node happens to sort last in topological order":

        1. If any node is declared ``@pipeline.output`` (``NodeType.OUTPUT``),
           exactly one must be — return it, or raise naming them when several
           are declared.
        2. Otherwise fall back to the single terminal (leaf) node — one with
           no outbound edge.  When several leaves exist the result is
           ambiguous (a fan-out), so we raise naming them and instruct the
           user to mark one with ``@pipeline.output``.
        """
        output_nodes = [n for n in order if n.config.get("_node_type") == NodeType.OUTPUT]
        if len(output_nodes) == 1:
            return output_nodes[0]
        if len(output_nodes) > 1:
            raise ExecutionError(
                "Pipeline declares multiple @pipeline.output nodes; exactly one "
                "is allowed so run()/score() return an unambiguous result.",
                outputs=[n.name for n in output_nodes],
            )

        consumed = {edge.source for edge in self._edges}
        leaves = [n for n in order if n.name not in consumed]
        if len(leaves) == 1:
            return leaves[0]
        raise ExecutionError(
            "Pipeline has multiple terminal nodes, so run()/score() cannot "
            "return an unambiguous result. Mark exactly one node with "
            "@pipeline.output (or leave a single leaf node).",
            terminals=[n.name for n in leaves],
        )

    def _execute_transform(self, n: Node, outputs: dict[str, Any]) -> None:
        """Resolve *n*'s wired inputs from *outputs* and store its result.

        Shared by :meth:`run` and :meth:`score`.  Fails loud when the node
        has no inbound edges, when an upstream result is missing, or when the
        node carries unresolved instance references the standalone executor
        cannot honour.
        """
        input_edges = self._get_input_edges(n.name)
        if not input_edges:
            raise ValueError(
                f"Node '{n.name}' has no inbound edges. Use pipeline.connect() to wire it up."
            )
        missing = [edge.source for edge in input_edges if edge.source not in outputs]
        if missing:
            raise ValueError(
                f"Node '{n.name}' is missing input(s) from: {missing}. "
                "Upstream node(s) may have failed or not been registered."
            )
        if n.config.get("_instance") or n.config.get("instanceOf") or n.config.get("inputMapping"):
            raise ExecutionError(
                "Standalone run()/score() cannot resolve an @pipeline.instance "
                "node's 'instanceOf'/'inputMapping' references. Run the pipeline through "
                "the graph executor, or inline the referenced logic into this node.",
                node=n.name,
            )
        from haute._execute_lazy import _pick_source_frame

        input_dfs: list[pl.DataFrame] = [
            cast(
                pl.DataFrame,
                _pick_source_frame(
                    outputs[edge.source],
                    GraphEdge(
                        id=_edge_id(edge.source, edge.target, edge.source_port, edge.target_port),
                        source=edge.source,
                        target=edge.target,
                        sourceHandle=edge.source_port,
                        targetHandle=edge.target_port,
                    ),
                ),
            )
            for edge in input_edges
        ]
        outputs[n.name] = n(*input_dfs)

    def run(self) -> pl.DataFrame:
        """Execute the full pipeline, following edges for data flow."""
        from haute._model_scorer import _scenario_ctx

        if not self._nodes:
            raise ValueError("Pipeline has no nodes")

        _token = _scenario_ctx.set("batch")
        try:
            order = self._topo_order()
            outputs: dict[str, Any] = {}

            for n in order:
                if n.is_source:
                    outputs[n.name] = n()
                else:
                    self._execute_transform(n, outputs)

            return cast(pl.DataFrame, outputs[self._resolve_output_node(order).name])
        finally:
            _scenario_ctx.reset(_token)

    def score(self, df: pl.DataFrame | dict[str, pl.DataFrame]) -> pl.DataFrame:
        """Run the pipeline on an input DataFrame, seeding the live input.

        Sources marked as the live API input — via the ``@pipeline.api_input``
        decorator or an explicit ``api_input=True`` — are seeded with *df*;
        every other source runs its own load logic (e.g. static rating
        tables).

        When *no* source is marked, *df* seeds the source only if there is
        exactly one (unambiguous).  With multiple unmarked sources the seed
        target cannot be inferred, so we raise rather than silently seed every
        source with the same frame — mark the live input with
        ``@pipeline.api_input``.
        """
        from haute._model_scorer import _scenario_ctx

        _token = _scenario_ctx.set("live")
        try:
            order = self._topo_order()
            outputs: dict[str, Any] = {}

            sources = [n for n in order if n.is_source]
            deploy_inputs = [n for n in sources if n.is_deploy_input]
            if len(deploy_inputs) > 1:
                raise ExecutionError(
                    "score() found multiple live input sources. Mark exactly "
                    "one source with @pipeline.api_input or api_input=True.",
                    sources=[n.name for n in deploy_inputs],
                )
            if not deploy_inputs and len(sources) > 1:
                raise ExecutionError(
                    "score() cannot infer which source receives the input "
                    "DataFrame: no source is marked as the live input and there "
                    "is more than one. Mark exactly one source with "
                    "@pipeline.api_input.",
                    sources=[n.name for n in sources],
                )

            seed_nodes = deploy_inputs or sources
            seed_names = {n.name for n in seed_nodes}
            connected_ports = list(
                dict.fromkeys(
                    edge.source_port
                    for edge in self._edges
                    if edge.source in seed_names and edge.source_port is not None
                )
            )
            if isinstance(df, dict):
                expected_ports = set(connected_ports)
                supplied_ports = set(df)
                if not expected_ports:
                    unknown_ports = sorted(supplied_ports)
                    raise ExecutionError(
                        "score() received a frame dictionary for a source with no connected ports.",
                        source_nodes=sorted(seed_names),
                        unknown_ports=unknown_ports,
                    )
                missing_ports = sorted(expected_ports - supplied_ports)
                unknown_ports = sorted(supplied_ports - expected_ports)
                if missing_ports or unknown_ports:
                    raise ExecutionError(
                        "score() frame dictionary must match the connected source ports exactly.",
                        connected_ports=connected_ports,
                        missing_ports=missing_ports,
                        unknown_ports=unknown_ports,
                    )
                seed_value: Any = df
            else:
                if len(connected_ports) > 1:
                    raise ExecutionError(
                        "score() cannot seed a bare DataFrame across multiple "
                        "connected source ports; provide a frame dictionary.",
                        connected_ports=connected_ports,
                    )
                seed_value = df
            for n in sources:
                if n.name in seed_names:
                    outputs[n.name] = seed_value
                else:
                    # Not a deploy input - run its own load logic.
                    outputs[n.name] = n()

            for n in order:
                if n.is_source:
                    continue
                self._execute_transform(n, outputs)

            return cast(pl.DataFrame, outputs[self._resolve_output_node(order).name])
        finally:
            _scenario_ctx.reset(_token)

    def to_graph(self) -> dict:
        """Convert the pipeline to a React Flow compatible graph."""
        nodes = []
        rf_edges = []
        x_spacing = 280

        for i, n in enumerate(self._nodes):
            is_last = i == len(self._nodes) - 1
            if n.config.get("_node_type"):
                rf_type = n.config["_node_type"]
            elif n.is_source:
                rf_type = NodeType.DATA_INPUT
            elif is_last:
                rf_type = NodeType.OUTPUT
            else:
                rf_type = NodeType.POLARS

            nodes.append(
                {
                    "id": n.name,
                    "type": rf_type,
                    "position": {"x": i * x_spacing, "y": 0},
                    "data": {
                        "label": n.name.replace("_", " ").title(),
                        "description": n.description,
                        "nodeType": rf_type,
                        "config": {k: v for k, v in n.config.items() if not k.startswith("_")},
                    },
                }
            )

        for edge in self._edges:
            rf_edges.append(
                {
                    "id": _edge_id(edge.source, edge.target, edge.source_port, edge.target_port),
                    "source": edge.source,
                    "target": edge.target,
                    "sourceHandle": edge.source_port,
                    "targetHandle": edge.target_port,
                }
            )

        return {"nodes": nodes, "edges": rf_edges}

    def submodel(self, file: str) -> Pipeline:
        """Import a submodel from a separate .py file.

        The submodel's nodes and internal edges are registered on this
        pipeline so they participate in execution.  The *file* path is
        stored for round-tripping by the code-generator.

        Can be chained: ``pipeline.submodel("a.py").submodel("b.py")``
        """
        self._submodel_files.append(file)
        return self

    @property
    def submodel_files(self) -> list[str]:
        """Paths passed to :meth:`submodel`."""
        return list(self._submodel_files)


class Submodel(NodeRegistry):
    """A reusable group of nodes defined in a separate .py file.

    Mirrors :class:`Pipeline`'s node/edge registration surface but is
    intended for submodel files that the main pipeline imports via
    ``pipeline.submodel("modules/x.py")``.  A ``Submodel`` is **not executed
    directly** — it has no ``run``/``score``/``to_graph`` of its own; its
    nodes and internal edges participate in execution once registered onto the
    importing :class:`Pipeline`.

    Usage::

        submodel = haute.Submodel("model_scoring")

        # A source node so downstream connects resolve; folder-backed types
        # reference a JSON sidecar, matching the scaffold and the parser.
        @submodel.data_input(config="config/data_input/policies.json")
        def policies() -> pl.LazyFrame: ...

        @submodel.external_file(config="config/external_file/frequency_model.json")
        def frequency_model(policies: pl.LazyFrame) -> pl.LazyFrame: ...

        submodel.connect("policies", "frequency_model")
    """
