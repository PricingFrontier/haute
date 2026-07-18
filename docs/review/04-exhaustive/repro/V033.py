"""Isolated reproduction for V033.

Claim: the parse-time contract-fallback exception set in
``_config_builder`` (``_compute_contract_resolve_fallback_exceptions`` /
``_is_contract_resolve_fallback_exception``) still contains ``ImportError``
and ``RuntimeError``, whereas its documented runtime mirror in
``_execute_lazy`` (``_compute_boundary_check_exceptions`` /
``_is_boundary_check_exception``) was deliberately narrowed (commit
34aff403) to drop those two.  The parse-time copy's docstring asserts it
"must not swallow more than the runtime boundary check would" and claims to
match ``_execute_lazy._BOUNDARY_CHECK_EXCEPTIONS`` — but it is now a strict
SUPERSET, so the documented invariant is violated.

Behavioural consequence demonstrated here: when ``_derive_parse_time_contract``
raises ``ImportError`` or ``RuntimeError``, ``_validate_user_contract``
silently swallows a *mismatching* user-declared contract (falls back to
``Contract.opaque()`` -> no ``ContractMismatchError``), even though the
runtime boundary predicate would classify the very same exception as
NON-recoverable and re-raise it.

ISOLATION: pure in-memory; we only import the two modules and call the
predicates / ``_validate_user_contract`` directly.  No rating/, src/, tests/,
or real project files are read or written; no MLflow / model artifacts are
touched (the MODEL_SCORE parse path never loads artifacts, so we patch
``_derive_parse_time_contract`` to inject the raise deterministically).
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Make the in-repo source importable without touching project data files.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))

import haute._config_builder as cb  # noqa: E402
import haute._execute_lazy as el  # noqa: E402
from haute._contracts import Contract  # noqa: E402
from haute._types import NodeType  # noqa: E402
from haute.errors import ContractMismatchError  # noqa: E402


def main() -> int:
    failures: list[str] = []

    # ------------------------------------------------------------------
    # Part 1 — the predicates disagree on ImportError / RuntimeError.
    # The parse-time copy claims (in its docstring) to "match" the runtime
    # boundary check and to "not swallow more than" it.  Prove the value is
    # wrong: parse-time returns True (swallow) where runtime returns False
    # (re-raise) for both ImportError and RuntimeError.
    # ------------------------------------------------------------------
    print("--- Part 1: predicate parity on ImportError / RuntimeError ---")
    for exc in (
        ImportError("No module named 'catboost'"),
        RuntimeError("Persistently corrupt model artifact"),
    ):
        parse_time = cb._is_contract_resolve_fallback_exception(exc)
        runtime = el._is_boundary_check_exception(exc)
        print(
            f"  {type(exc).__name__:<13} parse-time swallow={parse_time!s:<5} "
            f"runtime swallow={runtime!s:<5}"
        )
        # Documented invariant: parse-time must NOT swallow more than runtime.
        # i.e. parse_time True while runtime False is exactly the forbidden case.
        if not (parse_time is True and runtime is False):
            failures.append(
                f"Part1/{type(exc).__name__}: expected parse-time=True & runtime=False "
                f"(the documented-forbidden superset), got parse-time={parse_time}, "
                f"runtime={runtime} — drift not present as predicted."
            )

    # The 'computed tuple' siblings must show the same divergence so the bug
    # is not merely in the hand-written isinstance copy.
    parse_tuple = cb._compute_contract_resolve_fallback_exceptions()
    runtime_tuple = el._compute_boundary_check_exceptions()
    print(f"  parse-time tuple : {tuple(t.__name__ for t in parse_tuple)}")
    print(f"  runtime   tuple : {tuple(t.__name__ for t in runtime_tuple)}")
    extra = set(parse_tuple) - set(runtime_tuple)
    extra_names = {t.__name__ for t in extra}
    if not {"ImportError", "RuntimeError"}.issubset(extra_names):
        failures.append(
            "Part1/tuple: expected parse-time tuple to be a strict superset over "
            f"runtime by at least {{ImportError, RuntimeError}}, got extra={extra_names}."
        )

    # ------------------------------------------------------------------
    # Part 2 — the docstring references a symbol that does not exist,
    # confirming the docstring drifted from reality.
    # ------------------------------------------------------------------
    print("--- Part 2: docstring-referenced symbol existence ---")
    has_symbol = hasattr(el, "_BOUNDARY_CHECK_EXCEPTIONS")
    print(f"  _execute_lazy._BOUNDARY_CHECK_EXCEPTIONS exists = {has_symbol}")
    if has_symbol:
        failures.append(
            "Part2: _BOUNDARY_CHECK_EXCEPTIONS unexpectedly EXISTS; the finding's "
            "claim that the docstring references a non-existent symbol is wrong."
        )

    # ------------------------------------------------------------------
    # Part 3 — behavioural end-to-end: a MISMATCHING user contract is
    # silently accepted at parse time when derivation raises ImportError /
    # RuntimeError, because the wide superset swallows it.  Same exception
    # under the runtime predicate would be re-raised (asserted in Part 1).
    # ------------------------------------------------------------------
    print("--- Part 3: parse-time _validate_user_contract swallows mismatch ---")
    node_type = NodeType.POLARS
    config: dict[str, object] = {}
    # User declares a concrete contract; the builder, were it resolvable,
    # would derive something DIFFERENT (POLARS is opaque -> any concrete
    # user inputs/outputs would normally either match-or-be-accepted; we use
    # a side the builder would contradict).  To make the mismatch
    # unambiguous we force derivation to raise, then confirm NO mismatch is
    # reported (i.e. it fell back to opaque and swallowed).
    user_declared = Contract(inputs=frozenset({"a"}), outputs=frozenset({"b"}))

    orig_derive = cb._derive_parse_time_contract

    for exc_factory, label in (
        (lambda: ImportError("No module named 'rustystats'"), "ImportError"),
        (lambda: RuntimeError("Persistently corrupt model artifact"), "RuntimeError"),
    ):

        def _raising_derive(_nt: NodeType, _cfg: dict, _exc=exc_factory) -> Contract:
            raise _exc()

        cb._derive_parse_time_contract = _raising_derive  # type: ignore[assignment]
        try:
            raised: BaseException | None = None
            try:
                cb._validate_user_contract(node_type, config, user_declared, "demo_node")
            except BaseException as e:  # noqa: BLE001 - characterising behaviour
                raised = e
        finally:
            cb._derive_parse_time_contract = orig_derive  # type: ignore[assignment]

        swallowed = raised is None
        print(
            f"  derivation raises {label:<13} -> _validate_user_contract "
            f"{'SWALLOWED (no error)' if swallowed else f'raised {type(raised).__name__}'}"
        )
        # Bug: the parse-time validator swallows the raise and accepts the
        # mismatching contract.  If instead it re-raised (matching the
        # narrowed runtime semantics), the finding would be refuted.
        if raised is not None:
            # If it re-raised the original exc, the superset is NOT in effect.
            if not isinstance(raised, ContractMismatchError):
                failures.append(
                    f"Part3/{label}: expected the wide set to SWALLOW the raise "
                    f"(fall back to opaque, no error), but it propagated "
                    f"{type(raised).__name__} — superset not in effect."
                )
            # A ContractMismatchError would mean opaque-vs-concrete somehow
            # disagreed, which cannot happen (opaque side short-circuits), so
            # treat that as an unexpected setup problem too.
            else:
                failures.append(
                    f"Part3/{label}: unexpected ContractMismatchError under opaque "
                    "fallback — repro setup invalid."
                )

    print()
    if failures:
        print("REPRO RESULT: claim NOT reproduced as predicted")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("REPRO RESULT: DRIFT REPRODUCED — parse-time fallback set is a strict")
    print("superset of the narrowed runtime boundary check: it swallows ImportError")
    print("and RuntimeError (returning True / falling back to opaque) where the")
    print("runtime sibling re-raises (False).  The documented invariant 'must not")
    print("swallow more than the runtime boundary check would' is violated, and the")
    print("referenced symbol _BOUNDARY_CHECK_EXCEPTIONS does not exist.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # pragma: no cover - surface unexpected harness errors
        traceback.print_exc()
        raise SystemExit(2)
