"""What the assistant says and checks about each node type: one description per type.

The node specification (``haute._node_specs``) holds the facts every module
reads, the display name and input cardinality among them. What only the
assistant needs, its prose and its per-type checks, is one row of
:data:`ASSISTANT_NODE_DESCRIPTIONS`, a ``node_type_table`` that lists every node
type, so a new type fails at import until it is described. The capability
catalogue, the graph-edit layer, the application service, the data check, the
tools, the loop, the example bundles and the recipes read it.

This module sits below every assistant module that reads it: it imports none
of them, so the config parsers and the data check's parts are declared here
rather than looked up in their consumers. It imports only the node
specification at import time (a config parser loads its parser when it runs),
so the recipes can read it without loading the engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from haute._io import EXTERNAL_FILE_TYPES
from haute._node_specs import NODE_SPECS, node_type_table
from haute._node_type import NodeType

__all__ = [
    "ASSISTANT_NODE_DESCRIPTIONS",
    "INPUT_NAMING_RULE",
    "AssistantNodeDescription",
    "recipe_node_type",
]

#: The types a Load File accepts, in words, derived so the prompt cannot disagree with the loader.
_LOAD_FILE_TYPE_NAMES = ["JSON" if name == "json" else name for name in EXTERNAL_FILE_TYPES]
_LOAD_FILE_TYPES = f"{', '.join(_LOAD_FILE_TYPE_NAMES[:-1])} or {_LOAD_FILE_TYPE_NAMES[-1]}"

#: How an incoming edge names the input it gives, in the words of
#: ``haute._graph_utils.executable_input_name``. The Polars descriptor, the
#: system prompt, the authoring guide and every node card that names inputs
#: state it verbatim, held so by test.
INPUT_NAMING_RULE = (
    "An input is named after its incoming edge: the upstream node's name, except that "
    "an edge from a Quote Input or Workbench Input frame, which `add_edge`'s "
    "`source_handle` selects, is named by that frame, and an edge from a submodel output "
    "by its port on the owner and, on an instance, by the instance's name, or "
    "`<instance>_<port>` when its submodel has several outputs."
)

#: The structured configuration the assistant measures and judges: Banding's
#: factors, a Rating Step's tables and an Edge Join's join keys.
StructuredConfig = Literal["factors", "tables", "join"]

#: What a node the assistant writes must prove beyond save validation: a
#: Model Training's training settings, a Load File's file loading.
ReadyCheck = Literal["training", "file_load"]

#: An artifact a preview would fetch: an optimisation result or a scoring model.
PreviewArtifact = Literal["optimisation_result", "scoring_model"]


def _parse_banding_config(config: dict[str, Any]) -> object:
    from haute._rating import validate_banding_config

    return validate_banding_config(config)


def _parse_rating_step_config(config: dict[str, Any]) -> None:
    from haute._rating import normalise_combined_outputs
    from haute._rating_step_config import normalise_rating_step_config

    normalise_rating_step_config(config)
    normalise_combined_outputs(config)


@dataclass(frozen=True, slots=True)
class AssistantNodeDescription:
    """The assistant's description of one node type."""

    #: The one-line purpose shown beside the palette name in the prompt's node index.
    summary: str
    #: The longer, hand-authored authoring note.
    usage: str
    #: What the node does to the schema (the descriptor prefixes a sink-only or
    #: pass-through type's own clause).
    schema_effect: str
    #: The example bundles that teach the type.
    examples: tuple[str, ...] = ()
    #: The recipes that create a node of the type.
    recipes: tuple[str, ...] = ()
    #: The descriptor's input ports in words, for a type whose inputs are not one
    #: frame, its roles or nothing.
    input_ports: str | None = None
    #: The descriptor's output ports in words, for a type whose outputs are not
    #: its frame.
    output_ports: str | None = None
    #: The whole wiring rule of a type that states its own.
    wiring_rules: str | None = None
    #: The wiring rule's first sentence for a type whose incoming edges are optional.
    incoming_edges: str | None = None
    #: The wiring rule's type-specific sentence.
    wiring_extra: str | None = None
    #: The descriptor's anti-patterns beyond the ones every type shares.
    anti_patterns: tuple[str, ...] = ()
    #: The descriptor's execution class and side effects, for a type that runs
    #: other than lazily in the pipeline.
    execution: tuple[str, str] | None = None
    #: The field naming each entry of a config list, where that name is pipeline
    #: metadata (an output column, an output path, a pivot id): what a refused
    #: blind rewrite may say it would change, as ``(list key, entry field)``.
    list_entry_names: tuple[tuple[str, str], ...] = ()
    #: A read-only (workbench) type's hint to wire to its tables instead.
    read_only_wiring: str | None = None
    #: The fix for a pivot step that does not render on the type's steps.
    pivot_step_fix: str | None = None
    #: Its ``output_mapping`` rows name incoming frames by ``source_port``.
    output_mapping: bool = False
    #: The parser a config the assistant writes must pass, so a refusal names the node.
    config_parser: Callable[[dict[str, Any]], object] | None = None
    structured_config: StructuredConfig | None = None
    ready_check: ReadyCheck | None = None
    #: The builder binds the preamble, so the data check fails it with a preamble failure.
    binds_preamble: bool = False
    #: The file types the data check never opens (a Load File's opaque objects).
    opaque_file_types: frozenset[str] = frozenset()
    preview_artifact: PreviewArtifact | None = None
    #: An example bundle's smoke builds this node's input snapshot before it runs.
    example_input_snapshot: bool = False


