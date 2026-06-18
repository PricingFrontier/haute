# Frontend design principles

Durable UI conventions for the haute editor. Each principle is a contract: new
code conforms, and existing code migrates toward it. When something here is
violated for a good reason, say so at the call site.

---

## 1. Unified data-schema selectors (input & output mirror each other)

**Principle.** Anywhere the user chooses, orders, or renames the columns of a
frame — for an input *or* an output, on *any* node type — it is the same
component family with the same row grammar. Input selectors and output
selectors **mirror each other's design as closely as the schema shape allows**;
they differ only where the data genuinely differs, never gratuitously. No
node gets a bespoke one-off column UI.

This generalises the existing `selected_columns` / `ColumnsTab` surface (which
already drops deselected columns via Polars `.select()`) into one reusable
selector used everywhere.

### 1.1 The column-row grammar

Each selectable column is one row. Left → right, the cells are:

| # | Cell | Editable | Reorders with the row | Purpose |
|---|------|----------|----------------------|---------|
| 1 | **select** | toggle | — | tick to keep the column; untick to drop it |
| 2 | **incoming order** | read-only | no (shows original) | the position the column arrived in (1-based), so after you reorder you can still see where it came from |
| 3 | **rename** | text | yes | pre-populated with the fed-forward column name; edit to rename the column on the way out |
| 4 | **incoming name** | read-only | yes | the original upstream column name (the identity the rename maps *from*) |
| 5 | **type** | read-only | yes | the column dtype; travels with the row but is not itself reorderable as a field |

The **list itself is drag-reorderable**, and the resulting order *is* the
output order: the selector emits a Polars `.select([...])` of the ticked
columns, **in list order**, with each renamed via `.alias(...)` where its
rename cell differs from its incoming name. Rows only — never changes row count.

`incoming order` is fixed to the input ordering; `rename`/`incoming name`/`type`
move with their row when dragged so a column's identity stays legible.

### 1.2 Semantics

- **Empty selection = keep all** (today's `selected_columns == [] ⇒ all`
  convention is preserved). An explicit ordering/rename still serialises even
  when every column is kept.
- **Rename** only emits an alias when `rename !== incomingName`. Blank rename is
  invalid (mirror the existing blank-name refusal in apiInput; surface inline,
  do not silently drop — see [readV2 render-gate invariant]).
- **Stale columns** (in the saved selection but absent upstream) render as
  flagged ghost rows (as `ColumnsTab` does today) rather than vanishing.
- **Per frame.** A frame gets one selector. Multi-frame surfaces (a bundle, a
  multi-output wrapper) render one selector per frame, stacked, each labelled by
  its frame/port name.

### 1.3 Schematic shapes (what mirrors, what differs)

| Surface | Shape | Notes |
|---|---|---|
| **Output column selection** (OutputEditor, ColumnsTab/GroupedColumnsTab, ModelScore/Banding/RatingStep/Sink outputs) | one frame | the canonical case above |
| **Submodel output port** | one frame | the port *is* a frame; the port **name is the frame name** (see §2) |
| **Input selector** | one-or-many incoming sources | same row grammar; the "From" cell is the upstream node/var, the editable "binding name" is the local parameter name. The binding name is backed by the backend `GraphEdge.inputAlias` (a codegen + parser round-trip), **not** a frontend-only relabel — node inputs are wired positionally via the `connect()` edges, so the alias only renames the emitted parameter. No dtype cell (inputs are whole frames); rename-only today (reorder deferred — argument order is the edge order, a separate backend concern). |
| **Multi-frame input bundle** (apiInput / wrapper single input connector) | structure of frames | one selector per frame in the bundle; mirrors the multi-frame data model (`notes-haute/_DATA_MODEL.md`) |

The input and output variants share: the table chrome, the
read-only-vs-editable cell split, and the uncontrolled commit-on-blur rename
cell (the shared `RenameCell` — its keying discipline is load-bearing). They
diverge where the data genuinely differs: the output selector adds
drag-reorder, the empty-means-all keep-list, dtype, and stale ghost rows; the
input selector has none of these (an input is removed by deleting its edge, not
unticking; it is keyed by the opaque `edgeId` since two upstreams can sanitize
to the same name; argument order is the edge order). Rather than one component
with render slots, they are sibling components sharing the `RenameCell` + their
own framework-free domain models (`columnSelection.ts` / `inputBindingSelection.ts`).

### 1.4 Where it applies (migration status)

