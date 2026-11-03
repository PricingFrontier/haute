"""Each node type's config model: the one definition of what its config may hold.

A node type's model is its config ``TypedDict``, which Pydantic validates closed
(``extra="forbid"``) and strict: a key the shape does not declare, a value of
another type, and a value outside a ``Literal`` are refused, and an integer is a
valid number. Every key is optional unless the shape marks it ``Required``; an
absent key is an unset setting, and a key holds ``null`` only where its type says
so, which is where Haute writes ``null`` for a cleared setting. Each key's
attribute docstring is its description: the generated node reference and the
generated browser contract read it.

The shapes stay ``TypedDict``s rather than ``BaseModel`` classes because the
engine reads a config as a ``dict``: the code that handles one, the recovery
walk over its fields and the generated contract all read one definition, and a
key such as ``validate`` or ``model_path`` needs no alias. Value rules that need
more than a type (a banding boundary, a Polars call's arguments, a GLM family and
link) stay the engine's own functions; a :class:`NodeConfigModel`'s hooks call
them, so each rule has one implementation. ``haute._config_validation.
validate_node_config`` is the one validation boundary over
:data:`NODE_CONFIG_MODELS`.
"""

import functools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import (
    Annotated,
    Any,
    Literal,
    NamedTuple,
    NotRequired,
    Required,
    Union,
    get_args,
    get_origin,
)

from pydantic import ConfigDict, Field, TypeAdapter, create_model, with_config
from typing_extensions import TypedDict

from haute._api_input_schema import TableV2
from haute._explore_chart_contracts import ExploreChartConfig
from haute._node_specs import STEPPED_NODE_TYPES
from haute._types import NodeType

_CLOSED = ConfigDict(extra="forbid", strict=True, use_attribute_docstrings=True)

closed = with_config(_CLOSED)
"""Close a config shape: undeclared keys and values of another type are refused."""


# ---------------------------------------------------------------------------
# Shared keys
# ---------------------------------------------------------------------------

MlflowDestination = Literal["", "databricks", "server"]
"""A node's MLflow destination: ``""`` (or no key) is the local folder."""


@closed
class ContractColumns(TypedDict):
    """A declared column contract: the columns a node reads and produces."""

    inputs: list[str] | None
    """The columns the node reads from its input, or `null` when they are not known."""
    outputs: list[str] | None
    """The columns the node produces, or `null` when they are not known."""
    inputs_by_parent: NotRequired[dict[str, list[str] | None] | None]
    """For a node with several inputs, the columns it reads from each input, by node id."""


ContractDeclaration = Literal["opaque"] | ContractColumns
"""The ``contract=`` annotation: ``"opaque"``, or the columns read and produced."""


@closed
class NodeConfigBase(TypedDict, total=False):
    """The keys any node type's config may carry."""

    inputMapping: dict[str, str]
    """The input names the node's code uses, each mapped to the connected input it reads."""
    contract: ContractDeclaration
    """The node's column contract as its decorator declares it: `"opaque"`, or the columns it reads
    and produces."""
    selected_columns: list[str]
    """The columns the node passes downstream, chosen on its **Columns** tab; absent passes all."""
    column_renames: dict[str, str]
    """New names for columns passed downstream, from each column's name to its new one."""


@closed
class CodeSurfaceConfig(NodeConfigBase, total=False):
    """The keys of a node type whose function may carry Polars code."""

    code: str
    """The Polars code the node's function runs; when `steps` are present, their rendering."""
    steps: list[dict[str, Any]]
    """The low-code steps the node's code is rendered from, in order."""


# ---------------------------------------------------------------------------
# Request inputs and responses
# ---------------------------------------------------------------------------


@closed
class ApiInputConfig(NodeConfigBase, total=False):
    """A Quote Input: the request's sample file and the tables read from it."""

    path: str
    """The sample request file, relative to the project root (a JSON, JSONL, NDJSON, XML or flat
    file)."""
    tables: list[TableV2]
    """For a structured request file, the tables read from it: each a path into the document, a
    label, and its columns."""


@closed
class WorkbenchInputConfig(NodeConfigBase, total=False):
    """A Workbench Input: given its tables and sample from the project's form wherever it runs."""

    tables: list[dict[str, Any]]
    """The form's input tables, given to the node where it runs; never stored in its config file."""
    sample: dict[str, Any]
    """The form's sample quote, given to the node where it runs; never stored in its config file."""


@closed
class WorkbenchOutputConfig(NodeConfigBase, total=False):
    """A Workbench Output: fills the form's response tables from its connected frames."""

    tables: list[dict[str, Any]]
    """The form's response tables, given to the node where it runs; never stored in its config file.
    """


@closed
class OutputMappingEntry(TypedDict):
    """One row of a Quote Response's mapping: a column and the response path it fills."""

    source_port: str
    """The incoming frame the column comes from, by its input name."""
    source_column: str
    """The column whose values fill the path."""
    output_path: str
    """The JSONPath in the response document the column fills, such as `$[:].premium`."""
    enabled: bool
    """Whether the row is part of the response."""


@closed
class OutputConfig(NodeConfigBase, total=False):
    """A Quote Response: the JSON response document assembled from mapped columns."""

    outputMapping: list[OutputMappingEntry]
    """The mapping rows, each a column of an incoming frame and the response path it fills."""


# ---------------------------------------------------------------------------
# Data Input and Data Output
# ---------------------------------------------------------------------------


@closed
class _DataInputCommon(CodeSurfaceConfig, total=False):
    arguments: dict[str, Any]
    """Arguments passed to the Polars read or scan call, by parameter name."""


@closed
class _DataInputPolarsCommon(_DataInputCommon, total=False):
    mode: Literal["", "read", "scan"]
    """Whether the file is read eagerly or scanned lazily; empty or absent takes the format's
    default."""


@closed
class DataInputFileConfig(_DataInputPolarsCommon, total=False):
    """A Data Input reading a file."""

    inputType: Required[Literal["file"]]
    """The provider: a file."""
    format: str
    """The file format, such as `parquet`, `csv` or `ndjson`."""
    path: str
    """The file, relative to the project root or absolute."""


@closed
class DataInputDatabaseConfig(_DataInputCommon, total=False):
    """A Data Input reading a database query."""

    inputType: Required[Literal["database"]]
    """The provider: a database."""
    format: Literal["database"]
    """The format of a database read: `database`."""
    connection: str
    """The named connection in `connections.toml`; exactly one of `connection` and `uri`."""
    uri: str
    """A credential-free database URI; exactly one of `connection` and `uri`."""
    query: str
    """The SQL query whose rows the node reads."""


