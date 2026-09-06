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
from typing import (
    Any,
    ClassVar,
    Literal,
    Protocol,
    Required,
    Self,
    TypeAlias,
    TypedDict,
    runtime_checkable,
)

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from haute._graph_utils import _sanitize_func_name, build_parents_of

# Type alias - nodes pass lazy frames between each other
_Frame = pl.LazyFrame


class NodeType(StrEnum):
    """Canonical node-type identifiers shared with the React Flow frontend.

    Inherits from ``StrEnum`` so ``NodeType.API_INPUT == "apiInput"`` is ``True``
    and JSON serialization produces the plain string value.
    """

    API_INPUT = "apiInput"
    DATA_INPUT = "dataInput"
    DATA_OUTPUT = "dataOutput"
    POLARS = "polars"
    EDGE_JOIN = "edgeJoin"
    MODEL_SCORE = "modelScore"
    BANDING = "banding"
    RATING_STEP = "ratingStep"
    OUTPUT = "output"
    EXPLORE = "explore"
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
    "data_input": NodeType.DATA_INPUT,
    "data_output": NodeType.DATA_OUTPUT,
    "api_input": NodeType.API_INPUT,
    "polars": NodeType.POLARS,
    "edge_join": NodeType.EDGE_JOIN,
    "model_score": NodeType.MODEL_SCORE,
    "banding": NodeType.BANDING,
    "rating_step": NodeType.RATING_STEP,
    "output": NodeType.OUTPUT,
    "explore": NodeType.EXPLORE,
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
    """Config for apiInput nodes.

    See `src/haute/_api_input_schema.py` for the persisted
    `tables[]`/columns structure.
    """

    path: str
    contract: str
    tables: list[dict[str, Any]]


class _DataInputCommon(TypedDict, total=False):
    arguments: dict[str, Any]
    code: str


class _DataInputPolarsCommon(_DataInputCommon, total=False):
    mode: Literal["read", "scan"]


class DataInputFileConfig(_DataInputPolarsCommon, total=False):
    inputType: Required[Literal["file"]]
    format: Required[str]
    path: Required[str]


class DataInputDatabaseConfig(_DataInputCommon, total=False):
    inputType: Required[Literal["database"]]
    format: Required[Literal["database"]]
    connection: str
    uri: str
    query: Required[str]


class DataInputLakehouseConfig(_DataInputPolarsCommon, total=False):
    inputType: Required[Literal["lakehouse"]]
    format: Required[Literal["delta", "iceberg"]]
    path: Required[str]


class DataInputDatabricksConfig(_DataInputCommon, total=False):
    inputType: Required[Literal["databricks"]]
    http_path: Required[str]
    table: Required[str]
    query: str


class DataInputInlineConfig(_DataInputPolarsCommon, total=False):
    inputType: Required[Literal["inline"]]
    format: Required[Literal["records"]]
    records: Required[list[dict[str, Any]]]


DataInputConfig: TypeAlias = (
    DataInputFileConfig
    | DataInputDatabaseConfig
    | DataInputLakehouseConfig
    | DataInputDatabricksConfig
    | DataInputInlineConfig
)
DATA_INPUT_CONFIG_TYPES = (
    DataInputFileConfig,
    DataInputDatabaseConfig,
    DataInputLakehouseConfig,
    DataInputDatabricksConfig,
    DataInputInlineConfig,
)


class _DataOutputCommon(TypedDict, total=False):
    mode: Literal["sink", "write"]
    arguments: dict[str, Any]


class DataOutputFileConfig(_DataOutputCommon, total=False):
    outputType: Required[Literal["file"]]
    format: Required[str]
    path: Required[str]


class DataOutputDatabaseConfig(_DataOutputCommon, total=False):
    outputType: Required[Literal["database"]]
    format: Required[Literal["database"]]
    connection: str
    uri: str
    table: Required[str]


class DataOutputLakehouseConfig(_DataOutputCommon, total=False):
    outputType: Required[Literal["lakehouse"]]
    format: Required[Literal["delta", "iceberg"]]
    path: Required[str]


