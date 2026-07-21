# API Input UI issue notes

**Status:** Resolved — implemented in v0.5.0 (branch `api-input`, ships the v0.4.1
presentation foundation and the input-identity convergence as one release). The v0.4.1 work
(`docs/trip/plans/F_0.4.1_api-input-frame-identity.plan.md`) addressed issues 1–4 visually;
field-testing then exposed the underlying display-vs-executable naming split (panel listed
`quotes`, generated code bound `Quote_Input_1`/`Quote_Input_1_2`, plus a latent edge-reorder
silent-rebind hazard). Ralph ruled full convergence — a node's listed inputs ARE its argument
names, 1:1, no hidden names — implemented via
`docs/trip/plans/F_0.5.0_input-identity-convergence.plan.md`: one shared derivation
(`edge_input_name` / `edgeInputName`) feeds codegen, the executor, projection, deploy, and
every panel surface; frame labels are validated as ASCII Python identifiers (invariant B4);
labelled handles exist from one frame up; duplicate input names fail loudly at drag, save,
and run time; and frame renames migrate edges, `input_scenario_map` keys, and instance
`inputMapping` entries in one undoable commit. The issue bodies below predate the fix and
refer to since-retired helpers (`apiInputEmitPortLabels`, `varName`/`displayLabel`) — they
are retained as the historical record. New observations can continue to be collected here.

**Current as of:** 2026-07-21

## Purpose

This is a lightweight holding list for user-facing API Input issues observed while
working with the node. It captures the symptom and the outcome we want without
committing to an implementation. The issues can be investigated, specified, and
scheduled in a later session.

## Issues

### Cross-issue layout requirement

Any replacement layout or handle-positioning method must be data-driven rather
than tailored to the two-frame example. It must behave predictably for one, two,
and any supported number of emitted frames. In particular, adding or removing an
emitted frame must keep every remaining name, handle, and edge association
correct without special-case pixel positions.

### 1. Multi-frame connection lines do not align with their output names

**Observed behaviour**

When an API Input emits multiple frames, the expanded canvas node lists their
names on the right side of the node body. In the observed two-frame example the
outputs are `quote_id` and `driver_claims`, but the two connection lines leave
the node at heights that do not line up with those names. The upper connection
appears to leave near the header/body boundary, and the lower connection appears
closer to the first name than the second. It is therefore difficult to tell which
line belongs to which emitted frame.

This is especially confusing when both outputs connect to the same downstream
node: line direction cannot compensate for the missing visual association.

**Likely reason / investigation starting point**

The output names and output handles share the same ordered label source, so this
does not initially look like a frame-order or persisted-handle identity problem.
The likely issue is that they use different layout coordinate systems:

- `PipelineNode` renders the names as a compact vertical list inside the node
  body.
- `_SourceHandles` independently spaces the handles at percentages of the full
  node height, including the header.

The two layouts can consequently have the same ordering while still having
different vertical positions. Changes to body height, status content, trace
content, label wrapping, or the number of emitted frames may make the mismatch
more obvious.

Relevant starting points:

- `frontend/src/nodes/PipelineNode.tsx` — `_SourceHandles`, `showBodyLabels`, and
  the emitted-label list in the full-detail node body.
- `frontend/src/utils/apiInputPorts.ts` — the shared derivation of eligible
  emitted-frame labels. This identity logic should remain authoritative.
- `frontend/src/__tests__/nodes/ApiInputHandles.test.tsx` — current handle count,
  ID, and label coverage.
- `frontend/src/nodes/__tests__/PipelineNode.test.tsx` — general node/handle
  rendering coverage.

**Desired outcome**

Each visible emitted-frame name should have one clearly associated output dot on
the same visual row, and every outgoing line should begin at that dot. A user
should be able to identify the source frame of an edge without selecting the
edge, opening the editor, or relying on line order.

**Things to preserve**

- The raw frame label remains the React Flow handle ID and runtime source port.
- Existing saved edges remain connected to the same frames.
- Blank, duplicate, or otherwise invalid labels do not gain synthetic handles.
- Single-frame API Inputs retain their default single-handle connection
  semantics, while issue 2 makes the emitted frame's name visible.
- Re-alignment does not regress node zoom/detail modes or ordinary non-API nodes.

**Suggested acceptance coverage for the later implementation**

- Exercise two, three, four, and a larger representative number of emitted
  frames and verify label-to-handle ordering and visual alignment. The layout
  method must derive positions from the rendered frame rows rather than contain
  a separate arrangement for each frame count.
- Cover different label lengths and node-body height changes, including status,
  warnings, and trace content where those can affect layout.
- Verify edges still carry the correct `sourceHandle` after render, save/reload,
  and an in-place frame rename.
- Add a browser-level or visual assertion for the geometry. DOM-only component
  tests that check handle count and IDs cannot prove that the lines visually
  align with their labels.

### 2. A single emitted frame does not show its output name