_TRAINING_EXECUTION = (
    "explicit long-running local computation; unavailable to ordinary assistant tools",
    "creates model or optimisation artifacts only through owning services",
)

#: Where an analyst's pivot table goes when a pivot step on an Explore does not render.
_EXPLORE_PIVOT_FIX = (
    "An analyst's pivot table is a pivots entry in the Explore's config, not a step; a "
    'pivot step only reshapes the frame. Call read_reference with "node:explore" for the '
    "entry's shape and add it with update_node."
)

#: The assistant's description of every node type. Usage notes are the
#: manifest's intentionally hand-authored knowledge.
ASSISTANT_NODE_DESCRIPTIONS = node_type_table(
    "assistant node descriptions",
    {
        NodeType.API_INPUT: AssistantNodeDescription(
            summary="The live quote request, one frame per declared request table.",
            usage=(
                "Declare the request contract and its input tables; use this as the "
                "pipeline's external quote boundary."
            ),
            schema_effect="emits the declared request-table schemas",
            examples=("minimal_live_quote",),
        ),
        NodeType.WORKBENCH_INPUT: AssistantNodeDescription(
            summary="The live quote request, one frame per table the workbench defines.",
            usage=(
                "Read-only to you: the live quote request, split into the tables the installed "
                "workbench defines, which the editor copies and keeps current. Wire downstream "
                "nodes to its frames as to a Quote Input's; never add, change, rename or "
                "delete it."
            ),
            schema_effect="emits the request-table schemas the workbench defines",
            # The assistant authors no Workbench Input or Output, so no example shows one.
            examples=(),
            read_only_wiring="Wire nodes to its frames instead.",
        ),
        NodeType.WORKBENCH_OUTPUT: AssistantNodeDescription(
            summary="The quote's response: the tables the workbench defines, filled.",
            usage=(
                "Read-only to you: the pipeline's response, the tables the workbench "
                "defines, each filled from the frame connected to its port, column by column "
                "through its mapping. Wire one upstream node to each table; never add, "
                "change, rename or delete it."
            ),
            schema_effect="fills the response's tables with their declared columns",
            examples=(),
            input_ports="one frame per table the workbench defines, on that table's port",
            wiring_extra=(
                "Connect each table's frame with target_handle set to the table's name; a "
                "table takes one edge and a node fills one table."
            ),
            read_only_wiring="Wire nodes to its tables instead.",
        ),
        NodeType.DATA_INPUT: AssistantNodeDescription(
            summary="Read a file, database, lakehouse, Databricks table or inline records.",
            usage=(
                "Read a file, database, lakehouse, Databricks table, or inline records through "
                "an explicit provider and format; use a snapshot for remote or eager-only "
                "inputs."
            ),
            schema_effect="emits the selected source schema",
            examples=("minimal_batch",),
            example_input_snapshot=True,
        ),
        NodeType.DATA_OUTPUT: AssistantNodeDescription(
            summary="Write a frame to a file, database, lakehouse or Databricks table.",
            usage=(
                "Write or sink its one input frame to a file, database, lakehouse or "
                "Databricks table when the analyst runs the output; it has no output, so it "
                "ends its branch and stays out of the scoring path."
            ),
            schema_effect="writes its input schema to the destination",
            execution=(
                "explicit output execution; unavailable to ordinary assistant tools",
                "writes to the configured destination",
            ),
        ),
        NodeType.POLARS: AssistantNodeDescription(
            summary="Transform one or more frames with Polars steps.",
            usage=(
                "Apply a Polars transform to connected inputs; new logic is written as steps. "
                + INPUT_NAMING_RULE
            ),
            schema_effect="derives the schema from validated Polars expressions",
            input_ports="one or more frames, each named by its edge",
            anti_patterns=(
                "Do not discard immutable Polars results; assign them to df.",
                "Do not write new logic as code: start the steps with a source step naming "
                "the input edge, which binds df, then transform df in a free_code step.",
                "When editing the code of a node already in code mode (it has no steps), do "
                "not read df before assigning it: there df is only the output, so start from "
                "an input by name (df = claims.filter(...)).",
            ),
            binds_preamble=True,
        ),
        NodeType.EDGE_JOIN: AssistantNodeDescription(
            summary="Join a base frame with a lookup frame on keys.",
            usage=(
                "Join the base input with a connected input; specify the join keys and "
                "join type explicitly, especially when the two key names differ."
            ),
            schema_effect=("combines base and reference columns under join suffix/coalesce rules"),
            examples=("reference_join",),
            recipes=("reference_join",),
            wiring_rules=(
                'Connect the primary input with target_handle="base" and the lookup '
                'input with target_handle="join"; exactly one incoming edge of each '
                "role is required."
            ),
            anti_patterns=("Do not omit or duplicate edgeJoin target_handle roles.",),
            structured_config="join",
        ),
        NodeType.MODEL_SCORE: AssistantNodeDescription(
            summary="Score rows with a saved model.",
            usage=(
                "Score rows with a saved model selected by run or registered-model "
                "metadata; configure the feature contract and prediction output deliberately."
            ),
            schema_effect=("adds the configured prediction output and optional post-processing"),
            preview_artifact="scoring_model",
        ),
        NodeType.BANDING: AssistantNodeDescription(
            summary="Group number, date or categorical values into named bands.",
            usage=(
                "Band a number or date column at breakpoints, or map categorical values to "
                "named bands. Breakpoint rules are {boundary, label} rows: each boundary is "
                "its band's upper bound (inclusive unless right_closed is false), every "
                "boundary is a number, a date, or a date and time of one kind, and one "
                "final row may leave the boundary empty for the open-ended band. "
                "Categorical rules are {value, assignment} rows. Give each factor an "
                "output_column and an explicit default for values outside the rules."
            ),
            schema_effect="adds each configured factor output column",
            examples=("discrete_banding",),
            recipes=("categorical_banding",),
            list_entry_names=(("factors", "output_column"),),
            config_parser=_parse_banding_config,
            structured_config="factors",
        ),
        NodeType.RATING_STEP: AssistantNodeDescription(
            summary="Look up rating factors from tables and combine them.",
            usage=(
                "Rate its first input: look up rating factors from one or more tables and "
                "combine their outputs with the chosen operation; make table factors and "
                "miss policy explicit."
            ),
            schema_effect=("adds table-factor and combined-output columns to its first input"),
            examples=("rating_step",),
            recipes=("rating_step",),
            list_entry_names=(
                ("tables", "output_column"),
                ("combined_outputs", "output_column"),
            ),
            config_parser=_parse_rating_step_config,
            structured_config="tables",
        ),
        NodeType.OUTPUT: AssistantNodeDescription(
            summary="Assemble the quote's JSON response from upstream columns.",
            usage=(
                "Assemble the top-level JSON response from selected upstream columns "
                "with an explicit output_mapping whose source_port names the incoming "
                "frame; it has no output edge."
            ),
            schema_effect="projects mapped columns into the declared JSON response",
            recipes=("response_output",),
            input_ports="one or more frames, each addressed by a mapping row's source_port",
            wiring_extra="Each output_mapping row's source_port names one incoming frame.",
            list_entry_names=(("output_mapping", "output_path"),),
            output_mapping=True,
        ),
        NodeType.EXPLORE: AssistantNodeDescription(
            summary="Analyse an upstream frame with summaries, pivots and charts.",
            usage=(
                "Summarise, pivot and chart its one input frame for analysis; it has no "
                "output, so it ends an analysis branch and is never a pricing stage."
            ),
            schema_effect="reads its input for summaries, pivots and charts",
            anti_patterns=(
                "An analyst's pivot table is a pivots entry on the Explore node, never a "
                "step; Explore steps only shape the frame before it is explored.",
            ),
            list_entry_names=(("pivots", "id"),),
            pivot_step_fix=_EXPLORE_PIVOT_FIX,
        ),
        NodeType.EXTERNAL_FILE: AssistantNodeDescription(
            summary=f"Load a {_LOAD_FILE_TYPES} file for use in steps.",
            usage=(
                f"Load a {_LOAD_FILE_TYPES} file and expose the loaded object as `obj`; "
                "the first input is `df` and further inputs are named by their edges. "
                "Without steps or code the node returns its first input unchanged. Set "
                "file_type. A model Haute trains is scored with Model Scoring, not loaded here."
            ),
            schema_effect="the steps or code see the loaded object as `obj`",
            input_ports=(
                "optional; the first input is `df`, further inputs are named by their edges"
            ),
            incoming_edges=(
                "Incoming edges are optional: the first input is `df` and further inputs "
                "are named by their edges."
            ),
            execution=(
                "lazy pipeline execution; the file loads when the node runs",
                "reads the file",
            ),
            ready_check="file_load",
            opaque_file_types=frozenset({"pickle", "joblib"}),
        ),
        NodeType.LIVE_SWITCH: AssistantNodeDescription(
            summary="Route the live request or a batch source by scenario.",
            usage=(
                "Route one of several connected frames by scenario. A pipeline normally "
                "needs only one switch; name the scenario map unambiguously."
            ),
            schema_effect="requires compatible schemas across selected scenarios",
            input_ports="one per configured scenario",
            binds_preamble=True,
        ),
        NodeType.MODELLING: AssistantNodeDescription(
            summary="Train a gradient boosting, t-boost, EBM or GLM model.",
            usage=(
                "Train a model from its one input frame; configure target, algorithm, "
                "task, features, and evaluation settings. It has no output, so nothing is "
                "wired downstream of it and training stays outside the live quote path."
            ),
            schema_effect=("reads its input to train; model artifacts are written out of band"),
            execution=_TRAINING_EXECUTION,
            ready_check="training",
        ),
        NodeType.OPTIMISER: AssistantNodeDescription(
            summary="Optimise prices under an objective and constraints.",
            usage=(
                "Optimise prices under an objective and constraints: choose mode online or "
                "ratebook, map the quote_id, scenario_index, scenario_value and objective "
                "columns, and name the frame to optimise with data_input when several "
                "inputs are connected (and the Banding input with banding_source in "
                "ratebook mode). The solve runs from the editor and saves a result that "
                "Apply Optimisation reads; the node has no output."
            ),
            schema_effect=(
                "reads its inputs to solve; the optimisation result is saved for Apply Optimisation"
            ),
            input_ports=(
                "one or more frames; data_input names the frame to optimise when several "
                "are connected"
            ),
            wiring_extra=(
                "With several inputs, data_input names the frame to optimise and, in "
                "ratebook mode, banding_source names the Banding input."
            ),
            execution=_TRAINING_EXECUTION,
        ),
        NodeType.SCENARIO_EXPANDER: AssistantNodeDescription(
            summary="Repeat each row across a grid of scenario values.",
            usage=(
                "Repeat each row once per scenario step: step_count (required, at least 1) "
                "sets how many; step_column names the 0-based index column (default "
                "scenario_index); column_name, when set, adds a value column spaced evenly "
                "from min_value to max_value; quote_id names the column identifying each "
                "quote."
            ),
            schema_effect=(
                "repeats rows step_count times, adding the step index and optional value columns"
            ),
        ),
        NodeType.OPTIMISER_APPLY: AssistantNodeDescription(
            summary="Apply a saved optimisation result to price rows.",
            usage=(
                "Apply a saved optimisation result from a file, run or registered model; a "
                "ratebook result applies to the input named by ratebook_input and any other "
                "to the first input. Keep the version and optimised-value column conventions."
            ),
            schema_effect="adds the configured version and optimised-value columns",
            input_ports=(
                "one or more frames; a ratebook result applies to the input named by "
                "ratebook_input, any other result to the first input"
            ),
            wiring_extra=(
                "A ratebook result applies to the input named by ratebook_input; any other "
                "result applies to the first input."
            ),
            preview_artifact="optimisation_result",
        ),
        NodeType.CONSTANT: AssistantNodeDescription(
            summary="A one-row frame of named constant values.",
            usage=(
                "Create a one-row frame of named literal values for defaults or lookup "
                "inputs; use values entries with stable names rather than hidden literals "
                "in code."
            ),
            schema_effect="emits one row with the configured literal columns",
            list_entry_names=(("values", "name"),),
        ),
        NodeType.SUBMODEL: AssistantNodeDescription(
            summary="An occurrence of a reusable sub-pipeline.",
            usage=(
                "Reference a separate pipeline module as a top-level graph boundary; "
                "connect only its declared input and output ports, never its internal nodes."
            ),
            schema_effect="exposes the declared schemas of its output ports",
            input_ports="declared submodel input ports",
            output_ports="declared submodel output ports",
        ),
        NodeType.SUBMODEL_PORT: AssistantNodeDescription(
            summary="A submodel's structural input or output port.",
            usage=(
                "Use only for the structural ports of a submodel boundary; it has no "
                "user config or decorator and must not be edited as an ordinary transform."
            ),
            schema_effect="carries its enclosing submodel port schema",
            input_ports="structural only",
            output_ports="structural only",
        ),
    },
)


