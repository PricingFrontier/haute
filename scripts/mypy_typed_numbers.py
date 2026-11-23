"""A mypy plugin: no code reads text as a number but the one reader.

Text typed as a number is read by one grammar, the same in every field, in every browser
language and on the server (specs/frontend-shared high-level, "Typed numbers"):
``haute._typed_numbers``, held with the browser's ``frontend/src/utils/typedNumbers.ts`` to
one case file. Code that reads text with Python's own ``float``, ``int`` or ``Decimal`` takes
``1_000`` as a thousand, ``nan`` and ``inf`` as numbers, ``١٢`` as twelve and refuses
``1,000``, where a number field of the editor or a cell of the sheet reads the same text
otherwise. This plugin, by the sources' types, reports each such call given a value whose
type may be text (``str``, ``bytes``, or a union holding one) outside the reader, unless the
call itself is on ``NOT_TYPED`` (by its function and its source) with the reason its text is
not typed by anyone (an
environment variable, a regular expression's matched digits, a version control answer).

A value typed ``Any`` is not seen, since settings arrive so: a setting typed as text is read
through ``haute._typed_numbers.setting_number``. The frontend's twin is
``frontend/src/components/form/__tests__/typedNumbersByHand.test.ts``.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable

from mypy.nodes import CallExpr, Expression, FuncBase, MemberExpr, NameExpr, RefExpr
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

#: Digits a temporal value's text holds, matched by a regular expression first.
_REGEX_TEMPORAL_DIGITS = (
    "digits a regular expression matched in a date-time, time or duration: no sign, "
    "separator or exponent to read"
)

#: ``module.function::call`` -> why the text that call reads as a number is not typed by
#: anyone. Each exception covers its one call (a second of the same call in one function is
#: ``call#2``), so another conversion beside it is still refused.
NOT_TYPED: dict[str, str] = {
    "haute._project_settings._parse_bytes::int(at.group(1))": (
        "the line tomllib's own refusal names in digits, not text anyone types"
    ),
    "haute._project_settings._parse_bytes::int(at.group(2))": (
        "the column tomllib's own refusal names in digits, not text anyone types"
    ),
    "haute._chunked_writes.<module>::int(path.name[len(PART_PREFIX) : -len(PART_SUFFIX)])": (
        "a chunk file's number in its own name, which Haute wrote"
    ),
    "haute._chunked_writes.is_part_name::int(stem)": (
        "a part file's number in its own name, which Haute wrote"
    ),
    "haute._env.float_env::float(raw)": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute._env.int_env::int(raw)": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    'haute._temporal_text._below_a_microsecond::int(digits[6:].ljust(3, "0"))': (
        _REGEX_TEMPORAL_DIGITS
    ),
    'haute._temporal_text._duration_physical::int(match["hours"])': (_REGEX_TEMPORAL_DIGITS),
    'haute._temporal_text._duration_physical::int(match["minutes"])': (_REGEX_TEMPORAL_DIGITS),
    'haute._temporal_text._duration_physical::int(match["seconds"])': (_REGEX_TEMPORAL_DIGITS),
    'haute._temporal_text._duration_physical::int(match["days"] or 0)': (_REGEX_TEMPORAL_DIGITS),
    'haute._temporal_text._duration_physical::int((match["digits"] or "").ljust(9, "0"))': (
        _REGEX_TEMPORAL_DIGITS
    ),
    "haute._source_layout.number_spelling::int(mantissa)": (
        "a step's number as Python source spells it (its ``text``), already held to Python's "
        "literal grammar by ``_NUMBER_TEXT``: the editor reads what is typed with the typed reader"
    ),
    "haute._source_layout.number_spelling::float(spelled)": (
        "a step's number as Python source spells it (its ``text``), already held to Python's "
        "literal grammar by ``_NUMBER_TEXT``: the editor reads what is typed with the typed reader"
    ),
    "haute._polars_steps._Renderer._spelling::float(spelling)": (
        "a step's number as Python source spells it (its ``text``), already held to Python's "
        "literal grammar by ``_NUMBER_TEXT``: the editor reads what is typed with the typed reader"
    ),
    "haute._env.optional_int_env::int(raw)": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute._execute_lazy._extract_error_line::int(match.group(1))": (
        "a line number in Python's own error text, matched by its pattern"
    ),
    "haute._git_core._leg_state::int(parts[0])": "a count in git's own output",
    "haute._git_core._leg_state::int(parts[1])": "a count in git's own output",
    "haute._git_history.commit_context::int(count_raw.strip())": "a count in git's own output",
    "haute._git_history.commit_context::int(delta_raw.strip())": "a count in git's own output",
    "haute._host_memory._level_headroom::int(raw_current)": (
        "a figure the operating system writes in a cgroup file"
    ),
    "haute._host_memory._level_headroom::int(raw_limit)": (
        "a figure the operating system writes in a cgroup file"
    ),
    "haute._host_memory.available_vram_bytes::int(line)": "a figure the GPU driver's tool prints",
    'haute._lint_refusal.lint_problems::int(match["line"])': (
        "a line number in the linter's own output, matched by its pattern"
    ),
    "haute._local_security._normalise_authority::int(port_text)": (
        "a port in a request's Host header, digits matched by its pattern"
    ),
    "haute._local_security._normalise_authority::int(port_text)#2": (
        "a port in a request's Host header, digits matched by its pattern"
    ),
    "haute._mlflow_utils.<module>::int(v.version)": "a model version, MLflow's own digits",
    (
        "haute._native_memory_limit._linux_cgroup_current::"
        'int((path / "memory.current").read_text(encoding="ascii").strip())'
    ): ("a figure the operating system writes in a cgroup file"),
    "haute._native_memory_limit.cgroup_oom_events::int(value)": (
        "a count the operating system writes in a cgroup file"
    ),
    "haute._polars_environment.reap_polars_spill_dirs::int(folder.name)": (
        "a process id in a spill folder's name, which Polars wrote"
    ),
    "haute._polars_steps_lift._decimal_value::float(text)": (
        "a number literal of pipeline code, Python's own grammar"
    ),
    "haute._polars_steps_lift._decimal_value::float(text)#2": (
        "a number literal of pipeline code, Python's own grammar"
    ),
    "haute._polars_steps_lift._decimal_value::int(text)": (
        "a number literal of pipeline code, Python's own grammar"
    ),
    "haute._polars_utils._configured_streaming_chunk_size::int(raw)": (
        "Polars' own setting, as Polars holds it"
    ),
    "haute._polars_utils.apply_spawned_streaming_chunk_size::int(raw)": (
        "Polars' own setting, as Polars holds it"
    ),
    "haute._project_python.pinned_python_version::int(match.group(1))": (
        "a Python version's digits in the project's .python-version"
    ),
    "haute._quote_store._book.QuoteBook._search_filter::int(named.group(2))": (
        "an option number in a searched reference (ABC-1/2), digits matched by its pattern"
    ),
    "haute._quote_store._book.details_of::int(value)": (
        "a quote's option, which the app writes into its details as digits"
    ),
    "haute._quote_store._providers._sqlserver_address::int(number)": (
        "a port in a database connection address"
    ),
    "haute._quote_store._providers.server_url::int(number)": (
        "a port in a database connection address"
    ),
    'haute._rating._duration_key_to_physical::int(match.group("days") or 0)': (
        "a duration key's day, hour and minute digits, matched by its pattern"
    ),
    'haute._rating._duration_key_to_physical::int(match.group("hours") or 0)': (
        "a duration key's day, hour and minute digits, matched by its pattern"
    ),
    'haute._rating._duration_key_to_physical::int(match.group("minutes") or 0)': (
        "a duration key's day, hour and minute digits, matched by its pattern"
    ),
    "haute._trace_correlation._typed_value_match_expr::int(value)": (
        "a traced value's canonical whole-number text, matched by its pattern"
    ),
    "haute._trace_waterfall._is_unsafe_integer_string::int(stripped)": (
        "a priced value's digits, which the pipeline wrote"
    ),
    "haute._uc_transport._list_pointer_generations::int(name[:6])": (
        "a bundle generation's digits in its own file name"
    ),
    "haute._uc_transport._prune_uc_bundles::int(generation_text)": (
        "a bundle generation's digits in its own file name"
    ),
    "haute._workbench_export._typed_value::int(held)": (
        "a priced whole number past what JSON holds, its digits as the pricing sent them"
    ),
    "haute._workbench_export.cell_address::int(digits)": (
        "a spreadsheet cell's row, the digits of an address like B12 matched by its pattern"
    ),
    'haute._workbench_export.number_format::Decimal("0.01")': (
        "a number's own shortest text, read back exactly"
    ),
    "haute._workbench_impact._wait::float(retry_after)": "a server's Retry-After header",
    "haute._workbench_tables.index_number::int(value)": (
        "a numbered index's digits, which the sheets number"
    ),
    "haute._workbench_tables.index_number::int(value)#2": (
        "a numbered index's digits, which the sheets number"
    ),
    "haute._workbench_visibility._day_number::int(day[5:7])": "a day's digits, held to YYYY-MM-DD",
    "haute._workbench_visibility._day_number::int(day[8:])": "a day's digits, held to YYYY-MM-DD",
    "haute._workbench_visibility._day_number::int(day[:4])": "a day's digits, held to YYYY-MM-DD",
    "haute._workbench_visibility._is_day::int(text[5:7])": "a day's digits, held to YYYY-MM-DD",
    "haute._workbench_visibility._is_day::int(text[8:])": "a day's digits, held to YYYY-MM-DD",
    "haute._workbench_visibility._is_day::int(text[:4])": "a day's digits, held to YYYY-MM-DD",
    "haute.assistant._config._max_output_tokens::int(raw, 10)": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute.assistant._providers._retry_after_seconds::float(raw)": (
        "a provider's Retry-After header"
    ),
    "haute.deploy._mlflow.<module>::int(v.version)": "a model version, MLflow's own digits",
    "haute.deploy._mlflow.get_deploy_status::int(latest.version)": (
        "a model version, MLflow's own digits"
    ),
    "haute.deploy._request_limits._content_length_bytes::int(raw)": (
        "a request's Content-Length header"
    ),
    "haute.deploy._request_limits._positive_int_from_config::int(raw)": (
        "an environment variable of the deployed service"
    ),
    "haute.modelling._algorithms._poll_metric_file::int(parts[0])": (
        "a training metric a model library writes to its own file"
    ),
    'haute.modelling._shap._significant::float(f"{value:.{figures}g}")': (
        "a number's own text, rounded to significant figures"
    ),
    "haute.modelling._split._group_mask::int(h[:8], 16)": "a hash's hexadecimal digits",
    'haute.modelling._training_job._LiveRounds.__init__::float("-inf")': (
        "the literal -inf, the clock's start"
    ),
    "haute.routes._optimiser_artifacts._artifact_stale_seconds::int(raw)": (
        "an environment variable, set by whoever runs Haute, not typed into a field"
    ),
    "haute.routes._optimiser_quotes._equals::float(value)": (
        "a filter's value, which the request holds as a strict JSON number for a number column"
    ),
    "haute.routes._pivot_service._aggregate_comparable::Decimal(str(value))": (
        "a pivot answer's value, an exact decimal Haute wrote"
    ),
    "haute.routes._pivot_service._member_literal::Decimal(cast(str, value))": (
        "a pivot member's value, an exact decimal Haute wrote"
    ),
    "haute.routes._pivot_service._member_literal::int(cast(str, value))": (
        "a pivot member's value, an exact decimal Haute wrote"
    ),
    "haute.routes._pivot_service._member_sort_key::Decimal(cast(str, value))": (
        "a pivot member's value, an exact decimal Haute wrote"
    ),
    "haute.routes.mlflow.<module>::int(v.version)": "a model version, MLflow's own digits",
    "haute.routes.project_files._plan_write::int(length_header)": (
        "an upload's Content-Length header"
    ),
    "scripts.check_changed_coverage.parse_unified_zero_diff::int(match.group(1))": (
        "a line range in git's own diff"
    ),
    'scripts.check_changed_coverage.parse_unified_zero_diff::int(match.group(2) or "1")': (
        "a line range in git's own diff"
    ),
    "scripts.check_release_version._parts::int(match[1])": "a release version's digits",
    "scripts.check_release_version._parts::int(match[2])": "a release version's digits",
    "scripts.check_release_version._parts::int(match[3])": "a release version's digits",
    "scripts.memory_smoke._proc_status_rss_bytes::int(parts[1])": (
        "a figure the operating system writes in /proc"
    ),
    'scripts.refresh_test_durations.module_durations::float(case.get("time", ""))': (
        "a duration in a test report"
    ),
    "scripts.run_frontend_e2e_server._port_from_env::int(raw_value)": (
        "an environment variable of the browser tests"
    ),
    'scripts.run_mutation_suite._parse_survival_rate::float(match.group("rate"))': (
        "a rate in the mutation tool's own output"
    ),
    "scripts.run_perf_suite._nonnegative_float::float(value)": (
        "a command-line argument of the performance suite"
    ),
    "scripts.spec_corpus_inventory._parse_ranges::int(end_text)": (
        "a line range in a specification's own index"
    ),
    "scripts.spec_corpus_inventory._parse_ranges::int(start_text)": (
        "a line range in a specification's own index"
    ),
    "tests._property_budget.exploration_examples::int(raw)": (
        "an environment variable of the property tests"
    ),
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


@functools.lru_cache(maxsize=512)
def _source_lines(path: str) -> tuple[str, ...]:
    with open(path, encoding="utf-8") as handle:
        return tuple(handle.read().splitlines())


#: A language reader's name where a call of it starts, qualified or not.
_CALLEE = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*(?:float|int|Decimal)\(")


def _call_text(lines: tuple[str, ...], line: int, column: int) -> str | None:
    """The source of the reader called at *line* (from 1) and *column*: its name and its
    parenthesised arguments, across lines, its spaces collapsed; or None when no call starts
    there. mypy gives a call inside an f-string the f-string's place, which names no one call,
    so such a call has no source of its own."""
    found = _CALLEE.match(lines[line - 1], column)
    if found is None:
        return None
    depth, taken = 0, []
    for row in range(line - 1, len(lines)):
        piece = lines[row][found.start() :] if row == line - 1 else lines[row]
        for at, character in enumerate(piece):
            depth += {"(": 1, ")": -1}.get(character, 0)
            if depth == 0 and character == ")":
                taken.append(piece[: at + 1])
                return " ".join(" ".join(taken).split())
        taken.append(piece)
    return " ".join(" ".join(taken).split())


def _call_key(ctx: FunctionContext, where: str) -> str | None:
    """The call's key on ``NOT_TYPED``: ``module.function::call``, the call's own source text
    with its spaces collapsed, a second of the same call in one function ``call#2`` by its
    place in the source, so an exception covers that one call and no other beside it; None for
    a call that cannot be told from others by its source (one inside an f-string), which no
    exception covers."""
    node = ctx.context
    lines = _source_lines(ctx.api.path)
    call = _call_text(lines, node.line, node.column)
    if call is None:
        return None
    function = ctx.api.scope.current_function()  # type: ignore[attr-defined]
    first = function.line if isinstance(function, FuncBase) else 1
    before = "\n".join([*lines[first - 1 : node.line - 1], lines[node.line - 1][: node.column]])
    earlier = " ".join(before.split()).count(call)
    return f"{where}::{call}" + (f"#{earlier + 1}" if earlier else "")


def _check(ctx: FunctionContext) -> Type:
    if not (ctx.arg_types and ctx.arg_types[0] and ctx.args and ctx.args[0]):
        return ctx.default_return_type
    if _may_be_text(ctx.arg_types[0][0]) and not _numbers_own_text(ctx, ctx.args[0][0]):
        where = _where(ctx)
        key = _call_key(ctx, where)
        if not where.startswith(f"{READER}.") and (key is None or key not in NOT_TYPED):
            call = ctx.context
            reader = (
                call.callee.name
                if isinstance(call, CallExpr) and isinstance(call.callee, NameExpr | MemberExpr)
                else "a reader"
            )
            at = (
                f"`{key}`"
                if key is not None
                else f"{reader}() in an f-string in {where}, which no exception covers"
            )
            ctx.api.fail(
                f"Text read as a number by hand at {at}: read typed text with "
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