DataOutputConfig: TypeAlias = (
    DataOutputFileConfig | DataOutputDatabaseConfig | DataOutputLakehouseConfig
)
DATA_OUTPUT_CONFIG_TYPES = (
    DataOutputFileConfig,
    DataOutputDatabaseConfig,
    DataOutputLakehouseConfig,
)


class TransformConfig(TypedDict, total=False):
    """Config for transform nodes."""

    code: str
    instanceOf: str
    inputMapping: dict[str, str]
    selected_columns: list[str]


class EdgeJoinConfig(TypedDict, total=False):
    """Config for edgeJoin nodes."""

    how: str
    on: str | list[str]
    leftOn: str | list[str]
    rightOn: str | list[str]
    suffix: str
    coalesce: bool
    validate: str
    maintainOrder: str


EDGE_JOIN_CONFIG_KEYS: tuple[str, ...] = (
    "how",
    "on",
    "leftOn",
    "rightOn",
    "suffix",
    "coalesce",
    "validate",
    "maintainOrder",
)


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
    categorical_levels: dict[str, list[str | None]]
    code: str  # optional post-processing code
    instanceOf: str
    inputMapping: dict[str, str]


class BandingFactor(TypedDict, total=False):
    """A single factor in a banding node config."""

    banding: Literal["continuous", "categorical", "breakpoints"]
    column: str
    outputColumn: str
    rules: list[dict[str, Any]] | dict[str, Any]
    default: str | None
    rightClosed: bool


class BandingConfig(TypedDict, total=False):
    """Config for banding nodes."""

    factors: list[BandingFactor]


class RatingTableEntry(TypedDict, total=False):
    """A single entry (row) in a rating table."""

    # Keys are dynamic factor names; values are strings/numbers


class RatingTable(TypedDict, total=False):
    """A single table in a ratingStep config."""

    factors: list[str]
    factorDtypes: dict[str, dict[str, Any]]
    outputColumn: str
    defaultValue: str | None
    # Miss policy when no usable defaultValue exists: "error" (default)
    # fails loudly at materialisation; "neutral" opts in to null table
    # output (combined outputs fill the operation's neutral element) with
    # misses counted and logged at WARNING.
    onMissing: str
    entries: list[dict[str, Any]]


class RatingCombinedOutput(TypedDict):
    """One canonical combined output in a ratingStep config."""

    outputColumn: str
    operation: str
    baseValue: float


class RatingStepConfig(TypedDict, total=False):
    """Config for ratingStep nodes."""

    tables: list[RatingTable]
    combinedOutputs: list[RatingCombinedOutput]
    code: str


class OutputMappingEntry(TypedDict):
    """One row of an OUTPUT node's ``outputMapping`` (STATE_OF_PLAY §4 B1).

    A source column, the destination JSONPath it populates, and a per-row
    enable toggle. One column duplicated to several paths appears as several
    entries (the multi-map); ``source_port`` names the incoming frame.
    """

    source_port: str
    source_column: str
    output_path: str
    enabled: bool


class OutputConfig(TypedDict, total=False):
    """Config for output nodes."""

    outputMapping: list[OutputMappingEntry]
    outputFormat: str  # "json" (only "json" built initially; jsonl/jsonseq later)


class ExploreOverviewConfig(TypedDict, total=False):
    """Config for the overview-cards block of an explore node.

    Each field toggles a single overview card in the UI.  Kept as a separate
    TypedDict so the structure round-trips cleanly through codegen as a
    decorator kwarg (``@pipeline.explore(overview={...})``).
    """

    dataset_snapshot: bool
    data_quality: bool
    numeric_summary: bool
    categorical_summary: bool
    schema: bool


class ExploreChartStyle(TypedDict):
    mark: Literal["column", "line", "area"]
    axis: Literal["primary", "secondary"]
    stack_group: str | None
    stack_normalize: bool
    color: str | None
    data_labels: bool
    markers: bool


class ExploreChartValueEncoding(ExploreChartStyle):
    id: str
    value_id: str


class ExploreChartSeriesOverride(ExploreChartStyle):
    id: str
    series_key: str


