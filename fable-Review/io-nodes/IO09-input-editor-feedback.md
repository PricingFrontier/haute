# IO09 — Input editors: no read feedback in DataSource, a swallowed error in ApiInput, and browser-only paths

**Severity: MEDIUM (UX) · Effort: M · Review mode: batch**

Frontend-reviewer findings, verified with file:line. The theme: the input editors defer or
drop feedback the backend already produces.

---

## IO09-a — ApiInputEditor silently discards the schema-fetch error (MEDIUM, fail-loud violation)

`ApiInputEditor.tsx:157` destructures `useSchemaFetch(currentPath)` **without** taking
`error`, which the hook maintains and returns (`useSchemaFetch.ts:14,29,49`) — no consumer
reads it. When the bootstrap schema fetch fails (malformed JSON, deleted file), `schema` stays
null, `SchemaPreview` renders nothing (`_shared.tsx:302`), and the user sees a blank —
contrast "Infer Tables", which has its own `inferError` surface (`ApiInputEditor.tsx:485-495`).

**Fix.** Consume `error` and render it beside `SchemaPreview`, same treatment as `inferError`.
**TDD.** Mock `fetchSchema` to reject → editor renders the error text.

## IO09-b — DataSource gives zero in-editor confirmation a file is readable (MEDIUM)

After a pick, the editor's only feedback is a green box echoing the path string
(`DataSourceEditor.tsx:54-66`); validity is deferred to the *detached* bottom preview panel
the user must independently notice. No schema chip, row count, or inline "couldn't read this"
state — although `/api/schema` already returns columns+preview+row count (`files.py:139`) and
`useSchemaFetch`'s docstring says it exists for both editors; DataSource never adopted it.

**Fix.** Reuse `useSchemaFetch` + `SchemaPreview` in `DataSourceEditor` (schema summary +
inline error on select). This is also the seeding surface IO08's dtype rows need — build them
together. **TDD.** Selecting a file renders a schema summary; a rejected fetch renders an
inline error.

## IO09-c — Browser-only path entry blocks legitimate files; Sink is the exact opposite (MEDIUM)

`DataSourceEditor` can only choose files the `FileBrowser` can reach — rooted at `Path.cwd()`,
`goUp` stops at `"."` (`_shared.tsx:207-213`) — and only those surviving the (currently wrong,
IO01) extension filter. No manual path field. `SinkEditor` is free-text-only. The two ends of
the same pipeline disagree on the most basic interaction.

**Fix.** One shared path-entry affordance (browse + "type a path" toggle) used by both; manual
entries still validated server-side (`validate_safe_path` / `_validate_source_path` are the
authority — CLEARED). **TDD.** Manual path commits via `onUpdate("path", …)`; browser and
manual stay in sync.

## IO09-d — Small state/a11y burrs (LOW, batch)

- `setTimeout(() => onRefreshPreview?.(), 50)` after a pick (`DataSourceEditor.tsx:72-76`) —
  an undocumented race guess; drive the refresh from a config-change effect instead.
- Both editors hard-pass `currentPath={undefined}` to `FileBrowser`
  (`DataSourceEditor.tsx:71`, `ApiInputEditor.tsx:411`), defeating its own seeding — "change"
  on a deep file restarts navigation from the root. Pass the real current path.
- Unlabeled controls: Source-Type `ToggleButtonGroup` gets no `ariaLabel`/`ariaLabelledBy`
  (`DataSourceEditor.tsx:37-45`; same pattern as IO06-h), Databricks SQL `<textarea>` has only
  a placeholder (`:98-105`). Wire `id`/`ariaLabelledBy`.

**TDD.** One pick triggers exactly one preview refresh against the new path; `FileBrowser`
opens in the current file's directory; axe/aria assertions for the named radiogroup and
textarea.

---

Cross-refs: IO01 (extension filter correctness), IO08 (dtype rows share IO09-b's schema
fetch), IO06-h (the shared path-entry + label fixes should land as one component change).