**Observed behaviour**

When two or more frames are emitted, their names appear in grey on the right of
the expanded API Input node. When exactly one frame is emitted, the node keeps a
single output dot but does not show that frame's name. The canvas therefore hides
useful information that is visible as soon as a second frame is enabled.

**Likely reason / investigation starting point**

This is explicit in the current canvas rendering contract rather than a styling
failure. `PipelineNode` only enables its body-label list for a multi-frame API
Input. In addition, `apiInputEmitPortLabels` deliberately returns an empty list
for zero or one eligible frame because that helper currently signals when named
multi-port handles are required. As a result, the render path has no singleton
label to display even though the table config still has one.

The investigation should separate two concepts that are currently represented
by the same derived list:

- which eligible frame names should be visible to the user; and
- whether React Flow needs named multi-port handles or the legacy single default
  handle.

Showing the singleton name should not require changing the persisted/default
handle identity unless a later design decision explicitly calls for that.

Relevant starting points:

- `frontend/src/nodes/PipelineNode.tsx` — `emitTableLabels`,
  `showBodyLabels`, `_SourceHandles`, and the full-detail body.
- `frontend/src/utils/apiInputPorts.ts` — `apiInputEmitPortLabels` and its
  zero/one-frame fallback contract.
- `frontend/src/__tests__/nodes/ApiInputHandles.test.tsx` — tests that currently
  expect the default single handle for a single emitted frame.

**Desired outcome**

When exactly one valid frame is emitted, its raw name is shown in the same grey
style used for multi-frame output names and is visually associated with the
single output dot. The node should present output identity consistently whether
one frame or several frames are enabled.

**Things to preserve**

- A single emitted frame continues to use the existing default single-output
  handle unless handle semantics are deliberately redesigned separately.
- No name is fabricated when there are no eligible emitted frames.
- Invalid or duplicate persisted labels do not produce synthetic display names
  or handles.
- Moving between zero, one, and multiple emitted frames does not orphan or
  silently rebind existing edges.

**Suggested acceptance coverage for the later implementation**

- Show the grey name for exactly one emitted frame and align it with the default
  output dot.
- Preserve the correct names and associations while transitioning through
  one → two → many → one emitted frames.
- Cover one emitted table alongside additional non-emitted tables so visibility
  follows runtime eligibility rather than total table count.
- Cover long labels and the relevant node zoom/detail modes, with a browser or
  visual assertion for the final geometry.

### 3. The generic node name dominates the emitted-frame names

**Observed behaviour**

In the expanded API Input node, the body gives a large, bold area to the generic
instance name (for example, `Quote Input 1`). The emitted-frame names are placed
in a small grey column beside it. With several frames, the generic name consumes
space that would be more useful for the outputs, while important frame names are
visually secondary and can be heavily truncated.

The header already identifies the node as `QUOTE IN` and carries the API badge,
so repeating a default instance name in the body provides little additional
information. On the canvas, users primarily need to see which named frames can
be connected downstream.

**Proposed product direction**

When an API Input has at least one eligible emitted frame:

- remove the generic node instance name from the visible body; and
- use the body as a prominent emitted-frame list, with each frame name clearly
  associated with its output handle and connection line.

“Remove” here means suppress the instance name in this canvas presentation. It
must not delete or rewrite the persisted node label: that identity may still be
needed by the editor, selection model, generated code, tracing, diagnostics,
accessibility, and test automation.

The later design should decide the empty state explicitly. If there are no
eligible emitted frames, retaining the instance name or replacing it with a
clear `No emitted frames` state may be more useful than rendering an empty body.

**Relationship to issues 1 and 2**

These three issues should be designed together. A strong candidate is a single
rendered output-row primitive that owns the visible name and corresponding
handle position. That could simultaneously:

- make frame names the primary body content;
- show the same treatment for one or many frames; and
- ensure each connection starts on the row carrying its name.

This is a direction to investigate, not a requirement to use a specific DOM or
CSS implementation. Any chosen method still has to satisfy the cross-issue
one/two/many-frame requirement above.

Relevant starting points:

- `frontend/src/nodes/PipelineNode.tsx` — the full-detail body currently renders
  `nodeData.label` as the dominant left column and emitted labels as a small
  right-aligned column.
- `frontend/src/types/node.ts` and graph persistence — confirm every non-visual
  consumer of the node label before changing presentation.
- Existing canvas accessibility and node tests — confirm that removing visible
  text does not remove the node's accessible name or stable automation identity.

**Desired outcome**

The expanded API Input node reads as a source with named outputs, rather than as
one large generic node name with incidental metadata. Emitted-frame names should
be immediately scannable, less aggressively truncated, and visually stronger
than they are today. Their alignment with handles should make the source of each
edge unambiguous.

**Things to preserve**

- The persisted node label and every non-visual identity derived from it.
- A useful accessible name for the node after the visible instance label is
  suppressed.
