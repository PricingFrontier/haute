"""No backend module names a node type outside the permitted forms (PCFG-R09).

The node specification (``src/haute/_node_specs.py``) is the index of a node type,
and what a type does in each concern is a row of that concern's
``node_type_table``. This test scans every ``src/haute`` module and refuses any
other per-type reference, so a concern cannot grow a per-type branch that a new
node type would silently fall through (``specs/pipeline-config/low-level.md``,
"The ratchet").

Flagged:

- an enum member, ``<alias>.<MEMBER>``, through any name the module binds to
  ``NodeType`` (or a dotted ``haute._types.NodeType.MEMBER``);
- ``NodeType("<value>")`` (or ``NodeType(value="<value>")``) with a literal;
- a camelCase node-type string anywhere outside a docstring;
- a single-word node-type string in a comparison, a dict key, a set, a ``match``
  case, a ``NodeType(...)`` argument, or a node-type argument position (a
  parameter annotated ``NodeType``, ``NodeType | str`` or ``str | NodeType``).

Permitted:

1. the keys of a dict literal passed directly to ``node_type_table`` (every key
   an enum member, no ``**`` unpacking, no repeated key);
2. the node-type argument of a registration call;
3. a single-type endpoint in :data:`SINGLE_TYPE_ENDPOINTS`, comparing only its own
   member with ``==``, ``!=``, ``is`` or ``is not``, or passing it as an argument;
4. a string that is not about node types, in :data:`NOT_NODE_TYPES`, bound to its
   function, its literal, its syntactic context and its count.

Until the concern modules are converted, :data:`BASELINE` records each module's
remaining flagged count: it may only shrink, and must equal the scan.
"""

from __future__ import annotations

import ast
import textwrap
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from haute._node_type import NodeType
from tests._source_files import source_files

SRC = Path(__file__).resolve().parents[1] / "src" / "haute"

#: The enum's own module and the specification table: exempt from the reference rules.
EXEMPT_MODULES = frozenset({"_node_type.py", "_node_specs.py"})

MEMBERS = frozenset(member.name for member in NodeType)
VALUES = frozenset(member.value for member in NodeType)
CAMEL_VALUES = frozenset(value for value in VALUES if value != value.lower())
WORD_VALUES = VALUES - CAMEL_VALUES

#: Registration calls and the argument that names the type registered.
REGISTRATIONS: Mapping[str, int | str] = {
    "_register": 0,
    "register_exec": 0,
    "_register_codegen": 0,
    "register_codegen": 0,
    "set_codegen": 0,
    "NodeConfigModel": 0,
    "_register_node": "_node_type",
}

_NODE_TYPE_ANNOTATIONS = frozenset({"NodeType", "NodeType | str", "str | NodeType"})

