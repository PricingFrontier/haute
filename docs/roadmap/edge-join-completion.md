# Edge Join completion roadmap

**Status:** Implemented

**Current as of:** 2026-07-24

## Outcome

An analyst can insert and configure an Edge Join confidently in the canvas, and can rely on its saved pipeline to preserve the same topology, role bindings, and join semantics through preview, tracing, and deployment.

## Verified baseline

Edge Join is already a first-class `edgeJoin` node rather than edge metadata. The backend validates its two explicit input roles, executes lazy Polars joins, round-trips generated code and graph state, and uses the normal preview, tracing, save/load, and deployment execution paths. The editor already has compact-node metadata, base/join role handles, pure graph-rewrite helpers, configuration UI, save-time validation, and focused coverage for insertion and role preservation. See the public [Edge Join guide](../building-models/nodes/edge-join.md) for the current user contract.

At the planning baseline, edge insertion was detected only when
`onConnectEnd` asked `document.elementsFromPoint(...)` for the released pointer
position, so there was no pre-release candidate state or insertion marker;
`frontend/e2e/` had no Edge Join workflow; and the public guide did not describe
the dynamic role-handle geometry or list the implemented `right` join mode.

The delivered work remained limited to those gaps and did not reopen the
completed backend, graph-rewrite, configuration, or integration work.

## Delivered milestones

### 1. Make edge insertion discoverable and accessible

**Scope:** While a source-connection gesture is over a compatible existing edge, provide clear, transient feedback that an Edge Join can be inserted. Ensure the feedback and resulting marker meet pointer and accessibility expectations, clear immediately when the target is left, and never imply that an incompatible target is valid. The implementation should choose the smallest rendering/hit-testing design that fits the existing React Flow architecture; this roadmap does not prescribe a custom edge component or a visual treatment.

**Tests first:**

- Add focused component or hook tests for entering, moving between, and leaving valid candidate edges, including cleanup on cancel and connection end.
- Cover non-source gestures, stale/incomplete edge endpoints, self/cycle candidates, and non-edge canvas drops: no graph mutation and an actionable existing-style error/toast on rejected releases where appropriate.
- Assert the feedback has an accessible name or equivalent semantic state, is not exposed once invalid, and does not alter ordinary node-to-node connection behavior.
- Retain regression coverage for the existing output-to-output creation path, for source and target handle preservation, and for the edit remaining one undoable operation.

**Acceptance criteria:**

- A compatible target is visibly and programmatically distinguishable before release; a user is never asked to infer insertion from a silent pointer position.
- Leaving, cancelling, or using an invalid target removes the indicator and leaves nodes, edges, selection, and undo history unchanged.
- A successful insertion preserves role-bound edges and selects the new Edge Join without degrading standard connections.

### 2. Exercise the real browser workflow

**Scope:** Add dedicated deterministic Playwright coverage for Edge Join. It must cover creation from an edge, common configuration, execution feedback, persistence, repeated insertion, downstream tracing, and named API-input source-handle preservation. Keep deployment-pruning assurance in backend tests rather than turning this feature workflow into an unrelated platform test.

**Tests first:**

- Build small, stable fixtures with compatible inputs and deterministic joined output, including an API input with an observable named output handle.
- Drive edge insertion through the actual canvas gesture; configure same-name join keys; preview and assert joined columns and rows.
- Save, reload, and assert the compact node, role handles, configuration, and split topology remain intact.
- Insert a second join on the same branch and assert both joins survive save and reload.
- Insert from a named API-input output and prove the selected source handle survives the rewrite and reload.
- Trace a downstream output and assert that the Edge Join is retained as an ancestor and presented as a normal highlighted trace step.

**Acceptance criteria:**

- The test runs in the normal browser suite without relying on timing-sensitive coordinate guesses or production data.
- It fails if insertion, configuration, preview, persistence, repeated joins, named source-handle preservation, or downstream trace behaviour regresses.
- The test asserts observable outcomes rather than private component state.

