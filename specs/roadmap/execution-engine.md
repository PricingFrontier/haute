# Execution engine roadmap

## Scope

Owns the canonical eager/lazy/chunked execution boundary, projection planning,
admission and memory bounds, fault vocabulary, metrics, and request-local
cleanup.

## Priorities

No active implementation packages.

## Planned improvements

There are no active execution-engine roadmap packages.

## Delivered outcomes

- `EXEC-01` re-raises `ContractMismatchError` and `SchemaMismatchError`
  through one eager boundary even in swallow mode, preserves node-local
  failures as per-node results, and adapts both mismatch types to the same
  in-situ preview response rather than a generic HTTP 500.
