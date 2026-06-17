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
| **Input selector** | one-or-many incoming sources | same row grammar; the "incoming name" is the upstream node/var, "rename" is the local binding name; reorder governs argument order where it matters |
| **Multi-frame input bundle** (apiInput / wrapper single input connector) | structure of frames | one selector per frame in the bundle; mirrors the multi-frame data model (`notes-haute/_DATA_MODEL.md`) |

The input and output variants share: the table chrome, the drag-reorder, the
read-only-vs-editable cell split, the empty-means-all rule, and the
ghost-row/blank-name handling. They diverge only in which identity each cell
binds to.

### 1.4 Where it applies (migration inventory)

Build the shared component, then migrate (each its own commit):

- `components/ColumnTable.tsx` → evolve into the selector (it already backs
  OutputEditor, ColumnsTab, SchemaPreview; checkbox + name + type exist).
- `panels/editors/ColumnsTab.tsx`, `GroupedColumnsTab.tsx`
- `panels/editors/OutputEditor.tsx`
- `panels/editors/ApiInputEditor.tsx` (per-table column rows: select + rename already exist — converge them onto the shared row)
- output-column surfaces in `ModelScoreEditor`, `BandingEditor`, `RatingStepEditor`, `SinkEditor`
- input-source lists in `panels/editors/_shared.tsx` and its consumers (the input mirror)
- the **submodel/wrapper** surfaces in §2

A test should assert these surfaces render the shared selector rather than a
bespoke table, so new editors can't drift back to one-offs.

---

## 2. Submodel (wrapper) input/output surface

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
