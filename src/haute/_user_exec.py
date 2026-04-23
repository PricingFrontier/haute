"""Execution of user-provided node code.

This module owns the sandboxed ``exec()`` path that used to live in
``haute.executor``.  Keeping it separate makes the executor module
smaller while preserving the same runtime behavior and traceback shape
for user code failures.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from haute._graph_utils import build_instance_mapping
from haute._sandbox import UnsafeCodeError, safe_globals, validate_user_code
from haute._types import _Frame


def _exec_user_code(
    code: str,
    src_names: list[str],
    dfs: tuple[_Frame, ...],
    extra_ns: dict[str, Any] | None = None,
    orig_source_names: list[str] | None = None,
    input_mapping: dict[str, str] | None = None,
) -> _Frame:
    """Execute user-provided code and return the ``df`` variable."""
    local_ns: dict[str, Any] = {"pl": pl}
    for i, d in enumerate(dfs):
        if i < len(src_names):
            local_ns[src_names[i]] = d
    if orig_source_names:
        mapping = build_instance_mapping(orig_source_names, src_names, input_mapping)
        for orig, inst in mapping.items():
            if orig not in local_ns and inst in local_ns:
                local_ns[orig] = local_ns[inst]
    if dfs:
        local_ns["df"] = dfs[0]
    if extra_ns:
        local_ns.update(extra_ns)

    try:
        validate_user_code(code)
    except UnsafeCodeError as uce:
        if isinstance(uce.__cause__, SyntaxError):
            raise uce.__cause__ from None
        raise

    try:
        exec(code, safe_globals(pl=pl, **(extra_ns or {})), local_ns)
    except Exception as exc:
        if exc.__traceback__:
            import traceback as _tb

            for frame in reversed(_tb.extract_tb(exc.__traceback__)):
                if frame.filename == "<string>" and frame.lineno is not None:
                    exc._user_code_line = frame.lineno  # type: ignore[attr-defined]
                    break
        raise

    result = local_ns.get("df", dfs[0] if dfs else pl.LazyFrame())
    if isinstance(result, pl.DataFrame):
        result = result.lazy()
    return result  # type: ignore[no-any-return]
