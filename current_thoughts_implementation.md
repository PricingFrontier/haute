# current_thoughts Implementation Plan

> **For agentic workers:** Execute task-by-task with checkbox (`- [ ]`) tracking. Execution routing follows `CLAUDE.md` / `AGENTS.md`: judgment stays in the root thread; a `sonnet-implementer` worker may implement a task after the root supplies it verbatim from this plan; the root inspects every diff. Review of the accumulated change set goes to Codex (`codex-code-review`) — never to Claude subagents.

**Goal:** Implement every item in `current_thoughts.md` (11 UI/UX notes + the 28-Jul decisions): quote_info default label, mlflow-by-default, node typography/status-dot fixes, provider buttons, scan-only parquet, shared path picker, Polars tab, shared cache button, All/None draft semantics, auto-built persistent snapshots, no silent snapshot eviction, and edge-join key auto-population.

**Status (28-Jul-2026): complete and ready for review.** The per-task checkboxes below preserve the
original execution recipe, including intentionally unperformed commit and broad
verification steps. The authoritative targeted verification/build record is in
the checked Completion section.

**Architecture:** Small independent slices on the `manual-tweaks` branch, accumulated into one PR. Frontend work reuses existing shared components (`ToggleButtonGroup`, `CacheFetchButton`, `PolarsCodePanel`, `FileBrowser`); the only new machinery is a frontend snapshot-ensure orchestration ahead of preview execution, preserving the backend invariant that snapshot execution never contacts the provider (`_input_providers.py:229`).

**Tech Stack:** Python 3.11+ / FastAPI / Polars (uv, pytest, ruff, mypy) · React 19 + TypeScript (vitest, @testing-library/react, eslint).

## Global Constraints