class ExploreChartCategory(TypedDict):
    source: Literal["rows"]
    include_grand_total: bool
    label_rotation: int


class ExploreChartAxis(TypedDict):
    title: str
    minimum: int | float | None
    maximum: int | float | None
    number_format: Literal[
        "inherit", "number", "integer", "percent", "currency_gbp", "currency_usd", "currency_eur"
    ]


class ExploreChartSecondaryAxis(ExploreChartAxis):
    enabled: bool


class ExploreChartAxes(TypedDict):
    primary: ExploreChartAxis
    secondary: ExploreChartSecondaryAxis


class ExploreChartLegend(TypedDict):
    visible: bool
    position: Literal["top", "right", "bottom", "left"]


class ExploreChartConfig(TypedDict):
    """Persisted version-1 state for one Explore chart card."""

    version: Literal[1]
    id: str
    name: str
    enabled: bool
    pivot_id: str | None
    kind: Literal["combo"]
    orientation: Literal["vertical", "horizontal"]
    category: ExploreChartCategory
    value_encodings: list[ExploreChartValueEncoding]
    series_overrides: list[ExploreChartSeriesOverride]
    axes: ExploreChartAxes
    legend: ExploreChartLegend


class ExplorePivotMember(TypedDict):
    kind: Literal[
        "null",
        "string",
        "boolean",
        "integer",
        "float",
        "nan",
        "date",
        "datetime",
        "time",
        "decimal",
    ]
    value: str | float | int | bool | None


class ExplorePivotFilterPlacement(TypedDict):
    id: str
    field: str
    members: list[ExplorePivotMember]


class ExplorePivotAxisPlacement(TypedDict):
    id: str
    field: str
    decimal_places: int | None
    number_format: Literal[
        "general", "number", "percent", "currency_gbp", "currency_usd", "currency_eur"
    ]
    use_grouping: bool


class ExplorePivotRowPlacement(ExplorePivotAxisPlacement):
    sort: Literal["ascending", "descending"]


class ExplorePivotValuePlacement(TypedDict):
    id: str
    field: str
    aggregation: Literal["sum", "count", "average", "min", "max", "median", "distinct_count"]
    reference: str
    display_name: str
    sort_rows: Literal["none", "ascending", "descending"]
    color_scale: Literal["none", "low_red_high_green", "low_green_high_red"]
    color_scale_split_by: str | None
    decimal_places: int | None
    number_format: Literal[
        "general", "number", "percent", "currency_gbp", "currency_usd", "currency_eur"
    ]
    use_grouping: bool


class ExplorePivotFormula(TypedDict):
    id: str
    reference: str
    display_name: str
    expression: str
    decimal_places: int | None
    number_format: Literal[
        "general", "number", "percent", "currency_gbp", "currency_usd", "currency_eur"
    ]
    use_grouping: bool


class ExplorePivotOptions(TypedDict):
    row_grand_totals: bool
    column_grand_totals: bool
    sort_by: str | None


class ExplorePivotConfig(TypedDict):
    """Resolved runtime state for one version-1 Explore pivot card."""

    version: Literal[1]
    id: str
    name: str
    enabled: bool
    filters: list[ExplorePivotFilterPlacement]
    columns: list[ExplorePivotAxisPlacement]
    rows: list[ExplorePivotRowPlacement]
    values: list[ExplorePivotValuePlacement]
    formulas: list[ExplorePivotFormula]
    value_order: list[str]
    options: ExplorePivotOptions


class ExplorePivotPersistedConfig(TypedDict):
    """Persisted pivot state; shared formulas are selected by id only."""

    version: Literal[1]
    id: str
    name: str
    enabled: bool
    filters: list[ExplorePivotFilterPlacement]
    columns: list[ExplorePivotAxisPlacement]
    rows: list[ExplorePivotRowPlacement]
    values: list[ExplorePivotValuePlacement]
    formulas: list[str]
    value_order: list[str]
    options: ExplorePivotOptions


