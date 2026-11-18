"""A mypy plugin: no code reads text as a number but the one reader.

Text typed as a number is read by one grammar, the same in every field, in every browser
language and on the server (specs/frontend-shared high-level, "Typed numbers"):
``haute._typed_numbers``, held with the browser's ``frontend/src/utils/typedNumbers.ts`` to
one case file. Code that reads text with Python's own ``float``, ``int`` or ``Decimal`` takes
``1_000`` as a thousand, ``nan`` and ``inf`` as numbers, ``١٢`` as twelve and refuses
``1,000``, where a number field of the editor or a cell of the sheet reads the same text
otherwise. This plugin, by the sources' types, reports each such call given a value whose
type may be text (``str``, ``bytes``, or a union holding one) outside the reader, unless the
function it is in is on ``NOT_TYPED`` with the reason its text is not typed by anyone (an
environment variable, a regular expression's matched digits, a version control answer).

A value typed ``Any`` is not seen, since settings arrive so: a setting typed as text is read
through ``haute._typed_numbers.setting_number``. The frontend's twin is
``frontend/src/components/form/__tests__/typedNumbersByHand.test.ts``.
"""

from __future__ import annotations

from collections.abc import Callable

from mypy.nodes import CallExpr, Expression, FuncBase, RefExpr
from mypy.plugin import FunctionContext, Plugin
from mypy.types import Instance, Type, UnionType, get_proper_type

#: The language's own readers of a number from text.
PARSERS = frozenset({"builtins.float", "builtins.int", "decimal.Decimal"})
#: The types of text.
TEXT_TYPES = frozenset({"builtins.str", "builtins.bytes"})
#: A number's own text, which is never typed: ``Decimal(str(value))``, ``Decimal(repr(value))``.
OWN_TEXT = frozenset({"builtins.str", "builtins.repr"})
#: The one reader, where the language's own readers are called on text it has checked.
READER = "haute._typed_numbers"

#: ``module.function`` -> why the text it reads as a number is not typed by anyone.
NOT_TYPED: dict[str, str] = {
    "haute._chunked_writes.<module>": "a chunk file's number in its own name, which Haute wrote",
    "haute._chunked_writes.is_part_name": "a part file's number in its own name, which Haute wrote",
    "haute._env.float_env": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute._env.int_env": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute._env.optional_int_env": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute._execute_lazy._extract_error_line": (
        "a line number in Python's own error text, matched by its pattern"
    ),
    "haute._git_core._leg_state": "a count in git's own output",
    "haute._git_history.commit_context": "a count in git's own output",
    "haute._host_memory._level_headroom": "a figure the operating system writes in a cgroup file",
    "haute._host_memory.available_vram_bytes": "a figure the GPU driver's tool prints",
    "haute._lint_refusal.lint_problems": (
        "a line number in the linter's own output, matched by its pattern"
    ),
    "haute._local_security._normalise_authority": (
        "a port in a request's Host header, digits matched by its pattern"
    ),
    "haute._mlflow_utils.<module>": "a model version, MLflow's own digits",
    "haute._native_memory_limit._linux_cgroup_current": (
        "a figure the operating system writes in a cgroup file"
    ),
    "haute._native_memory_limit.cgroup_oom_events": (
        "a count the operating system writes in a cgroup file"
    ),
    "haute._polars_environment.reap_polars_spill_dirs": (
        "a process id in a spill folder's name, which Polars wrote"
    ),
    "haute._polars_steps_lift._decimal_value": (
        "a number literal of pipeline code, Python's own grammar"
    ),
    "haute._polars_utils._configured_streaming_chunk_size": (
        "Polars' own setting, as Polars holds it"
    ),
    "haute._polars_utils.apply_spawned_streaming_chunk_size": (
        "Polars' own setting, as Polars holds it"
    ),
    "haute._project_python.pinned_python_version": (
        "a Python version's digits in the project's .python-version"
    ),
    "haute._quote_store._book.QuoteBook._search_filter": (
        "an option number in a searched reference (ABC-1/2), digits matched by its pattern"
    ),
    "haute._quote_store._book.details_of": (
        "a quote's option, which the app writes into its details as digits"
    ),
    "haute._quote_store._providers._sqlserver_address": "a port in a database connection address",
    "haute._quote_store._providers.server_url": "a port in a database connection address",
    "haute._rating._duration_key_to_physical": (
        "a duration key's day, hour and minute digits, matched by its pattern"
    ),
    "haute._trace_correlation._parse_duration": "a duration's digits, matched by its pattern",
    "haute._trace_correlation._typed_value_match_expr": (
        "a traced value's canonical whole-number text, matched by its pattern"
    ),
    "haute._trace_waterfall._is_unsafe_integer_string": (
        "a priced value's digits, which the pipeline wrote"
    ),
    "haute._uc_transport._list_pointer_generations": (
        "a bundle generation's digits in its own file name"
    ),
    "haute._uc_transport._prune_uc_bundles": "a bundle generation's digits in its own file name",
    "haute._workbench_export._typed_value": (
        "a priced whole number past what JSON holds, its digits as the pricing sent them"
    ),
    "haute._workbench_export.cell_address": (
        "a spreadsheet cell's row, the digits of an address like B12 matched by its pattern"
    ),
    "haute._workbench_export.number_format": "a number's own shortest text, read back exactly",
    "haute._workbench_impact._wait": "a server's Retry-After header",
    "haute._workbench_tables.index_number": "a numbered index's digits, which the sheets number",
    "haute._workbench_visibility._day_number": "a day's digits, held to YYYY-MM-DD",
    "haute._workbench_visibility._is_day": "a day's digits, held to YYYY-MM-DD",
    "haute.assistant._config._max_output_tokens": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute.assistant._providers._retry_after_seconds": "a provider's Retry-After header",
    "haute.deploy._mlflow.<module>": "a model version, MLflow's own digits",
    "haute.deploy._mlflow.get_deploy_status": "a model version, MLflow's own digits",
    "haute.deploy._request_limits._content_length_bytes": "a request's Content-Length header",
    "haute.deploy._request_limits._positive_int_from_config": (
        "an environment variable of the deployed service"
    ),
    "haute.modelling._algorithms._poll_metric_file": (
        "a training metric a model library writes to its own file"
    ),
    "haute.modelling._shap._significant": "a number's own text, rounded to significant figures",
    "haute.modelling._split._group_mask": "a hash's hexadecimal digits",
    "haute.modelling._training_job._LiveRounds.__init__": "the literal -inf, the clock's start",
    "haute.routes._optimiser_artifacts._artifact_stale_seconds": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute.routes._optimiser_quotes._equals": (
        "a filter's value, which the request holds as a strict JSON number for a number column"
    ),
    "haute.routes._pivot_service._aggregate_comparable": (
        "a pivot answer's value, an exact decimal Haute wrote"
    ),
    "haute.routes._pivot_service._member_literal": (
        "a pivot member's value, an exact decimal Haute wrote"
    ),
    "haute.routes._pivot_service._member_sort_key": (
        "a pivot member's value, an exact decimal Haute wrote"
    ),
    "haute.routes.mlflow.<module>": "a model version, MLflow's own digits",
    "haute.routes.project_files._plan_write": "an upload's Content-Length header",
    "scripts.check_changed_coverage.parse_unified_zero_diff": "a line range in git's own diff",
    "scripts.check_release_version._parts": "a release version's digits",
    "scripts.memory_smoke._proc_status_rss_bytes": "a figure the operating system writes in /proc",
    "scripts.refresh_test_durations.module_durations": "a duration in a test report",
    "scripts.run_frontend_e2e_server._port_from_env": (
        "an environment variable of the browser tests"
    ),
    "scripts.run_mutation_suite._parse_survival_rate": "a rate in the mutation tool's own output",
    "scripts.run_perf_suite._nonnegative_float": "a command-line argument of the performance suite",
    "scripts.spec_corpus_inventory._parse_ranges": "a line range in a specification's own index",
    "tests._property_budget.exploration_examples": "an environment variable of the property tests",
}