@closed
class DataInputLakehouseConfig(_DataInputPolarsCommon, total=False):
    """A Data Input reading a lakehouse table."""

    inputType: Required[Literal["lakehouse"]]
    """The provider: a lakehouse table."""
    format: Literal["delta", "iceberg"]
    """The table format: `delta` or `iceberg`."""
    path: str
    """The table's location."""


@closed
class DataInputDatabricksConfig(_DataInputCommon, total=False):
    """A Data Input reading a Databricks table."""

    inputType: Required[Literal["databricks"]]
    """The provider: Databricks."""
    http_path: str
    """The SQL warehouse's HTTP path."""
    table: str
    """The table, as `catalog.schema.table`."""
    query: str
    """A SQL query to read instead of the whole table."""


@closed
class DataInputInlineConfig(_DataInputPolarsCommon, total=False):
    """A Data Input holding its rows inline."""

    inputType: Required[Literal["inline"]]
    """The provider: rows held in the config."""
    format: Literal["records"]
    """The format of inline rows: `records`."""
    records: list[dict[str, Any]]
    """The rows, each an object from column name to value."""


DATA_INPUT_CONFIG_TYPES = (
    DataInputFileConfig,
    DataInputDatabaseConfig,
    DataInputLakehouseConfig,
    DataInputDatabricksConfig,
    DataInputInlineConfig,
)
DataInputConfig = Annotated[
    DataInputFileConfig
    | DataInputDatabaseConfig
    | DataInputLakehouseConfig
    | DataInputDatabricksConfig
    | DataInputInlineConfig,
    Field(discriminator="inputType"),
]


@closed
class _DataOutputCommon(NodeConfigBase, total=False):
    arguments: dict[str, Any]
    """Arguments passed to the Polars write or sink call, by parameter name."""


@closed
class _DataOutputPolarsCommon(_DataOutputCommon, total=False):
    mode: Literal["sink", "write"]
    """Whether the frame is streamed to the destination (`sink`) or collected and written (`write`).
    """


@closed
class DataOutputFileConfig(_DataOutputPolarsCommon, total=False):
    """A Data Output writing a file."""

    outputType: Required[Literal["file"]]
    """The destination: a file."""
    format: str
    """The file format, such as `parquet` or `csv`."""
    path: str
    """The file written, relative to the project root or absolute."""


@closed
class DataOutputDatabaseConfig(_DataOutputPolarsCommon, total=False):
    """A Data Output writing a database table."""

    outputType: Required[Literal["database"]]
    """The destination: a database."""
    format: Literal["database"]
    """The format of a database write: `database`."""
    connection: str
    """The named connection in `connections.toml`; exactly one of `connection` and `uri`."""
    uri: str
    """A credential-free database URI; exactly one of `connection` and `uri`."""
    table: str
    """The table written."""


@closed
class DataOutputLakehouseConfig(_DataOutputPolarsCommon, total=False):
    """A Data Output writing a lakehouse table."""

    outputType: Required[Literal["lakehouse"]]
    """The destination: a lakehouse table."""
    format: Literal["delta", "iceberg"]
    """The table format: `delta` or `iceberg`."""
    path: str
    """The table's location."""


@closed
class DataOutputDatabricksConfig(_DataOutputCommon, total=False):
    """A Data Output loading a Databricks table."""

    outputType: Required[Literal["databricks"]]
    """The destination: Databricks."""
    http_path: str
    """The SQL warehouse's HTTP path."""
    table: str
    """The table, as `catalog.schema.table`."""
    write_mode: Literal["overwrite", "append"]
    """Whether a load replaces the table's rows or adds to them."""


DATA_OUTPUT_CONFIG_TYPES = (
    DataOutputFileConfig,
    DataOutputDatabaseConfig,
    DataOutputLakehouseConfig,
    DataOutputDatabricksConfig,
)
DataOutputConfig = Annotated[
    DataOutputFileConfig
    | DataOutputDatabaseConfig
    | DataOutputLakehouseConfig
    | DataOutputDatabricksConfig,
    Field(discriminator="outputType"),
]


# ---------------------------------------------------------------------------
# Transforms, joins and switches
# ---------------------------------------------------------------------------


@closed
class KeywordParameter(TypedDict):
    """One keyword-only parameter of a transform written as code, and its literal default."""

    name: str
    """The parameter's name in the function signature."""
    default: str | int | float | bool | None
    """Its literal default, which a run binds."""


@closed
class TransformConfig(CodeSurfaceConfig, total=False):
    """A Polars transform: code, or the steps it is rendered from."""

    keyword_parameters: list[KeywordParameter]
    """The keyword-only parameters of a transform written as code, each with its literal default."""


@closed
class EdgeJoinConfig(NodeConfigBase, total=False):
    """An Edge Join: joins its `join` input onto its `base` input."""

    how: Literal["inner", "left", "right", "full", "semi", "anti", "cross"]
    """The join type."""
    on: str | list[str]
    """The key column or columns both inputs share; or use `leftOn` and `rightOn`."""
    leftOn: str | list[str]
    """The key column or columns on the base input."""
    rightOn: str | list[str]
    """The key column or columns on the join input, matched in order with `leftOn`."""
    suffix: str
    """The suffix added to a join-input column whose name the base input already has."""
    coalesce: bool | None
    """Whether key columns of the same name are merged into one; `null` leaves it to the join
    type, as Polars does."""
    validate: Literal["1:1", "1:m", "m:1", "m:m"] | None
    """The cardinality the keys must have, checked when the join runs; `null` checks none."""
    maintainOrder: Literal["none", "left", "right", "left_right", "right_left"]
    """Which input's row order the result keeps; required, which the engine's join rules
    check with the values it takes."""


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

COLUMN_CONFIG_KEYS: tuple[str, ...] = (
    "selected_columns",
    "column_renames",
)
"""Authored column metadata any executable node may carry, in the order inline decorators write it.
"""


@closed
class LiveSwitchConfig(NodeConfigBase, total=False):
    """A Source Switch: routes one of its inputs by the run's source."""

    input_scenario_map: dict[str, str]
    """For each connected input, the source that routes to it, such as `live` or `batch`."""
    inputs: list[str]
    """The connected inputs, in the order of the function's parameters (recorded by the parse)."""


# ---------------------------------------------------------------------------
# Scoring and rating
# ---------------------------------------------------------------------------


