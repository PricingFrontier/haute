"""Pipeline and node decorator for haute."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self

import polars as pl

from haute._logging import get_logger
from haute._types import GraphEdge, NodeType
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

    @property
    def is_deploy_input(self) -> bool:
        """Whether this node is marked as the live API input for deployment."""
        return bool(self.config.get("api_input"))

    @property
    def is_live_switch(self) -> bool:
        """Whether this node is a live/batch switch."""
        return bool(self.config.get("live_switch"))

    @property
    def n_inputs(self) -> int:
        """Number of DataFrame inputs the function accepts."""
        if self.is_source:
            return 0
        sig = inspect.signature(self.fn)
        return len([p for p in sig.parameters.values() if p.name != "self"])

    def __call__(self, *dfs: pl.DataFrame) -> pl.DataFrame:
        if self.is_source:
            result: pl.DataFrame = self.fn()
            return result
        if len(dfs) == 0:
            raise ValueError(
                f"Node '{self.name}' expects {self.n_inputs} input(s) but received none"
            )
        # If function accepts multiple params, pass separately
        if self.n_inputs > 1:
            result = self.fn(*dfs[: self.n_inputs])
            return result
        # Single-param function gets first df
        result = self.fn(dfs[0])
        return result


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
        self._edges: list[tuple[str, str]] = []
        self._submodel_files: list[str] = []

    def _register_node(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Internal decorator to register a function as a node.

        Type-specific public decorators (``transform``, ``data_source``, etc.)
        delegate to this method.
        """

        def _register(f: Callable) -> Callable:
            sig = inspect.signature(f)
            params = [p for p in sig.parameters.values() if p.name != "self"]
            is_source = len(params) == 0

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

    def data_source(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for data-source nodes."""
        return self._register_node(fn, _node_type=NodeType.DATA_SOURCE, **config)

    def polars(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for polars nodes."""
        return self._register_node(fn, _node_type=NodeType.POLARS, **config)

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

    def data_sink(self, fn: Callable | None = None, **config: Any) -> Callable:
        """Decorator alias for data-sink nodes."""
        return self._register_node(fn, _node_type=NodeType.DATA_SINK, **config)

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
        """Decorator alias for instance nodes."""
        return self._register_node(fn, _node_type=NodeType.POLARS, **config)

    def connect(self, source: str, target: str) -> Self:
        """Declare an edge: source node's output feeds into target node.

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
        self._edges.append((source, target))
        return self

    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes)

    @property
    def edges(self) -> list[tuple[str, str]]:
        return list(self._edges)


class Pipeline(NodeRegistry):
    """A haute pricing pipeline - a DAG of decorated nodes.

    Nodes are functions. Edges define data flow: the output DataFrame
    of the source node is passed as the input to the target node.

    Usage:
        pipeline = Pipeline("main")

        @pipeline.data_source(path="data.parquet")
        def read_data() -> pl.DataFrame: ...

        @pipeline.polars
        def transform(df: pl.DataFrame) -> pl.DataFrame: ...

        pipeline.connect("read_data", "transform")
        result = pipeline.run()
    """

    def _topo_order(self) -> list[Node]:
        """Return nodes in topological order based on edges."""
        if not self._edges:
            # No explicit edges - fall back to registration order
            return list(self._nodes)

        node_ids = [n.name for n in self._nodes]
        edges = [GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt) for src, tgt in self._edges]
        sorted_ids = topo_sort_ids(node_ids, edges)

        if len(sorted_ids) != len(self._nodes):
            missing = {n.name for n in self._nodes} - set(sorted_ids)
            raise ValueError(f"Cycle detected or disconnected nodes: {missing}")

        return [self._node_map[name] for name in sorted_ids if name in self._node_map]

    def _get_inputs(self, node_name: str) -> list[str]:
        """Get the names of all nodes that feed into this node."""
        return [src for src, tgt in self._edges if tgt == node_name]

    def run(self) -> pl.DataFrame:
        """Execute the full pipeline, following edges for data flow."""
        from haute._model_scorer import _scenario_ctx

        if not self._nodes:
            raise ValueError("Pipeline has no nodes")

        _token = _scenario_ctx.set("batch")
        try:
            order = self._topo_order()
            outputs: dict[str, pl.DataFrame] = {}

            for n in order:
                if n.is_source:
                    outputs[n.name] = n()
                else:
                    input_names = self._get_inputs(n.name)
                    if input_names:
                        missing = [name for name in input_names if name not in outputs]
                        if missing:
                            raise ValueError(
                                f"Node '{n.name}' is missing input(s) from: {missing}. "
                                "Upstream node(s) may have failed or not been registered."
                            )
                        input_dfs = [outputs[name] for name in input_names]
                        outputs[n.name] = n(*input_dfs)
                    else:
                        raise ValueError(
                            f"Node '{n.name}' has no inbound edges. "
                            "Use pipeline.connect() to wire it up."
                        )

            # Return the output of the last node in topo order
            return outputs[order[-1].name]
        finally:
            _scenario_ctx.reset(_token)

    def score(self, df: pl.DataFrame) -> pl.DataFrame:
        """Run the pipeline on an input DataFrame, skipping source nodes.

        If any source node is marked ``api_input=True``, only those
        sources are seeded with *df*.  Other sources still execute their
        own loading logic (e.g. static rating tables).  When no node is
        marked, **all** sources are seeded.
        """
        from haute._model_scorer import _scenario_ctx

        _token = _scenario_ctx.set("live")
        try:
            order = self._topo_order()
            outputs: dict[str, pl.DataFrame] = {}

            deploy_inputs = [n for n in order if n.is_source and n.is_deploy_input]
            seed_all = len(deploy_inputs) == 0  # fallback: seed every source

            for n in order:
                if n.is_source:
                    if seed_all or n.is_deploy_input:
                        outputs[n.name] = df
                    else:
                        # Not a deploy input - run its own load logic
                        outputs[n.name] = n()

            for n in order:
                if n.is_source:
                    continue
                input_names = self._get_inputs(n.name)
                if input_names:
                    missing = [name for name in input_names if name not in outputs]
                    if missing:
                        raise ValueError(
                            f"Node '{n.name}' is missing input(s) from: {missing}. "
                            "Upstream node(s) may have failed or not been registered."
                        )
                    input_dfs = [outputs[name] for name in input_names]
                    outputs[n.name] = n(*input_dfs)
                else:
                    raise ValueError(
                        f"Node '{n.name}' has no inbound edges. "
                        "Use pipeline.connect() to wire it up."
                    )

            return outputs[order[-1].name]
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
                rf_type = NodeType.DATA_SOURCE
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

        if self._edges:
            for src, tgt in self._edges:
                rf_edges.append(
                    {
                        "id": f"e_{src}_{tgt}",
                        "source": src,
                        "target": tgt,
                    }
                )
        else:
            # No explicit edges - infer linear chain
            for i in range(1, len(self._nodes)):
                rf_edges.append(
                    {
                        "id": f"e_{self._nodes[i - 1].name}_{self._nodes[i].name}",
                        "source": self._nodes[i - 1].name,
                        "target": self._nodes[i].name,
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

    Mirrors :class:`Pipeline` but is intended for submodel files that
    the main pipeline imports via ``pipeline.submodel("modules/x.py")``.

    Usage::

        submodel = haute.Submodel("model_scoring")

        @submodel.external_file(path="models/freq.cbm", file_type="catboost")
        def frequency_model(policies: pl.LazyFrame) -> pl.LazyFrame: ...

        submodel.connect("policies", "frequency_model")
    """
