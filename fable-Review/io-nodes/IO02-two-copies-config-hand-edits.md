# IO02 — The data-source config lives in two places, and hand-edits to one are silently discarded

**Severity: HIGH (architecture / "file is the source of truth" story) · Effort: M · Review mode: pair**

The README sells a two-way promise: *"Edit a node in the visual editor and the Python file
updates. Edit the Python file in a text editor and the visual editor updates."*
`docs/CODE_GUI_SYNC.md:5` repeats it: *"Python is the source of truth."* For the single most
likely hand edit a user will ever make — **changing which file a Data Source reads** — the
promise is currently false, silently.

All citations at `aca58177`.

---

## The mechanism (verified end-to-end)

1. **Codegen writes the config twice.** `_node_to_code` (`src/haute/codegen.py:333-364`)
   rewrites the decorator of every folder-backed node to a sidecar reference
   (`@pipeline.data_source(config="config/data_source/<name>.json")`), while the generated
   function **body** keeps a fully-baked copy of the same config as an inline expression:
   `df = read_data_source({"sourceType": "flat_file", "path": str(Path(__file__).parent / "data/x.csv"), ...})`
   (`_data_source_parts` / `_data_source_runtime_config_expr`,
   `src/haute/_codegen_builders.py:317-364`). The body copy exists so the saved file executes
   standalone (`python main.py`) without the parser.

2. **The parser trusts only the sidecar.** `_resolve_node_config`
   (`src/haute/_config_builder.py:404-486`) loads the sidecar for `config=` nodes and takes
   *only user code* from the body via `_attach_code_from_body`.

3. **Hand edits inside the body's load statement are classified as boilerplate and thrown
   away.** `_strip_source_load_boilerplate_from_code` (`src/haute/_code_extraction.py:419-438`)
   skips every leading statement matching `_is_source_load_statement_start` (`:490-493`) —
   `_statement_end_index` (`:496-503`) swallows the **whole parenthesised statement regardless
   of its arguments**. So a user who edits the path (or adds `"dtypes": ...`) inside the
   generated `read_data_source({...})` call sees:
   - the canvas does **not** update (sidecar wins on parse);
   - the next GUI save **regenerates the body from the sidecar**, silently reverting their edit;
   - until that save, `python main.py` and the canvas read **different files** — the worst
     version of drift, because both look healthy.

4. **Nothing in the generated file warns about this.** The generated body contains no comment
   saying "this load expression is generated from `config/data_source/<name>.json`; edit that
   file". A text-editor user has no way to know the rule. (Editing the *sidecar* does propagate
   correctly — the file watcher and parser honour it. The failure is specific to the `.py`
   body, which is exactly where the README points people.)

This is not hypothetical duplication: it is two on-disk sources of truth for the same fact with
a silent winner. Every neighbouring subsystem treats "canvas and saved file cannot drift" as a
design invariant (shared `*_from_config` helpers exist precisely for this — see
`_builders.py` comments, e.g. banding: "Shared with apply_banding_from_config … so the canvas
and the saved file cannot drift"). The data-source load is the one place the pattern was not
applied.

---

## Fix design (in preference order — 1 is the real fix, 2+3 are the cheap guard rails)

1. **Single-source the load through the sidecar at runtime, like every other config-folder
   node.** Generated body becomes:

   ```python
   @pipeline.data_source(config="config/data_source/read_data.json")
   def read_data() -> pl.LazyFrame:
       """..."""
       from haute.graph_utils import read_data_source_from_config
       df = read_data_source_from_config(Path(__file__).parent, "config/data_source/read_data.json")
       return df
   ```

   `read_data_source_from_config` = `load_node_config` + existing `read_data_source`, mirroring
   `apply_banding_from_config` (`_node_apply.py:47-55` pattern). Then the sidecar is the *only*
   copy; a hand edit to it changes both canvas and standalone runs; there is nothing in the
   body to edit wrongly. Path anchoring semantics are preserved (the helper anchors relative
   paths to the passed base dir, exactly as `_portable_path_expr` does today,
   `_codegen_builders.py:95-103`).
   *Cost:* deploy bundles must keep shipping the sidecar — they already do (config folder is
   bundled; verify in `deploy/_bundler.py` during implementation).

2. **Emit the guidance comment regardless** (belt-and-braces for old files):
   `# Generated from config/data_source/read_data.json — edit that file; this body is regenerated on save.`

3. **Parser-side conflict detection.** When stripping a source-load statement, cheaply compare
   the baked `"path"` literal against the sidecar's `path`; on mismatch attach a loud
   `_load_error`-style warning to the node (the config-error surfacing channel already exists,
   `_config_io.py:368-388`) instead of silently preferring the sidecar. This catches the
   already-diverged files fix 1 can't retroactively repair.

## TDD plan (failing tests first)

- `tests/test_parser_roundtrip.py` (or the existing round-trip suite): generate a pipeline with
  a data source; hand-rewrite the path inside the body's `read_data_source({...})`; re-parse;
  assert the node surfaces a **conflict warning** (fix 3) rather than silently using the sidecar.
- After fix 1: parse→codegen→parse round-trip test asserting the generated body contains
  `read_data_source_from_config` and **no baked literal path**; standalone-exec test (the
  `tests/test_e2e.py::test_full_lifecycle` family) asserting `python`-level execution of the
  generated file reads the sidecar-declared path after a sidecar-only edit — that is the README
  promise, now pinned by a test.
- Regression guard: user code *below* the load statement must still round-trip untouched
  (existing `_strip_source_load_boilerplate_from_code` tests cover the extraction; extend with
  an edited-arguments variant).

## Note for the implementer

The same two-copies pattern exists for `EXTERNAL_FILE` (baked `load_external_object(path,...)`
call in the body, `_EXTERNAL` template `_codegen_builders.py:475-483`) and the flat
`API_INPUT` (`_api_input_template` else-branch `:276-284`). Fix 1's helper should cover all
three; do them together so the codebase has zero baked-config bodies left.
