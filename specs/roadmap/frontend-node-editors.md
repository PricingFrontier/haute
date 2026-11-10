# Frontend node editors roadmap

## Scope

The node editors' panels, as [the frontend node editors
specification](../frontend-node-editors/high-level.md) describes them: which
answer a picker shows when two of its requests overlap. The package below
comes from the review plan's browser sweep of 10 November 2026 and was
reproduced in a real browser, with the two answers held and released in the
reverse order.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEN-01 | Planned | P3 | The Databricks schema list is the chosen catalog's, whichever answer arrives last. |

## Planned improvements

### FEN-01 — A late answer does not replace the chosen catalog's schemas
**Why:** Reproduced on 10 November 2026 in a browser. In a Databricks Data
Input, the analyst picks one catalog and then another before the first list
of schemas has arrived. When the first catalog's answer comes last, it
replaces the list: the Catalog box shows the second catalog and the Schema
box offers no schema at all, with nothing said, as if the catalog were
empty. Clicking into the Schema box asks again and recovers. The request has
no guard, while the table picker in the same file keeps "only the answer to
the latest ask". A failed older request would show its error against the
newer catalog by the same path (read, not run). The overlap needs only that
the first list is slower than the second.

**Plan:** Give the catalog and schema requests the guard the table picker
has: an answer for anything but the current choice is dropped.

**Acceptance:** With the answers for two catalogs delivered in either order,
the Schema box lists the chosen catalog's schemas, and an older failure
shows no error against the newer choice.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/_DatabricksSelector.tsx`.