def _may_be_text(typ: Type) -> bool:
    proper = get_proper_type(typ)
    if isinstance(proper, UnionType):
        return any(_may_be_text(item) for item in proper.items)
    return isinstance(proper, Instance) and proper.type.fullname in (
        "builtins.str",
        "builtins.bytes",
    )


def _numbers_own_text(ctx: FunctionContext, argument: Expression) -> bool:
    """Whether *argument* is a number's own text: ``str`` or ``repr`` of a value that is not
    text."""
    if not (isinstance(argument, CallExpr) and isinstance(argument.callee, RefExpr)):
        return False
    if argument.callee.fullname not in OWN_TEXT or len(argument.args) != 1:
        return False
    return not _may_be_text(ctx.api.get_expression_type(argument.args[0]))


def _where(ctx: FunctionContext) -> str:
    """The function the call is in, ``module.function`` (``module.Class.method``); a function
    nested in another by its own name within the module."""
    api = ctx.api
    module: str = api.tree.fullname  # type: ignore[attr-defined]
    function = api.scope.current_function()  # type: ignore[attr-defined]
    if isinstance(function, FuncBase) and function.fullname:
        name: str = function.fullname
        return name if name.startswith(f"{module}.") else f"{module}.{name}"
    return f"{module}.<module>"


def _check(ctx: FunctionContext) -> Type:
    if not (ctx.arg_types and ctx.arg_types[0] and ctx.args and ctx.args[0]):
        return ctx.default_return_type
    if _may_be_text(ctx.arg_types[0][0]) and not _numbers_own_text(ctx, ctx.args[0][0]):
        where = _where(ctx)
        if not where.startswith(f"{READER}.") and where not in NOT_TYPED:
            ctx.api.fail(
                f"Text read as a number by hand in {where}: read typed text with "
                "haute._typed_numbers (read_typed_number, read_typed_whole, setting_number), "
                "or list text no one types on NOT_TYPED in scripts/mypy_typed_numbers.py "
                "with its reason",
                ctx.context,
            )
    return ctx.default_return_type


class TypedNumbersPlugin(Plugin):
    """Reports a language reader of a number called on text outside the one reader."""

    def get_function_hook(self, fullname: str) -> Callable[[FunctionContext], Type] | None:
        return _check if fullname in PARSERS else None


def plugin(version: str) -> type[Plugin]:
    """The plugin mypy loads (``[tool.mypy] plugins``)."""
    return TypedNumbersPlugin
