"""Pipeline and node decorator for haute."""

from __future__ import annotations

import dataclasses
import functools
import inspect
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self, cast

import polars as pl

from haute._edge_join import (
    normalise_edge_join_decorator_kwargs,
    resolve_edge_join_role_indices,
)
from haute._executable_names import (
    ROOT_MODULE,
    NameParty,
    NameViolation,
    reserved_or_builtin,
)
from haute._global_constants import (
    STANDALONE_GLOBAL_CONSTANTS,
    GlobalConstantsNamespace,
    bind_function_view,
)
from haute._graph_utils import _edge_id, _sanitize_func_name
from haute._logging import get_logger
from haute._standalone_nodes import (
    SOURCE_NODE_TYPES,
    FunctionKind,
    function_kind,
    is_configured,
    run_configured_node,
)
from haute._submodel_paths import is_pipeline_dir
from haute._types import (
    GLOBAL_CONSTANTS_FILE,
    GraphEdge,
    NodeType,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelOutputPort,
)
from haute.errors import ExecutionError
from haute.graph_utils import topo_sort_ids

logger = get_logger(component="pipeline")


@dataclass
class Node:
    """A single step in a pipeline.

    A ``polars`` node's function is its transform. Every other node type is
    configured: its decorator performs the node's work, and its function is
    either a declaration (never called) or a hook that receives the work's
    result as ``df`` (see :mod:`haute._standalone_nodes`).
    """

    name: str
    description: str
    fn: Callable
    is_source: bool
    config: dict = field(default_factory=dict)
    #: From the defining file to its pipeline's directory, where ``config=``
    #: paths resolve: ``.`` except in a submodel definition file.
    pipeline_dir: str = "."
    kind: FunctionKind = field(init=False, repr=False)
    _input_arity: _InputArity = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.kind = function_kind(self.node_type, self.fn, self.name, self.config)
        self._input_arity = _inspect_input_arity(
            self.fn,
            is_source=self.is_source,
            node_name=self.name,
        )

    @property
    def node_type(self) -> NodeType:
        """The node's type, from its decorator (``polars`` when it has none)."""
        return NodeType(self.config.get("_node_type", NodeType.POLARS))

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

    def _raise_if_unresolved_instance(self) -> None:
        if self.config.get("_instance") or self.config.get("instanceOf"):
            raise ExecutionError(
                "Standalone run()/score() cannot resolve an @pipeline.instance "
                "node's 'instanceOf' reference. Run the pipeline through "
                "the graph executor, or inline the referenced logic into this node.",
                node=self.name,
            )

    def _run_configured(self, fn: Callable, frames: tuple[pl.DataFrame, ...]) -> pl.DataFrame:
        result: pl.DataFrame = run_configured_node(
            self.node_type,
            self.kind,
            name=self.name,
            config=self.config,
            fn=fn,
            frames=frames,
            pipeline_dir=self.pipeline_dir,
        )
        return result

    def __call__(self, *dfs: pl.DataFrame) -> pl.DataFrame:
        return self._invoke(dfs, None)

    def _invoke(
        self,
        dfs: tuple[pl.DataFrame, ...],
        constants: GlobalConstantsNamespace | None,
    ) -> pl.DataFrame:
        """Run the node on *dfs*, its function reading *constants* when a run gives them."""
        self._raise_if_unresolved_instance()
        fn = self.fn if constants is None else bind_function_view(self.fn, constants)
        if self.is_source:
            if dfs:
                raise ExecutionError(
                    f"Node '{self.name}' is a source and takes no input frames, "
                    f"but received {len(dfs)}.",
                    node=self.name,
                    received=len(dfs),
                )
            if self.kind != "transform":
                return self._run_configured(fn, ())
            result: pl.DataFrame = fn()
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
        if self.kind != "transform":
            return self._run_configured(fn, dfs)
        result = fn(*dfs)
        return result