#: Functions whose job is one node type, each with its member and why.
SINGLE_TYPE_ENDPOINTS: Mapping[tuple[str, str], tuple[str, str]] = {
    ("_ram_estimate.py", "estimate_safe_training_rows"): (
        "MODELLING",
        "the Model Training RAM estimate: a tree model target trains only its selected columns",
    ),
    # The shared configured operations, one per type: each loads its type's sidecar.
    ("_node_apply.py", "resolve_api_input_from_config"): ("API_INPUT", "the Quote Input's read"),
    ("_node_apply.py", "resolve_workbench_input_from_config"): (
        "WORKBENCH_INPUT",
        "the Workbench Input's tables",
    ),
    ("_node_apply.py", "workbench_input_tables_from_config"): (
        "WORKBENCH_INPUT",
        "the Workbench Input's tables, as a run gives them",
    ),
    ("_node_apply.py", "load_external_object_from_config"): (
        "EXTERNAL_FILE",
        "the Load File's object",
    ),
    ("_node_apply.py", "expand_scenarios_from_config"): (
        "SCENARIO_EXPANDER",
        "the Expander's expansion",
    ),
    ("_node_apply.py", "apply_optimiser_apply_from_config"): (
        "OPTIMISER_APPLY",
        "the Apply Optimisation's apply",
    ),
    ("_node_apply.py", "assemble_output_from_config"): ("OUTPUT", "the Quote Response's document"),
    ("_node_apply.py", "assemble_workbench_output_from_config"): (
        "WORKBENCH_OUTPUT",
        "the Workbench Output's tables",
    ),
    ("_rating.py", "apply_banding_from_config"): ("BANDING", "the Banding's apply"),
    ("_rating.py", "apply_rating_step_from_config"): ("RATING_STEP", "the Rating Step's apply"),
    ("_model_scorer.py", "score_from_config"): (
        "MODEL_SCORE",
        "the Model Scoring's standalone run",
    ),
    ("_input_providers.py", "resolve_data_input_from_config"): (
        "DATA_INPUT",
        "the Data Input's standalone read",
    ),
    ("_builders.py", "external_file_node_fn"): (
        "EXTERNAL_FILE",
        "the Load File's body, shared with deploy's bundled builder, which has no node",
    ),
    ("_standalone_nodes.py", "seeded_workbench_input_tables"): (
        "WORKBENCH_INPUT",
        "the tables score() seeds a Workbench Input with",
    ),
    ("_standalone_nodes.py", "live_switch_routes"): (
        "LIVE_SWITCH",
        "a Source Switch's routes, read from its sidecar",
    ),
    # A type's own config rules: each is registered for its type in NODE_CONFIG_MODELS.
    ("_model_source.py", "parse_model_source"): ("MODEL_SCORE", "a Model Scoring's model source"),
    ("_edge_join.py", "build_edge_join_kwargs"): ("EDGE_JOIN", "an Edge Join's join keywords"),
    ("_node_config_models.py", "_transform_gaps"): ("POLARS", "the Transform's completeness"),
    ("_node_config_models.py", "_check_model_score"): ("MODEL_SCORE", "the Model Scoring's check"),
    ("_node_config_models.py", "_optimiser_inputs"): ("OPTIMISER", "the Optimisation's selectors"),
    ("_node_config_models.py", "_check_optimiser_apply"): (
        "OPTIMISER_APPLY",
        "the Apply Optimisation's check",
    ),
    ("_node_config_models.py", "_optimiser_apply_inputs"): (
        "OPTIMISER_APPLY",
        "the Apply Optimisation's selectors",
    ),
    ("modelling/_train_config.py", "validate_modelling_config_values"): (
        "MODELLING",
        "the Model Training's value rules",
    ),
    # Building nodes of one type.
    ("_parser_submodels.py", "_merge_registered_submodels"): (
        "SUBMODEL",
        "builds each registered submodel's occurrence node",
    ),
    ("_pipeline_recovery.py", "_recover_ast_submodel_registrations"): (
        "SUBMODEL",
        "recovers each submodel registration as an occurrence candidate",
    ),
    ("_pipeline_recovery.py", "_recover_registered_submodels"): (
        "SUBMODEL",
        "recovers each registered submodel as an occurrence candidate",
    ),
    ("_seed_plans.py", "SeedPlan.estimation_graph"): (
        "DATA_INPUT",
        "estimates each seed as the Data Input scanning its generation's Parquet parts",
    ),
    ("routes/_submodel_ops.py", "create_submodel_graph"): (
        "SUBMODEL",
        "the create-submodel route: refuses nesting and builds the occurrence",
    ),
    ("routes/_submodel_ops.py", "create_node_instance_graph"): (
        "SUBMODEL",
        "the node-instance route: builds the owner and instance occurrences",
    ),
    ("pipeline.py", "Pipeline._with_submodels_expanded"): (
        "SUBMODEL",
        "names each registered submodel occurrence's output edges as the parser names them",
    ),
    # Routes and guards for one type.
    ("pipeline.py", "_refuse_explore_decorator_settings"): (
        "EXPLORE",
        "an Explore node's settings are its sidecar's alone (READ-43)",
    ),
    ("executor.py", "_data_output_config"): ("DATA_OUTPUT", "the Data Output write's config"),
    ("routes/pipeline.py", "_prepare_data_output_request"): (
        "DATA_OUTPUT",
        "the Data Output write route",
    ),
    ("routes/output_assemble.py", "output_assemble_dry_run"): (
        "OUTPUT",
        "the Quote Response dry-run route",
    ),
    ("routes/input_cache.py", "_safe_config"): (
        "DATA_INPUT",
        "the input-cache routes' Data Input source config",
    ),
    ("routes/_optimiser_input.py", "_find_optimiser_node"): (
        "OPTIMISER",
        "the optimiser routes' node lookup",
    ),
    ("routes/_optimiser_service.py", "OptimiserSolveService._validate_config"): (
        "OPTIMISER",
        "the solve route's Optimisation config",
    ),
    ("routes/_optimiser_solver.py", "_ratebook_factor_level_order"): (
        "BANDING",
        "a ratebook's factor levels come from its Banding source",
    ),
    ("routes/_training_lifecycle.py", "TrainService._validate_config"): (
        "MODELLING",
        "the train route's Model Training config",
    ),
    ("routes/_training_preparation.py", "_find_modelling_node"): (
        "MODELLING",
        "the training routes' node lookup",
    ),
}