**Done.** `ColumnsTab` is the shared "Columns" tab for ~13 node types
(everything not in NodePanel's `NO_COLUMNS_TAB`), so migrating it onto
`ColumnSelector` unified output-column selection for all of them in one move —
they now get rename + drag-reorder + incoming-order, and keep the filter.

**Boundary cases — mirror the §1 row grammar, do NOT force-fit the component**
(different data model and/or another session's live domain):

- **`OutputEditor`** — selects response `fields`, and the multi-frame session is
  actively rewriting it into the per-frame mapper (§1.5); migrating it here
  would collide at merge. It adopts the grammar via that work.
- **`ApiInputEditor` v2 tables** — per-column select+rename live on the v2
  `tables[].columns[]` model, not `selected_columns`/`column_renames`, in the
  delicate readV2 area. A literal share needs a model adapter — deferred; mirror
  the grammar visually when touched.
- **Input-source selectors** (`_shared.tsx` + consumers) — **done** (task #3).
  Landed as `InputBindingSelector`, replacing the chip-bar `InputSourcesBar`
  across its consumers (the 6 direct editors + the shared `PolarsCodePanel`),
  reusing the shared `RenameCell` + the input-domain model
  `inputBindingSelection.ts`. The binding name persists to `GraphEdge.inputAlias`
  and round-trips through backend codegen/parser (it was backend-gated, which is
  why this was a real feature, not a CSS job). Edge-join / live-switch / instance
  inputs are deliberately NOT aliasable (their parameter names are structural).

When a second `selected_columns`-model surface appears, add a guard test that it
renders `ColumnSelector` rather than a bespoke table, so editors can't drift back
to one-offs.

### 1.5 Column source & frame identity (cross-session contract)

The column data a selector binds to is **not** re-derived per surface — it comes
from one backend contract so every frame-aware UI agrees (per the per-frame
OUTPUT-editor work on `worktree-multi-frame`):

- **Per-frame columns:** `PreviewNodeResponse.node_frame_columns[nodeId][frameLabel]`
  → `ColumnInfo[]` (additive alongside `node_columns`). A selector's
  `availableColumns` for a frame is this list. The OUTPUT editor's `frameColumns(edge)`
  is the single derivation point — converge on it rather than reading a producer's
  config client-side.
- **Frame key:** a frame is keyed by `edge.sourceHandle` when set, else
  `sanitize(sourceNodeLabel)` for the single-port case. `sanitize` MUST match the
  backend `_sanitize_func_name` exactly (`utils/sanitizeName.ts` — a
  Unicode-whitespace divergence was recently fixed). A single-frame consumer may
  fall back by position; a true multi-frame consumer requires an exact name match.
- **Multi-frame producers** (apiInput tables, a multi-emit submodel, bundle-aware
  polars/banding) each expose one frame per emitted label; that label doubles as
  the producer's `Handle.id` / `sourceHandle`, so the same key flows editor↔executor.

**Vocabulary:** prose and UI say *frame*; identifiers stay as the graph-boundary
concept (`sourceHandle`, `source_port`, submodel `input_ports`/`output_ports`) —
the port→frame rename is **prose-only** (Nick's ruling).

---

## 2. Submodel input/output surface

Tracks the wrapper-node redesign in `notes-haute/_SUBMODELS.md` §2 (a wrapper is
a *virtual* surface — its frames are produced by the internal nodes; outputs are
a *selected subset* of internal interfaces). The UI contract:

- **An output port is a frame.** Its **name is the port name.**
- **Inputs** are system-driven affordances, induced when an external frame is
  linked to the wrapper (one input connector carrying a structure of frames —
  the multi-frame convention). The user does not place inputs.
- **Outputs are placed deliberately** — a wrapper output is added from the
  sidebar palette and the canvas right-click menu (it has to come from
  somewhere; nothing external creates it).
- **Each output port carries one §1 selector** for its single frame (select /
  reorder / rename / type), so the port's surfaced schema is editable.
- **Each emitted frame's label doubles as its `sourceHandle`** (the existing
  `Handle.id == frame-label` convention, §1.5) and its column schema is exposed
  through `node_frame_columns` keyed by that label — so the OUTPUT editor and any
  frame-aware UI render the right columns per submodel frame for free, with no
  submodel-specific column path.
- **Port name is editable in two mirrored places:** the port node's own
  sidepane (inside the wrapper canvas) **and** the parent wrapper node's
  sidepane, which shows a **frames table** listing the ports and lets the user
  **rename, reorder, and delete** them. A port's component can also be deleted
  from inside the wrapper canvas directly.
- The wrapper sidepane is a hook for **data preview + schema** of each frame.

Open design items (resolve in `_SUBMODELS.md` before the I/O surface lands):
output-port naming independence, boundary-link representation in Peek, the
terminology rename (`submodel` → `wrapper`), nesting depth, and storage layout.

---

*Origin: Nick's directive, 2026-06-17 (single-PR `worktree-ui-improvement`).
Companion notes: `notes-haute/ui-improvements/SUBMODELS_UI_IMPROVEMENTS.md`,
`notes-haute/_SUBMODELS.md`, `notes-haute/_DATA_MODEL.md`.*
