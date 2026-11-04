"""Core type definitions for Haute graph structures.

These Pydantic models are the **single source of truth** for the graph
data that flows between the parser, executor, codegen, deploy, and
server API layers.  ``schemas.py`` re-exports the graph models for
FastAPI endpoint validation.
"""

from __future__ import annotations

import datetime as _dt
import keyword
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from types import MappingProxyType
from typing import (
    Any,
    ClassVar,
    Literal,
    Protocol,
    Self,
    TypeAlias,
    runtime_checkable,
)

import polars as pl
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    PrivateAttr,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from haute._graph_utils import _sanitize_func_name, build_parents_of

# Type alias - nodes pass lazy frames between each other
_Frame = pl.LazyFrame


class NodeType(StrEnum):
    """Canonical node-type identifiers shared with the React Flow frontend.

    Inherits from ``StrEnum`` so ``NodeType.API_INPUT == "apiInput"`` is ``True``
    and JSON serialization produces the plain string value.
    """

    API_INPUT = "apiInput"
    WORKBENCH_INPUT = "workbenchInput"
    WORKBENCH_OUTPUT = "workbenchOutput"
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
    "workbench_input": NodeType.WORKBENCH_INPUT,
    "workbench_output": NodeType.WORKBENCH_OUTPUT,
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
}

NODE_TYPE_TO_DECORATOR: dict[NodeType, str] = {v: k for k, v in DECORATOR_TO_NODE_TYPE.items()}

#: Node types with no output: nothing may be wired downstream of them. The
#: editor's ``SINK_ONLY_TYPES`` (``frontend/src/utils/nodeTypes.ts``) is held
#: equal to this set by test.
SINK_ONLY_NODE_TYPES: frozenset[NodeType] = frozenset(
    {
        NodeType.OUTPUT,
        NodeType.WORKBENCH_OUTPUT,
        NodeType.DATA_OUTPUT,
        NodeType.EXPLORE,
        NodeType.MODELLING,
        NodeType.OPTIMISER,
    }
)

#: The request inputs: the node types that read the quote request. The Quote
#: Input's tables are built in its panel and the Workbench Input's are copied
#: from the project's workbench (specs/workbench), but every
#: context reads a request through either exactly alike, so code that means "the
#: request input" tests membership here rather than naming one type, which
#: ``tests/test_request_inputs.py`` enforces. The editor's ``REQUEST_INPUT_TYPES``
#: (``frontend/src/utils/nodeTypes.ts``) is held equal to this set by test.
REQUEST_INPUT_NODE_TYPES: frozenset[NodeType] = frozenset(
    {NodeType.API_INPUT, NodeType.WORKBENCH_INPUT}
)

#: The response nodes: the node types whose result is the pipeline's response. The
#: Quote Response maps columns to paths in its panel and the Workbench Output fills
#: the tables the project's workbench supplies (specs/workbench), but run(),
#: score() and deploy answer with either alike, and a pipeline holds one. The
#: editor's ``RESPONSE_TYPES`` (``frontend/src/utils/nodeTypes.ts``) is the twin.
RESPONSE_NODE_TYPES: frozenset[NodeType] = frozenset({NodeType.OUTPUT, NodeType.WORKBENCH_OUTPUT})

#: The one name each request input and response node has (READ-46): a pipeline holds one
#: of each, so its type names it, never numbered. The editor's ``SINGLETON_NODE_NAMES``
#: (``frontend/src/utils/nodeTypes.ts``) is the twin.
SINGLETON_NODE_NAMES: Mapping[NodeType, str] = MappingProxyType(
    {
        NodeType.API_INPUT: "quote_input",
        NodeType.OUTPUT: "quote_response",
        NodeType.WORKBENCH_INPUT: "workbench_input",
        NodeType.WORKBENCH_OUTPUT: "workbench_output",
    }
)

#: How an analyst knows each of them, for messages.
_SINGLETON_LABELS: Mapping[NodeType, str] = MappingProxyType(
    {
        NodeType.API_INPUT: "Quote Input",
        NodeType.OUTPUT: "Quote Response",
        NodeType.WORKBENCH_INPUT: "Workbench Input",
        NodeType.WORKBENCH_OUTPUT: "Workbench Output",
    }
)