#: Node-type-looking strings that are not about node types:
#: ``(module, qualname, value, context) -> (count, reason)``.
NOT_NODE_TYPES: Mapping[tuple[str, str, str, str], tuple[int, str]] = {
    (
        "_workbench_tables.py",
        "WorkbenchTable._holds_its_port_and_key",
        "output",
        "compare:self.side",
    ): (1, "a workbench table's output side, not a pipeline node type"),
    ("projection.py", "_node_selector_roots", "polars", "compare:node.module"): (
        1,
        "the polars module name in an import",
    ),
    ("projection.py", "_is_selector_construction", "polars", "compare:val.value.id"): (
        1,
        "the polars module name as a call receiver",
    ),
    ("_trace_lineage.py", "_Derivation._rule_references", "banding", "compare:detail_type"): (
        1,
        "trace detail type",
    ),
    ("_trace_waterfall.py", "_detail_uses_default", "banding", "compare:detail_type"): (
        1,
        "trace detail type",
    ),
    ("assistant/_change_record.py", "_not_checked_words", "submodel", "compare:reason"): (
        1,
        "the data check's not-checked reason for a submodel",
    ),
    ("assistant/_data_check.py", "_Judgement._record", "banding", "dict-key"): (
        1,
        "the data check record's banding field",
    ),
    ("assistant/_recipes.py", "expand_recipe", "banding", "dict-key"): (
        1,
        "a banding factor's banding-mode config key",
    ),
    # Python names: the polars module, and a submodel file's ``submodel`` receiver.
    ("_ast_helpers.py", "_get_decorator_node_type", "submodel", "compare:decorator.value.id"): (
        1,
        "the submodel file's receiver name",
    ),
    ("_ast_helpers.py", "_module_boundaries", "polars", "compare:imported_names"): (
        1,
        "the polars module",
    ),
    ("_ast_helpers.py", "_is_generated_call", "submodel", "compare:method"): (
        1,
        "the pipeline.submodel(...) method",
    ),
    ("_ast_helpers.py", "_is_standard_import", "polars", "compare:alias.name"): (
        1,
        "the polars module",
    ),
    ("_ast_helpers.py", "_extract_preamble", "submodel", "compare:receiver"): (
        1,
        "the receiver names",
    ),
    ("_executable_names.py", "<module>", "submodel", "dict-key"): (
        1,
        "the name a submodel file binds itself",
    ),
    ("_expression_parser.py", "<module>", "polars", "set"): (1, "the polars module's names"),
    ("_lint_refusal.py", "<module>", "submodel", "set"): (1, "the receiver names"),
    ("_parser_submodels.py", "_registration_definition", "submodel", "compare:definition.attr"): (
        1,
        "the <module>.submodel attribute",
    ),
    ("_parser_submodels.py", "registered_modules", "submodel", "compare:definition.attr"): (
        1,
        "the <module>.submodel attribute",
    ),
    ("_pipeline_recovery.py", "_recover_file_graph", "submodel", "compare:receiver"): (
        1,
        "the receiver name",
    ),
    (
        "_submodel_recovery.py",
        "submodel_registration_evidence",
        "submodel",
        "compare:call.func.attr",
    ): (
        1,
        "the pipeline.submodel(...) method",
    ),
    (
        "_submodel_recovery.py",
        "submodel_registration_evidence",
        "submodel",
        "compare:positional.attr",
    ): (
        1,
        "the <module>.submodel attribute",
    ),
    (
        "_polars_environment.py",
        "apply_polars_process_environment",
        "polars",
        "compare:sys.modules",
    ): (
        1,
        "the polars module",
    ),
    ("_polars_io_schema.py", "_installed_callable", "polars", "compare:owner"): (
        1,
        "the polars module",
    ),
    ("_polars_selectors.py", "preamble_selector_aliases", "polars", "compare:statement.module"): (
        1,
        "the polars module",
    ),
    ("_mlflow_io.py", "_prepare_predict_frame", "polars", "compare:predict_frame"): (
        1,
        "a model family's predict-frame kind",
    ),
    ("_code_extraction.py", "<module>", "polars", "dict-key"): (
        1,
        "the code-extraction kind of a Transform's body",
    ),
    # Column names, operand kinds and record keys.
    ("_builders.py", "_constant_columns", "constant", "set"): (1, "the Constant's default column"),
    ("_node_apply.py", "constant_frame", "constant", "dict-key"): (
        1,
        "the Constant's default column",
    ),
    (
        "_global_constants.py",
        "step_constant_reads.visit",
        "constant",
        "compare:value.get('kind')",
    ): (
        1,
        "a step operand of kind constant",
    ),
    ("_polars_steps.py", "_Renderer._operand", "constant", "compare:kind"): (
        1,
        "a step operand of kind constant",
    ),
    ("_polars_steps.py", "_Renderer._function_arg", "constant", "compare:operand.get('kind')"): (
        1,
        "a step operand of kind constant",
    ),
    ("_optimiser_apply_explainability.py", "_explain_online", "output", "dict-key"): (
        1,
        "the explanation record's output entry",
    ),
    ("_optimiser_apply_explainability.py", "_explain_ratebook", "output", "dict-key"): (
        1,
        "the explanation record's output entry",
    ),
    ("_polars_io_registry.py", "registry_capabilities", "output", "dict-key"): (
        1,
        "a format's output capability",
    ),
    (
        "routes/_training_artifacts.py",
        "_validate_training_artifacts",
        "output",
        "compare:relative.parts[0]",
    ): (
        1,
        "the staged artifacts' output folder",
    ),
    ("routes/_job_store.py", "<module>", "optimiser", "set"): (1, "a job store prefix"),
    ("routes/_job_store.py", "<module>", "explore", "set"): (1, "a job store prefix"),
    ("routes/mlflow.py", "_run_summaries._match", "optimiser", "compare:artifact_filter"): (
        1,
        "the MLflow run browser's artifact filter",
    ),
    # Message labels the refusals have always used.
    (
        "_builders.py",
        "_select_optimiser_apply_input",
        "optimiserApply",
        "call:resolve_exact_input_index",
    ): (
        1,
        "the ratebook-input refusal's node label",
    ),
    (
        "_node_config_models.py",
        "_optimiser_apply_inputs",
        "optimiserApply",
        "call:validate_optimiser_input_selectors",
    ): (
        1,
        "the selector refusal's node label",
    ),
    # The input-cache HTTP contract's source kinds: how a source is read (a Workbench
    # Input's tables are sent as apiInput), spelled as the types they were named after.
    ("schemas.py", "InputCacheSourceRequest", "dataInput", "literal:Tuple"): (
        1,
        "the input-cache request's read mode (a Workbench Input is read as apiInput)",
    ),
    ("schemas.py", "InputCacheSourceRequest", "apiInput", "literal:Tuple"): (
        1,
        "the input-cache request's read mode (a Workbench Input is read as apiInput)",
    ),
    ("schemas.py", "InputCacheSourceRequest", "dataInput", "literal:AnnAssign"): (
        1,
        "the input-cache request's default read mode",
    ),
    ("routes/input_cache.py", "build_input_cache", "apiInput", "compare:body.node_type"): (
        1,
        "the input-cache request's read mode (a Workbench Input is read as apiInput)",
    ),
    ("routes/input_cache.py", "build_input_cache", "apiInput", "literal:Dict"): (
        1,
        "the build's identity payload names its read mode",
    ),
    ("routes/input_cache.py", "get_input_cache_status", "apiInput", "compare:body.node_type"): (
        1,
        "the input-cache request's read mode (a Workbench Input is read as apiInput)",
    ),
    ("routes/input_cache.py", "clear_input_cache", "apiInput", "compare:body.node_type"): (
        2,
        "the input-cache request's read mode (a Workbench Input is read as apiInput)",
    ),
}