@closed
class ModelScoreConfig(CodeSurfaceConfig, total=False):
    """A Model Score: scores a model from a file, an MLflow run or a registered model."""

    sourceType: Literal["", "run", "registered", "file"]
    """Where the model comes from; empty or absent when no source is chosen yet."""
    model_path: str
    """For a file source, the model file in the project, such as `models/freq.cbm`."""
    experiment_name: str
    """For a run source, the experiment's name, shown when the panel reopens."""
    experiment_id: str
    """For a run source, the MLflow experiment's id."""
    run_id: str
    """For a run source, the MLflow run whose model is scored."""
    run_name: str
    """For a run source, the run's name, shown when the panel reopens."""
    artifact_path: str
    """For an MLflow source, the model's path inside the run's artifacts, such as `model.cbm`."""
    registered_model: str
    """For a registered source, the registered model's name, such as `catalog.schema.model`."""
    version: str
    """For a registered source, the version scored, or `latest`; absent when `alias` is set."""
    alias: str
    """For a registered source, the alias whose version is scored, such as `champion`; excludes
    `version`."""
    task: Literal["regression", "classification"]
    """Whether the model predicts a value or a class probability."""
    output_column: str
    """The column the prediction is written to; `prediction` when absent."""
    feature_contract_path: str
    """The feature contract the model scores under, when it is not beside the model."""
    mlflow_destination: MlflowDestination
    """The MLflow server or Databricks workspace an MLflow source is read from; empty or absent is
    the local folder."""


@closed
class BandingFactor(TypedDict, total=False):
    """One factor of a Banding node: an input column mapped to bands."""

    banding: Literal["breakpoints", "categorical"]
    """Whether the column is banded by numeric or date boundaries or by category."""
    column: str
    """The input column banded."""
    outputColumn: str
    """The column the band is written to."""
    rules: list[dict[str, Any]] | dict[str, Any]
    """The bands: rule rows in the editor, or a map from each boundary or value to its band in the
    config file."""
    default: str | None
    """The band of a value no rule matches; `null` leaves it empty."""
    rightClosed: bool
    """For breakpoints, whether each band includes its upper boundary (`true`, the default) or its
    lower one."""


@closed
class BandingConfig(NodeConfigBase, total=False):
    """A Banding node: one or more factors banded from input columns."""

    factors: list[BandingFactor]
    """The factors, each an input column, an output column and its bands."""


@closed
class RatingTable(TypedDict, total=False):
    """One table of a Rating Step: a value looked up by up to three factor columns."""

    factors: list[str]
    """The factor columns the table is keyed by, at most three."""
    factorDtypes: dict[str, dict[str, Any]]
    """For each factor, the type its levels are read as."""
    outputColumn: str
    """The column the looked-up value is written to."""
    defaultValue: str | int | float | None
    """The value of a factor combination the table does not hold; `null` for none."""
    onMissing: Literal["error", "neutral"]
    """With no default, whether a missing combination fails the run (`error`) or takes the neutral
    value (`neutral`)."""
    entries: list[dict[str, Any]]
    """The table's rows, each a level of every factor and its `value`."""


@closed
class RatingCombinedOutput(TypedDict):
    """A column a Rating Step combines from its tables' values."""

    outputColumn: str
    """The combined column."""
    operation: Literal["multiply", "add", "min", "max"]
    """How the tables' values are combined."""
    baseValue: str | int | float
    """The value the combination starts from: a number, or the editor's text of one."""


@closed
class RatingStepConfig(CodeSurfaceConfig, total=False):
    """A Rating Step: rating tables looked up by factor and their combined outputs."""

    tables: list[RatingTable]
    """The rating tables."""
    combinedOutputs: list[RatingCombinedOutput]
    """Columns combined from the tables' values."""


@closed
class ScenarioExpanderConfig(CodeSurfaceConfig, total=False):
    """A Scenario Expander: one row per quote and grid value."""

    quote_id: str
    """The column identifying each quote."""
    column_name: str
    """The column the grid value is written to, such as `scenario_value`."""
    min_value: float
    """The grid's first value."""
    max_value: float
    """The grid's last value."""
    stepCount: int
    """The number of grid values, at least 1."""
    step_column: str
    """The column the grid position (from 0) is written to, such as `scenario_index`."""


SCENARIO_EXPANDER_CONFIG_KEYS: tuple[str, ...] = (
    "quote_id",
    "column_name",
    "min_value",
    "max_value",
    "stepCount",
    "step_column",
)


@closed
class ConstantValue(TypedDict, total=False):
    """One value of a Constant node; an entry without a name is skipped."""

    name: str
    """The column the value is written to."""
    value: str | int | float | bool | None
    """The value, read as a number where it is one and kept as it is otherwise."""


@closed
class ConstantConfig(NodeConfigBase, total=False):
    """A Constant node: a one-row frame of named values."""

    values: list[ConstantValue]
    """The values, one column each."""


@closed
class ExternalFileConfig(CodeSurfaceConfig, total=False):
    """A Load File node: loads an object from a file for its code to use."""

    path: str
    """The file, relative to the project root."""
    fileType: Literal["pickle", "json", "joblib"]
    """How the file is loaded."""


# ---------------------------------------------------------------------------
# Explore
# ---------------------------------------------------------------------------


@closed
class ExploreOverviewConfig(TypedDict, total=False):
    """Which overview cards an Explore node shows."""

    dataset_snapshot: bool
    """Show the dataset snapshot card."""
    data_quality: bool
    """Show the data quality card."""
    numeric_summary: bool
    """Show the numeric summary card."""
    categorical_summary: bool
    """Show the categorical summary card."""
    schema: bool
    """Show the schema card."""


PivotNumberFormat = Literal[
    "general", "number", "percent", "currency_gbp", "currency_usd", "currency_eur"
]


@closed
class ExplorePivotMember(TypedDict):
    """One member of a pivot filter, typed so dates and numbers keep their identity."""

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
    """The member's type."""
    value: str | float | int | bool | None
    """The member's value, as JSON holds a value of its type."""


@closed
class ExplorePivotFilterPlacement(TypedDict):
    """A field filtering a pivot to chosen members."""

    id: str
    """The placement's id."""
    field: str
    """The column filtered."""
    members: list[ExplorePivotMember]
    """The members kept."""


@closed
class ExplorePivotAxisPlacement(TypedDict):
    """A field laid across a pivot's columns."""

    id: str
    """The placement's id."""
    field: str
    """The column."""
    decimal_places: int | None
    """Decimal places shown; `null` uses the format's default."""
    number_format: PivotNumberFormat
    """How its values are shown."""
    use_grouping: bool
    """Whether thousands are grouped."""


@closed
class ExplorePivotRowPlacement(ExplorePivotAxisPlacement):
    """A field laid down a pivot's rows."""

    sort: Literal["ascending", "descending"]
    """The order of its members."""


