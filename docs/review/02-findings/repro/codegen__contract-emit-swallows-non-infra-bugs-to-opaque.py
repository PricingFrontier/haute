"""Adversarial repro for claim:
  contract-emit-swallows-non-infra-bugs-to-opaque

Claim: ``haute.codegen._format_contract_kwarg`` wraps the column-contract
computation in a broad ``except Exception`` that re-raises only ``ConfigError``.
Every OTHER exception class -- including genuine code/data defects such as
``TypeError``, ``KeyError``, ``AttributeError`` and the domain-level
``ContractMismatchError`` -- is downgraded to ``contract="opaque"`` with a mere
warning log, instead of failing loud at save time.

This script ISOLATES all I/O to a tempdir, builds synthetic in-memory graph
nodes, and ASSERTS on the specific wrong VALUE returned (``contract="opaque"``)
rather than merely "something happened".

Run:
  uv run python review/02-findings/repro/codegen__contract-emit-swallows-non-infra-bugs-to-opaque.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# Sandbox: pin the project root to a throwaway tempdir so nothing this script
# does can touch the real project tree. (Belt-and-braces: this repro never
# performs disk I/O anyway.)
import haute._sandbox as _sandbox

_TMP = Path(tempfile.mkdtemp(prefix="repro_contract_opaque_"))
_sandbox.set_project_root(_TMP)

from haute import codegen
from haute._contracts import OPAQUE_CONTRACT_SENTINEL
from haute._types import GraphNode
from haute.errors import ConfigError, ContractMismatchError, HauteError


def make_banding_node(factors: object) -> GraphNode:
    """Build a synthetic BANDING node with the given ``factors`` config."""
    return GraphNode.model_validate(
        {
            "id": "band-1",
            "data": {
                "label": "MyBanding",
                "nodeType": "banding",
                "config": {"factors": factors},
            },
        }
    )


OPAQUE_KWARG = f'contract="{OPAQUE_CONTRACT_SENTINEL}"'

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str) -> None:
    results.append((name, passed, detail))
    status = "BUG-REPRODUCED" if passed else "not-reproduced"
    print(f"[{status}] {name}: {detail}")


# A well-formed node so the *only* reason a contract computation fails is the
# injected defect, not malformed input.
good_node = make_banding_node(
    [{"column": "age", "outputColumn": "age_band"}]
)

# Sanity baseline: with no injected fault, a real concrete contract is emitted
# (NOT opaque). This proves the node itself is healthy.
baseline = codegen._format_contract_kwarg(good_node)
assert baseline is not None and "opaque" not in baseline, (
    f"baseline expected concrete contract, got {baseline!r}"
)
print(f"[baseline] healthy node emits concrete contract: {baseline}")
print("-" * 78)


# ---------------------------------------------------------------------------
# Scenario 1: a genuine TypeError (code defect) is swallowed to opaque.
# ---------------------------------------------------------------------------
_orig = codegen.get_column_contract


def _raise_typeerror(node_type, config):  # noqa: ANN001, ARG001
    raise TypeError("genuine contract-computation bug: 'int' object is not subscriptable")


codegen.get_column_contract = _raise_typeerror
try:
    out = codegen._format_contract_kwarg(good_node)
finally:
    codegen.get_column_contract = _orig

# The claim predicts: NO raise; returns the opaque sentinel kwarg.
reproduced_1 = out == OPAQUE_KWARG
record(
    "TypeError downgraded to opaque",
    reproduced_1,
    f"expected a raised TypeError; instead got return value {out!r}",
)


# ---------------------------------------------------------------------------
# Scenario 2: a domain ContractMismatchError (drift) is swallowed to opaque.
# ContractMismatchError is a HauteError but NOT a ConfigError, so it is caught
# by the broad except and downgraded -- a wrong-but-plausible contract drift is
# never surfaced at save time.
# ---------------------------------------------------------------------------
assert issubclass(ContractMismatchError, HauteError)
assert not issubclass(ContractMismatchError, ConfigError), (
    "if ContractMismatchError were a ConfigError the broad catch would re-raise it"
)


def _raise_mismatch(node_type, config):  # noqa: ANN001, ARG001
    raise ContractMismatchError(
        "declared contract disagrees with derived columns",
        node="band-1",
        missing=["age_band"],
    )


codegen.get_column_contract = _raise_mismatch
try:
    out2 = codegen._format_contract_kwarg(good_node)
finally:
    codegen.get_column_contract = _orig

reproduced_2 = out2 == OPAQUE_KWARG
record(
    "ContractMismatchError downgraded to opaque",
    reproduced_2,
    f"expected the mismatch to propagate; instead got return value {out2!r}",
)


# ---------------------------------------------------------------------------
# Scenario 3 (control): a ConfigError DOES propagate. This proves the catch is
# *asymmetric* -- only ConfigError is honoured as fail-loud; every other class
# is silently downgraded. If this control did NOT raise, the asymmetry claim
# would be false.
# ---------------------------------------------------------------------------
def _raise_configerror(node_type, config):  # noqa: ANN001, ARG001
    raise ConfigError("misconfigured node", missing_field="x")


codegen.get_column_contract = _raise_configerror
config_error_propagated = False
try:
    codegen._format_contract_kwarg(good_node)
except ConfigError:
    config_error_propagated = True
finally:
    codegen.get_column_contract = _orig

record(
    "ConfigError correctly propagates (asymmetry control)",
    config_error_propagated,
    "ConfigError raised as expected" if config_error_propagated
    else "ConfigError was unexpectedly swallowed",
)


# ---------------------------------------------------------------------------
# Scenario 4: REALISTIC, no monkeypatch. A malformed banding factor (a bare
# string where a dict is expected) makes the *real* registered
# ``_banding_columns`` raise AttributeError ('str' object has no attribute
# 'get'). This is a genuine data/code defect that is NOT a ConfigError, and it
# is swallowed to opaque by the production code path -- no patching involved.
# ---------------------------------------------------------------------------
malformed_node = make_banding_node(["age"])  # string, not a dict

# First confirm the real contract fn genuinely raises AttributeError on this
# input (i.e. the swallow is hiding a true defect, not a no-op).
real_raises_attribute_error = False
try:
    codegen.get_column_contract(malformed_node.data.nodeType, malformed_node.data.config)
except AttributeError:
    real_raises_attribute_error = True
except Exception as exc:  # pragma: no cover - diagnostic
    print(f"[diag] real _banding_columns raised {type(exc).__name__}: {exc}")

out4 = codegen._format_contract_kwarg(malformed_node)
reproduced_4 = real_raises_attribute_error and out4 == OPAQUE_KWARG
record(
    "Real malformed banding factor (AttributeError) downgraded to opaque",
    reproduced_4,
    f"real fn raised AttributeError={real_raises_attribute_error}; "
    f"_format_contract_kwarg returned {out4!r}",
)


print("-" * 78)
core_reproduced = reproduced_1 and reproduced_2 and config_error_propagated
print(
    "CLAIM VERDICT:",
    "REPRODUCED" if core_reproduced else "NOT REPRODUCED",
)
print(
    "  Non-ConfigError exceptions (TypeError, ContractMismatchError, real "
    "AttributeError) are\n  downgraded to contract=\"opaque\"; only ConfigError "
    "fails loud."
)

# Hard assertions so a non-zero exit signals a problem with the repro harness.
assert reproduced_1, "expected TypeError to be swallowed to opaque"
assert reproduced_2, "expected ContractMismatchError to be swallowed to opaque"
assert config_error_propagated, "expected ConfigError to propagate"
assert reproduced_4, "expected real AttributeError to be swallowed to opaque"
