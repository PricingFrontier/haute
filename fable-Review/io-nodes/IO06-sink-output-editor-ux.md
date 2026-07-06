# IO06 — Sink & Output editors: invisible destination, remount-fragile writes, a migration banner on brand-new nodes

**Severity: MEDIUM (UX) · Effort: M · Review mode: batch (S3 state store: pair)**

Frontend findings (verified with file:line by the frontend reviewer; backend interactions
verified in the main pass). The OUTPUT mapping editor itself is strong — grammar mirroring,
stale-response sequencing, edge-preserving renames are CLEARED; these are the gaps around it.

---

## IO06-a — Sink: the destination is invisible until after the write, and a typed extension silently doubles (MEDIUM)

`SinkEditor.tsx:76-89` is a bare text input (empty placeholder). The real target is computed
server-side by `_resolve_sink_path` (`_graph_utils.py:66-77`): `results` →
`outputs/results.parquet`; and because the appended extension keys off the **format toggle**,
typing `results.csv` while the toggle says parquet yields **`outputs/results.csv.parquet`**.
The user learns the destination only from the post-write message (`executor.py:1678`).

**Fix.** Mirror `_resolve_sink_path` in TypeScript (the codebase already mirrors backend logic
— `sanitizeName`, the JSONPath grammar) and render the resolved target live under the input;
warn on a typed-extension/format mismatch. **TDD:** `results`+parquet shows
`outputs/results.parquet`; `results.csv`+parquet shows a mismatch warning.

## IO06-b — Sink: Write silently overwrites (MEDIUM, pairs with IO05-d)

No exists-check anywhere in the UI flow; the route only validates containment
(`routes/pipeline.py:753`). Clicking Write clobbers whatever is at the resolved path.
**Fix:** consume IO05-d's `overwrite` flag: when the resolved target exists, ask once
(or annotate "will replace existing file, 34 MB, modified yesterday"). **TDD:** pre-existing
target → confirm step rendered; confirming sends `overwrite: true`.

## IO06-c — Sink: write state is component-local — lost on panel switch, enables overlapping writes (MEDIUM)

`writing`/`writeResult` are `useState` in `SinkEditor` (`SinkEditor.tsx:26-27`). Switching
nodes unmounts the editor: the result is discarded, and on return the button is an enabled
"Write" **while the first request still runs server-side** (client timeout 300 s,
`client.ts:661`) — inviting a second overlapping write to the same path (which IO05-c makes
actively dangerous today). **Fix:** lift sink write status into a per-node store keyed by
`nodeId` (the column/cache stores are the in-repo pattern); disable across remounts while
pending; persist the last result. **TDD:** start write → switch node → return: button still
"Writing…" disabled; result persists after completion.

## IO06-d — Sink: no progress or cancel for a multi-minute write (LOW)

Only the button label changes (`SinkEditor.tsx:94-99`); a full-dataset streaming sink can run
to the 300 s timeout with no feedback or abort. The apiInput cache flow already has
`getProgress`/`cancelFetch` to copy. **Fix:** job-style progress + cancel for `/pipeline/sink`.

## IO06-e — Sink: path commits per keystroke (LOW)

`onChange` → `onUpdate("path", …)` per character (`SinkEditor.tsx:80`) churns config /
`structuralVersion` / fingerprints. Every other IO editor buffers via `CommittedTextInput`.
**Fix:** adopt the shared committed-input pattern. **TDD:** typing does not call `onUpdate`
until blur/Enter.

## IO06-f — Sink: structured `path`/`row_count` response fields ignored (LOW)

`SinkResponse` carries structured `path` + `row_count` (`schemas.py:405-411`) but
`handleWrite` renders only free-text `message` (`SinkEditor.tsx:47-48`). **Fix:** render the
structured fields (also unlocks the IO06-c store-backed "last wrote N rows to X" chip).

## IO06-g — Every brand-new OUTPUT node shows the legacy-migration banner (MEDIUM)

`nodeTypes.ts:58` gives OUTPUT `defaultConfig: { fields: [] }` — the **v1** shape.
`classifyConfig({fields: []})` → v1 (`outputMappingSchema.ts:57-61`), so `OutputEditor.tsx:719-732`
renders *"This OUTPUT node uses the legacy format; saving will convert it…"* on a node the
user just dragged in — above the empty-state hint, on a tool whose v1 path was deliberately
removed everywhere else. **Fix:** default to the v2 empty shape (`{}` classifies as
`empty → emptyV2()`, or `{ outputMapping: [], outputFormat: "" }`). Nothing consumes `fields`
at create. **TDD:** render `OutputEditor` with `NODE_TYPE_META.output.defaultConfig` → banner
absent.

## IO06-h — Path-entry models are opposites; a11y labels missing on both (LOW)

`SinkEditor` is free-text-only; `DataSourceEditor` is browser-only (IO09-c) — neither offers
both. The `ToggleButtonGroup`s in both editors omit `ariaLabel`/`ariaLabelledBy` (the
component itself is a model a11y citizen — roving tabindex, arrow keys — only callers drop the
name); Sink's path input has no `htmlFor`/`id` association. **Fix:** one shared path-entry
component (browse + manual) used by both; wire the label associations. (OUTPUT's
single-option format `<select>` — JSON only, `OutputEditor.tsx:698-709` — is deliberate
pending jsonl/jsonseq (IO12); do not "simplify" the placeholder away.)

---

Cross-refs: IO05 (backend halves of a/b), IO12 (resolved-path preview and the format toggle
should both derive from the registry's `/api/formats` payload rather than new literals).
