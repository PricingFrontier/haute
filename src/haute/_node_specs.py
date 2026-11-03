"""The node specification: one entry per node type, the index of what it is.

Each :class:`NodeSpec` holds every fact about a node type that more than one
module reads: its name in the editor, its decorator and sidecar folder, its
role in the graph, how many inputs it takes and how they are named, where its
code lives, whether it is authored with steps, and how a run treats it. The
sets of node types code tests membership of are derived here from the entries,
so each is declared once (``specs/pipeline-config``, "Node specification").

What a node type *does* in one concern (projection, the RAM estimate, a trace,
deploy, the assistant) is one row of that concern's :func:`node_type_table`,
which must list every node type, so a new type fails at import until each
concern has its row. The config model is the specification's config column,
read as ``haute._node_config_models.NODE_CONFIG_MODELS[node_type]``; it is not
held here, so this module stays a standard-library leaf importable from
anywhere (``tests/test_node_specs.py`` imports it in a fresh interpreter).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeVar

from haute._node_type import NodeType

__all__ = [
    "CODE_NODE_TYPES",
    "DECORATOR_TO_NODE_TYPE",
    "DEFAULT_NODE_TYPE",
    "FOLDER_TO_NODE_TYPE",
    "NODE_SPECS",
    "NODE_TYPE_TO_DECORATOR",
    "NODE_TYPE_TO_FOLDER",
    "PASS_THROUGH_NODE_TYPES",
    "REQUEST_INPUT_KINDS",
    "REQUEST_INPUT_NODE_TYPES",
    "RESPONSE_NODE_TYPES",
    "SINGLETON_NODE_GROUPS",
    "SINK_ONLY_NODE_TYPES",
    "SOURCE_NODE_TYPES",
    "STANDALONE_PASSTHROUGH_TYPES",
    "STEPPED_NODE_TYPES",
    "STRUCTURAL_NODE_TYPES",
    "WORKBENCH_NODE_TYPES",
    "InputCardinality",
    "NodeSpec",
    "RecomputeCost",
    "StepStart",
    "SteppedSurface",
    "known_node_spec",
    "node_spec",
    "node_type_table",
]

#: Where ``df`` comes from when a step list starts: ``input`` means the first
#: step chooses an input and renders ``df = <input>`` (a transform); ``frame``
#: means the surface hands the steps a frame already bound to ``df`` (a Data
#: Input's opened snapshot), so no start step exists and an empty list is
#: simply no code.
StepStart = Literal["input", "frame"]

#: Declared recompute cost of a node type's builder.
RecomputeCost = Literal["cheap", "costly", "code", "source"]

#: How many inputs a node type takes, descriptively: the editor's ``maxInputs``
#: is ``1`` exactly for ``exactly_one`` and ``2`` exactly for ``exactly_two``. No
#: backend validation follows from it (Explore's one enforced input is
#: :attr:`NodeSpec.input_count_enforced`).
InputCardinality = Literal[
    "none", "exactly_one", "exactly_two", "zero_or_more", "one_or_more", "structural"
]

#: Where a node type's settings live: a required ``config/<folder>/`` sidecar,
#: an optional one (the Transform's steps), its decorator keywords, or nowhere.
SidecarPolicy = Literal["required", "optional", "decorator", "none"]

#: Where the user's code lives: the Transform's function is its code; a frame
#: hook post-processes ``df``; Load File's object hook receives ``obj``.
CodeSurface = Literal["transform", "frame_hook", "object_hook", "none"]

#: What a builder returns unchanged: nothing, its first input, or the input its
#: :attr:`NodeSpec.pass_through_selector` names.
PassThrough = Literal["none", "first", "selected"]

#: A group a pipeline holds at most one node of.
SingletonGroup = Literal["request_input", "response"]

#: The part of a submodel a structural type is: the Submodel is an ``occurrence``
#: of a definition, the Port a definition's public input ``port``.
StructuralRole = Literal["occurrence", "port"]


@dataclass(frozen=True)
class SteppedSurface:
    """How one node type authors steps.

    ``start`` is the render mode. ``inputs`` is what ``join``/``concat`` steps
    may reference: ``edges`` for a surface whose code sees its incoming edges
    by name, ``none`` for one whose code sees only ``df``. Every path that
    renders a node's steps takes its eligible names from
    :data:`STEPPED_NODE_TYPES` through ``haute._polars_steps.step_input_names``,
    so the editor, the parser, codegen and execution agree on what a step may
    name.
    """

    start: StepStart
    inputs: Literal["edges", "none"]


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """Every fact about one node type that more than one module reads."""

    node_type: NodeType
    #: The editor's name (``NODE_TYPE_META[...].name`` in
    #: ``frontend/src/utils/nodeTypes.ts``, held equal by test); every message
    #: naming a node type uses it.
    display_name: str
    #: The ``pipeline.<decorator>`` name; ``None`` for the Submodel and its Port.
    decorator: str | None
    sidecar: SidecarPolicy
    #: The ``config/<folder>/`` of a required or optional sidecar.
    config_folder: str | None
    inputs: InputCardinality
    recompute_cost: RecomputeCost
    #: The node types that read the quote request (the Quote Input and the
    #: Workbench Input): code that means "the request input" tests this.
    request_input: bool = False
    #: The node types whose result is the pipeline's response.
    response: bool = False
    #: A configured source: a source whatever its function's signature.
    source: bool = False
    #: Nothing may be wired downstream of it.
    sink_only: bool = False
    #: The Submodel and its Port: graph structure, never executed as a node.
    structural: bool = False
    #: Given its tables from the project's workbench form wherever it runs.
    workbench: bool = False
    #: The saved graph must give it exactly its one input (Explore's function
    #: binds that input as ``df``).
    input_count_enforced: bool = False
    #: The target ports a two-input type's edges carry.
    input_roles: tuple[str, ...] = ()
    #: The config fields that name one of the node's inputs.
    input_selectors: tuple[str, ...] = ()
    #: The config field naming the input a ``selected`` pass-through returns.
    pass_through_selector: str | None = None
    #: Routes one of its inputs by the run's scenario (the Source Switch).
    scenario_router: bool = False
    #: Runs with no inputs connected (the Workbench Output fills its tables).
    runs_unconnected: bool = False
    singleton_group: SingletonGroup | None = None
    code: CodeSurface = "none"
    stepped: SteppedSurface | None = None
    #: A stateful transform: its standalone run routes through the executor's
    #: shared helper, so it is never a standalone passthrough.
    behavioural: bool = False
    #: Whether Polars pushes a slice through this builder step unchanged (false
    #: for a row-multiplying transform).
    slice_transparent: bool = True
    pass_through: PassThrough = "none"
    #: A standalone run returns one of its inputs.
    standalone_passthrough: bool = False
    #: A structural type's part of a submodel. An ``occurrence`` (the Submodel) is
    #: named by its alias, may be an ``instanceOf`` another occurrence, outputs on
    #: ``out__<port>`` handles and is expanded into its definition's nodes; a ``port``
    #: (the Port) is a definition's public input, named by its handle.
    structural_role: StructuralRole | None = None
    #: Scores its input a batch at a time: a capture below it drains its whole
    #: scored output into its own capture kind (the Model Scoring's scorer).
    batch_scored: bool = False


_FRAME_STEPS = SteppedSurface(start="frame", inputs="none")

#: The node specification, one entry per :class:`NodeType`.
NODE_SPECS: Mapping[NodeType, NodeSpec] = MappingProxyType(
    {
        NodeType.API_INPUT: NodeSpec(
            NodeType.API_INPUT,
            display_name="Quote Input",
            decorator="api_input",
            sidecar="required",
            config_folder="quote_input",
            inputs="none",
            recompute_cost="source",
            request_input=True,
            source=True,
            singleton_group="request_input",
        ),
        NodeType.WORKBENCH_INPUT: NodeSpec(
            NodeType.WORKBENCH_INPUT,
            display_name="Workbench Input",
            decorator="workbench_input",
            sidecar="required",
            config_folder="workbench_input",
            inputs="none",
            recompute_cost="source",
            request_input=True,
            source=True,
            workbench=True,
            singleton_group="request_input",
        ),
        NodeType.WORKBENCH_OUTPUT: NodeSpec(
            NodeType.WORKBENCH_OUTPUT,
            display_name="Workbench Output",
            decorator="workbench_output",
            sidecar="required",
            config_folder="workbench_output",
            inputs="one_or_more",
            recompute_cost="cheap",
            response=True,
            sink_only=True,
            workbench=True,
            runs_unconnected=True,
            singleton_group="response",
            behavioural=True,
        ),
        NodeType.DATA_INPUT: NodeSpec(
            NodeType.DATA_INPUT,
            display_name="Data Input",
            decorator="data_input",
            sidecar="required",
            config_folder="data_input",
            inputs="none",
            recompute_cost="source",
            source=True,
            code="frame_hook",
            stepped=_FRAME_STEPS,
        ),
        NodeType.DATA_OUTPUT: NodeSpec(
            NodeType.DATA_OUTPUT,
            display_name="Data Output",
            decorator="data_output",
            sidecar="required",
            config_folder="data_output",
            inputs="exactly_one",
            recompute_cost="cheap",
            sink_only=True,
            pass_through="first",
            standalone_passthrough=True,
        ),
        NodeType.POLARS: NodeSpec(
            NodeType.POLARS,
            display_name="Transform",
            decorator="polars",
            sidecar="optional",
            config_folder="polars",
            inputs="one_or_more",
            recompute_cost="code",
            code="transform",
            stepped=SteppedSurface(start="input", inputs="edges"),
        ),
        NodeType.EDGE_JOIN: NodeSpec(
            NodeType.EDGE_JOIN,
            display_name="Edge Join",
            decorator="edge_join",
            sidecar="decorator",
            config_folder=None,
            inputs="exactly_two",
            recompute_cost="costly",
            input_roles=("base", "join"),
        ),
        NodeType.MODEL_SCORE: NodeSpec(
            NodeType.MODEL_SCORE,
            display_name="Model Scoring",
            decorator="model_score",
            sidecar="required",
            config_folder="model_scoring",
            inputs="exactly_one",
            recompute_cost="costly",
            code="frame_hook",
            stepped=_FRAME_STEPS,
            behavioural=True,
            batch_scored=True,
        ),
        NodeType.BANDING: NodeSpec(
            NodeType.BANDING,
            display_name="Banding",
            decorator="banding",
            sidecar="required",
            config_folder="banding",
            inputs="exactly_one",
            recompute_cost="cheap",
            behavioural=True,
        ),
        NodeType.RATING_STEP: NodeSpec(
            NodeType.RATING_STEP,
            display_name="Rating Step",
            decorator="rating_step",
            sidecar="required",
            config_folder="rating_step",
            inputs="exactly_one",
            recompute_cost="costly",
            code="frame_hook",
            stepped=_FRAME_STEPS,
            behavioural=True,
        ),
        NodeType.OUTPUT: NodeSpec(
            NodeType.OUTPUT,
            display_name="Quote Response",
            decorator="output",
            sidecar="required",
            config_folder="quote_response",
            inputs="one_or_more",
            recompute_cost="cheap",
            response=True,
            sink_only=True,
            singleton_group="response",
            behavioural=True,
        ),
        NodeType.EXPLORE: NodeSpec(
            NodeType.EXPLORE,
            display_name="Explore",
            decorator="explore",
            sidecar="decorator",
            config_folder=None,
            inputs="exactly_one",
            recompute_cost="cheap",
            sink_only=True,
            input_count_enforced=True,
            code="frame_hook",
            # The single input is bound as df by codegen; steps live in the decorator.
            stepped=_FRAME_STEPS,
            standalone_passthrough=True,
        ),
        NodeType.EXTERNAL_FILE: NodeSpec(
            NodeType.EXTERNAL_FILE,
            display_name="Load File",
            decorator="external_file",
            sidecar="required",
            config_folder="load_file",
            inputs="zero_or_more",
            recompute_cost="code",
            code="object_hook",
            # The first input is already `df`; the other edges stay addressable.
            stepped=SteppedSurface(start="frame", inputs="edges"),
            standalone_passthrough=True,
        ),
        NodeType.LIVE_SWITCH: NodeSpec(
            NodeType.LIVE_SWITCH,
            display_name="Source Switch",
            decorator="live_switch",
            sidecar="required",
            config_folder="source_switch",
            inputs="one_or_more",
            recompute_cost="cheap",
            scenario_router=True,
            behavioural=True,
        ),
        NodeType.MODELLING: NodeSpec(
            NodeType.MODELLING,
            display_name="Model Training",
            decorator="modelling",
            sidecar="required",
            config_folder="model_training",
            inputs="exactly_one",
            recompute_cost="cheap",
            sink_only=True,
            pass_through="first",
            standalone_passthrough=True,
        ),
        NodeType.OPTIMISER: NodeSpec(
            NodeType.OPTIMISER,
            display_name="Optimisation",
            decorator="optimiser",
            sidecar="required",
            config_folder="optimisation",
            inputs="one_or_more",
            recompute_cost="cheap",
            sink_only=True,
            input_selectors=("data_input", "banding_source", "analysis_input"),
            pass_through_selector="data_input",
            pass_through="selected",
            standalone_passthrough=True,
        ),
        NodeType.SCENARIO_EXPANDER: NodeSpec(
            NodeType.SCENARIO_EXPANDER,
            display_name="Expander",
            decorator="scenario_expander",
            sidecar="required",
            config_folder="expander",
            inputs="exactly_one",
            recompute_cost="cheap",
            code="frame_hook",
            stepped=_FRAME_STEPS,
            behavioural=True,
            slice_transparent=False,
        ),
        NodeType.OPTIMISER_APPLY: NodeSpec(
            NodeType.OPTIMISER_APPLY,
            display_name="Apply Optimisation",
            decorator="optimiser_apply",
            sidecar="required",
            config_folder="apply_optimisation",
            inputs="one_or_more",
            recompute_cost="costly",
            input_selectors=("ratebook_input",),
            behavioural=True,
        ),
        NodeType.CONSTANT: NodeSpec(
            NodeType.CONSTANT,
            display_name="Constant",
            decorator="constant",
            sidecar="required",
            config_folder="constant",
            inputs="none",
            recompute_cost="source",
            source=True,
        ),
        NodeType.SUBMODEL: NodeSpec(
            NodeType.SUBMODEL,
            display_name="Submodel",
            decorator=None,
            sidecar="none",
            config_folder=None,
            inputs="one_or_more",
            recompute_cost="cheap",
            structural=True,
            structural_role="occurrence",
        ),
        NodeType.SUBMODEL_PORT: NodeSpec(
            NodeType.SUBMODEL_PORT,
            display_name="Port",
            decorator=None,
            sidecar="none",
            config_folder=None,
            inputs="structural",
            recompute_cost="cheap",
            structural=True,
            structural_role="port",
        ),
    }
)


def _validate_node_specs(specs: Mapping[NodeType, NodeSpec]) -> None:
    """Refuse a specification that misses a type or breaks an invariant.

    Called at import; ``tests/test_node_specs.py`` plants each violation.
    Repeated keys are refused by ``tests/test_node_spec_ratchet.py``'s AST
    check, since a dict has dropped them before this runs.
    """
    missing = set(NodeType) - set(specs)
    extra = set(specs) - set(NodeType)
    if missing or extra:
        raise RuntimeError(
            "NODE_SPECS must hold every NodeType exactly once: "
            f"missing={sorted(t.value for t in missing)}, extra={sorted(map(str, extra))}"
        )
    problems: list[str] = []
    decorators: dict[str, NodeType] = {}
    folders: dict[str, NodeType] = {}
    for key, spec in specs.items():
        name = key.value

        def problem(text: str, name: str = name) -> None:
            problems.append(f"{name}: {text}")

        if spec.node_type is not key:
            problem(f"keyed under {name!r} but declares {spec.node_type.value!r}")
        if spec.decorator is not None:
            if spec.decorator in decorators:
                problem(
                    f"decorator {spec.decorator!r} is also {decorators[spec.decorator].value!r}'s"
                )
            decorators[spec.decorator] = key
        if spec.config_folder is not None:
            if spec.config_folder in folders:
                problem(
                    f"config folder {spec.config_folder!r} is also "
                    f"{folders[spec.config_folder].value!r}'s"
                )
            folders[spec.config_folder] = key
        if spec.request_input and not spec.source:
            problem("a request input is a source")
        if spec.response and not spec.sink_only:
            problem("a response is sink-only")
        if (spec.decorator is None) != spec.structural:
            problem("exactly the structural types have no decorator")
        if (spec.sidecar in ("required", "optional")) != (spec.config_folder is not None):
            problem("a required or optional sidecar, and only one, has a config folder")
        if (spec.inputs == "exactly_two") != (
            len(spec.input_roles) == 2 and len(set(spec.input_roles)) == 2
        ):
            problem("exactly the two-input types declare two distinct input roles")
        if (spec.pass_through == "selected") != (spec.pass_through_selector is not None):
            problem("exactly a selected pass-through names its pass-through selector")
        if (
            spec.pass_through_selector is not None
            and spec.pass_through_selector not in spec.input_selectors
        ):
            problem("its pass-through selector is one of its input selectors")
        if spec.stepped is not None and spec.code == "none":
            problem("a stepped type has a code surface")
        if spec.pass_through != "none" and not spec.standalone_passthrough:
            problem("a builder pass-through is a standalone passthrough")
        if spec.behavioural and spec.standalone_passthrough:
            problem(
                "a behavioural type is never a standalone passthrough: route it through "
                "the executor's shared apply_*_from_config helper"
            )
        if spec.structural != (spec.structural_role is not None):
            problem("exactly the structural types have a structural role")
    if problems:
        raise RuntimeError("NODE_SPECS is inconsistent:\n  " + "\n  ".join(problems))


_validate_node_specs(NODE_SPECS)


def node_spec(node_type: NodeType | str) -> NodeSpec:
    """The specification of *node_type*; ``KeyError`` for an unknown type."""
    try:
        key = NodeType(node_type)
    except ValueError:
        raise KeyError(f"no node specification for {node_type!r}") from None
    return NODE_SPECS[key]


def known_node_spec(value: object) -> NodeSpec | None:
    """The specification of *value* when it names a node type, else ``None``.

    For the places that legitimately meet a value that is not a node type: a
    recovered file's node of a type this install does not know, or a ``str``
    argument left unset. Everywhere a value is a validated ``NodeType``,
    :func:`node_spec` is the lookup, and an unknown type is its ``KeyError``.
    """
    try:
        return node_spec(str(value))
    except KeyError:
        return None


def _types_where(test: Callable[[NodeSpec], bool]) -> frozenset[NodeType]:
    return frozenset(key for key, spec in NODE_SPECS.items() if test(spec))


# ---------------------------------------------------------------------------
# Derived category views: each declared once, from the entries
# ---------------------------------------------------------------------------

#: The request inputs: the node types that read the quote request. Code that
#: means "the request input" tests membership here rather than naming one type
#: (``tests/test_request_inputs.py``). The editor's ``REQUEST_INPUT_TYPES``
#: (``frontend/src/utils/nodeTypes.ts``) is held equal to this set by test.
REQUEST_INPUT_NODE_TYPES: frozenset[NodeType] = _types_where(lambda s: s.request_input)

#: The request inputs' node types as plain values.
REQUEST_INPUT_KINDS: frozenset[str] = frozenset(t.value for t in REQUEST_INPUT_NODE_TYPES)

#: The response nodes: the node types whose result is the pipeline's response.
#: The editor's ``RESPONSE_TYPES`` is the twin.
RESPONSE_NODE_TYPES: frozenset[NodeType] = _types_where(lambda s: s.response)

#: Node types with no output: nothing may be wired downstream of them. The
#: editor's ``SINK_ONLY_TYPES`` is held equal to this set by test.
SINK_ONLY_NODE_TYPES: frozenset[NodeType] = _types_where(lambda s: s.sink_only)

#: Configured node types that are sources whatever their function's signature.
SOURCE_NODE_TYPES: frozenset[NodeType] = _types_where(lambda s: s.source)

#: Configured node types whose function may carry the user's code as a hook
#: (the Transform's function *is* its code, so it is not one of these).
CODE_NODE_TYPES: frozenset[NodeType] = _types_where(
    lambda s: s.code in ("frame_hook", "object_hook")
)

#: The stepped surface of each node type authored with structured steps.
STEPPED_NODE_TYPES: Mapping[NodeType, SteppedSurface] = MappingProxyType(
    {key: spec.stepped for key, spec in NODE_SPECS.items() if spec.stepped is not None}
)

#: Configured node types whose standalone work returns one of their inputs.
STANDALONE_PASSTHROUGH_TYPES: frozenset[NodeType] = _types_where(lambda s: s.standalone_passthrough)

#: Node types whose builder always returns one of the node's inputs unchanged:
#: their output *is* that input's data. A stubbed Model Score or Optimiser
#: Apply that happens to pass its input through is not listed: that is a
#: configuration state, not what the node type is.
PASS_THROUGH_NODE_TYPES: frozenset[NodeType] = _types_where(lambda s: s.pass_through != "none")

#: The workbench node types, given their tables from the project's form.
WORKBENCH_NODE_TYPES: frozenset[NodeType] = _types_where(lambda s: s.workbench)

#: The Submodel and its Port.
STRUCTURAL_NODE_TYPES: frozenset[NodeType] = _types_where(lambda s: s.structural)

#: The decorator name of every node type that has one, and back.
NODE_TYPE_TO_DECORATOR: Mapping[NodeType, str] = MappingProxyType(
    {key: spec.decorator for key, spec in NODE_SPECS.items() if spec.decorator is not None}
)
DECORATOR_TO_NODE_TYPE: Mapping[str, NodeType] = MappingProxyType(
    {decorator: key for key, decorator in NODE_TYPE_TO_DECORATOR.items()}
)

#: The ``config/<folder>/`` of every node type with a required or optional
#: sidecar, and back.
NODE_TYPE_TO_FOLDER: Mapping[NodeType, str] = MappingProxyType(
    {key: spec.config_folder for key, spec in NODE_SPECS.items() if spec.config_folder is not None}
)
FOLDER_TO_NODE_TYPE: Mapping[str, NodeType] = MappingProxyType(
    {folder: key for key, folder in NODE_TYPE_TO_FOLDER.items()}
)


def _singleton_groups() -> tuple[tuple[frozenset[NodeType], str], ...]:
    groups: dict[str, list[NodeType]] = {}
    for key, spec in NODE_SPECS.items():
        if spec.singleton_group is not None:
            groups.setdefault(spec.singleton_group, []).append(key)
    # The editor's own type first, the workbench's last: "Quote Input or Workbench Input".
    return tuple(
        (
            frozenset(members),
            " or ".join(
                NODE_SPECS[t].display_name
                for t in sorted(members, key=lambda t: NODE_SPECS[t].workbench)
            ),
        )
        for members in groups.values()
    )


#: The groups a pipeline holds at most one node of, each with the label its
#: refusal names ("Quote Input or Workbench Input").
SINGLETON_NODE_GROUPS: tuple[tuple[frozenset[NodeType], str], ...] = _singleton_groups()

#: The type of a node declared without one: a transform.
DEFAULT_NODE_TYPE: NodeType = NodeType.POLARS


_V = TypeVar("_V")


def node_type_table(name: str, entries: Mapping[NodeType, _V]) -> Mapping[NodeType, _V]:
    """A concern's per-type table: *entries* for every node type, read-only.

    A missing or extra key is a ``RuntimeError`` at import naming *name* and the
    types, so a new node type cannot fall through a concern. A type the concern
    does not apply to is listed explicitly (``None`` or a named
    not-applicable entry). Pass the table as a dict literal:
    ``tests/test_node_spec_ratchet.py`` checks its keys and refuses a
    repeated one, which a dict has already dropped by the time it arrives here.
    """
    keys = set(entries)
    missing = set(NodeType) - keys
    extra = keys - set(NodeType)
    if missing or extra:
        raise RuntimeError(
            f"node type table {name!r} must list every NodeType exactly once: "
            f"missing={sorted(t.value for t in missing)}, extra={sorted(map(str, extra))}"
        )
    non_enum = [key for key in entries if type(key) is not NodeType]
    if non_enum:
        raise RuntimeError(
            f"node type table {name!r} is keyed by NodeType members, not {non_enum!r}"
        )
    return MappingProxyType(dict(entries))
