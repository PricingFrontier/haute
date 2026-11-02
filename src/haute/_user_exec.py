"""Execution of user-provided node code.

This module owns the sandboxed ``exec()`` path that used to live in
``haute.executor``.  Keeping it separate makes the executor module
smaller while preserving the same runtime behavior and traceback shape
for user code failures.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from haute._executable_names import RESERVED_NAMES, reserved_input_problem
from haute._global_constants import bind_code_view
from haute._sandbox import (
    UnsafeCodeError,
    compile_project_code,
    project_code_module,
    safe_globals,
    validate_user_code,
)
from haute._types import _Frame
from haute.errors import ExecutionError


def user_code_line(exc: BaseException) -> int | None:
    """The line of node code *exc* was raised from, or None when node code did not raise it.

    ``_exec_user_code`` records the line from the traceback of any exception
    that escapes the code it runs.
    """
    line = getattr(exc, "_user_code_line", None)
    return None if line is None else int(line)


def _exec_user_code(
    code: str,
    src_names: list[str],
    dfs: tuple[_Frame, ...],
    extra_ns: dict[str, Any] | None = None,
    orig_source_names: list[str] | None = None,
    *,
    alias_first_input_as_df: bool = False,
    parameters: Mapping[str, Any] | None = None,
) -> _Frame:
    """Execute user-provided code and return the ``df`` variable.

    Inputs are bound only under their names in *src_names*; ``df`` is the
    output variable the code must assign.  Hook-style nodes whose code box
    operates on a single implicit frame called ``df`` (external files,
    explore, and post-code hooks) pass ``alias_first_input_as_df=True`` to
    keep that contract. Polars transforms never do. A transform's keyword-only
    *parameters* are bound beside its inputs, so they too take precedence
    over the preamble, as a function's parameters do.
    """
    local_ns: dict[str, Any] = {}
    for i, d in enumerate(dfs):
        if i < len(src_names):
            local_ns[src_names[i]] = d
    if orig_source_names is not None:
        # Bind together: one logical name may be another input's current name.
        local_ns.update(
            {
                logical: local_ns[current]
                for logical, current in zip(orig_source_names, src_names, strict=True)
            }
        )
    # ``df`` has its own rule below; the other reserved names would replace
    # what node code reads under them (polars, the constants view).
    reserved_inputs = [name for name in local_ns if name in RESERVED_NAMES]
    if reserved_inputs:
        problem = reserved_input_problem(reserved_inputs)
        raise ExecutionError(str(problem))
    if not alias_first_input_as_df and "df" in local_ns:
        raise ExecutionError(
            "The input name 'df' conflicts with the reserved output name in a Transform's "
            "code. Rename the upstream node or frame so the transform can assign its result "
            "to 'df'."
        )
    if alias_first_input_as_df and dfs:
        local_ns["df"] = dfs[0]
    local_ns.update(parameters or {})

    try:
        validate_user_code(code)
    except UnsafeCodeError as uce:
        if isinstance(uce.__cause__, SyntaxError):
            raise uce.__cause__ from None
        raise

    # Start from the execution globals/preamble, then overlay dataframe
    # bindings so inputs have ordinary function-parameter precedence. One
    # shared namespace also makes those inputs visible to comprehensions and
    # nested helpers, matching generated function execution. ``df`` is the
    # transform's reserved output slot (or an explicitly seeded implicit input
    # above), so a preamble binding must never supply it.
    # The code reads the pipeline's constants through a view restricted to the
    # ones it names, so a read the cache identities cannot see fails here.
    code_ns = bind_code_view(extra_ns, code)
    global_ns = {name: value for name, value in (code_ns or {}).items() if name != "df"}
    execution_ns = safe_globals(pl=pl, **global_ns)
    execution_ns.update(local_ns)

    try:
        with project_code_module(execution_ns):
            exec(compile_project_code(code), execution_ns, execution_ns)
    except Exception as exc:
        if exc.__traceback__:
            import traceback as _tb

            for frame in reversed(_tb.extract_tb(exc.__traceback__)):
                if frame.filename == "<string>" and frame.lineno is not None:
                    exc._user_code_line = frame.lineno  # type: ignore[attr-defined]
                    break
        raise

    if "df" not in execution_ns:
        inputs = [name for name in src_names if name in execution_ns]
        detail = (
            "Inputs are available by name: " + ", ".join(inputs)
            if inputs
            else "This node has no inputs, so the code must construct a frame itself."
        )
        raise ExecutionError(
            f"Node code must assign its result to 'df'; nothing was assigned. {detail}"
        )
    result = execution_ns["df"]
    if isinstance(result, pl.DataFrame):
        result = result.lazy()
    return result  # type: ignore[no-any-return]
