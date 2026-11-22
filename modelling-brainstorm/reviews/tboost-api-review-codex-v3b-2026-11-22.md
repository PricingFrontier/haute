The revised design addresses all four P1 findings and the P2 serialization issue. **No implementation-blocking findings remain within the requested scope.**

| Previous finding | Re-check |
|---|---|
| Operation/document consistency | Addressed: both loaders compare `Prune.path` with the inference document, and chain loading validates cached evidence before checking the keep-set. |
| Path-policy propagation | Addressed: pruning preserves `selection.path`; banding and smoothing preserve their base operation; Haute uses the configured selection on every fit. |
| Transitive identity migration | Addressed: descendant revisions, evidence digests, and family IDs are covered by the documented exception. Derived pruning evidence stays outside the fit digest. |
| Haute configuration and goldens | Addressed: draft controls and applied recipes have distinct ownership; frontend, diagnostics, and deliberate structural golden changes are included. |
| `StageInfo` plain data | Addressed: its complete representation is separate from the fixed point-metadata encoding used by `path_id`. |
| Non-staged parameters | Addressed: ordinary fits retain `prune_main_effects`; staged configurations already reject it in Parameters. |

Persisting the [whole evidence cache](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:50) also resolves the restart/recomputation concern for prepared paths whose parent is full.

Two nonblocking implementation clarifications follow from making that cache durable:

- Serialize entries in a canonical settings order, reject duplicate settings entries, and preserve unavailable outcomes. Equivalent cache contents should not produce different checkpoint bytes merely because queries occurred in a different order.
- Clarify [recipe atomicity](/home/ralph/suite/haute/modelling-brainstorm/tboost-api-design-2026-11-22.md:219): a failed recipe may warm the shared derived cache while leaving stages, recipes, and identities unchanged. Consequently, later checkpoint bytes can change. The phrase “all original state remains” currently promises more than that.

Read-only review; no files modified or tests run this round.

APPROVED