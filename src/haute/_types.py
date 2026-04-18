"""Core type definitions for Haute graph structures.

These Pydantic models are the **single source of truth** for the graph
data that flows between the parser, executor, codegen, deploy, and
server API layers.  ``schemas.py`` re-exports the graph models for
FastAPI endpoint validation.
"""

from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from typing import Any, Protocol, TypedDict, runtime_checkable

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

# Type alias - nodes pass lazy frames between each other
_Frame = pl.LazyFrame


from haute.errors import HauteError as HauteError
from haute._graph_utils import (
    _resolve_sink_path as _resolve_sink_path,
    _sanitize_func_name as _sanitize_func_name,
    build_instance_mapping as build_instance_mapping,
    build_parents_of as build_parents_of,
    resolve_orig_source_names as resolve_orig_source_names,
)


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
    cv_folds: int
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
    # e.g. {"volume": {"min": 0.90}, "loss_ratio": {"max": 1.05}}

    # Solver tuning
    max_iter: int
    tolerance: float
    chunk_size: int
    record_history: bool

    # Frontier
    frontier_enabled: bool
    frontier_points_per_dim: int
    frontier_threshold_ranges: dict[str, list[float]]

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
    "frontier_points_per_dim",
    "frontier_threshold_ranges",
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

    @cached_property
    def node_map(self) -> dict[str, GraphNode]:
        """Map node ID to node, cached for repeated access."""
        return {n.id: n for n in self.nodes}

    @cached_property
    def parents_of(self) -> dict[str, list[str]]:
        """Map each node to its parent node IDs (built from edges)."""
        return build_parents_of(self.edges)