### 3. Align public and specification documentation

**Scope:** Update user-facing and architecture/specification documentation to describe the implemented interaction precisely: Edge Join can be created by dropping a source connection on an edge or by connecting two outputs; it has a base input on the left, a join input positioned above or below according to the connected source (both candidates are available before connection), and one output on the right; and swapping inputs updates the handles and stored roles together. Document the supported Polars semantics: `inner`, `left`, `right`, `full`, `semi`, `anti`, and `cross`; cross joins do not accept keys, while other joins require either `on` or matching `leftOn`/`rightOn`.

**Tests first:**

- Add or extend documentation accuracy/link checks for the supported join set and the role-handle terminology.
- Verify examples distinguish same-name keys from asymmetric keys, and that cross joins cannot be documented with key fields.
- Check that no retained doc claims a fixed top-only join handle, omits implemented join modes, or describes Edge Join as a palette-created node.

**Acceptance criteria:**

- The public and low-level docs agree with the runtime validation and canvas behavior.
- Every supported join mode and key invariant is discoverable from the documentation without reading source.
- Documentation build/link checks pass and do not introduce duplicate competing descriptions of handle placement.

## Non-goals

- Replacing the two-input, role-bound Edge Join model with generic edge annotations or arbitrary custom join code.
- Adding implicit key inference, best-effort repair of malformed graphs, or fallback selection of input roles.
- Redesigning general canvas connections, node palette behavior, or Polars join semantics as part of this work.
- Generic executor resilience and scale gates, worker-process migration, and graph-wide Polars planning; those are owned by the [backend execution hardening](backend-execution-hardening.md), [worker isolation](worker-isolation.md), and [Polars execution strategy](polars-execution-strategy.md) roadmaps.

## Dependencies and sequencing

Milestone 1 established the interaction contract exercised by Milestone 2.
Milestone 3 followed the settled interaction so its description is
authoritative. The work continued to use the existing graph-rewrite and
validation utilities; no backend contract gap or backend change was required.

Milestone 2 consumes the shared production-shaped fixture and deterministic
browser-harness conventions in the [Frontend UI quality](frontend-ui-quality.md)
roadmap, and its invariant-level regressions should follow the oracle and
fixture policy in [Test-suite hardening](test-suite-hardening.md). Those shared
conventions do not replace this roadmap's mandatory Edge Join-specific browser
workflow: it must still prove insertion, configuration, preview, persistence,
repeated joins, named source handles, and tracing.

## Completion and retirement criteria

Retire this roadmap when the insertion feedback and accessibility tests pass; the dedicated Edge Join browser coverage proves insertion, preview, persistence, repeated joins, named API-input handles, and tracing in the normal E2E suite; and the checked documentation reflects the actual role-handle placement and seven supported join modes. At that point, ongoing behavior belongs in the public guide, component specifications, and ordinary regression suites rather than a separate roadmap.

## Completion evidence

The retirement criteria were met on 2026-07-24:

- The focused Edge Join frontend suite passes candidate, cleanup,
  release-revalidation, insertion, handle-preservation, mode, key, and
  accessibility regressions.
- `frontend/e2e/edge-join.spec.ts` passes in Chromium and proves two real
  edge-drop insertions, pre-release feedback, configuration and preview,
  save/reload topology, the named API-input source handle, and downstream
  trace highlighting.
- The documentation accuracy suite and strict MkDocs build pass. The public
  guide and durable graph-canvas, node-editor, runtime, and
  engineering-quality specifications now own the delivered contract.
- The repository quick preflight passes, including Python lint/format/type
  checks and frontend TypeScript/ESLint checks.

This roadmap is therefore retired from the active roadmap index and
navigation. Its file remains as dated completion context; current behaviour is
defined by the linked guide, specifications, implementation, and ordinary
regression suites.