@closed
class ExplorePivotValuePlacement(TypedDict):
    """An aggregated value shown in a pivot's cells."""

    id: str
    """The placement's id."""
    field: str
    """The column aggregated."""
    aggregation: Literal["sum", "count", "average", "min", "max", "median", "distinct_count"]
    """How the column is aggregated."""
    reference: str
    """The name a formula refers to the value by."""
    display_name: str
    """The value's heading."""
    sort_rows: Literal["none", "ascending", "descending"]
    """Whether rows are sorted by this value."""
    color_scale: Literal["none", "low_red_high_green", "low_green_high_red"]
    """The colour scale of its cells."""
    color_scale_split_by: str | None
    """The field the colour scale is computed within; `null` for the whole value."""
    decimal_places: int | None
    """Decimal places shown; `null` uses the format's default."""
    number_format: PivotNumberFormat
    """How its values are shown."""
    use_grouping: bool
    """Whether thousands are grouped."""


@closed
class ExplorePivotFormula(TypedDict):
    """A formula over a pivot's values, shared by the node's pivots."""

    id: str
    """The formula's id."""
    reference: str
    """The name other formulas refer to it by."""
    display_name: str
    """The formula's heading."""
    expression: str
    """The formula, over values' references."""
    decimal_places: int | None
    """Decimal places shown; `null` uses the format's default."""
    number_format: PivotNumberFormat
    """How its values are shown."""
    use_grouping: bool
    """Whether thousands are grouped."""


@closed
class ExplorePivotOptions(TypedDict):
    """A pivot's totals and sorting."""

    row_grand_totals: bool
    """Show a grand total row."""
    column_grand_totals: bool
    """Show a grand total column."""
    sort_by: str | None
    """The value rows are sorted by; `null` for none."""


@closed
class ExplorePivotConfig(TypedDict):
    """Resolved runtime state for one Explore pivot card, with its formulas inlined."""

    version: Literal[1]
    """The pivot state's version."""
    id: str
    """The pivot's id."""
    name: str
    """The pivot's name."""
    enabled: bool
    """Whether the pivot is shown."""
    filters: list[ExplorePivotFilterPlacement]
    """The filters."""
    columns: list[ExplorePivotAxisPlacement]
    """The fields laid across the columns."""
    rows: list[ExplorePivotRowPlacement]
    """The fields laid down the rows."""
    values: list[ExplorePivotValuePlacement]
    """The aggregated values."""
    formulas: list[ExplorePivotFormula]
    """The formulas the pivot shows."""
    value_order: list[str]
    """The order its values and formulas are shown in, by id."""
    options: ExplorePivotOptions
    """Its totals and sorting."""


@closed
class ExplorePivotPersistedConfig(TypedDict):
    """One Explore pivot card as stored: shared formulas are selected by id."""

    version: Literal[1]
    """The pivot state's version."""
    id: str
    """The pivot's id."""
    name: str
    """The pivot's name."""
    enabled: bool
    """Whether the pivot is shown."""
    filters: list[ExplorePivotFilterPlacement]
    """The filters."""
    columns: list[ExplorePivotAxisPlacement]
    """The fields laid across the columns."""
    rows: list[ExplorePivotRowPlacement]
    """The fields laid down the rows."""
    values: list[ExplorePivotValuePlacement]
    """The aggregated values."""
    formulas: list[str]
    """The shared formulas the pivot shows, by id."""
    value_order: list[str]
    """The order its values and formulas are shown in, by id."""
    options: ExplorePivotOptions
    """Its totals and sorting."""


@closed
class ExploreConfig(CodeSurfaceConfig, total=False):
    """An Explore node: overview cards, pivots and charts over its input."""

    overview: ExploreOverviewConfig
    """Which overview cards are shown."""
    pivot_formulas: list[ExplorePivotFormula]
    """The formulas the node's pivots share."""
    pivots: list[ExplorePivotPersistedConfig]
    """The pivot cards."""
    charts: list[ExploreChartConfig]
    """The chart cards."""


# ---------------------------------------------------------------------------
# Model training and optimisation
# ---------------------------------------------------------------------------


@closed
class ModellingConfig(NodeConfigBase, total=False):
    """A Model Training node: trains a model on its input."""

    name: str
    """The model's name, used for its run and exported file."""
    target: str
    """The column the model predicts."""
    weight: str
    """The column weighting each row, such as exposure."""
    feature_columns: list[str]
    """The columns the model trains on."""
    algorithm: str
    """The model family: `catboost`, `glm`, `xgboost`, `lightgbm`, `ebm` or `tboost`; CatBoost when
    absent."""
    task: Literal["regression", "classification"]
    """Whether the model predicts a value or a class."""
    params: dict[str, Any]
    """The family's own parameters, by name."""
    evaluation: dict[str, Any]
    """How the model is evaluated: the holdout or cross-validation split."""
    tuning: dict[str, Any] | None
    """Hyperparameter tuning settings; `null` or absent trains with `params` as set."""
    refit_on_development: bool
    """Whether the final model is refit on all development rows (`true`, the default) or keeps the
    holdout fit."""
    metrics: list[str]
    """The evaluation metrics reported."""
    mlflow_experiment: str
    """The MLflow experiment a logged run goes to."""
    output_dir: str
    """The folder a trained model is written to."""
    model_export_path: str
    """The Export pane's file path; empty or absent uses the default."""
    row_limit: int | None
    """The most rows trained on; `null` trains on every row."""
    terms: dict[str, Any]
    """For a GLM, each feature's term: linear, categorical, a spline or a banded factor."""
    family: str
    """For a GLM, the error distribution, such as `poisson` or `gamma`."""
    link: str
    """For a GLM, the link function, such as `log`."""
    offset: str | None
    """For a GLM, the offset column; `null` for none."""
    interactions: list[dict[str, Any]]
    """For a GLM, interaction terms between features."""
    regularization: str | None
    """For a GLM, the penalty: `ridge`, `lasso` or `elastic_net`; empty or `null` for none."""
    alpha: float | None
    """For a GLM, the penalty strength; `null` chooses it by cross-validation."""
    l1_ratio: float
    """For an elastic-net GLM, the share of the L1 penalty, from 0 to 1."""
    cv_folds: int
    """For a GLM, the cross-validation folds that choose the penalty strength (2 to 20)."""
    cv_selection: Literal["min", "1se"]
    """For a GLM, which cross-validated strength is chosen: the minimum or one standard error above
    it."""
    cv_seed: int
    """For a GLM, the seed of the cross-validation split."""
    max_iter: int | None
    """For a GLM, the most solver iterations; `null` takes the solver's default."""
    tol: float | None
    """For a GLM, the solver's convergence tolerance; `null` takes the solver's default."""
    robust_standard_errors: Literal["HC0", "HC1", "HC2", "HC3"] | None
    """For a GLM, the robust standard error type; `null` for model-based errors."""
    intercept: bool
    """For a GLM, whether the model has an intercept."""
    var_power: float | None
    """For a Tweedie GLM, the variance power (1 to 2); `null` when not set."""
    theta: float | None
    """For a negative binomial GLM, the dispersion; `null` when not set."""
    loss_function: str | None
    """The loss trained on, from the family's own losses; `null` takes the task's default."""
    variance_power: float | None
    """For a Tweedie loss, the variance power; `null` when not set."""
    monotone_constraints: dict[str, int] | None
    """For each constrained feature, `1` (increasing) or `-1` (decreasing); `null` for none."""
    feature_weights: dict[str, float]
    """For each weighted feature, its weight in feature selection."""
    fold_column: str
    """The column assigning rows to cross-validation folds."""
    id_columns: list[str]
    """Columns identifying rows, kept with the predictions but never trained on."""
    mlflow_destination: MlflowDestination
    """The MLflow server or Databricks workspace a run is logged to; empty or absent is the local
    folder."""
    positive_class: str | int | float | bool
    """For a binary classifier, the label trained as positive; `true` or `1` when absent."""
    device: Literal["cpu", "gpu"]
    """For XGBoost, whether it trains on the CPU or a GPU; the CPU when absent."""