def _runs_node(node: Node) -> Callable[..., pl.DataFrame]:
    """What a configured node's decorator returns: a callable that runs the node."""

    @functools.wraps(node.fn)
    def run(*frames: pl.DataFrame) -> pl.DataFrame:
        return node(*frames)

    return run


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


@dataclass(frozen=True)
class RegisteredSubmodel:
    """One canonical file-backed submodel occurrence registered on a pipeline."""

    file: str
    name: str
    instance_of: str | None = None
    #: The module file whose code made the registration, which ``file`` is relative to.
    registered_in: str | None = None


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
        self._submodel_registrations: list[RegisteredSubmodel] = []
        self._pipeline_dir = "."

    @property
    def global_constants(self) -> Any:
        """What module-level code reads as ``global_constants``.

        A generated pipeline or submodel file binds this right after its
        constructor so node code reads a defined name. Outside a node
        function's run it is a sentinel whose every read says where constants
        are read.
        """
        return STANDALONE_GLOBAL_CONSTANTS

    def _refuse_unavailable_node_name(self, f: Callable) -> None:
        """Refuse a node name the executable-name rule refuses, naming the node.

        A reserved or built-in name, one another node or submodel occurrence
        takes ignoring case, or one the module already binds to something
        other than *f* (a preamble helper, say): the runner would rebind it
        for every later node body. A function defined inside another one is
        not a module binding, and ``pipeline.polars(f)`` on an existing *f*
        finds *f* itself.
        """
        name = f.__name__
        kind = reserved_or_builtin(name)
        if kind is not None:
            party = NameParty(node_id=name, label=name, module=ROOT_MODULE)
            raise ValueError(NameViolation(kind=kind, name=name, parties=(party,)).message())
        folded = name.casefold()
        clashing_node = next(
            (other for other in self._node_map if other.casefold() == folded), None
        )
        if clashing_node == name:
            raise ValueError(
                f"Duplicate node name '{name}'. Each node must have a "
                "unique function name; rename one of the two functions."
            )
        if clashing_node is not None:
            raise ValueError(
                f"Node name {name!r} differs from the node {clashing_node!r} only in case; "
                "node names must differ by more than case. Rename one of them."
            )
        registrations = getattr(self, "_submodel_registrations", ())
        if any(registered.name.casefold() == folded for registered in registrations):
            raise ValueError(
                f"Pipeline node name {name!r} conflicts with a registered submodel identity."
            )
        if "<locals>" not in f.__qualname__:
            bound = getattr(f, "__globals__", {}).get(name, f)
            if bound is not f:
                raise ValueError(
                    f"Node name {name!r} is already bound in this module to a "
                    f"{type(bound).__name__}, which the node would replace for every "
                    "other node. Rename the node or the existing binding."
                )

    def _register_node(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Internal decorator to register a function as a node.

        Type-specific public decorators (``transform``, ``data_input``, etc.)
        delegate to this method.
        """

        def _register(f: Callable) -> Callable:
            node_type = NodeType(config.get("_node_type", NodeType.POLARS))
            if is_configured(node_type, config):
                # A configured source's hook takes df, yet it has no inputs.
                is_source = node_type in SOURCE_NODE_TYPES
            else:
                sig = inspect.signature(f)
                params = [p for p in sig.parameters.values() if p.name != "self"]
                is_source = len(params) == 0

            self._refuse_unavailable_node_name(f)

            n = Node(
                name=f.__name__,
                description=(f.__doc__ or "").strip(),
                fn=f,
                is_source=is_source,
                config=config,
                pipeline_dir=self._pipeline_dir,
            )
            self._nodes.append(n)
            self._node_map[n.name] = n
            if n.kind == "transform" and not n.config.get("_instance"):
                return f
            # A declaration's body does nothing, so the name it defines runs the
            # node instead: calling it does what a pipeline run does.
            return _runs_node(n)

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
        submodel_names = {registration.name for registration in self._submodel_registrations}
        known_endpoints = set(self._node_map) | submodel_names
        if source not in known_endpoints:
            raise ValueError(
                f"Source node '{source}' not found in pipeline. "
                f"Known endpoints: {sorted(known_endpoints)}"
            )
        if target not in known_endpoints:
            raise ValueError(
                f"Target node '{target}' not found in pipeline. "
                f"Known endpoints: {sorted(known_endpoints)}"
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


def _collect_standalone_output(result: Any) -> pl.DataFrame:
    """Collect a lazy standalone output through Haute's collect seam.

    Collecting inside ``run()``/``score()`` keeps the scenario context active
    while the plan executes and restores typed errors raised inside Haute's
    Python scans, which a caller's own ``.collect()`` would see as Polars
    ``ComputeError``.
    """
    if isinstance(result, pl.LazyFrame):
        from haute._polars_utils import execution_collect

        return execution_collect(result)
    return cast(pl.DataFrame, result)


class Pipeline(NodeRegistry):
    """A haute pricing pipeline - a DAG of decorated nodes.

    Nodes are functions. Edges define data flow: the output DataFrame
    of the source node is passed as the input to the target node.

    Usage:
        pipeline = Pipeline("main")

        # A configured node's decorator names its sidecar and does its work;
        # its function only declares the node's inputs:
        @pipeline.data_input(config="config/data_input/read_data.json")
        def read_data(): ...

        @pipeline.polars
        def transform(read_data: pl.LazyFrame) -> pl.LazyFrame:
            return read_data.filter(pl.col("premium") > 0)

        @pipeline.output(config="config/quote_response/result.json")
        def result(transform): ...

        pipeline.connect("read_data", "transform").connect("transform", "result")
        result = pipeline.run(source="live")
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        global_constants: str | None = None,
    ) -> None:
        if global_constants is not None and global_constants != GLOBAL_CONSTANTS_FILE:
            raise ValueError(
                f"Pipeline global_constants must be {GLOBAL_CONSTANTS_FILE!r}, the one place a "
                f"pipeline's global constants live: got {global_constants!r}."
            )
        super().__init__(name, description)
        self._global_constants_file = global_constants

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
        target_handles = [edge.target_port for edge in incoming]
        base_index, join_index = resolve_edge_join_role_indices(target_handles)
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

    @contextmanager
    def _submodels_expanded(self) -> Iterator[Pipeline]:
        """This pipeline with its submodels expanded, their modules registered meanwhile.

        Each definition's module stays in ``sys.modules`` while the run uses it,
        as an imported module would, so ``dataclasses`` and ``get_type_hints``
        resolve its string annotations; the entries go when the run ends.
        """
        module_names: list[str] = []
        try:
            yield self._with_submodels_expanded(module_names)
        finally:
            for name in module_names:
                sys.modules.pop(name, None)

    def _with_submodels_expanded(self, module_names: list[str]) -> Pipeline:
        """A copy of this pipeline with each submodel occurrence replaced by its nodes.

        Each occurrence's definition is imported from its file, and its nodes run
        as ``<occurrence>.<node>``. An edge into an occurrence's input port feeds
        every node the port targets, and an edge out of an output port leaves the
        node the port names, as flattening wires them for ``haute run``. A
        submodel's node that takes several inputs receives them in its
        parameters' order; the pipeline's own nodes keep their connection order.
        """
        definitions: dict[str, Submodel] = {}
        occurrences: dict[str, Submodel] = {}
        for registration in self._submodel_registrations:
            if registration.file not in definitions:
                definitions[registration.file] = _load_submodel_definition(
                    registration, module_names
                )
            occurrences[registration.name] = definitions[registration.file]

        nodes = list(self._nodes)
        # Each edge carries the name its target's parameter has for it.
        labelled: list[tuple[str, RegisteredEdge]] = []
        for occurrence, definition in occurrences.items():
            nodes.extend(
                dataclasses.replace(node, name=f"{occurrence}.{node.name}")
                for node in definition._nodes
            )
            labelled.extend(
                (
                    edge.source,
                    RegisteredEdge(
                        f"{occurrence}.{edge.source}",
                        f"{occurrence}.{edge.target}",
                        edge.source_port,
                        edge.target_port,
                    ),
                )
                for edge in definition._edges
            )
        for edge in self._edges:
            label = edge.source
            sources: list[tuple[str, str | None]] = [(edge.source, edge.source_port)]
            if edge.source in occurrences:
                endpoint = _submodel_output(occurrences[edge.source], edge.source, edge.source_port)
                sources = [(f"{edge.source}.{endpoint.node_id}", endpoint.handle_id)]
            targets: list[tuple[str, str | None]] = [(edge.target, edge.target_port)]
            if edge.target in occurrences:
                label = edge.target_port or ""
                port_targets = _submodel_input(
                    occurrences[edge.target], edge.target, edge.target_port
                )
                targets = [
                    (f"{edge.target}.{endpoint.node_id}", endpoint.handle_id)
                    for endpoint in port_targets
                ]
            labelled.extend(
                (label, RegisteredEdge(source, target, source_port, target_port))
                for source, source_port in sources
                for target, target_port in targets
            )

        expanded = Pipeline(self.name, self.description)
        expanded._global_constants_file = self._global_constants_file
        expanded._nodes = nodes
        expanded._node_map = {node.name: node for node in nodes}
        inner = {node.name for node in nodes[len(self._nodes) :]}
        expanded._edges = _in_parameter_order(labelled, expanded._node_map, inner)
        return expanded

    def _pipeline_file(self) -> str | None:
        """The file that defines this pipeline: its first node's, or its registrations'."""
        files = [
            getattr(node.fn, "__globals__", {}).get("__file__") for node in self._nodes[:1]
        ] + [registration.registered_in for registration in self._submodel_registrations]
        return next((file for file in files if isinstance(file, str) and file), None)

    def _missing_source_message(self) -> str:
        """Why ``run()`` needs a source, naming the sources the pipeline's sidecar lists."""
        message = (
            'pipeline.run() needs the source to run under, such as pipeline.run(source="live")'
        )
        file = self._pipeline_file()
        if file is None:
            return f"{message}; the pipeline's sources are listed in its .haute.json sidecar."
        from haute._sidecar import SidecarModel, read_sidecar_state

        sidecar = read_sidecar_state(Path(file))
        if sidecar.state == "absent":
            sources = SidecarModel().sources
        elif sidecar.data is not None:
            sources = sidecar.data.sources
        else:
            return f"{message}; its sidecar {sidecar.path.name} could not be read for its sources."
        return f"{message}. This pipeline's sources are {', '.join(sources)}."

    def _run_constants(self, source: str) -> GlobalConstantsNamespace:
        """The run's global constants for *source*, from the file the constructor names.

        The file is read from the pipeline directory of the first node's
        function. A file that cannot be loaded is carried in the table, so only
        the code that reads a constant fails.
        """
        if self._global_constants_file is None:
            return GlobalConstantsNamespace((), source=source)
        from haute._standalone_nodes import pipeline_directory
        from haute.errors import ConfigError
        from haute.parser import load_declared_global_constants

        root = self._nodes[0]
        try:
            base_dir = pipeline_directory(root.fn, root.name, root.pipeline_dir)
        except ConfigError:
            base_dir = None
        constants, error = load_declared_global_constants(base_dir)
        return GlobalConstantsNamespace(constants, source=source, error=error)

    def _execute_transform(
        self,
        n: Node,
        outputs: dict[str, Any],
        constants: GlobalConstantsNamespace | None = None,
    ) -> None:
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
        outputs[n.name] = n._invoke(tuple(input_dfs), constants)

    def run(self, *, source: str | None = None) -> pl.DataFrame:
        """Execute the full pipeline under *source*, following edges for data flow.

        *source* is required: it is what Source Switches route on, which value
        each global constant takes, and (anything but ``"live"``) selects
        batched model scoring. A run without it is refused, naming the
        pipeline's sources, rather than guessing one.
        """
        from haute._model_scorer import _scenario_ctx

        if not self._nodes and not self._submodel_registrations:
            raise ValueError("Pipeline has no nodes")
        if source is None:
            raise TypeError(self._missing_source_message())
        if self._submodel_registrations:
            with self._submodels_expanded() as expanded:
                return expanded.run(source=source)

        _token = _scenario_ctx.set(source)
        try:
            order = self._topo_order()
            constants = self._run_constants(source)
            outputs: dict[str, Any] = {}

            for n in order:
                if n.is_source:
                    outputs[n.name] = n._invoke((), constants)
                else:
                    self._execute_transform(n, outputs, constants)

            return _collect_standalone_output(outputs[self._resolve_output_node(order).name])
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

        if self._submodel_registrations:
            with self._submodels_expanded() as expanded:
                return expanded.score(df)
        _token = _scenario_ctx.set("live")
        try:
            order = self._topo_order()
            constants = self._run_constants("live")
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
                n._raise_if_unresolved_instance()
                if n.name in seed_names:
                    outputs[n.name] = seed_value
                else:
                    # Not a deploy input - run its own load logic.
                    outputs[n.name] = n._invoke((), constants)

            for n in order:
                if n.is_source:
                    continue
                self._execute_transform(n, outputs, constants)

            return _collect_standalone_output(outputs[self._resolve_output_node(order).name])
        finally:
            _scenario_ctx.reset(_token)

    def to_graph(self) -> dict:
        """Convert the live registry through the canonical static graph builders."""
        from haute._graph_builders import _build_edges, _build_rf_nodes

        raw_nodes: list[dict[str, Any]] = []
        for node in self._nodes:
            signature = inspect.signature(node.fn)
            positional_param_names = [
                parameter.name
                for parameter in signature.parameters.values()
                if parameter.name != "self"
                and parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            raw_nodes.append(
                {
                    "func_name": node.name,
                    "node_type": node.config.get("_node_type", NodeType.POLARS),
                    "description": node.description,
                    "config": {
                        key: value for key, value in node.config.items() if not key.startswith("_")
                    },
                    "param_names": positional_param_names,
                    "edge_param_names": positional_param_names,
                }
            )
        explicit_edges = [
            (edge.source, edge.target, edge.source_port, edge.target_port) for edge in self._edges
        ]
        return {
            "nodes": [
                node.model_dump(mode="json", by_alias=True) for node in _build_rf_nodes(raw_nodes)
            ],
            "edges": [
                edge.model_dump(mode="json", by_alias=True)
                for edge in _build_edges(raw_nodes, explicit_edges)
            ],
        }

    def submodel(
        self,
        file: str,
        name: str,
        *,
        instance_of: str | None = None,
    ) -> Pipeline:
        """Register one occurrence of a file-backed submodel definition."""
        identities = {
            "file": file,
            "name": name,
        }
        for field_name, value in identities.items():
            if not isinstance(value, str):
                raise TypeError(f"Submodel {field_name} must be a string.")
            if not value or value != value.strip():
                raise ValueError(f"Submodel {field_name} must be a non-empty unpadded string.")
        sanitized_name = _sanitize_func_name(name)
        if sanitized_name != name:
            raise ValueError(
                f"Submodel name must be a canonical identifier "
                f"(got '{name}'; expected '{sanitized_name}')."
            )
        if instance_of is not None:
            if not isinstance(instance_of, str):
                raise TypeError("Submodel instance_of must be a string or None.")
            if not instance_of or instance_of != instance_of.strip():
                raise ValueError("Submodel instance_of must be a non-empty unpadded string.")

        kind = reserved_or_builtin(name)
        if kind is not None:
            party = NameParty(node_id=name, label=name, module=ROOT_MODULE)
            raise ValueError(NameViolation(kind=kind, name=name, parties=(party,)).message())
        folded = name.casefold()
        if any(node_name.casefold() == folded for node_name in self._node_map):
            raise ValueError(f"Submodel name {name!r} conflicts with a registered node name.")

        if any(registered.name.casefold() == folded for registered in self._submodel_registrations):
            raise ValueError(f"Duplicate submodel name {name!r}.")

        self._submodel_files.append(file)
        self._submodel_registrations.append(
            RegisteredSubmodel(
                file=file,
                name=name,
                instance_of=instance_of,
                # ``file`` is relative to the pipeline file making this call.
                registered_in=sys._getframe(1).f_globals.get("__file__"),
            )
        )
        return self

    @property
    def submodel_files(self) -> list[str]:
        """Paths passed to :meth:`submodel`."""
        return list(self._submodel_files)

    @property
    def submodel_registrations(self) -> list[RegisteredSubmodel]:
        """Occurrence registrations, returned as an immutable-value copy."""
        return list(self._submodel_registrations)


class Submodel(NodeRegistry):
    """A reusable definition declared in a separate Python module.

    ``pipeline_dir`` leads from this file to the directory of the pipeline that
    registers it (``..`` for a file in ``modules/``): its nodes' ``config=``
    paths resolve there, as they do for the pipeline's own nodes.
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        definition_id: str,
        input_ports: list[dict[str, Any] | SubmodelInputPort],
        output_ports: list[dict[str, Any] | SubmodelOutputPort],
        pipeline_dir: str = ".",
    ) -> None:
        if not isinstance(definition_id, str):
            raise TypeError("Submodel definition_id must be a string.")
        if not definition_id or definition_id != definition_id.strip():
            raise ValueError("Submodel definition_id must be a non-empty unpadded string.")
        if not isinstance(input_ports, list) or not isinstance(output_ports, list):
            raise TypeError("Submodel input_ports and output_ports must be lists.")
        if not is_pipeline_dir(pipeline_dir):
            raise ValueError(
                "Submodel pipeline_dir must be '..' once per folder between this file and "
                f"the pipeline that registers it (got {pipeline_dir!r})."
            )

        super().__init__(name, description)
        self._pipeline_dir = pipeline_dir
        self._definition_id = definition_id
        for port_field, ports in (("input_ports", input_ports), ("output_ports", output_ports)):
            for port in ports:
                if isinstance(port, Mapping):
                    for key in port:
                        if key in {"portId", "label"}:
                            raise ValueError(
                                f"Submodel {port_field} port declares {key!r}; "
                                "a public port has one name: "
                                "replace 'portId' and 'label' with 'name'."
                            )
        self._input_ports = [SubmodelInputPort.model_validate(port) for port in input_ports]
        self._output_ports = [SubmodelOutputPort.model_validate(port) for port in output_ports]

    @property
    def definition_id(self) -> str:
        """Stable reusable-definition identity."""
        return self._definition_id

    @property
    def pipeline_dir(self) -> str:
        """From this file to the directory of the pipeline that registers it."""
        return self._pipeline_dir

    @property
    def input_ports(self) -> list[SubmodelInputPort]:
        """Typed public inputs, returned as a defensive copy."""
        return list(self._input_ports)

    @property
    def output_ports(self) -> list[SubmodelOutputPort]:
        """Typed public outputs, returned as a defensive copy."""
        return list(self._output_ports)


def _load_submodel_definition(
    registration: RegisteredSubmodel, module_names: list[str]
) -> Submodel:
    """Import the definition a ``pipeline.submodel(file, ...)`` registration names.

    The module is entered in ``sys.modules`` under a fresh name, appended to
    *module_names* for the caller to remove when the run ends.
    """
    import importlib.util
    import uuid

    from haute._submodel_paths import SubmodelPathError, resolve_submodel_reference

    file = registration.file
    if registration.registered_in is None:
        raise ExecutionError(
            f"Submodel {registration.name!r} was registered outside a pipeline file, so its "
            f"file {file!r} has no directory to resolve against. Run the pipeline from its .py "
            "file.",
            submodel=registration.name,
        )
    base_dir = Path(registration.registered_in).resolve().parent
    try:
        path, _ = resolve_submodel_reference(file, pipeline_dir=base_dir, project_root=base_dir)
    except SubmodelPathError as exc:
        raise ExecutionError(str(exc), submodel_file=file) from exc
    if not path.is_file():
        raise ExecutionError(
            f"Submodel file {file!r} does not exist beside the pipeline.", submodel_file=file
        )
    spec = importlib.util.spec_from_file_location(f"_haute_submodel_{uuid.uuid4().hex}", path)
    if spec is None or spec.loader is None:
        raise ExecutionError(f"Submodel file {file!r} cannot be imported.", submodel_file=file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    module_names.append(spec.name)
    spec.loader.exec_module(module)
    found = [value for value in vars(module).values() if isinstance(value, Submodel)]
    if len(found) != 1:
        count = "no haute.Submodel" if not found else f"{len(found)} haute.Submodel objects"
        raise ExecutionError(
            f"Submodel file {file!r} defines {count}; it must define exactly one.",
            submodel_file=file,
        )
    return found[0]


def _submodel_input(
    definition: Submodel, occurrence: str, port: str | None
) -> list[SubmodelEndpoint]:
    for candidate in definition.input_ports:
        if candidate.name == port:
            return list(candidate.targets)
    raise ExecutionError(
        f"Submodel {occurrence!r} has no input port {port!r}; its input ports are "
        f"{[candidate.name for candidate in definition.input_ports]}.",
        submodel=occurrence,
    )


def _submodel_output(definition: Submodel, occurrence: str, port: str | None) -> SubmodelEndpoint:
    for candidate in definition.output_ports:
        if candidate.name == port:
            return candidate.source
    raise ExecutionError(
        f"Submodel {occurrence!r} has no output port {port!r}; its output ports are "
        f"{[candidate.name for candidate in definition.output_ports]}.",
        submodel=occurrence,
    )


def _in_parameter_order(
    labelled: list[tuple[str, RegisteredEdge]], nodes: Mapping[str, Node], inner: set[str]
) -> list[RegisteredEdge]:
    """The edges, each *inner* (submodel) node's inputs in its parameters' order.

    A submodel's file declares a port input on the node rather than connecting
    it, so registration order cannot place it among the node's other inputs.
    *labelled* pairs each edge with the name it would fill: its source's name,
    or the input port's for an edge into an occurrence. An input whose name is a
    positional parameter takes that parameter's place; the others fill the
    remaining parameters in order, as a configured hook's first parameter,
    ``df``, names no input. An inner node with more inputs than parameters, and
    every node of the pipeline itself, keeps registration order, as a pipeline
    without submodels does.
    """
    from haute._standalone_nodes import _parameters

    by_target: dict[str, list[tuple[str, RegisteredEdge]]] = {}
    for label, edge in labelled:
        by_target.setdefault(edge.target, []).append((label, edge))
    ordered: list[RegisteredEdge] = []
    for target, incoming in by_target.items():
        node = nodes.get(target)
        if node is None or target not in inner or len(incoming) < 2:
            ordered.extend(edge for _, edge in incoming)
            continue
        positional = _parameters(node.fn)[0]
        slots: list[RegisteredEdge | None] = [None] * len(positional)
        unnamed: list[RegisteredEdge] = []
        for label, edge in incoming:
            index = positional.index(label) if label in positional else None
            if index is not None and slots[index] is None:
                slots[index] = edge
            else:
                unnamed.append(edge)
        open_slots = [index for index, edge in enumerate(slots) if edge is None]
        if len(unnamed) > len(open_slots):
            ordered.extend(edge for _, edge in incoming)
            continue
        for index, edge in zip(open_slots, unnamed, strict=False):
            slots[index] = edge
        ordered.extend(edge for edge in slots if edge is not None)
    return ordered