def _validate_descriptions(descriptions: Mapping[NodeType, AssistantNodeDescription]) -> None:
    """Refuse a description that disagrees with the node specification.

    Called at import; ``tests/test_assistant_catalog.py`` plants each violation.
    """
    problems: list[str] = []
    recipes: dict[str, NodeType] = {}
    for node_type, description in descriptions.items():
        spec = NODE_SPECS[node_type]
        name = node_type.value
        if (description.read_only_wiring is not None) != spec.workbench:
            problems.append(f"{name}: exactly a workbench type is read-only to the assistant")
        if (description.input_ports is not None) != (
            spec.inputs in ("one_or_more", "zero_or_more", "structural")
        ):
            problems.append(
                f"{name}: exactly a type taking several, optional or structural inputs "
                "describes its input ports"
            )
        for recipe in description.recipes:
            if recipe in recipes:
                problems.append(f"{name}: recipe {recipe!r} also creates {recipes[recipe].value}")
            recipes[recipe] = node_type
    if problems:
        raise RuntimeError(
            "ASSISTANT_NODE_DESCRIPTIONS is inconsistent:\n  " + "\n  ".join(problems)
        )


_validate_descriptions(ASSISTANT_NODE_DESCRIPTIONS)

_RECIPE_NODE_TYPES: dict[str, NodeType] = {
    recipe: node_type
    for node_type, description in ASSISTANT_NODE_DESCRIPTIONS.items()
    for recipe in description.recipes
}


def recipe_node_type(recipe_id: str) -> NodeType:
    """The node type the recipe *recipe_id* creates."""
    return _RECIPE_NODE_TYPES[recipe_id]
