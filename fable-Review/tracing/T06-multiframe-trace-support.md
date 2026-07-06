# T06 — Any trace through a multi-frame apiInput crashes with an opaque 500

**Severity:** HIGH (verified; escalated from MEDIUM by adversarial verification) · **Effort:** M
**Dev/reviewer pair: REQUIRED** (crash in a mainstream flow; the fix touches correlation semantics)
**Files:** `src/haute/trace.py`, `src/haute/_trace_correlation.py`, `src/haute/routes/pipeline.py`
(error mapping), tests
**Origin:** CORE-08 (backend-core review), CONFIRMED + broadened by the verification pass.
**Repros:** `repros/verify_core08.py`, `repros/verify_core08_real.py` (cold `execute_trace` over a
real per-port parquet cache — no mocks)

## The defect (verified, three crash sites)

A v2 apiInput with ≥2 emitting `tables[]` (the standard nested-JSON shape: policy root +
`drivers[]`) materialises as `dict[label, DataFrame]` in `eager_outputs`
(`_execute_lazy.py:1920`). The trace assumes `pl.DataFrame` everywhere:

| Case | Site | Result |
|---|---|---|
| Target = the multi-frame node, no `row_values` | `_trace_correlation.py:691` `target_df.row(...)` | `AttributeError: 'dict' object has no attribute 'row'` |
| Target = the multi-frame node, with `row_values` | `trace.py:475` row-verify block | same AttributeError |
| **Target = any downstream consumer** | `_trace_correlation.py:741` `set(parent_df.columns)` during the backward walk | `AttributeError: 'dict' object has no attribute 'columns'` |

The third case is the killer: the multi-frame node is a *source*, so **every trace in such a
pipeline** walks through it and 500s (`routes/pipeline.py:494-496` generic handler → "check the
server logs"). Note the bounds guards do not save it — `len(dict)` counts *frames*, so
`row_index < len(target_df)` passes and the crash lands one line later; there is no wrong-data
path, the failure is deterministic.

**Why users hit it:** preview/trace asymmetry. Preview materialises target-only
(`materialize_node_ids={target}`), leaving the multi-frame ancestor lazy — the downstream node
previews fine and renders clickable cells. The trace materialises everything
(`materialize_node_ids=None`, `trace.py:759-767`) and crashes. A node that previews perfectly
500s on click.

## Fix design (per-port routing, not skip-the-node)

Skipping dict-valued nodes (treating them as failed) would stop the crash but sever lineage at the
pipeline's source — unacceptable for the explainability feature. Route each consumer to the port
frame it actually consumes:

1. **Resolve the port per edge.** The consumer edge's `sourceHandle` carries the port label (the
   same mechanism preview uses to select a frame). Build a `(child_id, parent_id) → port_label`
   map from `graph.edges` during `_prepare_graph`/`execute_trace` setup.
2. **Correlation walk** (`_correlate_rows_posthoc`): when `eager_outputs[nid]` is a dict, select
   `frame = outputs[port_label]` for the resolved child edge before `:741`'s column projection and
   the fast-path/positional logic. The step's `output_values` become that port frame's correlated
   row; record the port label in the step payload (e.g. `node_detail: {"port": label}`) so the UI
   can say which table the row came from.
3. **Target-node case** (`trace.py:472`, `_trace_correlation.py:684-691`): a bare multi-frame node
   has no single row space. The frontend's `DataPreview` shows one selected port at a time — thread
   that `port_label` through `TraceRequest` (optional field, default None) and select the frame;
   when absent and the target is multi-frame, raise a specific
   `ValueError("Node '<id>' produces multiple tables — trace a specific table or a downstream
   node")` mapped to 400/422 at the route (never the generic 500).
4. **Regression net for the asymmetry class:** add a test asserting that every node type that can
   be previewed can also be traced (or fails with a mapped 4xx, never AttributeError) — parametrise
   over the golden graphs including a multi-frame apiInput. The verifier flagged this
   preview-vs-trace materialisation divergence as a latent *class*; this test is the tripwire for
   the next instance.

## Failing tests first

1. `execute_trace` targeting a downstream consumer of a 2-table apiInput (crib the per-port cache
   setup from `repros/verify_core08_real.py`): currently `AttributeError` — assert a complete trace
   whose apiInput step carries the correct port's row + port label.
2. Same, targeting the multi-frame node without a port → assert the specific `ValueError`
   (→ 400/422 route test), not `AttributeError`/500.
3. Same, targeting it *with* `port_label` → trace anchored in that table.
4. Route-level: the three cases through `POST /pipeline/trace` return 200/200/4xx — no 500s.

## Acceptance

- No `AttributeError` reachable from any graph containing multi-frame nodes; lineage flows through
  the correct port frame; the port is visible in the step payload.
- The preview-parity parametrised test is green and stays in the suite.
- `TraceRequest.port_label` (or equivalent) documented in `schemas.py` + mirrored in
  `types/trace.ts`/`guards.ts` if the frontend sends it (frontend change is optional for this
  package — the 4xx message alone removes the silent 500).