- Branch: `manual-tweaks`. Accumulate all tasks on this branch / one PR. **Never merge** — Ralph reviews and merges independently.
- AGENTS.md: behaviour changes are defined in specs before code — each behavioural task starts with its spec edit (specs live under `specs/<component>/`; find the file with the task's grep step).
- AGENTS.md: no speculative or silent fallbacks; unexpected states fail clearly. Preserve unrelated user changes.
- Coverage ratchets must not drop (frontend `package.json` `criticalCoverage`): touching `src/panels/NodePanel.tsx`, `src/hooks/usePipelineAPI.ts`, or `src/api/client.ts` requires accompanying tests. Never lower a gate.
- Verification ladder (AGENTS.md): run the lowest sufficient level while iterating.
  - Backend test: `uv run pytest tests/<file>.py -q` · lint: `uv run ruff check <files>` · types: `uv run mypy src/haute/`
  - Frontend test: `npm --prefix frontend test -- <path>` · `npm --prefix frontend run typecheck` · `npm --prefix frontend run lint`
- Every commit ends with: `-m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"` (second `-m`).
- Existing test-ids and ARIA labels are contracts for the test suite — when a task says "keep test-id X", that is load-bearing.
- Completion gate: after all tasks, run `/codex-code-review` on the full branch diff; resolve or rebut findings before declaring done.

**Recommended order:** 1 → 2 → 3 → 4 → 6 → 14 → 5 → 7 → 8 → 9 → 10 → 11 → 12 → 13. Tasks 5/7/8/9 all edit `DataInputEditor.tsx` — do them in that sequence to avoid conflicts. Task 11 must precede Task 12.

---

### Task 1: Default first-table label `quote_info`

**Files:**
- Modify: `src/haute/_json_shred.py:1965`
- Modify: tests that assert the inferred root label (candidates: `tests/test_v2_codec_and_shred.py`, `tests/test_v2_object_nesting_inference.py`, `tests/test_json_shred_mut_lifecycle.py`, `tests/test_load_v2_api_source.py`, `tests/test_json_cache_routes.py`, `tests/test_json_cache_integrity.py`, `tests/test_json_cache_corrupt_and_errors.py`)
- Modify: the json-shredding spec (find via Step 1)

**Interfaces:**
- Produces: inferred root-level table label `"quote_info"` (was `"root"`). The label remains the frame handle id and generated-code argument name. Persisted pipelines keep their saved labels — this changes inference output only.

- [ ] **Step 1: Update the spec.** `Grep '"root"' specs/` — update the json-shredding spec's statement of the default root-table label to `quote_info`.
- [ ] **Step 2: Write the failing regression test.** In `tests/test_v2_object_nesting_inference.py`, locate an existing inference test that asserts the root table's label and add (or adapt) an explicit assertion:

```python
def test_root_table_default_label_is_quote_info(tmp_path):
    # reuse the module's existing helper that shreds a sample payload
    tables = _infer_tables_for(tmp_path, [{"a": 1, "b": "x"}])  # module's existing fixture/helper
    root = next(t for t in tables if t["path"] == "$[:]")
    assert root["label"] == "quote_info"
    assert root["emit"] is True
```

(Adapt the helper name to the module's existing pattern — do not invent a new fixture stack.)
- [ ] **Step 3: Run it — expect FAIL** (`label == "root"`): `uv run pytest tests/test_v2_object_nesting_inference.py -q`
- [ ] **Step 4: Implement.** `src/haute/_json_shred.py:1965`:

```python
base_label = "quote_info" if not level else derive_identifier_label(level[-1][0])
```

- [ ] **Step 5: Sweep remaining assertions.** `Grep '"root"' tests/` — update only assertions about the *inferred label* (leave unrelated `"root"` strings — e.g. path roots, git — untouched). Run the candidate files listed above.
- [ ] **Step 6: Verify:** `uv run pytest tests/test_v2_codec_and_shred.py tests/test_v2_object_nesting_inference.py tests/test_json_shred_mut_lifecycle.py tests/test_load_v2_api_source.py tests/test_json_cache_routes.py -q` then `uv run ruff check src/haute/_json_shred.py` and `uv run mypy src/haute/`.
- [ ] **Step 7: Commit** `feat: default quote-input root table label to quote_info`

---

### Task 2: mlflow installed by default

**Files:**
- Modify: `pyproject.toml:29-70`
- Modify: build/distribution spec + any docs stating mlflow is databricks-only (find via grep)

- [ ] **Step 1:** `Grep -i "mlflow" specs/ docs/ README.md` — update statements that mlflow ships only with the `databricks` extra.
- [ ] **Step 2: Move the dependency.** In `pyproject.toml` add to `[project] dependencies` (keep alphabetical-ish grouping near other ML deps):

```toml
    # Core: Model Scoring and the MLflow registry are first-class palette
    # features; the SDKs below stay in the databricks extra.
    "mlflow>=3.11.0,<4",
```

and reduce the extra to:

```toml
[project.optional-dependencies]
databricks = [
    "databricks-sdk>=0.88.0,<0.89",
    "databricks-sql-connector>=4.2.5,<5",
]
```

- [ ] **Step 3:** `uv lock` then `uv sync` — confirm resolution succeeds.
- [ ] **Step 4: Verify:** `uv run python -c "import mlflow; print(mlflow.__version__)"` and `uv run pytest tests/test_hatch_build.py -q` (packaging assertions live there).
- [ ] **Step 5: Commit** `feat: install mlflow with core haute`

---

### Task 3: Quote-input frame labels use node-name typography

**Files:**
- Modify: `frontend/src/nodes/PipelineNode.tsx:185-191` (`_ApiInputFrameRows`)
- Test: the existing PipelineNode / node-rendering test suite (locate with `Glob frontend/src/__tests__/nodes/*` — extend, don't duplicate)

- [ ] **Step 1: Failing test.** Add to the node test file:

```tsx
it("renders api-input frame labels with node-name typography", () => {
  // render an apiInput node at full zoom with one emitted frame "quote_info"
  const label = screen.getByTestId("api-input-body-label-quote_info")
  expect(label.className).toContain("font-semibold")
  expect(label.className).toContain("text-[13px]")
})
```

- [ ] **Step 2: Run — expect FAIL:** `npm --prefix frontend test -- src/__tests__/nodes`
- [ ] **Step 3: Implement.** In `_ApiInputFrameRows`, change the span:

```tsx
<span
  data-testid={`api-input-body-label-${label}`}
  className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-pre text-right font-semibold text-[13px] leading-tight"
  style={{ color: "var(--text-primary)", whiteSpace: "pre" }}
  title={label}
>
```

(Was `font-mono text-[11px]` with `var(--text-muted)`.)
- [ ] **Step 4: Verify:** rerun the test file; `npm --prefix frontend run typecheck`.
- [ ] **Step 5: Commit** `fix: match quote-input frame label font to node names`

---

### Task 4: Edge-join status dot inside the marker

**Files:**
- Modify: `frontend/src/nodes/PipelineNode.tsx:381-398`

The container is 40×34; the visible ellipse is 32×22 centred (x∈[4,36], y∈[6,28]). The dots (`size-1.5` = 6px) currently sit at `-right-0.5` — outside the ellipse. Place them inside its right edge.

- [ ] **Step 1: Failing test.** In the node test suite:

```tsx
it("keeps edge-join status dot inside the marker ellipse", () => {
  // render an edgeJoin node with _status "ok"
  const dot = screen.getByTestId("edge-join-status-indicator")
  expect(dot.className).toContain("right-[6px]")
  expect(dot.className).not.toContain("-right-0.5")
})
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Status dot (line 383): `absolute -right-0.5 bottom-1` → `absolute right-[6px] bottom-[8px]`. Warning dot (line 392): `absolute -right-0.5 top-1` → `absolute right-[6px] top-[8px]`. Keep test-ids, roles, and pulse class.
- [ ] **Step 4: Verify:** rerun; visually confirm once via `npm --prefix frontend run dev` if convenient (optional).
- [ ] **Step 5: Commit** `fix: move edge-join status dots inside the marker`

---

### Task 5: Data Input provider as coloured toggle buttons

**Files:**
- Modify: `frontend/src/panels/editors/DataInputEditor.tsx:246-265`
- Test: the DataInputEditor test file (locate via `Glob frontend/src/**/DataInputEditor*`)

**Interfaces:**
- Consumes: `ToggleButtonGroup` (`frontend/src/components/ToggleButtonGroup.tsx`) — props `{value, onChange, options: {key,label,icon?}[], accentColor, ariaLabel?}`; renders `role="radiogroup"`.

- [ ] **Step 1: Failing test.** The provider control becomes a radiogroup:

```tsx
it("renders provider choices as a toggle radiogroup and switches provider", async () => {
  renderDataInputEditor() // module's existing render helper with capabilities mocked
  const group = await screen.findByRole("radiogroup", { name: "Provider" })
  const fileBtn = within(group).getByRole("radio", { name: /file/i })
  await userEvent.click(fileBtn)
  expect(onReplaceConfig).toHaveBeenCalledWith(expect.objectContaining({ inputType: "file" }))
})
```

Update/remove the old `combobox`-based provider assertions in the same file.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Replace the provider `<select>` block with:

```tsx
<div>
  <EditorLabel as="div">Provider</EditorLabel>
  <div className="mt-1">
    <ToggleButtonGroup
      value={group?.name ?? ""}
      onChange={(name) => {
        const next = groups.find((candidate) => candidate.name === name)
        if (next) onReplaceConfig(inputBranchConfig(config, next))
      }}
      options={groups.map((candidate) => ({ key: candidate.name, label: candidate.label }))}
      accentColor={accentColor}
      ariaLabel="Provider"
    />
  </div>
</div>
```

Add `import ToggleButtonGroup from "../../components/ToggleButtonGroup"`. With no provider selected, `value=""` matches no key — all buttons inactive (fine). Keep the existing unknown-provider error section unchanged.
- [ ] **Step 4: Verify:** run the file's tests; `npm --prefix frontend run typecheck && npm --prefix frontend run lint`.
- [ ] **Step 5: Commit** `feat: provider toggle buttons on Data Input`

---

### Task 6: Scan-only input mode when a scanner exists

**Files:**
- Modify: `src/haute/_polars_io_registry.py:907-909`
- Modify: io-layer spec (find via Step 1)
- Test: backend capabilities tests (locate: `Grep "input_modes\|cache_modes" tests/ -l`)

Decision (28 Jul): parquet is always scan. Generalised: a format with a scanner advertises **only** `scan` for input; `read` remains only for reader-only formats. Config validation (`_polars_io_registry.py:543`) still accepts `"read"` so existing configs execute; the editor shows `read (not valid for this format)` and readiness gating flags it — a loud, self-explanatory migration (AGENTS.md fail-clearly).

- [ ] **Step 1: Spec.** `Grep -l "scan" specs/` → io-layer spec: document "input modes: `scan` when a scanner exists, else `read`; stored `read` configs stay executable".
- [ ] **Step 2: Failing test.** In the capabilities test module:

```python
def test_scanner_formats_advertise_scan_only():
    caps = io_capabilities()  # module's existing accessor
    parquet = _input_format(caps, "file", "parquet")  # existing helper pattern
    assert parquet["input"]["modes"] == ["scan"]

def test_reader_only_formats_keep_read():
    caps = io_capabilities()
    # Inspect FORMATS in _polars_io_registry.py for an entry with a reader and
    # no scanner (likely candidates: json or excel) and pin it here.
    fmt = _input_format(caps, "file", "json")
    assert fmt["input"]["modes"] == ["read"]
```

- [ ] **Step 3: Run — expect FAIL** (parquet currently `["scan", "read"]`).
- [ ] **Step 4: Implement.**

```python
input_modes = (
    ["scan"] if fmt.scanner else (["read"] if fmt.reader else [])
)
```

- [ ] **Step 5:** Fix other capability tests asserting two modes. Confirm `validate` at line 543 still accepts `"read"` (no change there) and the executor read path is untouched.
- [ ] **Step 6: Verify:** affected test modules, `uv run mypy src/haute/`, and the frontend needs **no change** (`_IoFormatEditor.tsx:387` hides the Mode row when one mode and no explicit override; explicit `read` shows the invalid marker at line 545-550).
- [ ] **Step 7: Commit** `feat: advertise scan-only input mode for scanner-capable formats`

---

### Task 7: Shared path picker (Quote Input pattern everywhere)

**Files:**
- Create: `frontend/src/panels/editors/shared/PathPickerField.tsx`
- Modify: `frontend/src/panels/editors/ApiInputEditor.tsx:714-767`
- Modify: `frontend/src/panels/editors/ExternalFileEditor.tsx:64-73`
- Modify: `frontend/src/panels/editors/_IoFormatEditor.tsx:578-623`
- Test: `frontend/src/__tests__/editors/ExternalFileEditor.test.tsx` + new `frontend/src/panels/editors/shared/__tests__/PathPickerField.test.tsx`

**Interfaces:**

Correction (28 Jul): the shared pill is the sole selected-path summary while
expanded; embedded `FileBrowser` suppresses its duplicate summary. Data Input
paths are browser-only, matching Quote Input. Manual entry is enabled only for
Data Output destinations, where the target filename may not exist yet.

- Produces: `PathPickerField({label, sublabel?, value, onSelect, extensions?, manualEntry?, testIdPrefix?})` — collapsed green pill (mono path + `change`/`close`, test-id `file-change-btn` preserved) expanding to `FileBrowser`, with optional `CommittedTextField` manual-entry row.
- Consumes: `FileBrowser` from `./_shared` (`{currentPath?, onSelect, extensions?}`), `CommittedTextField` from `components/form`.

- [ ] **Step 1: Write the component** (this is the ApiInputEditor pill block, extracted verbatim and parameterised):

```tsx
import { useState } from "react"
import { Check } from "lucide-react"
import { CommittedTextField } from "../../../components/form"
import { FileBrowser } from "../_shared"

export default function PathPickerField({
  label,
  sublabel,
  value,
  onSelect,
  extensions,
  manualEntry = false,
  testIdPrefix = "path-picker",
}: {
  label: string
  sublabel?: string
  value: string
  onSelect: (path: string) => void
  extensions?: string
  /** Show a committed text field above the browser for hand-typed paths. */
  manualEntry?: boolean
  testIdPrefix?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const showBrowser = !value || expanded
  return (
    <div data-testid={testIdPrefix}>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em] mb-1.5 block" style={{ color: "var(--text-muted)" }}>
        {label}
        {sublabel && <span className="ml-1.5 normal-case tracking-normal font-normal">{sublabel}</span>}
      </label>
      {value && (
        <div
          className="px-2.5 py-2 rounded-lg flex items-center gap-2"
          style={{ background: "var(--success-soft)", border: "1px solid var(--success-border)" }}
        >
          <Check size={14} style={{ color: "var(--success)" }} className="shrink-0" />
          <span className="text-xs font-mono truncate flex-1" style={{ color: "var(--success-hover)" }}>
            {value}
          </span>
          <button
            data-testid="file-change-btn"
            onClick={() => setExpanded(!expanded)}
            className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded transition-colors"
            style={{ color: "var(--success-hover)" }}
          >
            {expanded ? "close" : "change"}
          </button>
        </div>
      )}
      {showBrowser && (
        <div className="mt-2 space-y-2">
          {manualEntry && (
            <CommittedTextField
              aria-label={label}
              value={value}
              onCommit={(next) => { onSelect(next); setExpanded(false) }}
              className="focus-ring w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          )}
          <FileBrowser
            currentPath={value || undefined}
            extensions={extensions}
            onSelect={(path) => { onSelect(path); setExpanded(false) }}
          />
        </div>
      )}
    </div>
  )
}
```

(Match `FileBrowser`'s actual prop names from `_shared.tsx` when wiring — adjust if its signature differs.)
- [ ] **Step 2: Component tests** (`PathPickerField.test.tsx`): (a) empty value renders browser immediately; (b) with value renders pill + no browser; (c) `change` expands, selecting a path calls `onSelect` and collapses; (d) `manualEntry` commit calls `onSelect`.
- [ ] **Step 3: Run — expect FAIL, then PASS** after Step 1: `npm --prefix frontend test -- src/panels/editors/shared/__tests__/PathPickerField.test.tsx`
- [ ] **Step 4: Adopt in ExternalFileEditor** — replace the File Path block (lines 64-73) with:

```tsx
<PathPickerField
  label="File Path"
  value={configField(config, "path", "")}
  onSelect={(path) => onUpdate("path", path)}
  extensions=".pkl,.pickle,.json,.joblib,.cbm,.onnx,.pmml"
/>
```

- [ ] **Step 5: Adopt in `_IoFormatEditor`** — replace the `field.kind === "path"` branch's label/browse-toggle/`CommittedTextField`/`FileBrowser` block with:

```tsx
if (field.kind === "path") {
  return (
    <PathPickerField
      key={field.name}
      label={`${field.label}${field.required ? " *" : ""}`}
      value={value}
      onSelect={(path) => updateField(field.name, path)}
      extensions={format && format.extensions.length > 0 ? format.extensions.join(",") : undefined}
      manualEntry
    />
  )
}
```

Remove the now-unused `browsingField` state.
- [ ] **Step 6: Adopt in ApiInputEditor** — replace the inline pill/browser block (lines 714-767) with the component (`label="Preview Data"`, `sublabel=".json, .jsonl, .ndjson, or .xml"`, `extensions=".json,.jsonl,.ndjson,.xml"`, `onSelect={(path) => { onUpdate("path", path); fetchForPath(path) }}`). The `file-change-btn` test-id is preserved by the shared component — existing ApiInputEditor tests must stay green.
- [ ] **Step 7: Verify:** `npm --prefix frontend test -- src/__tests__/editors` plus typecheck + lint.
- [ ] **Step 8: Commit** `refactor: shared PathPickerField across editors`

---

### Task 8: "Polars" tab with the shared code panel

**Files:**
- Modify: `frontend/src/panels/NodePanel.tsx:663,1010-1035,1108-1118` (tab union, tab bar, tab render)
- Modify: `frontend/src/panels/editors/shared/PolarsCodePanel.tsx:15-16` (hint becomes ReactNode)
- Modify: `frontend/src/panels/editors/DataInputEditor.tsx:432-441` (remove code section) and `frontend/src/panels/editors/ExternalFileEditor.tsx:75-91` (remove code section)
- Modify (8b): `frontend/src/panels/editors/RatingStepEditor.tsx:~877`, `frontend/src/panels/editors/ModelScoreEditor.tsx:~121`
- Test: NodePanel tests (coverage-ratcheted) + editor tests

**Interfaces:**
- Consumes: `PolarsCodePanel({config, onUpdate, inputSources, onDeleteInput?, errorLine?, upstreamColumns?, hint, placeholder?})` — already renders the "Polars Code" label, `CodeEditor`, and `return df` footer.
- Produces: `activeTab: "config" | "polars" | "columns"`; a `POLARS_TAB_TYPES` set naming which node types get the tab.

**8a — infrastructure + Data Input + Load File:**

- [ ] **Step 1: Failing NodePanel test:** selecting a Data Input node shows a `Polars` tab; clicking it renders the code panel; the config tab no longer contains a code editor.

```tsx
it("shows the Polars tab for data input and hosts the code editor there", async () => {
  renderNodePanelWithNode(dataInputNode)
  await userEvent.click(screen.getByRole("button", { name: /polars/i }))
  expect(screen.getByText("Polars Code")).toBeInTheDocument()
})
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement in NodePanel.**
  - `const [activeTab, setActiveTab] = useState<"config" | "polars" | "columns">("config")`
  - Near `NO_COLUMNS_TAB`, add:

```tsx
/** Node types whose additional Polars code lives in the shared Polars tab. */
const POLARS_TAB_TYPES = new Set<string>([
  NODE_TYPES.DATA_INPUT, NODE_TYPES.EXTERNAL_FILE,
  NODE_TYPES.RATING_STEP, NODE_TYPES.MODEL_SCORE,
])
const POLARS_TAB_HINTS: Record<string, React.ReactNode> = {
  [NODE_TYPES.DATA_INPUT]: <><code>df</code> = the opened source or cached snapshot</>,
  [NODE_TYPES.EXTERNAL_FILE]: <><code>obj</code> = loaded file, assign to <code>df</code></>,
  // ratingStep / modelScore filled in 8b from their current in-editor prose
}
```

  - `const showPolarsTab = isKnownNodeType && !isInstance && POLARS_TAB_TYPES.has(nodeType)`
  - Tab bar: render when `showColumnsTab || showPolarsTab`, mapping over `(["config", ...(showPolarsTab ? ["polars"] : []), ...(showColumnsTab ? ["columns"] : [])] as const)` — keep the existing active/inactive styles; capitalised label via existing rendering (`{tab}` — use `{tab === "polars" ? "Polars" : tab}` if labels are displayed raw).
  - Content: before the columns branch at line 1110:

```tsx
{activeTab === "polars" && showPolarsTab ? (
  <PolarsCodePanel
    config={config}
    onUpdate={handleConfigUpdate}
    inputSources={nodeType === NODE_TYPES.DATA_INPUT ? [] : inputSources}
    onDeleteInput={onDeleteEdge}
    errorLine={errorLine}
    upstreamColumns={upstreamColumns}
    hint={POLARS_TAB_HINTS[nodeType] ?? null}
  />
) : activeTab === "columns" && showColumnsTab ? ( ... existing ... ) : renderEditor()}
```

  - Reset tab on node switch if the new node lacks the active tab (extend the existing `useEffect` on `node?.id` to `setActiveTab("config")` when the active tab is unavailable).
- [ ] **Step 4:** `PolarsCodePanel` hint type: `hint: React.ReactNode` (string callers in `TransformEditor`/`ExploreCodeEditor` remain compatible).
- [ ] **Step 5:** Remove the code sections + `CodeEditor` imports from `DataInputEditor.tsx` (lines 432-441 and the prose line) and `ExternalFileEditor.tsx` (lines 75-91). ExternalFileEditor keeps its `InputSourcesBar`.
- [ ] **Step 6: Verify:** NodePanel + both editor test files; typecheck; confirm NodePanel coverage ratchet holds (`npm --prefix frontend run test:coverage` if in doubt).

**8b — Rating Step + Model Scoring:**

- [ ] **Step 7:** Read the code-section JSX around `RatingStepEditor.tsx:877` and `ModelScoreEditor.tsx:121`; move each into the tab by deleting the editor's `CodeEditor` block and adding its hint text (taken verbatim from the editor's current prose) to `POLARS_TAB_HINTS`. Acceptance: no `CodeEditor` import remains in either editor; the Polars tab shows the code with the node-appropriate hint; each editor's remaining config renders unchanged.
- [ ] **Step 8:** Update those editors' tests (code-editor assertions move to NodePanel polars-tab tests). Verify as Step 6.
- [ ] **Step 9: Commit** `feat: shared Polars tab for additional node code`

---

### Task 9: Data Input cache button = shared CacheFetchButton

**Files:**
- Create: `frontend/src/panels/editors/_InputSnapshotCacheButton.tsx`
- Modify: `frontend/src/panels/editors/DataInputEditor.tsx:373-382`
- Delete: `frontend/src/panels/editors/_InputCacheControls.tsx` (+ its test file)
- Test: new `frontend/src/panels/editors/__tests__/InputSnapshotCacheButton.test.tsx`

**Interfaces:**
- Consumes: `CacheFetchButton<TStatus>` (`components/CacheFetchButton.tsx`) — `{resourceKey, getStatus, startFetch, getProgress, deleteCache, cancelFetch?, timestampField, labels, disabled?, disabledReason?}` with `TStatus extends {cached,row_count,column_count,size_bytes}`; API fns `getInputCacheStatus/buildInputCache/getInputCacheJob/cancelInputCacheJob/clearInputCache` (`api/client.ts:845-878`).
- Produces: `InputSnapshotCacheButton({config, admittedEager, requiredReady})`.

- [ ] **Step 1: Failing tests** (mock the five client functions):
  - status `missing` → button shows "Cache as Parquet" + not-cached hint.
  - click → `buildInputCache` called with `{schema_version:1, config, refresh:false, profile:"lazy_sink"}`; job polled until `completed` → button flips to "Refresh Cache" with rows/cols/bytes stats.
  - `admittedEager` → profile `"preview_eager"`.
  - ready+`stale` → renders "Source changed since cache — Refresh to update".
  - click while building → `cancelInputCacheJob(jobId)`.
  - `clear` link → `clearInputCache` called.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement:**

```tsx
import { useRef } from "react"
import { CacheFetchButton } from "../../components/CacheFetchButton"
import {
  buildInputCache, cancelInputCacheJob, clearInputCache,
  getInputCacheJob, getInputCacheStatus,
} from "../../api/client"
import type { InputCacheJobStatusResponse, InputCacheSnapshotResponse } from "../../api/types"

type SnapshotButtonStatus = {
  cached: boolean
  row_count: number
  column_count: number
  size_bytes: number
  created_at: number
  freshness: InputCacheSnapshotResponse["freshness"]
}

function toButtonStatus(snapshot: InputCacheSnapshotResponse): SnapshotButtonStatus {
  const generation = snapshot.generation
  return {
    cached: snapshot.state === "ready",
    row_count: generation?.row_count ?? 0,
    column_count: generation?.column_count ?? 0,
    size_bytes: generation?.size_bytes ?? 0,
    created_at: generation?.created_at ?? 0,
    freshness: snapshot.freshness,
  }
}

const TERMINAL = new Set(["completed", "error", "cancelled", "superseded", "timed_out", "memory_limited"])

async function pollJobToTerminal(jobId: string): Promise<InputCacheJobStatusResponse> {
  for (;;) {
    const job = await getInputCacheJob(jobId)
    if (TERMINAL.has(job.status)) return job
    await new Promise((resolve) => setTimeout(resolve, 800))
  }
}

export default function InputSnapshotCacheButton({
  config, admittedEager, requiredReady,
}: {
  config: Record<string, unknown>
  admittedEager: boolean
  requiredReady: boolean
}) {
  const jobIdRef = useRef<string | null>(null)
  const cachedRef = useRef(false)
  const freshnessRef = useRef<SnapshotButtonStatus["freshness"]>("unknown")
  const resourceKey = JSON.stringify(config)
  const payload = { schema_version: 1 as const, config }

  const track = (status: SnapshotButtonStatus): SnapshotButtonStatus => {
    cachedRef.current = status.cached
    freshnessRef.current = status.freshness
    return status
  }

  return (
    <div>
      <CacheFetchButton<SnapshotButtonStatus>
        resourceKey={resourceKey}
        getStatus={() => getInputCacheStatus(payload).then((s) => track(toButtonStatus(s)))}
        startFetch={async () => {
          const started = await buildInputCache({
            ...payload,
            refresh: cachedRef.current,
            profile: admittedEager ? "preview_eager" : "lazy_sink",
          })
          jobIdRef.current = started.job_id
          const job = await pollJobToTerminal(started.job_id)
          jobIdRef.current = null
          if (job.status !== "completed" || !job.snapshot) {
            throw new Error(job.message || "Snapshot build failed.")
          }
          return track(toButtonStatus(job.snapshot))
        }}
        getProgress={async () => {
          const jobId = jobIdRef.current
          if (!jobId) return { active: false }
          const job = await getInputCacheJob(jobId)
          return {
            active: job.status === "running",
            rows: job.progress.rows,
            elapsed: Math.round(job.progress.elapsed_seconds),
            phase: job.progress.phase,
          }
        }}
        cancelFetch={() => {
          const jobId = jobIdRef.current
          return jobId ? cancelInputCacheJob(jobId) : Promise.resolve(null)
        }}
        deleteCache={() => clearInputCache(payload).then((s) => track(toButtonStatus(s)))}
        timestampField="created_at"
        labels={{
          fetchLabel: "Cache as Parquet",
          refreshLabel: "Refresh Cache",
          notCachedHint: "No cache yet — the first run creates it automatically",
          pendingLabel: "Processing...",
        }}
        disabled={!requiredReady}
        disabledReason="Complete the required source fields to build a snapshot."
      />
      {cachedRef.current && freshnessRef.current === "stale" && (
        <p className="mt-1 text-[10px] px-1" style={{ color: "var(--warning-strong)" }}>
          Source changed since cache — Refresh to update.
        </p>
      )}
    </div>
  )
}
```

(If the ref-driven freshness line proves non-reactive in tests, lift `freshness` into `useState` set inside `track` — same shape.)
- [ ] **Step 4:** In `DataInputEditor.tsx`, replace the `InputCacheControls` usage with `<InputSnapshotCacheButton config={config} admittedEager={format?.input?.snapshot_build === "admitted_eager"} requiredReady={requiredReady} />` for snapshot-backed providers only. File-backed Parquet is the one canonical Direct case and renders no cache action; do not add any other Direct compatibility or migration path. Delete `_InputCacheControls.tsx` and its test.
- [ ] **Step 5: Verify:** new test file + DataInputEditor tests; typecheck + lint.
- [ ] **Step 6: Commit** `feat: shared cache button for Data Input snapshots`

---

### Task 10: All/None = tick/untick every box (draft state)

**Files:**
- Modify: `frontend/src/panels/editors/ColumnsTab.tsx`
- Test: `frontend/src/panels/editors/__tests__/ColumnsTab.test.tsx`

Decision (28 Jul): `selected_columns: []` stays the committed "all" sentinel; "all unticked" is editor-local draft that never reaches config; commit resumes at ≥1 tick. Value-equal commits are skipped so no-op round trips can't churn `structuralVersion` (the source of the spurious refresh prompting).

- [ ] **Step 1: Failing tests:**

```tsx
it("None unticks every box without touching config", ...)      // all checkboxes unchecked; onUpdate NOT called; notice visible; counter 0 / N
it("first tick after None commits exactly that column", ...)   // onUpdate("selected_columns", ["b"]) once
it("ticking the last remaining box back on restores implicit all", ...) // draft covering all → onUpdate("selected_columns", [])
it("unticking the final committed column enters draft, not select-all", ...) // config ["a"], untick a → no onUpdate, draft empty
it("re-creating the identical selection does not re-commit", ...) // draft equal to committed value → no onUpdate call
it("All commits the all-sentinel from explicit mode", ...)     // onUpdate("selected_columns", [])
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Core state changes:

```tsx
const selectedColumns = configField<string[]>(config, "selected_columns", [])
const committedJson = JSON.stringify(selectedColumns)
// Draft: null = follow config; string[] = local un-committed ticks (only while < 1 selected).
const [draft, setDraft] = useState<string[] | null>(null)
const [lastCommitted, setLastCommitted] = useState(committedJson)
if (lastCommitted !== committedJson) {   // external config change wins over a stale draft
  setLastCommitted(committedJson)
  setDraft(null)
}

const isAllSelected = draft === null && selectedColumns.length === 0
const effectiveSelection = draft ?? selectedColumns
const isSelected = (col: string) =>
  draft !== null ? draft.includes(col) : isAllSelected || selectedColumns.includes(col)

const sameSelection = (a: string[], b: string[]) =>
  a.length === b.length && [...a].sort().every((v, i) => v === [...b].sort()[i])

const commit = (next: string[]) => {
  const normalized = next.length >= allColumns.length ? [] : next
  setDraft(null)
  if (sameSelection(normalized, selectedColumns)) return   // skip value-equal commits
  onUpdate("selected_columns", normalized)
}

const toggleColumn = (col: string) => {
  const base = draft ?? (isAllSelected ? allColumns.map((c) => c.name) : selectedColumns)
  const next = base.includes(col) ? base.filter((c) => c !== col) : [...base, col]
  if (next.length === 0) { setDraft([]) ; return }          // stay local — never commit empty
  commit(next)
}

const selectAll = () => commit([])
const selectNone = () => setDraft([])
```

Counter: `const selectedCount = draft !== null ? draft.length : isAllSelected ? allColumns.length : selectedColumns.length`. All button `disabled={isAllSelected}` as today; None button `disabled={draft !== null && draft.length === 0}`. Notice while `draft !== null`:

```tsx
{draft !== null && (
  <p role="status" className="text-[10px]" style={{ color: "var(--warning-strong)" }}>
    Select at least one column to apply.
  </p>
)}
```

(Note `commit([])` with `next.length >= allColumns.length` also collapses full-explicit → sentinel, preserving today's behaviour at `ColumnsTab.tsx:59-61`.)
- [ ] **Step 4: Run — expect PASS.** Also rerun any existing ColumnsTab tests and adapt ones that encoded the old None-keeps-first behaviour.
- [ ] **Step 5: Manual check of the original complaint:** with a previewed node, click None then re-tick the same columns — no "Stale columns / refresh" banner and no preview-stale churn (no config write happened). If a banner still appears from *real* selection changes referencing columns absent from `_availableColumns`, that is the backend `status="stale"` scoop (`executor.py:1268-1276`) working as designed — file it separately with the exact node type rather than widening this task.
- [ ] **Step 6: Commit** `fix: All/None tick semantics with local draft state`

---

### Task 11: Clear machine-readable missing-snapshot execution error

**Files:**
- Modify: `src/haute/_input_providers.py:232-236`
- Test: `tests/test_runtime_input_cache_invalidation.py` (or the module already covering `resolve_data_input` — locate with `Grep "resolve_data_input" tests/ -l`)

**Interfaces:**
- Produces: `PolarsIoConfigError` whose message starts `input_snapshot_missing:` when a snapshot-mode input has no published snapshot. Task 12's frontend treats this as "should have ensured first"; users see an actionable message.

- [ ] **Step 1: Failing test:**

```python
def test_snapshot_mode_without_snapshot_raises_actionable_error(tmp_path):
    config = {  # minimal non-Parquet file input in snapshot mode; adapt to module fixtures
        "inputType": "file", "cacheMode": "snapshot", "format": "csv",
        "mode": "scan", "path": "data.csv", "arguments": {},
    }
    with pytest.raises(PolarsIoConfigError, match="input_snapshot_missing"):
        resolve_data_input(config, store=SourceCacheStore(tmp_path), base_dir=tmp_path)
```

- [ ] **Step 2: Run — expect FAIL** (currently a raw `FileNotFoundError` escapes from `lease()` → `_read_pointer`).
- [ ] **Step 3: Implement** in `resolve_data_input`:

```python
    cache_store = store or SourceCacheStore(_cache_root())
    identity = source_cache_identity(validated, base_dir=base_dir)
    lease = cache_store.lease(identity)
    try:
        generation = lease.__enter__()
    except FileNotFoundError:
        raise PolarsIoConfigError(
            "input_snapshot_missing: This Data Input runs from a snapshot "
            "that has not been built yet. Build the snapshot (or run a "
            "preview, which builds it automatically) and try again."
        ) from None
```

(`SourceCacheCorruptError` keeps propagating as-is — corrupt ≠ missing.)
- [ ] **Step 4: Verify:** the test module, `uv run mypy src/haute/`, `uv run ruff check src/haute/_input_providers.py`.
- [ ] **Step 5: Commit** `fix: actionable error for unbuilt input snapshots`

---

### Task 12: Auto-build snapshots before execution

**Files:**
- Create: `frontend/src/hooks/ensureInputSnapshots.ts` + `frontend/src/hooks/__tests__/ensureInputSnapshots.test.ts`
- Modify: `frontend/src/hooks/usePipelineAPI.ts` (preview request path — `refreshPreview` at :695 and the shared fetch path it delegates to)
- Modify: caching section of the io/caching spec (Step 1)

Decision corrected (28 Jul): file-backed Parquet is scanned directly and has no
snapshot/cache action. Every other Data Input is snapshot-backed. Any execution needing a
non-Parquet input with **no** snapshot kicks the existing build job automatically
(visible, cancellable), then runs. `stale` never auto-refreshes. Snapshot execution never
contacts the provider; orchestration happens in the frontend against the existing job
endpoints. Cache-mode mismatches fail clearly and are not migrated.

- [ ] **Step 1: Spec.** Update the caching/io-layer spec: auto-build-on-demand semantics, stale-never-auto-refreshes, executor invariant unchanged.
- [ ] **Step 2: Failing unit tests** for the helper (mock client fns):
  - graph with one snapshot-mode Data Input, status `missing` → `buildInputCache` called (`refresh:false, profile:"lazy_sink"`), job polled to `completed`, resolves.
  - status `ready` (fresh **or stale**) → no build call.
  - status `building` → joins via `buildInputCache` (server returns `joined:true` + active job id) and waits.
  - build 400 `snapshot_build_unsupported` → retried once with `profile:"preview_eager"`.
  - job terminal ≠ completed → rejects with the job message.
  - canonical direct Parquet Data Inputs and non-dataInput nodes → ignored;
    any other non-snapshot Data Input → rejected.
- [ ] **Step 3: Implement the helper:**

```ts
import { buildInputCache, getInputCacheJob, getInputCacheStatus, ApiError } from "../api/client"
import type { Node } from "@xyflow/react"

const TERMINAL = new Set(["completed", "error", "cancelled", "superseded", "timed_out", "memory_limited"])

function snapshotConfigs(nodes: Node[]): Record<string, unknown>[] {
  return nodes.flatMap((node) => {
    if ((node.data as { nodeType?: string })?.nodeType !== "dataInput") return []
    const config = (node.data as { config?: Record<string, unknown> })?.config ?? {}
    const directParquet =
      config.inputType === "file" &&
      config.format === "parquet" &&
      (config.mode === undefined || config.mode === "" || config.mode === "scan") &&
      config.cacheMode === "direct"
    if (directParquet) return []
    if (config.cacheMode !== "snapshot") {
      throw new Error(`Data Input ${node.id} requires cacheMode "snapshot".`)
    }
    return [config]
  })
}

async function waitForJob(jobId: string): Promise<void> {
  for (;;) {
    const job = await getInputCacheJob(jobId)
    if (job.status === "completed") return
    if (TERMINAL.has(job.status)) {
      throw new Error(job.message || `Input snapshot build ${job.status}.`)
    }
    await new Promise((resolve) => setTimeout(resolve, 800))
  }
}

async function startBuild(config: Record<string, unknown>): Promise<string> {
  const payload = { schema_version: 1 as const, config, refresh: false }
  try {
    return (await buildInputCache({ ...payload, profile: "lazy_sink" })).job_id
  } catch (caught) {
    const detail = caught instanceof ApiError ? caught.detail ?? "" : ""
    if (detail.startsWith("snapshot_build_unsupported")) {
      return (await buildInputCache({ ...payload, profile: "preview_eager" })).job_id
    }
    throw caught
  }
}

/** Build (or join the build of) every missing snapshot the graph needs. Stale snapshots are served as-is. */
export async function ensureInputSnapshots(nodes: Node[]): Promise<void> {
  await Promise.all(
    snapshotConfigs(nodes).map(async (config) => {
      const status = await getInputCacheStatus({ schema_version: 1, config })
      if (status.state === "ready") return
      if (status.state === "corrupt" || status.state === "failed" || status.state === "missing" || status.state === "building") {
        // "building" joins the active job (the build endpoint returns joined:true with its id);
        // corrupt/failed rebuild explicitly rather than failing the run on a known-bad snapshot.
        await waitForJob(await startBuild(config))
      }
    }),
  )
}
```

(Match `ApiError`'s actual export/shape in `api/client.ts` — it is already imported by `CacheFetchButton`. Use the `NODE_TYPES.DATA_INPUT` constant instead of the `"dataInput"` literal if importable without a dependency cycle.)
- [ ] **Step 4: Wire into `usePipelineAPI.ts`.** In the preview execution path (the function `refreshPreview`/`fetchPreview` share for issuing the preview request), before the request is sent:

```ts
try {
  await ensureInputSnapshots(nodes)
} catch (caught) {
  const message = caught instanceof Error ? caught.message : String(caught)
  // Route `message` through whatever the surrounding function already does
  // with a failed preview request (its catch branch / error store write) —
  // reuse that exact mechanism, then return without firing the preview.
  return
}
```

Use the hook's existing node list + error-reporting mechanics (it already owns both — find the single point where the preview POST fires, guard it, and reuse its existing failure path for the error message). Add a toast (`useToastStore`) "Building input snapshot…" when at least one build starts, if a callback seam is cheap — otherwise the node's cache button status ("building") plus the eventual preview is acceptable for this pass.
- [ ] **Step 5: Extend `usePipelineAPI` tests** (coverage-ratcheted file): preview with a missing snapshot triggers build-then-preview ordering; build failure blocks the preview and surfaces the message; ready/stale snapshots go straight to preview.
- [ ] **Step 6: Verify:** helper + hook test files; typecheck + lint; backend untouched.
- [ ] **Step 7: Commit** `feat: auto-build missing input snapshots before preview`

---

### Task 13: Snapshots are never silently evicted

**Files:**
- Modify: `src/haute/_source_cache.py:571-647` (`_admit_publication_within_quota`), remove `_eviction_candidates` (:526-569) and `_EvictionCandidate` if now unused
- Modify: caching spec (Step 1)
- Test: the source-cache eviction tests (locate: `Grep "_eviction_candidates\|QuotaExceeded" tests/ -l`)

Decision (28 Jul): a built snapshot persists until the user refreshes or clears it. Quota pressure must **reject the new build** (`SourceCacheQuotaExceededError` → job `error_code: "cache_quota_exceeded"`, already surfaced) instead of deleting another input's current snapshot. Superseded-generation retirement (`_retire_unleased`) and staging cleanup stay.

- [ ] **Step 1: Spec.** Update the caching spec: current generations are durable; quota overflow fails the incoming build with a clear error; users clear snapshots explicitly or raise the quota.
- [ ] **Step 2: Failing test:**

```python
def test_quota_pressure_rejects_build_instead_of_evicting_other_snapshot(tmp_path):
    # Reuse the exact store construction and byte budget from the module's
    # current cross-identity eviction test — this test replaces its premise.
    store = SourceCacheStore(tmp_path, max_bytes=..., max_generations=...)
    _publish_snapshot(store, identity_a)          # module's existing helper
    with pytest.raises(SourceCacheQuotaExceededError):
        _publish_snapshot(store, identity_b)      # would previously evict A
    assert store.status(identity_a).state == "ready"   # A untouched
```

Adapt setup/helpers to the module's existing eviction tests; repurpose (don't duplicate) the tests that currently assert cross-identity eviction.
- [ ] **Step 3: Run — expect FAIL** (B currently succeeds by evicting A).
- [ ] **Step 4: Implement.** In `_admit_publication_within_quota`, keep the own-identity reclaim computation (lines 578-594), then replace the candidate-eviction loop (598-647) with:

```python
        projected_bytes = current_size - reclaimable + new_size_bytes
        projected_count = current_count - reclaimable_count + 1
        if projected_bytes <= self.max_bytes and projected_count <= self.max_generations:
            return
        raise SourceCacheQuotaExceededError(
            "source-cache quota exceeded: existing snapshots are kept until "
            "explicitly refreshed or cleared. Clear an unused Data Input "
            "snapshot or raise the cache quota."
        )
```

Delete `_eviction_candidates` and `_EvictionCandidate` if nothing else references them (grep first).
- [ ] **Step 5: Verify:** source-cache test module(s), `uv run mypy src/haute/`, `uv run ruff check src/haute/_source_cache.py`. Confirm the frontend build-error path (Task 9's button) displays the quota message (`error_code: cache_quota_exceeded` → job message shown).
- [ ] **Step 6: Commit** `feat: snapshots persist until refreshed - quota rejects new builds`

---

### Task 14: Edge join auto-populates the first common key

**Files:**
- Modify: `frontend/src/panels/editors/EdgeJoinEditor.tsx`
- Test: the EdgeJoinEditor test file (locate via `Glob frontend/src/**/EdgeJoin*`; render helper `frontend/src/__tests__/utils/renderEditor.tsx`)

Seeding happens in the editor (not on edge connect): when both inputs are connected, no join keys are configured, and `commonColumns` is known, seed `on: [firstCommon]`. A per-node guard prevents re-seeding after the user deliberately clears keys.

- [ ] **Step 1: Failing tests:**

```tsx
it("seeds the first common column as the join key", () => {
  // commonColumns come from analyzeEdgeJoinNode and are {name, dtype} objects —
  // build the fixture graph so both inputs share these columns.
  renderEdgeJoinEditor({
    config: { how: "left", suffix: "_right" },
    commonColumns: [{ name: "policy_id", dtype: "str" }, { name: "region", dtype: "str" }],
  })
  expect(onUpdate).toHaveBeenCalledWith({ on: ["policy_id"], leftOn: [], rightOn: [] })
})
it("does not seed when keys are already configured", ...)   // config { on: ["x"] } → no call
it("does not seed while common columns are unknown", ...)   // commonColumns [] → no call
it("does not re-seed after the user clears the seeded key", ...) // clear → rerender → single seed call total
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** in `EdgeJoinEditor` (after the `analysis` destructuring):

```tsx
// Auto-populate: with both inputs connected, no keys configured, and known
// common columns, seed the first common column once per node. The guard set
// makes a deliberate user clear stick instead of being re-seeded.
const seededNodesRef = useRef<Set<string>>(new Set())
const hasAnyKeys = onKeys.length > 0 || leftKeys.length > 0 || rightKeys.length > 0
const canSeed =
  !hasAnyKeys && how !== "cross" &&
  baseRoleEdge !== undefined && joinRoleEdge !== undefined &&
  commonColumns.length > 0 && !seededNodesRef.current.has(nodeId)
useEffect(() => {
  if (!canSeed) return
  seededNodesRef.current.add(nodeId)
  onUpdate({ on: [commonColumns[0].name], leftOn: [], rightOn: [] })
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [canSeed, nodeId])
```

Add `useEffect, useRef` to the react import.
- [ ] **Step 4: Run — expect PASS**; also rerun the editor's existing tests (some assert the empty-key initial render — adapt only where the seed legitimately changes the fixture).
- [ ] **Step 5: Verify:** typecheck + lint.
- [ ] **Step 6: Commit** `feat: edge join seeds first common column as join key`

---

## Completion

- [x] Run targeted tests and touched-file static checks for the affected surfaces. Leave the
  broad compatibility suite to CI.
- [x] Build the frontend production bundle so the accumulated UI changes can be reviewed.
- [x] Root-review the accumulated branch diff and resolve every finding.
- [x] Prepare the unpushed `manual-tweaks` diff for user review; push only when requested.

Final correction evidence (28-Jul-2026):

- Canonical policy tests: 82 passed across the registry and input-provider modules.
- Focused cross-stack regression batch: 24 passed, including direct Parquet, snapshot-backed
  formats, runtime cache identity, RAM metadata, chunking, deploy bundling/scoring, and
  submodel config fixtures.
- Focused Data Input frontend tests: 31 passed.
- Touched Python files pass Ruff; the focused frontend ESLint run has no errors.
- `npm.cmd --prefix frontend run build` completed successfully. Broad preflight and browser
  execution were deliberately left to CI/user review as requested.

## Deliberate decisions (so implementers don't relitigate)

- `read` mode survives in config validation and execution — only the *capability advertisement* narrows (Task 6). Loud in-editor migration, no data-loss risk.
- All/None never commits an empty selection; the `[]` sentinel keeps meaning "all" (Task 10). No schema change.
- Auto-build lives in the frontend against existing job endpoints and applies only to
  snapshot-backed inputs (Task 12). Direct file-backed Parquet scans its source; all other
  execution remains snapshot-offline. Non-studio snapshot callers (deploy/codegen) keep
  the clear Task-11 error instead of silent building.
- Quota overflow now fails the *new* build rather than deleting an old snapshot (Task 13) — durability beats admission, per the 28-Jul decision.
- Edge-join seeding is editor-side and once-per-node — a wrong guess is visible and editable in the same panel that made it (Task 14).