def singleton_name_problem(node_type: NodeType | str, name: str) -> str | None:
    """Why a node of *node_type* cannot be called *name*, or ``None`` when it can: a request
    input or response node has its type's one name (READ-46)."""
    try:
        kind = NodeType(node_type)
    except ValueError:
        return None
    fixed = SINGLETON_NODE_NAMES.get(kind)
    if fixed is None or name == fixed:
        return None
    return (
        f"A {_SINGLETON_LABELS[kind]} is always named {fixed!r}, since a pipeline holds one; "
        f"rename {name!r} to {fixed!r}, and each connect() that names it."
    )


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
    def constraint_bounds(self) -> dict[str, float]: ...
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
class PerFactorRecordLike(Protocol):
    """Structural interface for ``price_contour.PerFactorRecord``: one inner
    grouped solve of a ratebook coordinate descent."""

    @property
    def cd_iteration(self) -> int: ...
    @property
    def factor(self) -> str: ...
    @property
    def factor_index(self) -> int: ...
    @property
    def total_objective(self) -> float: ...
    @property
    def total_constraints(self) -> dict[str, float]: ...
    @property
    def lambdas(self) -> dict[str, float]: ...


@runtime_checkable
class RatebookSolveResultLike(SolveResultLike, Protocol):
    """Structural interface for ``price_contour.RatebookResult`` (ratebook mode).

    Extends the common interface with attributes specific to the ratebook
    solver: factor tables, coordinate-descent iteration count, clamp rate,
    baseline values, and the canonical per-quote evaluation (``quote_results``).
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
    @property
    def per_factor_results(self) -> Sequence[PerFactorRecordLike]: ...
    @property
    def quote_results(self) -> pl.DataFrame: ...


class NodeEditorState(BaseModel):
    """What the server knows about a node for its editor, carried beside the config.

    None of it is a setting: it is never persisted, hashed or validated as config.
    ``steps_error`` is derived from the node's ``steps`` whenever its data is
    built; ``steps_discarded`` (why the parse dropped a stepped surface's steps) is
    the parse's finding. A request may carry the field back, as a transform of the
    graph echoes it, but a save never reads the finding from it: it reparses the
    file on disk.
    """

    model_config = ConfigDict(extra="forbid")

    steps_error: str | None = None
    steps_discarded: str | None = None


class NodeData(BaseModel):
    """Data payload for a single pipeline node."""

    label: str = "Unnamed"
    description: str = ""
    nodeType: NodeType = NodeType.POLARS  # noqa: N815 — matches React Flow frontend convention
    config: dict[str, Any] = Field(default_factory=dict)
    editor: NodeEditorState = Field(default_factory=NodeEditorState)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """The generated contract's ``NodeData``: a union tagged by ``nodeType``.

        Each branch types ``config`` with its node type's config model
        (``haute._node_config_models.node_data_schema``); at run time a config
        stays a plain ``dict`` the validation boundary checks.
        """
        from haute._node_config_models import node_data_schema

        return handler(TypeAdapter(node_data_schema()).core_schema)

    @model_validator(mode="after")
    def _materialise_steps(self) -> Self:
        """Keep ``code`` equal to the rendering of ``steps`` on a stepped node.

        Every consumer of a node's program reads ``config["code"]``; a stepped
        node therefore never carries any other program than its rendered
        steps. A step list that cannot be rendered materialises as empty code
        plus the editor state's ``steps_error`` message, so consumers see an
        incomplete node exactly as they see a code-less one. The render mode
        comes from ``STEPPED_NODE_TYPES``; a ``steps`` key on a node type
        outside that table is left alone.
        In-process config replacement must go through ``GraphNode.with_config``
        so this validator runs; ``model_copy`` does not validate.
        """
        from haute._polars_steps import (
            STEPPED_NODE_TYPES,
            PolarsStepError,
            render_node_steps,
        )

        steps_error: str | None = None
        if "steps" in self.config and self.nodeType in STEPPED_NODE_TYPES:
            steps = self.config["steps"]
            if not isinstance(steps, list):
                raise ValueError("Steps must be a list.")
            config = dict(self.config)
            try:
                rendered = render_node_steps(self.nodeType, steps)
            except PolarsStepError as exc:
                config["code"] = ""
                steps_error = str(exc)
            else:
                config["code"] = rendered.code
            self.config = config
        if self.editor.steps_error != steps_error:
            self.editor = self.editor.model_copy(update={"steps_error": steps_error})
        return self


class GraphNode(BaseModel):
    """A single node in the React Flow graph."""

    id: str
    type: str = "pipelineNode"
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    data: NodeData = Field(default_factory=NodeData)

    def with_config(self, config: Mapping[str, Any]) -> GraphNode:
        """Return a copy carrying ``config``, re-running node data validation."""
        data = NodeData(
            label=self.data.label,
            description=self.data.description,
            nodeType=self.data.nodeType,
            config=deepcopy(dict(config)),
            editor=self.data.editor,
        )
        return self.model_copy(update={"data": data})


class GraphEdge(BaseModel):
    """A single edge in the React Flow graph."""

    id: str
    source: str
    target: str
    # A submodel occurrence's handles are ``in__<public-port>`` and
    # ``out__<public-port>``; its definition resolves each public port to an
    # internal endpoint.
    sourceHandle: str | None = None  # noqa: N815 — matches React Flow frontend convention
    targetHandle: str | None = None  # noqa: N815 — matches React Flow frontend convention

    @field_validator("sourceHandle", "targetHandle", mode="before")
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

    definition_id: str
    alias: str
    instance_of: str | None = Field(
        default=None,
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
        check_submodel_interface(self.graph, self.input_ports, self.output_ports)
        return self


def check_submodel_interface(
    graph: PipelineGraph,
    input_ports: Sequence[SubmodelInputPort],
    output_ports: Sequence[SubmodelOutputPort],
) -> None:
    """Raise ``ValueError`` unless a definition's ports are unique and name its own nodes.

    The definition model and the submodel parse share this check, so a file
    that breaks it is refused as that submodel's own failure.
    """
    if graph.submodels:
        raise ValueError("Nested submodels are not supported in a definition graph.")

    port_names = [
        *(port.name for port in input_ports),
        *(port.name for port in output_ports),
    ]
    duplicates = sorted(name for name in set(port_names) if port_names.count(name) > 1)
    if duplicates:
        raise ValueError(f"Submodel definition has duplicate public port names: {duplicates!r}.")

    graph_node_ids = {node.id for node in graph.nodes}
    endpoint_ids = [target.node_id for port in input_ports for target in port.targets]
    endpoint_ids.extend(port.source.node_id for port in output_ports)
    missing = sorted(set(endpoint_ids) - graph_node_ids)
    if missing:
        raise ValueError(
            f"Submodel public port endpoint references missing child nodes: {missing!r}."
        )


GLOBAL_CONSTANTS_NAME = "global_constants"
"""The one reserved name through which node code reads global constants."""

GLOBAL_CONSTANTS_FILE = "config/global_constants.json"
"""A pipeline's global constants file, relative to its folder; its constructor names no other."""

GlobalConstantType: TypeAlias = Literal["integer", "float", "text", "boolean", "date"]
GlobalConstantValue: TypeAlias = StrictBool | StrictInt | StrictFloat | StrictStr

_GLOBAL_CONSTANT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


#: The largest integer a global constant may hold: the editor edits values as
#: JavaScript numbers, which represent every integer up to this one exactly.
MAX_CONSTANT_INTEGER = 2**53 - 1


def _checked_constant_value(
    name: str,
    constant_type: GlobalConstantType,
    value: GlobalConstantValue,
    *,
    where: str,
) -> GlobalConstantValue:
    """Return *value* if it is valid for *constant_type* (a float as a float), else raise."""
    if constant_type == "integer":
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and abs(value) <= MAX_CONSTANT_INTEGER
        ):
            return value
        expected = f"a whole number between -{MAX_CONSTANT_INTEGER} and {MAX_CONSTANT_INTEGER}"
    elif constant_type == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
        expected = "a finite number"
    elif constant_type == "text":
        if isinstance(value, str):
            return value
        expected = "text"
    elif constant_type == "boolean":
        if isinstance(value, bool):
            return value
        expected = "true or false"
    else:
        if isinstance(value, str) and _ISO_DATE.fullmatch(value):
            try:
                _dt.date.fromisoformat(value)
            except ValueError:
                pass
            else:
                return value
        expected = "a real date written YYYY-MM-DD"
    raise ValueError(
        f"Global constant {name!r} {where} must be {expected} for type "
        f"{constant_type!r}, got {value!r}."
    )