MODELLING_CONFIG_KEYS: tuple[str, ...] = (
    "name",
    "target",
    "weight",
    "algorithm",
    "task",
    "params",
    "evaluation",
    "tuning",
    "metrics",
    "mlflow_experiment",
    "output_dir",
    "mlflow_destination",
    "model_export_path",
)


@closed
class OptimiserConfig(NodeConfigBase, total=False):
    """An Optimiser node: chooses each quote's scenario under constraints."""

    mode: Literal["online", "ratebook"]
    """Whether each quote is optimised (`online`) or a factor table is fitted (`ratebook`)."""
    quote_id: str
    """The column identifying each quote."""
    scenario_index: str
    """The column holding each scenario's grid position."""
    scenario_value: str
    """The column holding each scenario's value."""
    objective: str
    """The column the optimiser maximises."""
    constraints: dict[str, dict[str, float]]
    """For each constrained column, its bound: `min`, `max`, `min_pct` or `max_pct`."""
    max_iter: int
    """The most solver iterations."""
    tolerance: float
    """The solver's convergence tolerance."""
    frontier_ranges: dict[str, dict[str, float]]
    """For each constraint swept by the frontier, its range."""
    frontier_steps: int
    """The number of points on the frontier."""
    factor_columns: list[list[str]]
    """For a ratebook, the factor groups the table is fitted over."""
    max_cd_iterations: int
    """For a ratebook, the most coordinate-descent passes."""
    cd_tolerance: float
    """For a ratebook, the coordinate-descent tolerance."""
    data_input: str
    """The connected input optimised, by its input name; may be absent with one input."""
    banding_source: str
    """For a ratebook, the connected input giving the factors' bands."""
    analysis_input: str
    """The connected input the analysis columns come from; `data_input` when absent."""
    analysis_columns: list[str]
    """Up to 12 columns the result is broken down by; never given to the solver."""
    mlflow_experiment: str
    """The MLflow experiment a logged result goes to."""
    mlflow_destination: MlflowDestination
    """The MLflow server or Databricks workspace a result is logged to; empty or absent is the local
    folder."""
    result_export_path: str
    """The Export pane's save path, relative to the project root."""


OPTIMISER_CONFIG_KEYS: tuple[str, ...] = (
    "mode",
    "quote_id",
    "scenario_index",
    "scenario_value",
    "objective",
    "constraints",
    "max_iter",
    "tolerance",
    "frontier_ranges",
    "frontier_steps",
    "factor_columns",
    "max_cd_iterations",
    "cd_tolerance",
    "data_input",
    "banding_source",
    "analysis_input",
    "analysis_columns",
    "mlflow_experiment",
    "mlflow_destination",
    "result_export_path",
)


@closed
class OptimiserApplyConfig(NodeConfigBase, total=False):
    """An Optimiser Apply node: applies a saved optimiser result to quotes."""

    sourceType: Literal["", "file", "run", "registered"]
    """Where the optimiser result comes from; empty or absent when none is chosen yet."""
    artifact_path: str
    """For a file source, the saved optimiser result; for an MLflow source, its path in the run's
    artifacts."""
    version_column: str
    """The column the applied result's version is written to."""
    optimised_value_column: str
    """The column the chosen scenario value is written to."""
    optimiser_mode: Literal["", "online", "ratebook"]
    """The mode of the applied result, when known."""
    ratebook_input: str
    """For a ratebook result, the connected input it is applied to, by its input name."""
    registered_model: str
    """For a registered source, the registered model's name."""
    version: str
    """For a registered source, the version applied, or `latest`; absent when `alias` is set."""
    alias: str
    """For a registered source, the alias whose version is applied; excludes `version`."""
    experiment_id: str
    """For a run source, the MLflow experiment's id."""
    experiment_name: str
    """For a run source, the experiment's name, shown when the panel reopens."""
    run_id: str
    """For a run source, the MLflow run holding the result."""
    run_name: str
    """For a run source, the run's name, shown when the panel reopens."""
    mlflow_destination: MlflowDestination
    """The MLflow server or Databricks workspace an MLflow source is read from; empty or absent is
    the local folder."""


OPTIMISER_APPLY_CONFIG_KEYS: tuple[str, ...] = (
    "artifact_path",
    "version_column",
    "optimised_value_column",
    "optimiser_mode",
    "ratebook_input",
    "sourceType",
    "registered_model",
    "version",
    "alias",
    "experiment_id",
    "experiment_name",
    "run_id",
    "run_name",
    "mlflow_destination",
)

MODEL_SCORE_CONFIG_KEYS: tuple[str, ...] = (
    "sourceType",
    "run_id",
    "artifact_path",
    "run_name",
    "registered_model",
    "version",
    "alias",
    "task",
    "output_column",
    "experiment_name",
    "experiment_id",
    "mlflow_destination",
)


# ---------------------------------------------------------------------------
# Submodels
# ---------------------------------------------------------------------------


@closed
class SubmodelConfig(NodeConfigBase, total=False):
    """A submodel occurrence: which definition it is, and its name in the pipeline."""

    definitionId: str
    """The submodel definition this occurrence runs."""
    alias: str
    """The occurrence's name in the pipeline."""
    instanceOf: str
    """For an instance, the occurrence whose definition it shares."""


@closed
class SubmodelPortConfig(NodeConfigBase, total=False):
    """A submodel's input or output port: a structural node with no settings of its own."""


# ---------------------------------------------------------------------------
# The models: each node type's shape and its hooks into the engine's rules
# ---------------------------------------------------------------------------