#: Each module's remaining flagged sites while the concerns are converted. It may
#: only shrink, and it must equal the scan (a lower count updates it here).
BASELINE: Mapping[str, int] = {}


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    module: str
    qualname: str
    line: int
    form: str
    text: str


@dataclass
class Scan:
    flagged: list[Site] = field(default_factory=list)
    endpoint_hits: Counter[tuple[str, str]] = field(default_factory=Counter)
    exemption_hits: Counter[tuple[str, str, str, str]] = field(default_factory=Counter)


def node_type_parameters(sources: Mapping[str, str]) -> dict[str, set[int | str]]:
    """Every function's node-type argument positions, by function name.

    A parameter annotated ``NodeType``, ``NodeType | str`` or ``str | NodeType``
    is a node-type position, by keyword and by positional index (a method's
    ``self`` / ``cls`` not counted).
    """
    positions: dict[str, set[int | str]] = {}
    for source in sources.values():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            positional = [*node.args.posonlyargs, *node.args.args]
            offset = 1 if positional and positional[0].arg in {"self", "cls"} else 0
            for index, arg in enumerate(positional):
                if arg.annotation is not None and _annotation(arg.annotation) in (
                    _NODE_TYPE_ANNOTATIONS
                ):
                    positions.setdefault(node.name, set()).update({index - offset, arg.arg})
            for arg in node.args.kwonlyargs:
                if arg.annotation is not None and _annotation(arg.annotation) in (
                    _NODE_TYPE_ANNOTATIONS
                ):
                    positions.setdefault(node.name, set()).add(arg.arg)
    return positions