class ExploreConfig(TypedDict, total=False):
    """Config for explore nodes."""

    code: str
    overview: ExploreOverviewConfig
    pivot_formulas: list[ExplorePivotFormula]
    pivots: list[ExplorePivotPersistedConfig]
    charts: list[ExploreChartConfig]


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
    feature_columns: list[str]
    exclude: list[str]
    algorithm: str  # "catboost" | "glm"
    task: str  # "regression" | "classification"
    params: dict[str, Any]
    evaluation: dict[str, Any]
    tuning: dict[str, Any]
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
    fold_column: str
    id_columns: list[str]
    categorical_levels: dict[str, list[str | None]]


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

    # Executable incoming-edge frame name selected for optimisation.
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
    ratebook_input: str  # executable incoming-edge frame name for ratebook artifacts
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
    "categorical_levels",
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
    "evaluation",
    "tuning",
    "metrics",
    "mlflow_experiment",
    "model_name",
    "output_dir",
    "categorical_levels",
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
    "frontier_ranges",
    "frontier_steps",
    "factor_columns",
    "candidate_min",
    "candidate_max",
    "candidate_steps",
    "max_cd_iterations",
    "cd_tolerance",
    "structure_mode",
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


class SubmodelConfig(TypedDict):
    """Canonical identity for one submodel occurrence node."""

    definitionId: str
    alias: str


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
    # A submodel occurrence consumes one handle per boundary side to encode
    # ``in__<public-port>`` / ``out__<public-port>``. Its definition resolves
    # that public port to an internal endpoint. Preserve the authored connect
    # port separately until flattening or codegen restores it. Ordinary edges
    # omit these fields from serialized payloads.
    sourcePort: str | None = Field(  # noqa: N815 — serialized graph convention
        default=None,
        exclude_if=lambda value: value is None,
    )
    targetPort: str | None = Field(  # noqa: N815 — serialized graph convention
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_validator(
        "sourceHandle",
        "targetHandle",
        "sourcePort",
        "targetPort",
        mode="before",
    )
    @classmethod
    def _reject_empty_handle(cls, v: object) -> object:
        """An edge handle is either a non-empty port name OR ``None``.

        Empty string is NOT silently coerced to ``None`` — that would mask
        the case where a port is legitimately named ``""`` (which itself is
        invalid, but for a different reason and at a different layer).
        See MULTI_FRAME_PLAN.md §4b for the full reasoning.
        """
        if isinstance(v, str) and v == "":
            raise ValueError(
                "Edge handle must be either a non-empty port name or null; "
                "got empty string. Use null to signal 'no port specified'.",
            )
        return v


class SubmodelEndpoint(BaseModel):
    """One stable public-port binding to an internal definition endpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    node_id: str = Field(alias="nodeId")
    handle_id: str | None = Field(default=None, alias="handleId")

    @field_validator("node_id", "handle_id")
    @classmethod
    def _validate_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip():
            raise ValueError("Submodel endpoint identities must be non-empty and unpadded.")
        return value


class SubmodelInputPort(BaseModel):
    """A public input with ordered internal targets, or none while unrouted."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    targets: list[SubmodelEndpoint]

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        sanitised = _sanitize_func_name(value)
        if sanitised != value:
            raise ValueError(
                f"Submodel port names must be canonical identifiers "
                f"(got {value!r}; expected {sanitised!r})."
            )
        return value

    @model_validator(mode="after")
    def _reject_duplicate_targets(self) -> Self:
        identities = [(target.node_id, target.handle_id) for target in self.targets]
        if len(identities) != len(set(identities)):
            raise ValueError(f"Submodel input port {self.name!r} has duplicate targets.")
        return self


class SubmodelOutputPort(BaseModel):
    """A public output backed by exactly one internal source."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    source: SubmodelEndpoint

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        sanitised = _sanitize_func_name(value)
        if sanitised != value:
            raise ValueError(
                f"Submodel port names must be canonical identifiers "
                f"(got {value!r}; expected {sanitised!r})."
            )
        return value


class SubmodelInstanceConfig(BaseModel):
    """Typed identity carried by one parent-graph SUBMODEL occurrence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    definition_id: str = Field(alias="definitionId")
    alias: str
    instance_of: str | None = Field(
        default=None,
        alias="instanceOf",
        exclude_if=lambda value: value is None,
    )

    @field_validator("definition_id", "alias", "instance_of")
    @classmethod
    def _validate_identity(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value or value != value.strip():
            raise ValueError("Submodel definition ids and aliases must be non-empty and unpadded.")
        return value

    @field_validator("alias")
    @classmethod
    def _validate_canonical_alias(cls, value: str) -> str:
        sanitised = _sanitize_func_name(value)
        if sanitised != value:
            raise ValueError(
                f"Submodel occurrence aliases must be canonical identifiers "
                f"(got {value!r}; expected {sanitised!r})."
            )
        return value


class SubmodelDefinition(BaseModel):
    """One shared, file-backed submodel definition and its public interface."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    definition_id: str = Field(alias="definitionId")
    file: str
    graph: PipelineGraph
    input_ports: list[SubmodelInputPort] = Field(alias="inputPorts")
    output_ports: list[SubmodelOutputPort] = Field(alias="outputPorts")

    @field_validator("definition_id", "file")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("Submodel definition ids and files must be non-empty and unpadded.")
        return value

    @model_validator(mode="after")
    def _validate_interface(self) -> Self:
        if self.graph.submodels:
            raise ValueError("Nested submodels are not supported in a definition graph.")

        port_names = [
            *(port.name for port in self.input_ports),
            *(port.name for port in self.output_ports),
        ]
        duplicates = sorted(name for name in set(port_names) if port_names.count(name) > 1)
        if duplicates:
            raise ValueError(
                f"Submodel definition has duplicate public port names: {duplicates!r}."
            )

        graph_node_ids = {node.id for node in self.graph.nodes}
        endpoint_ids = [target.node_id for port in self.input_ports for target in port.targets]
        endpoint_ids.extend(port.source.node_id for port in self.output_ports)
        missing = sorted(set(endpoint_ids) - graph_node_ids)
        if missing:
            raise ValueError(
                f"Submodel public port endpoint references missing child nodes: {missing!r}."
            )
        return self


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
    source_revision: str | None = None
    submodels: dict[str, SubmodelDefinition] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_editor_recovery_document(cls, value: object) -> object:
        if isinstance(value, Mapping) and value.get("document_kind") == (
            "haute.pipeline_editor_document"
        ):
            raise ValueError("Editor recovery documents are not canonical pipeline graphs.")
        return value

    @field_validator("submodels", mode="before")
    @classmethod
    def _validate_submodel_definitions(
        cls,
        value: object,
    ) -> object:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("PipelineGraph.submodels must be a definition mapping.")

        definitions: dict[str, SubmodelDefinition] = {}
        for raw_key, raw_definition in value.items():
            if not isinstance(raw_key, str) or not raw_key or raw_key != raw_key.strip():
                raise ValueError("Submodel definition registry keys must be non-empty strings.")
            try:
                definition = (
                    raw_definition
                    if isinstance(raw_definition, SubmodelDefinition)
                    else SubmodelDefinition.model_validate(raw_definition)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Submodel definition {raw_key!r} must match the canonical schema: {exc}"
                ) from exc
            if definition.definition_id != raw_key:
                raise ValueError(
                    "Submodel definition registry key does not match definitionId: "
                    f"{raw_key!r} != {definition.definition_id!r}."
                )
            definitions[raw_key] = definition
        return definitions

    warning: str | None = None
    sources: list[str] = Field(default_factory=lambda: ["live"])
    active_source: str = "live"
    _parser_parameter_names: dict[str, list[str]] = PrivateAttr(default_factory=dict)
    _parser_edge_parameter_names: dict[str, list[str]] = PrivateAttr(default_factory=dict)

    _parser_definition_id: str | None = PrivateAttr(default=None)
    _parser_input_ports: list[SubmodelInputPort] | None = PrivateAttr(default=None)
    _parser_output_ports: list[SubmodelOutputPort] | None = PrivateAttr(default=None)

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


SubmodelDefinition.model_rebuild()
PipelineGraph.model_rebuild()