class ConfigGap(NamedTuple):
    """What an unfinished but valid config lacks before its node can run."""

    path: str
    """The setting's path in the config, such as `path` or `input_scenario_map.quotes`."""
    code: str
    """`required`, `incomplete`, `invalid_syntax` or `invalid_reference`."""
    message: str
    """The engine's own message."""


CheckHook = Callable[[Mapping[str, Any]], None]
InputsHook = Callable[[Mapping[str, Any], Sequence[str]], None]
CompletenessHook = Callable[[Mapping[str, Any], Sequence[str] | None], list[ConfigGap]]


@dataclass(frozen=True, slots=True)
class NodeConfigModel:
    """One node type's config model: its shape and the engine's rules beyond it."""

    node_type: NodeType
    shape: Any
    check: CheckHook | None = None
    check_inputs: InputsHook | None = None
    #: What an unfinished config lacks. A stepped type's reports its steps that do not
    #: render yet first, then this hook's gaps (``__post_init__``).
    completeness: CompletenessHook | None = None
    #: The key that selects the config's branch (a Data Input's ``inputType``, a Model
    #: Score's ``sourceType``), whose values are its ``Literal``'s, ``""`` aside.
    discriminant: str | None = None
    #: The discriminant's values when the engine, not a ``Literal``, holds them.
    discriminant_values: Callable[[], frozenset[str]] | None = None
    #: The builder applies ``check`` as its first step, so a run leaves it to the
    #: builder rather than pay for it twice (a Rating Step's tables' normalisation).
    builder_checks: bool = False
    keys: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", frozenset(_shape_keys(self.shape)))
        if self.node_type in STEPPED_NODE_TYPES:
            object.__setattr__(self, "completeness", _stepped(self.node_type, self.completeness))

    @property
    def adapter(self) -> TypeAdapter[Any]:
        """The shape's validator, built on first use: building every type's at import
        would add half a second to the server's start."""
        adapter = _ADAPTERS.get(self.node_type)
        if adapter is None:
            adapter = _ADAPTERS[self.node_type] = TypeAdapter(self.shape)
        return adapter


#: Each node type's built validator (``NodeConfigModel.adapter``).
_ADAPTERS: dict[NodeType, TypeAdapter[Any]] = {}


def branch_values(model: NodeConfigModel) -> frozenset[str]:
    """The values that select one of *model*'s branches; empty without a discriminant."""
    if model.discriminant is None:
        return frozenset()
    if model.discriminant_values is not None:
        return model.discriminant_values()
    from typing import get_type_hints

    values: set[str] = set()
    for variant in shape_variants(model.shape):
        annotation = get_type_hints(variant).get(model.discriminant)
        values.update(value for value in get_args(annotation) if isinstance(value, str))
    return frozenset(values - {""})


def _modelling_algorithms() -> frozenset[str]:
    from haute.modelling._descriptors import DESCRIPTORS

    return frozenset(DESCRIPTORS)


def shape_variants(shape: Any) -> tuple[Any, ...]:
    """The ``TypedDict`` branches of a shape: itself, or each member of a (tagged) union."""
    if get_origin(shape) is Annotated:
        shape = get_args(shape)[0]
    if get_origin(shape) is None and not get_args(shape):
        return (shape,)
    return tuple(member for member in get_args(shape) if isinstance(member, type))


def _shape_keys(shape: Any) -> set[str]:
    keys: set[str] = set()
    for variant in shape_variants(shape):
        keys.update(variant.__annotations__)
        for base in variant.__mro__:
            keys.update(getattr(base, "__annotations__", {}))
    return keys