def _annotation(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    return ast.unparse(node)


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ast.unparse(func)


class _Scanner(ast.NodeVisitor):
    def __init__(
        self,
        module: str,
        source: str,
        *,
        parameters: Mapping[str, set[int | str]],
        endpoints: Mapping[tuple[str, str], tuple[str, str]],
        exemptions: Mapping[tuple[str, str, str, str], tuple[int, str]],
        scan: Scan,
    ) -> None:
        self.module = module
        self.tree = ast.parse(source)
        self.parameters = parameters
        self.endpoints = endpoints
        self.exemptions = exemptions
        self.scan = scan
        self.aliases = self._aliases()
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        self.docstrings = self._docstrings()
        self.permitted: set[ast.AST] = set()
        self.scope: list[str] = []

    # -- setup ---------------------------------------------------------

    def _aliases(self) -> frozenset[str]:
        names = {"NodeType"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "NodeType":
                        names.add(alias.asname or alias.name)
        return frozenset(names)

    def _docstrings(self) -> set[ast.AST]:
        found: set[ast.AST] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                found.add(node.value)
        return found

    # -- classification --------------------------------------------------

    def is_member(self, node: ast.AST) -> str | None:
        """The member *node* names, for ``<alias>.MEMBER`` or ``...NodeType.MEMBER``."""
        if not isinstance(node, ast.Attribute) or node.attr not in MEMBERS:
            return None
        value = node.value
        if isinstance(value, ast.Name) and value.id in self.aliases:
            return node.attr
        if isinstance(value, ast.Attribute) and value.attr == "NodeType":
            return node.attr
        return None

    def qualname(self) -> str:
        return ".".join(self.scope) or "<module>"

    def flag(self, node: ast.AST, form: str) -> None:
        self.scan.flagged.append(
            Site(self.module, self.qualname(), getattr(node, "lineno", 0), form, ast.unparse(node))
        )

    def run(self) -> None:
        self._mark_permitted(self.tree)
        self.visit(self.tree)

    def _mark_permitted(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee(node)
            if callee == "node_type_table":
                entries = node.args[1] if len(node.args) > 1 else None
                for keyword in node.keywords:
                    if keyword.arg == "entries":
                        entries = keyword.value
                if not isinstance(entries, ast.Dict):
                    # A comprehension, ``dict.fromkeys`` or a variable would fill in a
                    # new node type silently: every row is written out.
                    self.flag(node, "table-not-literal")
                elif self._valid_table(entries):
                    self.permitted.update(k for k in entries.keys if k is not None)
            elif callee in REGISTRATIONS:
                position = REGISTRATIONS[callee]
                argument: ast.expr | None = None
                if isinstance(position, int) and len(node.args) > position:
                    argument = node.args[position]
                elif isinstance(position, str):
                    for keyword in node.keywords:
                        if keyword.arg == position:
                            argument = keyword.value
                if argument is not None and self.is_member(argument):
                    self.permitted.add(argument)

    def _valid_table(self, entries: ast.Dict) -> bool:
        if any(key is None for key in entries.keys):  # ``**`` unpacking
            self.flag(entries, "table-unpacking")
            return False
        members = [self.is_member(key) for key in entries.keys]
        if not all(members):
            self.flag(entries, "table-key")
            return False
        repeated = [m for m, n in Counter(members).items() if n > 1]
        if repeated:
            self.flag(entries, f"table-repeated-key:{','.join(sorted(map(str, repeated)))}")
            return False
        return True

    # -- traversal -------------------------------------------------------

    def _visit_scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_scoped(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_scoped(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        member = self.is_member(node)
        if member is not None and node not in self.permitted:
            if not self._endpoint_allows(node, member):
                self.flag(node, "enum")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in self.aliases:
            arguments = [*node.args, *(k.value for k in node.keywords if k.arg == "value")]
            if any(isinstance(a, ast.Constant) and a.value in VALUES for a in arguments):
                self.flag(node, "constructor")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if isinstance(value, str) and value in VALUES and node not in self.docstrings:
            context = self._string_context(node)
            if context is not None and not self._exempt(value, context):
                self.flag(node, f"string:{context}")

    def visit_Match(self, node: ast.Match) -> None:
        for case in node.cases:
            for pattern in ast.walk(case.pattern):
                if isinstance(pattern, ast.MatchValue):
                    candidate = pattern.value
                    if isinstance(candidate, ast.Constant) and candidate.value in VALUES:
                        if not self._exempt(str(candidate.value), "match"):
                            self.flag(candidate, "string:match")
                    elif self.is_member(candidate):
                        self.flag(candidate, "enum:match")
        self.generic_visit(node)

    def _string_context(self, node: ast.Constant) -> str | None:
        """Where a node-type string sits, or ``None`` where it is not flagged."""
        parent = self.parents.get(node)
        container = parent
        if isinstance(parent, ast.Tuple | ast.List | ast.Set):
            container = self.parents.get(parent)
        if isinstance(container, ast.Compare):
            operands = [container.left, *container.comparators]
            others = [o for o in operands if o is not node and o is not parent]
            return "compare:" + ",".join(ast.unparse(o) for o in others)
        if isinstance(parent, ast.Dict) and node in parent.keys:
            return "dict-key"
        if isinstance(parent, ast.Set):
            return "set"
        if isinstance(parent, ast.Call) and node in parent.args:
            callee = _callee(parent)
            index = parent.args.index(node)
            if node.value in CAMEL_VALUES or index in self.parameters.get(callee, set()):
                return f"call:{callee}"
            return None
        if isinstance(parent, ast.keyword):
            call = self.parents.get(parent)
            callee = _callee(call) if isinstance(call, ast.Call) else "?"
            if node.value in CAMEL_VALUES or parent.arg in self.parameters.get(callee, set()):
                return f"call:{callee}"
            return None
        if node.value in CAMEL_VALUES:
            return f"literal:{type(parent).__name__}"
        return None

    def _exempt(self, value: str, context: str) -> bool:
        key = (self.module, self.qualname(), value, context)
        if key in self.exemptions:
            self.scan.exemption_hits[key] += 1
            return True
        return False

    def _endpoint_allows(self, node: ast.Attribute, member: str) -> bool:
        if not self.scope:
            return False
        entry = self.endpoints.get((self.module, self.qualname()))
        if entry is None or entry[0] != member:
            return False
        parent = self.parents.get(node)
        if isinstance(parent, ast.Compare):
            if len(parent.ops) != 1 or not isinstance(
                parent.ops[0], ast.Eq | ast.NotEq | ast.Is | ast.IsNot
            ):
                return False
        elif isinstance(parent, ast.keyword):
            pass
        elif not (isinstance(parent, ast.Call) and node in parent.args):
            return False
        self.scan.endpoint_hits[(self.module, self.qualname())] += 1
        return True


def scan_sources(
    sources: Mapping[str, str],
    *,
    endpoints: Mapping[tuple[str, str], tuple[str, str]] = SINGLE_TYPE_ENDPOINTS,
    exemptions: Mapping[tuple[str, str, str, str], tuple[int, str]] = NOT_NODE_TYPES,
    parameters: Mapping[str, set[int | str]] | None = None,
) -> Scan:
    scan = Scan()
    positions = node_type_parameters(sources) if parameters is None else parameters
    for module, source in sources.items():
        if module in EXEMPT_MODULES:
            continue
        _Scanner(
            module,
            source,
            parameters=positions,
            endpoints=endpoints,
            exemptions=exemptions,
            scan=scan,
        ).run()
    return scan


def _src_sources() -> dict[str, str]:
    return {
        path.relative_to(SRC).as_posix(): path.read_text(encoding="utf-8")
        for path in source_files(SRC)
    }


def _repeated_keys(source: str, call_name: str | None, target: str | None) -> list[str]:
    """Repeated keys of the dict literals in *source* passed to *call_name* or assigned
    to *target*."""
    repeated: list[str] = []
    for node in ast.walk(ast.parse(source)):
        dicts: list[ast.Dict] = []
        if (
            call_name
            and isinstance(node, ast.Call)
            and _callee(node) == call_name
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Dict)
        ):
            dicts.append(node.args[1])
        if target and isinstance(node, ast.AnnAssign | ast.Assign):
            names = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(n, ast.Name) and n.id == target for n in names):
                dicts.extend(d for d in ast.walk(node) if isinstance(d, ast.Dict))
        for literal in dicts:
            keys = [ast.unparse(k) for k in literal.keys if k is not None]
            repeated.extend(k for k, n in Counter(keys).items() if n > 1)
    return repeated


# ---------------------------------------------------------------------------
# The real tree
# ---------------------------------------------------------------------------


def _counts(scan: Scan) -> Counter[str]:
    return Counter(site.module for site in scan.flagged)


@pytest.mark.meta
def test_every_module_keeps_to_its_baseline() -> None:
    scan = scan_sources(_src_sources())
    counts = _counts(scan)
    grown = {m: (n, BASELINE.get(m, 0)) for m, n in counts.items() if n > BASELINE.get(m, 0)}
    assert not grown, "Modules naming node types beyond their baseline:\n" + "\n".join(
        f"  {m}: {n} > {b}\n"
        + "\n".join(
            f"    {s.line}: {s.qualname}: {s.form}: {s.text}" for s in scan.flagged if s.module == m
        )
        for m, (n, b) in sorted(grown.items())
    )
    shrunk = {m: (counts.get(m, 0), b) for m, b in BASELINE.items() if counts.get(m, 0) < b}
    assert not shrunk, "Lower the baseline to the scan:\n" + "\n".join(
        f"  {m!r}: {n},  # was {b}" for m, (n, b) in sorted(shrunk.items())
    )


@pytest.mark.meta
def test_no_endpoint_or_exemption_is_stale() -> None:
    scan = scan_sources(_src_sources())
    stale_endpoints = sorted(set(SINGLE_TYPE_ENDPOINTS) - set(scan.endpoint_hits))
    assert not stale_endpoints, f"Endpoints holding no reference: {stale_endpoints}"
    wrong_counts = {
        key: (scan.exemption_hits.get(key, 0), count)
        for key, (count, _reason) in NOT_NODE_TYPES.items()
        if scan.exemption_hits.get(key, 0) != count
    }
    assert not wrong_counts, f"Exemptions whose count no longer matches: {wrong_counts}"


@pytest.mark.meta
def test_no_node_type_table_or_the_specification_repeats_a_key() -> None:
    sources = _src_sources()
    repeated = {
        module: keys
        for module, source in sources.items()
        if (keys := _repeated_keys(source, "node_type_table", None))
    }
    spec_repeats = _repeated_keys(sources["_node_specs.py"], None, "NODE_SPECS")
    assert not repeated, f"node_type_table literals repeating a key: {repeated}"
    assert not spec_repeats, f"NODE_SPECS repeats {spec_repeats}"


# ---------------------------------------------------------------------------
# Meta-tests: the scanner flags each escape and accepts each permitted form
# ---------------------------------------------------------------------------


def _flags(
    source: str,
    *,
    endpoints: Mapping[tuple[str, str], tuple[str, str]] | None = None,
    exemptions: Mapping[tuple[str, str, str, str], tuple[int, str]] | None = None,
    extra: Mapping[str, str] | None = None,
) -> list[str]:
    sources = {"probe.py": textwrap.dedent(source), **(extra or {})}
    scan = scan_sources(sources, endpoints=endpoints or {}, exemptions=exemptions or {})
    return [site.form for site in scan.flagged if site.module == "probe.py"]


ESCAPES = {
    "comparison": """
        from haute._types import NodeType
        def f(t):
            return t == NodeType.BANDING
    """,
    "set-literal": """
        from haute._types import NodeType
        TYPES = {NodeType.BANDING, NodeType.RATING_STEP}
    """,
    "aliased-enum": """
        from haute._types import NodeType as NT
        def f(t):
            return t is NT.BANDING
    """,
    "dotted-enum": """
        import haute._types
        def f(t):
            return t is haute._types.NodeType.BANDING
    """,
    "constructor": """
        from haute._types import NodeType
        def f():
            return NodeType("ratingStep")
    """,
    "raw-word-comparison": """
        def f(node_type):
            return node_type == "banding"
    """,
    "string-keyed-dispatch": """
        HANDLERS = {"ratingStep": 1, "banding": 2}
    """,
    "match-case": """
        def f(node_type):
            match node_type:
                case "banding":
                    return 1
    """,
    "comprehension-table": """
        from haute._node_specs import node_type_table
        from haute._types import NodeType
        TABLE = node_type_table("demo", {t: None for t in NodeType})
    """,
    "fromkeys-table": """
        from haute._node_specs import node_type_table
        from haute._types import NodeType
        TABLE = node_type_table("demo", dict.fromkeys(NodeType))
    """,
    "variable-table": """
        from haute._node_specs import node_type_table
        ENTRIES = build_entries()
        TABLE = node_type_table("demo", ENTRIES)
    """,
    "keyword-variable-table": """
        from haute._node_specs import node_type_table
        TABLE = node_type_table("demo", entries=build_entries())
    """,
    "unpacked-table": """
        from haute._node_specs import node_type_table
        from haute._types import NodeType
        DEFAULTS = {}
        TABLE = node_type_table("demo", {**DEFAULTS, NodeType.BANDING: 1})
    """,
    "repeated-table-key": """
        from haute._node_specs import node_type_table
        from haute._types import NodeType
        TABLE = node_type_table("demo", {NodeType.BANDING: 1, NodeType.BANDING: 2})
    """,
    "camel-call-argument": """
        def f(cfg):
            return resolve(cfg, ".", "ratingStep")
    """,
    "word-in-node-type-position": """
        def f(cfg):
            return check("banding", cfg)
    """,
}


@pytest.mark.parametrize(
    "name", ["comprehension-table", "fromkeys-table", "variable-table", "keyword-variable-table"]
)
def test_a_table_not_written_out_is_refused_as_such(name: str) -> None:
    """Refused for its form, not for an incidental member reference."""
    assert "table-not-literal" in _flags(ESCAPES[name]), name


@pytest.mark.parametrize("name", list(ESCAPES))
def test_the_scanner_flags_each_escape(name: str) -> None:
    api = "from haute._types import NodeType\ndef check(node_type: NodeType | str, cfg): ...\n"
    extra = {"api.py": api}
    assert _flags(ESCAPES[name], extra=extra), name


def test_an_endpoint_permits_only_its_member_and_its_forms() -> None:
    endpoint = {("probe.py", "write"): ("DATA_OUTPUT", "the Data Output write route")}
    allowed = """
        from haute._types import NodeType
        def write(node):
            if node.data.nodeType != NodeType.DATA_OUTPUT:
                raise ValueError
            return find(node, NodeType.DATA_OUTPUT)
    """
    assert _flags(allowed, endpoints=endpoint) == []
    second_type = """
        from haute._types import NodeType
        def write(node):
            return node.data.nodeType == NodeType.OUTPUT
    """
    assert _flags(second_type, endpoints=endpoint) == ["enum"]
    membership = """
        from haute._types import NodeType
        def write(node):
            return node.data.nodeType in {NodeType.DATA_OUTPUT}
    """
    assert _flags(membership, endpoints=endpoint) == ["enum"]
    nested = """
        from haute._types import NodeType
        def write(node):
            def inner(n):
                return n.data.nodeType == NodeType.DATA_OUTPUT
            return inner(node)
    """
    assert _flags(nested, endpoints=endpoint) == ["enum"]


def test_an_exemption_covers_only_its_context_and_count() -> None:
    exemption = {("probe.py", "f", "output", "compare:column"): (1, "a column named output")}
    legitimate = """
        def f(column):
            return column == "output"
    """
    assert _flags(legitimate, exemptions=exemption) == []
    beside = """
        def f(column, node_type):
            if node_type == "output":
                return 1
            return column == "output"
    """
    assert _flags(beside, exemptions=exemption) == ["string:compare:node_type"]


def test_the_scanner_accepts_each_permitted_form() -> None:
    permitted = """
        \"\"\"Explains "ratingStep" in a docstring.\"\"\"
        from haute._node_specs import node_type_table
        from haute._types import NodeType
        TABLE = node_type_table("demo", {NodeType.BANDING: 1, NodeType.RATING_STEP: 2})

        @_register(NodeType.BANDING, columns=None)
        def build(ctx):
            return config.get("banding")

        def decorator(self, fn):
            return self._register_node(fn, _node_type=NodeType.RATING_STEP)
    """
    assert _flags(permitted) == []


def test_the_specification_repeat_check_finds_a_repeated_key() -> None:
    source = "NODE_SPECS = MappingProxyType({NodeType.BANDING: 1, NodeType.BANDING: 2})\n"
    assert _repeated_keys(source, None, "NODE_SPECS") == ["NodeType.BANDING"]