- Frame-label handle IDs, edge routing semantics, and save/reload behaviour.
- Clear empty, single-frame, and multi-frame states.
- Sensible node sizing at larger frame counts without covering nearby canvas
  content or making handles overlap.

**Suggested acceptance coverage for the later implementation**

- Prove the generic instance name is visually absent when emitted frames are
  present, while the node retains its accessible and persisted identity.
- Verify one, two, and several emitted-frame names receive the new prominent
  treatment and remain paired with their handles.
- Cover long frame names: truncation, tooltip/full-name access, and node-width or
  wrapping behaviour should be intentional rather than accidental.
- Exercise zero emitted frames and define the body fallback explicitly.
- Add visual/browser evidence at the relevant zoom levels and with status,
  warning, and trace adornments present.

### 4. Downstream inputs show the parent node name instead of the connected frame

**Observed behaviour**

After connecting an emitted API Input frame to another node, the downstream
editor describes its input as `Quote_Input_1`. It does not show the individual
dataframe/frame name selected on the connection. This loses the most important
part of the edge identity: which output of the multi-frame source is actually
feeding the node.

If two frames from the same API Input are connected to one downstream node, the
problem is worse: both can appear to have the same `Quote_Input_1` identity even
though they carry different dataframes.

**Likely reason / investigation starting point**

`NodePanel` currently builds each `InputSource` from the upstream node's label:

- `varName` is the sanitised upstream node label; and
- `sourceLabel` is the upstream node label.

The incoming edge's `sourceHandle` is not included in `InputSource` or consulted
when these names are derived. For a multi-frame API Input, however,
`edge.sourceHandle` is the raw emitted-frame label and is the canonical identity
of the selected dataframe throughout canvas persistence and runtime routing.

There are two adjacent details to audit during implementation:

- `InputSourcesBar` currently keys rendered chips by `varName`, so two edges from
  the same parent node can also produce duplicate display keys.
- `upstreamLabelSignature` tracks the edge ID and source-node label but not the
  source handle. A frame rename/rebind may therefore need an explicit dependency
  so memoised downstream input metadata cannot remain stale.

`OutputEditor` already contains a useful precedent: it uses `sourceHandle` as the
frame identity for multi-frame edges and resolves the sole eligible emitted table
for the null-handle single-frame fallback.

Relevant starting points:

- `frontend/src/panels/NodePanel.tsx` — `upstreamLabelSignature` and the
  `inputSources` derivation.
- `frontend/src/panels/editors/_shared.tsx` — `InputSource` and
  `InputSourcesBar`.
- `frontend/src/panels/editors/OutputEditor.tsx` — existing edge-to-frame name
  resolution for multi-frame and single-frame API Inputs.
- `frontend/src/utils/apiInputPorts.ts` — source-handle/frame identity and
  rename reconciliation.
- `frontend/src/panels/__tests__/NodePanel.test.tsx` and editor tests consuming
  `InputSource`.

**Desired outcome**

A downstream node should identify an API Input connection by the dataframe it
receives:

- a named multi-frame edge displays the edge's emitted-frame label;
- a single-frame fallback displays the sole eligible emitted-frame label even
  though its React Flow `sourceHandle` remains null; and
- ordinary single-output nodes continue to display their upstream node name.

For example, connections from `quote_id` and `driver_claims` should appear as two
distinct inputs with those names, not as two copies of `Quote_Input_1`.

**Display identity versus executable identity**

The implementation must explicitly establish whether `InputSource.varName` is
only a UI/code-hint label or is expected to mirror an executable function
argument. The user-facing label must use the connected frame name, but changing
an executable variable contract accidentally would break existing editor code or
generated pipelines. If those identities differ, `InputSource` should represent
them separately rather than overloading one string for both purposes.

**Things to preserve**

- `edge.sourceHandle` remains the canonical persisted/runtime frame identity for
  named multi-frame edges.
- The null-handle single-frame compatibility path remains valid.
- Existing Polars/editor code and generated function arguments are not silently
  renamed by a presentation-only change.
- Frame renames update downstream presentation and edge binding together.
- Multiple edges from one API Input remain distinct and removable individually.

**Suggested acceptance coverage for the later implementation**

- Connect two different frames from one API Input to one downstream node and
  verify two distinct frame names, edge identities, and remove actions.
- Cover a single emitted frame with a null source handle and verify its real
  frame label is displayed rather than the parent node label.
- Cover ordinary non-API sources to ensure their existing node-name presentation
  remains unchanged.
- Rename an emitted frame in place and verify the canvas edge, downstream input
  label, memoised panel state, save/reload result, and runtime routing all move to
  the new name together.
- Verify duplicate-looking labels cannot create React key collisions or make the
  wrong edge get removed.

## Further issues to capture

Add subsequent observations here before turning this list into an implementation
plan. Keep each issue focused on the user-visible problem, reproduction context,
desired outcome, invariants, and the evidence needed to close it.