def _stepped_gaps(
    node_type: NodeType, config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    """The steps a stepped node holds that do not render yet."""
    from haute._polars_steps import (
        PolarsStepError,
        is_stepped_config,
        render_polars_steps,
        stepped_surface_for,
    )

    if not is_stepped_config(node_type, dict(config)):
        return []
    surface = stepped_surface_for(node_type)
    eligible: list[str] | None = (
        [] if surface.inputs == "none" else (None if input_names is None else list(input_names))
    )
    try:
        render_polars_steps(config["steps"], eligible, start=surface.start)
    except PolarsStepError as exc:
        return [ConfigGap("steps", "incomplete", str(exc))]
    return []


def _stepped(node_type: NodeType, other: CompletenessHook | None = None) -> CompletenessHook:
    def completeness(
        config: Mapping[str, Any], input_names: Sequence[str] | None
    ) -> list[ConfigGap]:
        gaps = _stepped_gaps(node_type, config, input_names)
        return gaps + (other(config, input_names) if other is not None else [])

    return completeness


def _io_gaps(gaps: Sequence[Any]) -> list[ConfigGap]:
    return [ConfigGap(gap.path, gap.code, gap.message) for gap in gaps]


def _api_input_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    """The schema rules a Quote Input's tables do not meet yet.

    The editor saves its unfinished rows (a table or column not yet named, a path
    still to set) and its panel shows them, so the rules are completeness here; a
    run applies them in full before it reads a request (``_node_apply``).
    """
    from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema

    if "tables" not in config:
        return []
    try:
        validate_v2_schema(dict(config))
    except ApiInputSchemaError as exc:
        return [ConfigGap("tables", "invalid_schema", str(exc))]
    return []


def _check_data_input(config: Mapping[str, Any]) -> None:
    from haute._polars_io_registry import validate_data_input_config

    validate_data_input_config(config, require_complete=False)


def _data_input_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    from haute._polars_io_registry import data_input_completeness

    return _io_gaps(data_input_completeness(config))


def _check_data_output(config: Mapping[str, Any]) -> None:
    from haute._polars_io_registry import validate_data_output_config

    validate_data_output_config(config, require_complete=False)


def _data_output_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    from haute._polars_io_registry import data_output_completeness

    return _io_gaps(data_output_completeness(config))


def _check_transform(config: Mapping[str, Any]) -> None:
    from haute._keyword_parameters import validate_keyword_parameters

    validate_keyword_parameters(dict(config))


def _transform_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    import ast

    from haute._polars_steps import is_stepped_config

    if is_stepped_config(NodeType.POLARS, dict(config)):
        return []
    code = config.get("code")
    if not isinstance(code, str) or not code.strip():
        return [ConfigGap("code", "required", "Polars code must be non-empty.")]
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return [ConfigGap("code", "invalid_syntax", str(exc.msg))]
    return []


def _check_edge_join(config: Mapping[str, Any]) -> None:
    from haute._edge_join import build_edge_join_kwargs

    build_edge_join_kwargs(dict(config))


def _check_model_score(config: Mapping[str, Any]) -> None:
    from haute._config_validation import validate_registered_model_alias
    from haute._model_source import IncompleteModelSourceError, parse_model_source

    validate_registered_model_alias(NodeType.MODEL_SCORE, config)
    try:
        parse_model_source(config)
    except IncompleteModelSourceError:
        # A source mode picked before its model: unfinished, which completeness names.
        return


def _model_score_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    from haute._model_source import IncompleteModelSourceError, parse_model_source

    try:
        parse_model_source(config)
    except IncompleteModelSourceError as exc:
        return [
            ConfigGap(str(exc.context.get("missing_field") or "sourceType"), "required", str(exc))
        ]
    return []


def _check_banding(config: Mapping[str, Any]) -> None:
    from haute._rating import validate_banding_config

    validate_banding_config(dict(config))


def _check_rating_step(config: Mapping[str, Any]) -> None:
    from haute._rating import normalise_combined_outputs
    from haute._rating_step_config import normalise_rating_step_config

    normalise_rating_step_config(dict(config))
    normalise_combined_outputs(dict(config))


def _check_output(config: Mapping[str, Any]) -> None:
    from haute._output_assembler import validate_v2_output_mapping

    mapping = config.get("outputMapping")
    if mapping is not None:
        validate_v2_output_mapping(list(mapping))


def _output_gaps(config: Mapping[str, Any], input_names: Sequence[str] | None) -> list[ConfigGap]:
    if "outputMapping" in config:
        return []
    return [ConfigGap("outputMapping", "required", "Quote Response has no outputMapping.")]


def _check_explore(config: Mapping[str, Any]) -> None:
    from haute._explore_charts import validate_explore_charts
    from haute._explore_overview import validate_explore_overview
    from haute._explore_pivots import validate_explore_pivot_state

    validate_explore_overview(config.get("overview", {}), context="config")
    validate_explore_pivot_state(
        config.get("pivot_formulas", []), config.get("pivots", []), context="config"
    )
    validate_explore_charts(config.get("charts", []), context="config")


def _external_file_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    gaps = []
    path = config.get("path")
    if not isinstance(path, str) or not path.strip():
        gaps.append(ConfigGap("path", "required", "external file config requires non-empty 'path'"))
    if not config.get("fileType"):
        gaps.append(
            ConfigGap("fileType", "required", "external file config requires non-empty 'fileType'")
        )
    return gaps


def _check_live_switch(config: Mapping[str, Any]) -> None:
    from haute.errors import NodeConfigError

    for name, scenario in (config.get("input_scenario_map") or {}).items():
        if not scenario:
            raise NodeConfigError(
                f"Source Switch input {name!r} maps to an empty source; choose a source "
                "or set the input to '-'.",
                setting=f"input_scenario_map.{name}",
            )


def _live_switch_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    if input_names is None:
        return []
    connected = set(input_names)
    return [
        ConfigGap(
            f"input_scenario_map.{name}",
            "invalid_reference",
            f"Source Switch maps {name!r}, which is no longer connected.",
        )
        for name in (config.get("input_scenario_map") or {})
        if name not in connected
    ]


def _check_modelling(config: Mapping[str, Any]) -> None:
    from haute.modelling._train_config import (
        blank_evaluation_settings,
        build_train_params,
        effective_metrics,
        evaluation_with_blanks_filled,
        parse_evaluation_config,
        parse_tuning_config,
        validate_modelling_config_values,
        validate_training_device,
    )

    validate_modelling_config_values(config)
    validate_training_device(config)
    # A present evaluation and tuning are each checked; an absent evaluation, and a
    # setting the editor left blank, are completeness, and tuning's one rule that needs
    # the split waits for them. The rest of an evaluation with a blank is still checked.
    raw_evaluation = config.get("evaluation")
    evaluation = None
    if raw_evaluation is not None:
        if blank_evaluation_settings(raw_evaluation):
            parse_evaluation_config(evaluation_with_blanks_filled(raw_evaluation))
        else:
            evaluation = parse_evaluation_config(raw_evaluation)
    if config.get("tuning") is not None:
        parse_tuning_config(
            config["tuning"],
            algorithm=str(config.get("algorithm", "catboost")),
            base_params=build_train_params(config),
            evaluation=evaluation,
            configured_metrics=effective_metrics(config),
        )


def _modelling_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    from haute.modelling._train_config import (
        blank_evaluation_settings,
        feature_selection_issue,
        training_objective_issue,
    )

    gaps = []
    if not config.get("target"):
        gaps.append(
            ConfigGap(
                "target",
                "required",
                "No target column selected. Open the config panel and choose a target column.",
            )
        )
    objective = training_objective_issue(config)
    if objective is not None:
        gaps.append(ConfigGap("", "incomplete_objective", objective))
    features = feature_selection_issue(config)
    if features is not None:
        gaps.append(ConfigGap("feature_columns", "required", features))
    if config.get("evaluation") is None:
        gaps.append(
            ConfigGap(
                "evaluation",
                "required",
                "Modelling config has no evaluation object. Open the Split pane and choose "
                "how the data is structured, how candidates are validated, and whether a "
                "final test is reserved.",
            )
        )
    for path in blank_evaluation_settings(config.get("evaluation")):
        gaps.append(
            ConfigGap(path, "required", f"{path} is not set. Open the Split pane and set it.")
        )
    return gaps


RESERVED_OPTIMISER_CONSTRAINT_NAMES = frozenset({"objective", "step", "scenario_value"})
"""Constraint names price-contour's own output columns already use."""


def _check_optimiser(config: Mapping[str, Any]) -> None:
    from haute._config_validation import validate_optimiser_analysis_config
    from haute.errors import NodeConfigError

    reserved = sorted(
        name
        for name in config.get("constraints") or {}
        if name in RESERVED_OPTIMISER_CONSTRAINT_NAMES
    )
    if reserved:
        raise NodeConfigError(
            f"Constraint name(s) {reserved} are reserved: price-contour already names its "
            "outputs total_objective, optimal_step and optimal_scenario_value, so a constraint "
            "called objective, step or scenario_value would overwrite one of them. Rename the "
            "constraint.",
            setting="constraints",
        )
    for key in ("max_iter", "frontier_steps", "max_cd_iterations"):
        if key in config and config[key] <= 0:
            raise NodeConfigError(f"Optimiser {key} must be a positive integer.", setting=key)
    for key in ("tolerance", "cd_tolerance"):
        if key in config and config[key] <= 0:
            raise NodeConfigError(f"Optimiser {key} must be a positive number.", setting=key)
    validate_optimiser_analysis_config(config)


def _optimiser_inputs(config: Mapping[str, Any], input_names: Sequence[str]) -> None:
    from haute._config_validation import validate_optimiser_input_selectors

    validate_optimiser_input_selectors(
        NodeType.OPTIMISER, config, input_names, node_label="optimiser"
    )


def _optimiser_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    gaps = []
    if not config.get("objective"):
        gaps.append(
            ConfigGap(
                "objective",
                "required",
                "No objective column configured. Open the config panel and set an objective.",
            )
        )
    if config.get("mode") == "ratebook" and not config.get("factor_columns"):
        gaps.append(
            ConfigGap(
                "factor_columns",
                "required",
                "Ratebook mode requires factor_columns. Add at least one factor group.",
            )
        )
    return gaps


def _check_optimiser_apply(config: Mapping[str, Any]) -> None:
    from haute._config_validation import validate_registered_model_alias
    from haute._node_apply import require_optimiser_apply_source_type

    validate_registered_model_alias(NodeType.OPTIMISER_APPLY, config)
    require_optimiser_apply_source_type(config)


def _optimiser_apply_inputs(config: Mapping[str, Any], input_names: Sequence[str]) -> None:
    from haute._config_validation import validate_optimiser_input_selectors

    validate_optimiser_input_selectors(
        NodeType.OPTIMISER_APPLY, config, input_names, node_label="optimiserApply"
    )


_OPTIMISER_APPLY_SOURCE_FIELD = {
    "file": "artifact_path",
    "run": "run_id",
    "registered": "registered_model",
}


def _optimiser_apply_gaps(
    config: Mapping[str, Any], input_names: Sequence[str] | None
) -> list[ConfigGap]:
    source = config.get("sourceType")
    source_field = _OPTIMISER_APPLY_SOURCE_FIELD.get(str(source or ""))
    if source_field is None or config.get(source_field):
        return []
    return [ConfigGap(source_field, "required", f"{source_field} is required for {source}.")]


def _check_scenario_expander(config: Mapping[str, Any]) -> None:
    from haute._node_apply import scenario_step_count

    scenario_step_count(config)


def _check_constant(config: Mapping[str, Any]) -> None:
    from haute._node_apply import constant_frame

    constant_frame(list(config.get("values") or []))


_MODELS: tuple[NodeConfigModel, ...] = (
    NodeConfigModel(
        NodeType.API_INPUT,  # the Quote Input alone
        ApiInputConfig,
        completeness=_api_input_gaps,
    ),
    NodeConfigModel(NodeType.WORKBENCH_INPUT, WorkbenchInputConfig),
    NodeConfigModel(NodeType.WORKBENCH_OUTPUT, WorkbenchOutputConfig),
    NodeConfigModel(
        NodeType.DATA_INPUT,
        DataInputConfig,
        check=_check_data_input,
        discriminant="inputType",
        completeness=_data_input_gaps,
    ),
    NodeConfigModel(
        NodeType.DATA_OUTPUT,
        DataOutputConfig,
        check=_check_data_output,
        discriminant="outputType",
        completeness=_data_output_gaps,
    ),
    NodeConfigModel(
        NodeType.POLARS,
        TransformConfig,
        check=_check_transform,
        completeness=_transform_gaps,
    ),
    NodeConfigModel(NodeType.EDGE_JOIN, EdgeJoinConfig, check=_check_edge_join),
    NodeConfigModel(
        NodeType.MODEL_SCORE,
        ModelScoreConfig,
        check=_check_model_score,
        discriminant="sourceType",
        completeness=_model_score_gaps,
    ),
    NodeConfigModel(NodeType.BANDING, BandingConfig, check=_check_banding),
    NodeConfigModel(
        NodeType.RATING_STEP,
        RatingStepConfig,
        check=_check_rating_step,
        builder_checks=True,
    ),
    NodeConfigModel(NodeType.OUTPUT, OutputConfig, check=_check_output, completeness=_output_gaps),
    NodeConfigModel(
        NodeType.EXPLORE,
        ExploreConfig,
        check=_check_explore,
    ),
    NodeConfigModel(
        NodeType.EXTERNAL_FILE,
        ExternalFileConfig,
        completeness=_external_file_gaps,
    ),
    NodeConfigModel(
        NodeType.LIVE_SWITCH,
        LiveSwitchConfig,
        check=_check_live_switch,
        completeness=_live_switch_gaps,
    ),
    NodeConfigModel(
        NodeType.MODELLING,
        ModellingConfig,
        check=_check_modelling,
        completeness=_modelling_gaps,
        discriminant="algorithm",
        discriminant_values=_modelling_algorithms,
    ),
    NodeConfigModel(
        NodeType.OPTIMISER,
        OptimiserConfig,
        check=_check_optimiser,
        discriminant="mode",
        check_inputs=_optimiser_inputs,
        completeness=_optimiser_gaps,
    ),
    NodeConfigModel(
        NodeType.SCENARIO_EXPANDER,
        ScenarioExpanderConfig,
        check=_check_scenario_expander,
    ),
    NodeConfigModel(
        NodeType.OPTIMISER_APPLY,
        OptimiserApplyConfig,
        check=_check_optimiser_apply,
        discriminant="sourceType",
        check_inputs=_optimiser_apply_inputs,
        completeness=_optimiser_apply_gaps,
    ),
    NodeConfigModel(NodeType.CONSTANT, ConstantConfig, check=_check_constant),
    NodeConfigModel(NodeType.SUBMODEL, SubmodelConfig),
    NodeConfigModel(NodeType.SUBMODEL_PORT, SubmodelPortConfig),
)

NODE_CONFIG_MODELS: Mapping[NodeType, NodeConfigModel] = {
    model.node_type: model for model in _MODELS
}
"""Each node type's config model; the node specification (PCFG-R09) reads it from here."""

_missing = set(NodeType) - set(NODE_CONFIG_MODELS)
if _missing:
    raise ImportError(f"node types without a config model: {sorted(_missing)}")


@functools.cache
def node_data_schema() -> Any:
    """``NodeData`` as the generated browser contract describes it.

    A union tagged by ``nodeType`` whose branch types ``config`` with that node
    type's config model, so a response's every config is typed by its node type.
    The ``NodeData`` that validates at run time keeps a plain ``dict`` config;
    the models check it at the validation boundary instead.
    """
    from haute._types import NodeEditorState

    variants = [
        create_model(
            f"{node_type.value[0].upper()}{node_type.value[1:]}NodeData",
            __doc__=f"A {node_type.value} node's data, its config typed by its config model.",
            label=(str, "Unnamed"),
            description=(str, ""),
            nodeType=(Literal[node_type.value], ...),
            config=(model.shape, ...),
            editor=(NodeEditorState, Field(default_factory=NodeEditorState)),
        )
        for node_type, model in NODE_CONFIG_MODELS.items()
    ]
    return Annotated[Union[tuple(variants)], Field(discriminator="nodeType")]  # noqa: UP007
