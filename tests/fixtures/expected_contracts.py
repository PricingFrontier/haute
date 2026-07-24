"""Target contract API for Item #57 (column-contract adoption everywhere).

This fixture is a **test-first spec**: it encodes the end-state API that the
developer agent should build, not the current implementation.  The test
suite in ``tests/test_column_contracts_adoption.py`` imports from here so
both producer (dev) and consumer (tests) agree on what "done" looks like.

End-state design
================

The column contract lives as an explicit ``columns=`` registration on the
``@_register`` decorator in :mod:`haute._builders`. Every one of the 19
``NodeType`` values declares a contract; genuinely data-dependent builders
use the explicit opaque contract rather than relying on an absent registration.

The proposed end-state is:

1. **Every ``NodeType`` has an explicit contract registration.**  Nodes
   that are genuinely opaque (user code, unknown external schemas) must
   register ``OPAQUE_CONTRACT`` so the system can tell "unknown but
   declared" apart from "forgot to register".

2. **Codegen emits the contract as a decorator kwarg.**  A
   ``@pipeline.polars(...)`` call that comes out of ``graph_to_code``
   includes ``contract=Contract(inputs=[...], outputs=[...])`` (or the
   sentinel ``OPAQUE`` string when either side is ``None``).  This lets a
   human reviewer see what a node is expected to read / produce without
   running the graph.

3. **Parser validates user-declared contracts.**  If the user writes

       @pipeline.banding(
           factors=[{"column": "age", "outputColumn": "age_band", ...}],
           contract=Contract(inputs=["age"], outputs=["age_band"]),
       )

   the parser cross-checks the declared contract against the one the
   builder derives from the config.  A mismatch raises
   :class:`ContractMismatchError` at parse time — not at runtime, not as
   a warning.

4. **Executor asserts contract at node boundaries.**  Before passing a
   LazyFrame into a node's function the executor verifies every column
   in ``contract.inputs`` exists in the upstream schema; after the node
   runs it verifies every column in ``contract.outputs`` is present in
   the result.  Violations raise :class:`ContractMismatchError`.

5. **Benchmarks show <5% overhead.**  A 100-node pipeline with
   contracts fully enforced must not slow execution by more than 5%
   compared to the same graph with contracts disabled.

Allowlist
=========

A handful of builders are *genuinely* opaque and should register
``OPAQUE_CONTRACT`` rather than a concrete set of columns:

- ``API_INPUT`` / ``DATA_INPUT`` — output schema is determined by the
  file on disk; we cannot know the columns without touching I/O.
- ``POLARS`` / ``EXTERNAL_FILE`` — user code can do arbitrary column
  manipulation; the only way to know the contract is to execute.

The difference from today: opacity is now declared (a concrete
``OPAQUE_CONTRACT`` entry in the registry) rather than implicit
(absence from the registry).

Proposed public API
===================

The tests below assume this minimal shape; the dev can refine names
and internals but the behaviour must match.

.. code-block:: python

    from haute._builders import (
        Contract,           # dataclass(frozen=True): inputs, outputs
        OPAQUE_CONTRACT,    # Contract(inputs=None, outputs=None)
        declare_contract,   # decorator for user-side explicit declaration
        get_contract,       # new name for get_column_contract
        validate_contract,  # parser-time cross-check
        assert_contract,    # executor-time boundary check
    )
"""

from __future__ import annotations

from haute._types import NodeType

# ---------------------------------------------------------------------------
# Allowlist — node types that are legitimately opaque.
# ---------------------------------------------------------------------------

#: Builders whose contract is genuinely opaque (user code or external data).
#:
#: These must register ``OPAQUE_CONTRACT`` (not be absent from the
#: registry).  Every other node type must register a concrete contract.
ALLOWED_OPAQUE_NODE_TYPES: frozenset[NodeType] = frozenset(
    {
        NodeType.API_INPUT,  # output schema determined by file
        NodeType.DATA_INPUT,  # output schema determined by the configured source
        NodeType.POLARS,  # arbitrary user code
        NodeType.EXTERNAL_FILE,  # arbitrary user code
        NodeType.EDGE_JOIN,  # output schema depends on both runtime input schemas
    }
)

#: All node types that must declare a contract (opaque or concrete).
#:
#: This is the full enum — the invariant is that *every* kind has a
#: registration entry, not that every kind has a concrete one.
ALL_NODE_KINDS: frozenset[NodeType] = frozenset(NodeType)


# ---------------------------------------------------------------------------
# Codegen round-trip sentinels
# ---------------------------------------------------------------------------

#: String literal that codegen emits when a contract side is ``None``.
#:
#: Example of a banding node with a known contract::
#:
#:     @pipeline.banding(
#:         factors=[...],
#:         contract={"inputs": ["age"], "outputs": ["age_band"]},
#:     )
#:
#: Example of an opaque polars node::
#:
#:     @pipeline.polars(contract="opaque")
OPAQUE_SENTINEL = "opaque"


# ---------------------------------------------------------------------------
# Parser / executor error class
# ---------------------------------------------------------------------------

#: The exception that all contract-violation paths must raise.
#:
#: The dev should add this under :mod:`haute.errors` inheriting from
#: ``HauteError`` so existing ``except HauteError`` handlers catch it
#: naturally.
CONTRACT_MISMATCH_ERROR_NAME = "ContractMismatchError"
