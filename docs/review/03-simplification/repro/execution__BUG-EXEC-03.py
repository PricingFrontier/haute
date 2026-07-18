"""Isolated reproduction for BUG-EXEC-03.

Claim: ``_compute_boundary_check_exceptions`` in ``src/haute/_execute_lazy.py``
builds and returns an exception tuple (ConfigError, OSError, [MlflowException])
that NO caller uses. The live boundary-check classification is done by a
*separate* function, ``_is_boundary_check_exception``, which re-derives the same
membership set independently (its own ``isinstance(exc, (ConfigError, OSError))``
plus its own MLflow import). Nothing ties the two together, so a future edit to
one but not the other silently desynchronises the "swallow this exception at
contract-check time" set.

This script is READ-ONLY w.r.t. the codebase: it only *imports* the two
functions and inspects/calls them. It never edits src/ or tests/. The "future
edit" in Part C is simulated entirely in this file via a local re-implementation
of each function — it does NOT mutate the real module.

Run: ``uv run python review/03-simplification/repro/execution__BUG-EXEC-03.py``
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

# --- Locate the real source module (read-only) --------------------------------
SRC = Path(__file__).resolve().parents[3] / "src" / "haute" / "_execute_lazy.py"
assert SRC.is_file(), f"expected source at {SRC}"

# Import the real functions and the real ConfigError so we exercise the genuine
# logic, not a paraphrase of it.
from haute._execute_lazy import (  # noqa: E402
    _compute_boundary_check_exceptions,
    _is_boundary_check_exception,
)
from haute.errors import ConfigError  # noqa: E402


def part_a_builder_is_dead_in_module() -> None:
    """PART A — prove ``_compute_boundary_check_exceptions`` has zero callers.

    Walk the AST of the whole module and collect every called name. The
    tuple-builder must NOT appear as a call target anywhere in the module
    (its only textual occurrence is its own ``def`` line).
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))

    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    # The live predicate IS called (sanity check the AST walk works).
    assert "_is_boundary_check_exception" in called_names, (
        "expected the live predicate to be called somewhere in the module; "
        "AST walk may be broken"
    )

    # The builder is NEVER called -> dead code.
    assert "_compute_boundary_check_exceptions" not in called_names, (
        "EXPECTED-FAIL-TO-REPRODUCE: builder appears to have a caller now; "
        "the dead-code premise no longer holds"
    )
    print(
        "PART A ok: `_compute_boundary_check_exceptions` is never called in "
        "the module (dead); `_is_boundary_check_exception` is the live consumer."
    )


def part_b_live_predicate_ignores_builder() -> None:
    """PART B — the live predicate does not consult the builder's tuple.

    Build the tuple the (dead) builder produces, then show the live predicate's
    answer is computed from its OWN inlined membership, independent of that
    tuple. We demonstrate independence by classifying an instance of every type
    in the builder's tuple and confirming the live predicate agrees *today* —
    i.e. they happen to be in sync now, which is exactly why drift would go
    unnoticed.
    """
    builder_tuple = _compute_boundary_check_exceptions()
    assert ConfigError in builder_tuple and OSError in builder_tuple

    # Today both agree: every class the builder lists is also accepted by the
    # live predicate, and a programmer-error class is rejected by both.
    for exc in (ConfigError("x"), OSError("disk")):
        assert isinstance(exc, builder_tuple), "builder tuple should match"
        assert _is_boundary_check_exception(exc), "live predicate should match"

    assert not _is_boundary_check_exception(KeyError("k")), (
        "programmer error must propagate (live predicate)"
    )
    assert not isinstance(KeyError("k"), builder_tuple), (
        "programmer error must propagate (builder tuple)"
    )
    print(
        "PART B ok: builder tuple and live predicate are *currently* in sync, "
        "so nothing forces them to stay in sync."
    )


def part_c_drift_is_silent() -> None:
    """PART C — simulate a one-sided edit; show the two classifiers diverge
    with nothing in the code tying them together to catch it.

    A maintainer decides a transient infra condition raised by the model
    backend should also be treated as a recoverable boundary-check exception.
    They add a dedicated ``BackendUnreachableError`` and wire it into the
    *builder* (the function that *looks* like the source of truth, since it
    returns a named tuple) but miss the live predicate. We reproduce that exact
    scenario with two local functions mirroring the real implementations, then
    assert the divergence on a concrete value.

    NOTE: This example deliberately uses a brand-new exception class that is
    NOT already a subclass of ConfigError/OSError. The bug report's own
    illustration (``TimeoutError``) would *not* have demonstrated drift, because
    ``TimeoutError`` is a subclass of ``OSError`` and is therefore already
    accepted by both classifiers — running the repro surfaced that, confirming
    the divergence only manifests for genuinely-new classes.
    """

    class BackendUnreachableError(Exception):
        """A new transient-infra error a maintainer wants treated as recoverable."""

    def builder_after_edit() -> tuple[type[BaseException], ...]:
        # Mirrors `_compute_boundary_check_exceptions` + the new class.
        return (ConfigError, OSError, BackendUnreachableError)

    def predicate_unchanged(exc: BaseException) -> bool:
        # Verbatim mirror of the CURRENT `_is_boundary_check_exception` body
        # (MLflow branch omitted; not importable / not relevant here).
        return isinstance(exc, (ConfigError, OSError))

    sample = BackendUnreachableError("tracking store unreachable")

    builder_says_recoverable = isinstance(sample, builder_after_edit())
    predicate_says_recoverable = predicate_unchanged(sample)

    # The bug: the two sources of truth now DISAGREE about the new class.
    assert builder_says_recoverable is True, "edited builder should include the new class"
    assert predicate_says_recoverable is False, "unedited predicate still excludes it"
    assert builder_says_recoverable != predicate_says_recoverable, (
        "EXPECTED-FAIL-TO-REPRODUCE: classifiers stayed in sync; no drift"
    )

    # And critically: the LIVE code path keys off the predicate, so the
    # maintainer's intended behaviour (recover from the new error) silently does
    # NOT happen — the edit to the dead builder has zero runtime effect. We
    # prove inertness against the REAL live predicate using an instance that is
    # not in its inlined set.
    assert _is_boundary_check_exception(BackendUnreachableError("x")) is False, (
        "the real live predicate rejects the new class regardless of any edit "
        "to the dead builder -> builder edits are inert"
    )
    print(
        "PART C ok: one-sided edit makes builder and predicate DISAGREE on a "
        "new error class (builder=recoverable, live predicate=not). The live "
        "path follows the predicate, so the builder edit is silently inert. "
        "Nothing in the source ties the two together to flag the discrepancy."
    )


def main() -> int:
    part_a_builder_is_dead_in_module()
    part_b_live_predicate_ignores_builder()
    part_c_drift_is_silent()
    print(
        "\nREPRODUCED: BUG-EXEC-03 confirmed. "
        "`_compute_boundary_check_exceptions` is dead (no callers); "
        "`_is_boundary_check_exception` re-implements the same set independently; "
        "a one-sided edit silently desynchronises them with no guard to catch it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
