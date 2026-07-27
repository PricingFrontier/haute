# Execution engine roadmap

## Scope

Owns the canonical eager/lazy/chunked execution boundary, projection planning,
admission and memory bounds, fault vocabulary, metrics, and request-local
cleanup.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| `EXEC-01` | Queued | P2 | Give eager preview one explicit schema/contract mismatch boundary. |

## Planned improvements

### EXEC-01 — Symmetric eager mismatch propagation

**Why:** Eager preview explicitly re-raises `ContractMismatchError`, but an
equivalent `SchemaMismatchError` follows the generic node-error path. Callers
therefore cannot rely on one run-level mismatch contract.

**Plan:** Route both public mismatch types through the same explicit eager
boundary and preserve node error status for genuinely node-local failures.

**Acceptance:** Focused tests prove both mismatch types propagate with their
typed details in eager preview while unrelated node exceptions retain the
existing per-node error result.

**Dependencies:** Pipeline configuration owns declared column contracts.

**Evidence:** `src/haute/_execute_lazy.py`,
`tests/test_execute_lazy_contracts.py`, and
`tests/test_execute_lazy_contract_coverage.py`.