class GlobalConstant(BaseModel):
    """One named, typed value that node code reads as ``global_constants.<name>``.

    A uniform constant holds ``value``, which every source reads. A constant
    split by source holds ``by_source``, which may lack a source: reading it
    under that source fails, naming both. Values stay JSON scalars, so a date
    is its ``YYYY-MM-DD`` text, and a ``float`` is stored as a float however it
    was written.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: GlobalConstantType
    value: GlobalConstantValue | None = None
    by_source: dict[str, GlobalConstantValue] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _GLOBAL_CONSTANT_NAME.fullmatch(value) or keyword.iskeyword(value):
            raise ValueError(
                f"Global constant name {value!r} must start with a letter, continue with "
                "letters, digits and underscores, and not be a Python keyword."
            )
        return value

    @model_validator(mode="after")
    def _validate_values(self) -> Self:
        if (self.value is None) == (self.by_source is None):
            raise ValueError(
                f"Global constant {self.name!r} needs exactly one of 'value' (the same for "
                "every source) and 'by_source' (split by source)."
            )
        if self.value is not None:
            self.value = _checked_constant_value(self.name, self.type, self.value, where="value")
        if self.by_source is not None:
            checked: dict[str, GlobalConstantValue] = {}
            for source, value in self.by_source.items():
                if not source or source != source.strip():
                    raise ValueError(
                        f"Global constant {self.name!r} has a by_source key {source!r}: a "
                        "source name must be non-empty and unpadded."
                    )
                checked[source] = _checked_constant_value(
                    self.name, self.type, value, where=f"value for source {source!r}"
                )
            self.by_source = checked
        return self


def _reject_duplicate_constant_names(constants: Sequence[GlobalConstant]) -> None:
    seen: set[str] = set()
    for constant in constants:
        if constant.name in seen:
            raise ValueError(f"Global constant {constant.name!r} is defined more than once.")
        seen.add(constant.name)


@dataclass(frozen=True)
class SubmodelSharedBinding:
    """Expanded sources of one public input bound by an instance and its owner."""

    owner: SubmodelEndpoint
    instance: SubmodelEndpoint


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
    global_constants: list[GlobalConstant] = Field(default_factory=list)
    # Why the declared constants file could not be loaded, set by the parser.
    # Executions honour it; save derives it from disk instead.
    global_constants_error: str | None = None
    source_file: str | None = None
    source_revision: str | None = None
    submodels: dict[str, SubmodelDefinition] | None = None

    @field_validator("global_constants")
    @classmethod
    def _validate_global_constant_names(
        cls,
        value: list[GlobalConstant],
    ) -> list[GlobalConstant]:
        _reject_duplicate_constant_names(value)
        return value

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
    _parser_edge_parameter_names: dict[str, list[str]] = PrivateAttr(default_factory=dict)
    # Execution-only metadata: absent from graph JSON, source and sidecars.
    _submodel_shared_bindings: dict[str, dict[str, SubmodelSharedBinding]] = PrivateAttr(
        default_factory=dict
    )

    _parser_definition_id: str | None = PrivateAttr(default=None)
    _parser_input_ports: list[SubmodelInputPort] | None = PrivateAttr(default=None)
    _parser_output_ports: list[SubmodelOutputPort] | None = PrivateAttr(default=None)
    # Whether the parsed constructor names the global constants file, which a
    # declared but empty file leaves invisible in ``global_constants``.
    _parser_global_constants_declared: bool = PrivateAttr(default=False)

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
        "_haute_global_constant_reads",
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

    @cached_property
    def _haute_global_constant_reads(self) -> Any:
        """The global constants this graph's nodes read, memoised like the base fingerprint.

        Returns a set of names or ``EVERY_CONSTANT`` (see
        :func:`haute._global_constants.graph_constant_reads`).
        """
        from haute._global_constants import graph_constant_reads

        return graph_constant_reads(self.nodes, self.node_map)


SubmodelDefinition.model_rebuild()
PipelineGraph.model_rebuild()
