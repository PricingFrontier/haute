"""Core type definitions for Haute graph structures.

These Pydantic models are the **single source of truth** for the graph
data that flows between the parser, executor, codegen, deploy, and
server API layers.  ``schemas.py`` re-exports the graph models for
FastAPI endpoint validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from functools import cached_property
from typing import Any, ClassVar, Protocol, Self, TypedDict, runtime_checkable

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from haute._graph_utils import build_parents_of

# Type alias - nodes pass lazy frames between each other
_Frame = pl.LazyFrame


class NodeType(StrEnum):
    """Canonical node-type identifiers shared with the React Flow frontend.

    Inherits from ``StrEnum`` so ``NodeType.API_INPUT == "apiInput"`` is ``True``
    and JSON serialization produces the plain string value.
    """

    API_INPUT = "apiInput"
    DATA_SOURCE = "dataSource"
    POLARS = "polars"
    MODEL_SCORE = "modelScore"
    BANDING = "banding"
    RATING_STEP = "ratingStep"
    OUTPUT = "output"
    DATA_SINK = "dataSink"
    EXTERNAL_FILE = "externalFile"
    LIVE_SWITCH = "liveSwitch"
    MODELLING = "modelling"
    OPTIMISER = "optimiser"
    SCENARIO_EXPANDER = "scenarioExpander"
    OPTIMISER_APPLY = "optimiserApply"
    CONSTANT = "constant"
    SUBMODEL = "submodel"
    SUBMODEL_PORT = "submodelPort"


DECORATOR_TO_NODE_TYPE: dict[str, NodeType] = {
    "data_source": NodeType.DATA_SOURCE,
    "api_input": NodeType.API_INPUT,
    "polars": NodeType.POLARS,
    "model_score": NodeType.MODEL_SCORE,
    "banding": NodeType.BANDING,
    "rating_step": NodeType.RATING_STEP,
    "output": NodeType.OUTPUT,
    "data_sink": NodeType.DATA_SINK,
    "external_file": NodeType.EXTERNAL_FILE,
    "live_switch": NodeType.LIVE_SWITCH,
    "modelling": NodeType.MODELLING,
    "optimiser": NodeType.OPTIMISER,
    "scenario_expander": NodeType.SCENARIO_EXPANDER,
    "optimiser_apply": NodeType.OPTIMISER_APPLY,
    "constant": NodeType.CONSTANT,
    "instance": NodeType.POLARS,  # instances default to polars; real type resolved at runtime
}

NODE_TYPE_TO_DECORATOR: dict[NodeType, str] = {
    v: k for k, v in DECORATOR_TO_NODE_TYPE.items() if k != "instance"
}


# ---------------------------------------------------------------------------
# Typed config shapes (documentation + IDE autocomplete, no runtime change)
# ---------------------------------------------------------------------------


class ApiInputConfig(TypedDict, total=False):
    """Config for apiInput nodes."""

    path: str
    row_id_column: str
    flattenSchema: dict[str, Any]


class DataSourceConfig(TypedDict, total=False):
    """Config for dataSource nodes."""

    path: str
    sourceType: str  # "flat_file" | "databricks"
    table: str
    http_path: str
    query: str
    code: str


class TransformConfig(TypedDict, total=False):
    """Config for transform nodes."""

    code: str
    instanceOf: str
    inputMapping: dict[str, str]
    selected_columns: list[str]


class ModelScoreConfig(TypedDict, total=False):
    """Config for modelScore nodes."""

    sourceType: str  # "run" | "registered"
    # run-based selection
    experiment_name: str  # UI-only: display name for panel re-open
    experiment_id: str  # UI-only: MLflow experiment ID for API calls
    run_id: str
    run_name: str  # UI-only: display name for panel re-open
    artifact_path: str  # e.g. "model.cbm"
    # registered model selection
    registered_model: str  # e.g. "catalog.schema.model" or "my-model"
    version: str  # "1", "2", etc. or "latest"
    # common
    task: str  # "regression" | "classification"
    output_column: str  # prediction column name, default "prediction"
    feature_contract_path: str  # local deploy/runtime feature-contract artifact
    code: str  # optional post-processing code
    instanceOf: str
    inputMapping: dict[str, str]


class BandingFactor(TypedDict, total=False):
    """A single factor in a banding node config."""

    banding: str  # "continuous" | "discrete"
    column: str
    outputColumn: str
    rules: list[dict[str, Any]]
    default: str | None


class BandingConfig(TypedDict, total=False):
    """Config for banding nodes."""

    factors: list[BandingFactor]


class RatingTableEntry(TypedDict, total=False):
    """A single entry (row) in a rating table."""

    # Keys are dynamic factor names; values are strings/numbers


class RatingTable(TypedDict, total=False):
    """A single table in a ratingStep config."""

    name: str
    factors: list[str]
    outputColumn: str
    defaultValue: str | None
    entries: list[dict[str, Any]]


class RatingStepConfig(TypedDict, total=False):
    """Config for ratingStep nodes."""

    tables: list[RatingTable]
    operation: str  # "multiply" | "add" | "min" | "max"
    combinedColumn: str
    combinedOutputs: list[dict[str, Any]]
    code: str


class OutputConfig(TypedDict, total=False):
    """Config for output nodes."""

    fields: list[str]


class DataSinkConfig(TypedDict, total=False):
    """Config for dataSink nodes."""

    path: str
    format: str  # "parquet" | "csv"


class ExternalFileConfig(TypedDict, total=False):
    """Config for externalFile nodes."""

    path: str
    fileType: str  # "pickle" | "json" | "joblib" | "catboost"
    modelClass: str  # "classifier" | "regressor" (catboost only)
    code: str


class LiveSwitchConfig(TypedDict, total=False):
    """Config for liveSwitch nodes.

    ``input_scenario_map`` maps each connected input name to the scenario
    that should route to it.  E.g. ``{"quotes": "live", "batch_quotes": "test_batch"}``.
    """

    input_scenario_map: dict[str, str]
    inputs: list[str]


class ModellingConfig(TypedDict, total=False):
    """Config for modelling (model training) nodes."""

    name: str
    target: str
    weight: str
    exclude: list[str]
    algorithm: str  # "catboost" | "glm"
    task: str  # "regression" | "classification"
    params: dict[str, Any]
    split: dict[str, Any]
    metrics: list[str]
    mlflow_experiment: str
    model_name: str
    output_dir: str
    row_limit: int
    # GLM-specific (RustyStats)
    terms: dict[str, Any]
    family: str
    link: str
    offset: str
    interactions: list[dict[str, Any]]
    regularization: str
    alpha: float
    l1_ratio: float
    intercept: bool
    var_power: float
    # CatBoost / shared
    loss_function: str
    variance_power: float
    monotone_constraints: dict[str, int]
    feature_weights: dict[str, float]


class OptimiserConfig(TypedDict, total=False):
    """Config for optimiser (price optimisation) nodes."""

    # Mode
    mode: str  # "online" | "ratebook"

    # Column mappings
    quote_id: str
    scenario_index: str
    scenario_value: str
    objective: str

    # Constraints
    constraints: dict[str, dict[str, float]]
    # e.g. {"premium": {"min": 1_000_000}, "claims": {"max": 650_000}}

    # Solver tuning
    max_iter: int
    tolerance: float
    chunk_size: int
    record_history: bool

    # Frontier
    frontier_enabled: bool
    frontier_min: float
    frontier_max: float
    frontier_ranges: dict[str, dict[str, float]]
    frontier_steps: int

    # Ratebook
    factor_columns: list[list[str]]
    candidate_min: float
    candidate_max: float
    candidate_steps: int
    max_cd_iterations: int
    cd_tolerance: float
    structure_mode: str  # "explicit" | "auto"

    # Two-input support for ratebook
    scored_input: str
    factors_input: str

    # Runtime node-ID references (set by the frontend / optimiser service)
    data_input: str
    banding_source: str

    # MLflow
    mlflow_experiment: str
    model_name: str


class OptimiserApplyConfig(TypedDict, total=False):
    """Config for optimiserApply nodes."""

    artifact_path: str  # path to saved optimiser artifact JSON
    version_column: str  # column name for version tracking (default "__optimiser_version__")
    optimised_value_column: str  # optional output column for the selected optimiser value
    optimiser_mode: str  # resolved optimiser artifact mode, when known
    ratebook_input: str  # upstream node id used when applying ratebook artifacts
    # MLflow source fields
    sourceType: str  # "file" | "run" | "registered"
    registered_model: str  # registered model name (when sourceType="registered")
    version: str  # model version or "latest" (when sourceType="registered")
    experiment_id: str  # MLflow experiment ID (when sourceType="run")
    experiment_name: str  # UI-only: display name for panel re-open
    run_id: str  # MLflow run ID (when sourceType="run")
    run_name: str  # UI-only: display name for panel re-open


class ScenarioExpanderConfig(TypedDict, total=False):
    """Config for scenarioExpander nodes."""

    quote_id: str  # column identifying each quote/row-group
    column_name: str  # name of the new value column (e.g. "scenario_value")
    min_value: float  # start of linspace
    max_value: float  # end of linspace
    steps: int  # number of steps
    step_column: str  # name of the 0-based step index column (e.g. "scenario_index")
    code: str  # optional Polars transformation code (post-expansion)


# ---------------------------------------------------------------------------
# Solve result Protocols — structural typing for price_contour results
# ---------------------------------------------------------------------------
# ``price_contour.SolveResult`` is a Rust/pyo3 class and
# ``price_contour.RatebookResult`` is a Python dataclass.  Both expose a
# similar interface but differ in some attributes.  These Protocols let
# route-layer code type-check without importing the external library.


@runtime_checkable
class SolveResultLike(Protocol):
    """Structural interface for the common attributes of any solve result.

    Covers the intersection of ``price_contour.SolveResult`` (online) and
    ``price_contour.RatebookResult`` (ratebook).  Used in code that handles
    both result types (e.g. ``_build_artifact_payload``, ``apply_lambdas``).
    """

    @property
    def lambdas(self) -> dict[str, float]: ...
    @property
    def total_objective(self) -> float: ...
    @property
    def total_constraints(self) -> dict[str, float]: ...
    @property
    def baseline_objective(self) -> float: ...
    @property
    def baseline_constraints(self) -> dict[str, float]: ...
    @property
    def converged(self) -> bool: ...


@runtime_checkable
class OnlineSolveResultLike(SolveResultLike, Protocol):
    """Structural interface for ``price_contour.SolveResult`` (online mode).

    Extends the common interface with attributes specific to the online
    solver: per-quote DataFrame, iteration count, quote/step counts,
    convergence history, and the underlying QuoteGrid.
    """

    @property
    def dataframe(self) -> pl.DataFrame: ...
    @property
    def baseline_objective(self) -> float: ...
    @property
    def baseline_constraints(self) -> dict[str, float]: ...
    @property
    def iterations(self) -> int: ...
    @property
    def n_quotes(self) -> int: ...
    @property
    def n_steps(self) -> int: ...
    @property
    def history(self) -> list[dict[str, float]] | None: ...
    @property
    def grid(self) -> Any: ...  # QuoteGrid — Rust opaque type


@runtime_checkable
class RatebookSolveResultLike(SolveResultLike, Protocol):
    """Structural interface for ``price_contour.RatebookResult`` (ratebook mode).

    Extends the common interface with attributes specific to the ratebook
    solver: factor tables, coordinate-descent iteration count, clamp rate,
    and baseline values.
    """

    @property
    def baseline_objective(self) -> float: ...
    @property
    def baseline_constraints(self) -> dict[str, float]: ...
    @property
    def factor_tables(self) -> dict[str, dict[str, float]]: ...
    @property
    def cd_iterations(self) -> int: ...
    @property
    def clamp_rate(self) -> float: ...


MODEL_SCORE_CONFIG_KEYS: tuple[str, ...] = (
    "sourceType",
    "run_id",
    "artifact_path",
    "run_name",
    "registered_model",
    "version",
    "task",
    "output_column",
    "experiment_name",
    "experiment_id",
)

MODELLING_CONFIG_KEYS: tuple[str, ...] = (
    "name",
    "target",
    "weight",
    "exclude",
    "algorithm",
    "task",
    "params",
    "split",
    "metrics",
    "mlflow_experiment",
    "model_name",
    "output_dir",
)

OPTIMISER_CONFIG_KEYS: tuple[str, ...] = (
    "mode",
    "quote_id",
    "scenario_index",
    "scenario_value",
    "objective",
    "constraints",
    "max_iter",
    "tolerance",
    "chunk_size",
    "record_history",
    "frontier_enabled",
    "frontier_min",
    "frontier_max",
    "frontier_ranges",
    "frontier_steps",
    "factor_columns",
    "candidate_min",
    "candidate_max",
    "candidate_steps",
    "max_cd_iterations",
    "cd_tolerance",
    "structure_mode",
    "scored_input",
    "factors_input",
    "data_input",
    "banding_source",
    "mlflow_experiment",
    "model_name",
)

OPTIMISER_APPLY_CONFIG_KEYS: tuple[str, ...] = (
    "artifact_path",
    "version_column",
    "optimised_value_column",
    "optimiser_mode",
    "ratebook_input",
    "sourceType",
    "registered_model",
    "version",
    "experiment_id",
    "experiment_name",
    "run_id",
    "run_name",
)

SCENARIO_EXPANDER_CONFIG_KEYS: tuple[str, ...] = (
    "quote_id",
    "column_name",
    "min_value",
    "max_value",
    "steps",
    "step_column",
)


class ConstantConfig(TypedDict, total=False):
    """Config for constant nodes.

    Each entry in ``values`` is a ``{"name": str, "value": str}`` dict.
    Values are coerced to float where possible; otherwise kept as strings.
    The node outputs a 1-row LazyFrame with one column per entry.
    """

    values: list[dict[str, str]]


class SubmodelConfig(TypedDict, total=False):
    """Config for submodel placeholder nodes."""

    file: str
    childNodeIds: list[str]
    inputPorts: list[str]
    outputPorts: list[str]


class NodeData(BaseModel):
    """Data payload for a single pipeline node."""

    label: str = "Unnamed"
    description: str = ""
    nodeType: NodeType = NodeType.POLARS  # noqa: N815 — matches React Flow frontend convention
    config: dict[str, Any] = Field(default_factory=dict)


class GraphNode(BaseModel):
    """A single node in the React Flow graph."""

    id: str
    type: str = "pipelineNode"
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    data: NodeData = Field(default_factory=NodeData)


class GraphEdge(BaseModel):
    """A single edge in the React Flow graph."""

    id: str
    source: str
    target: str
    sourceHandle: str | None = None  # noqa: N815 — matches React Flow frontend convention
    targetHandle: str | None = None  # noqa: N815 — matches React Flow frontend convention


class PipelineGraph(BaseModel):
    """React Flow graph structure used throughout Haute.

    This is the canonical type for the graph dict passed between
    parser, executor, codegen, deploy, and the server API layer.
    """

    model_config = ConfigDict(ignored_types=(cached_property,))

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    pipeline_name: str | None = None
    pipeline_description: str | None = None
    preamble: str | None = None
    preserved_blocks: list[str] = Field(default_factory=list)
    source_file: str | None = None
    submodels: dict[str, Any] | None = None
    warning: str | None = None
    sources: list[str] = Field(default_factory=lambda: ["live"])
    active_source: str = "live"

    # Names of ``@cached_property`` slots that must be invalidated when
    # ``model_copy`` produces a new instance with changed structure —
    # Pydantic's default ``model_copy`` shallow-copies ``__dict__``,
    # which includes any already-materialised ``cached_property`` values.
    # Without this invalidation, a copied graph would serve the parent's
    # stale ``node_map`` / ``parents_of`` / ``_haute_base_fingerprint``
    # after ``update={"nodes": ...}``.  See Pydantic's own docstring on
    # ``model_copy`` for the original warning about this footgun.
    _HAUTE_CACHED_PROPERTY_NAMES: ClassVar[tuple[str, ...]] = (
        "node_map",
        "parents_of",
        "_haute_base_fingerprint",
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Copy this graph, invalidating any cached-property slots.

        Overrides :meth:`pydantic.BaseModel.model_copy` so that the
        returned instance starts with a fresh property cache — both the
        ``node_map``/``parents_of`` memos and the
        ``_haute_base_fingerprint`` cache introduced for graph-fingerprint
        caching.

        Callers use ``model_copy(update={"nodes": ...})`` as the canonical
        way to evolve a graph immutably (see
        ``tests/test_graph_fingerprint_cached.py``); the override makes
        that pattern correct by construction.
        """
        copied = super().model_copy(update=update, deep=deep)
        for name in self._HAUTE_CACHED_PROPERTY_NAMES:
            copied.__dict__.pop(name, None)
        return copied

    @cached_property
    def node_map(self) -> dict[str, GraphNode]:
        """Map node ID to node, cached for repeated access."""
        return {n.id: n for n in self.nodes}

    @cached_property
    def parents_of(self) -> dict[str, list[str]]:
        """Map each node to its parent node IDs (built from edges)."""
        return build_parents_of(self.edges)

    @cached_property
    def _haute_base_fingerprint(self) -> str:
        """Structural fingerprint of the graph (node configs + edge topology).

        Cached once per ``PipelineGraph`` instance — the underlying
        computation (sorted-by-id node walk + canonical JSON + content
        hash) is measurable (hundreds of microseconds per call for
        ~100-node pipelines) and was previously recomputed on every
        preview cache-key lookup.  Callers evolve a pipeline with
        ``model_copy(update=...)``; the overridden :meth:`model_copy`
        above clears the memoised digest on the new instance so the
        cache boundary follows the immutable-copy idiom.

        See :func:`haute._cache.graph_fingerprint` for the public
        wrapper that mixes in per-call extra keys.  The base computation
        is kept module-level (as ``_graph_base_fingerprint`` in
        :mod:`haute._cache`) so the call-counting spies in
        ``tests/test_graph_fingerprint_cached.py`` can ``monkeypatch``
        the function and observe exactly one call per instance.
        """
        # Import here to avoid an import cycle: ``_cache`` imports
        # ``PipelineGraph`` from this module.
        from haute._cache import _graph_base_fingerprint

        return _graph_base_fingerprint(self)
