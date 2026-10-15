# Frontend shared roadmap

## Scope

The browser's shared hooks, stores, helpers and API client. Current
behaviour is specified in
[the frontend-shared specification](../frontend-shared/high-level.md).
Generating the API contract is planned in the
[server API roadmap](server-api.md) (`API-R03`). `FSH-R03` comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md), and `FSH-R04` from the
[15 October 2026 Workbench review](workbench-review-2026-10-15.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FSH-R03 | Planned | P3 | The results store holds results; validation and optimiser logic move out. |
| FSH-R04 | Planned | P3 | Confirmations, labelled rows, numeric fields, quiet actions, collapsible lists, moves by Alt+arrows and the toolbar's labelled buttons each come from one shared control. |

## Planned improvements

### FSH-R03 — The results store holds results
**Why:** `useNodeResultsStore.ts` combines the store with four recency
caches, module-level derived-preview caches and optimiser logic that
applies the server's frontier-point summaries and converts select
responses into them.

**Plan:** Move the remaining validation to the generated parsers
(`API-R03`), the optimiser logic into an optimiser module and the derived
caches into selectors, leaving the store with state and actions.

**Acceptance:** The store module contains no response validation or domain
derivation; its tests cover state transitions only.

**Dependencies:** `API-R03` (server API).

**Evidence:** `frontend/src/stores/useNodeResultsStore.ts::applyFrontierPointSummary`;
`frontend/src/stores/useNodeResultsStore.ts::hashConfig`.

### FSH-R04 — One kit for the controls the panels repeat
**Why:** The [Workbench review](workbench-review-2026-10-15.md) found the same controls built
several ways across the Workbench, the underwriter app and the pipeline editor (MS-2, FB-39,
FB-47):
- **Five confirmation mechanisms:** `window.confirm` (deleting a section or a test quote),
  the schema editor's inline `ConfirmBar`, `DeleteSheetDialog`, the Files panel's dialog,
  and the app's `ask` with `QuestionDialog`. The owner chose an in-page dialog for Delete
  sheet.
- **Repeated panel controls:**
  - `FIELD_CLASS` defined four times, with two paddings;
  - the labelled row built by hand in the properties panel, and as `Labelled` in the chart
    settings;
  - three whole-number validators, one without the safe-integer check;
  - two collapsed-set toggles;
  - four Alt+Up/Down key handlers, one for each list, though each keeps the focus through
    the one `useRefocusAfterMove`;
  - the quiet "+ Add" action in six places, beside `AddButton`;
  - `Section` beside `ConfigSection`;
  - six Workbench files importing `INPUT_STYLE` from the pipeline editors' private
    `_shared` module.
- **The toolbar:**
  - Six of the pipeline toolbar's labelled buttons, and two in `ProjectControls`, spell the
    toolbar action's style in Tailwind beside the kit's `ToolbarButton`, so a change to the
    kit moves half a toolbar.
  - `PanelHeader.tsx` is a re-export nothing imports.
  - The toast's `slide-in` keyframe repeats the side panel's.

**Plan:** In `frontend/src/components/form` and the kit:
- One in-page question, the app's `ask` generalised, for every destructive action.
- `LabelledRow`, `SelectRow`, `QuietAction`, `wholePixels`, `useCollapsedSet`, and one
  Alt+arrows key handler beside `useRefocusAfterMove`.
- The field class and input style exported from the form kit.
- Every labelled toolbar button through `ToolbarButton`, in a `ToolbarColumn`.
- The shim deleted, and one keyframe kept.

**Acceptance:**
- No `window.confirm` remains.
- Each control has one definition, and the panels import it.
- The toolbar's buttons share one style rule.
- Behaviour is unchanged, and the affected tests pass.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/sheetInteractions.ts::deleteSection` (a native
confirm); `frontend/src/workbench/SchemaEditor.tsx::ConfirmBar`;
`frontend/src/app/useAppStore.ts::confirm` (the app's own question);
`frontend/src/components/Toolbar.tsx` (buttons outside the kit);
`frontend/src/panels/PanelHeader.tsx` (the unused re-export);
`frontend/src/haute-ui/ToolbarControls.tsx::ToolbarButton`.
