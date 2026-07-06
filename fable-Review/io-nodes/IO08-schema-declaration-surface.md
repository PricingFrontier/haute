# IO08 — Declared dtypes exist in the engine but have no UI — CSV pipelines are undeployable from the GUI

**Severity: HIGH (feature gap blocking a promised path) · Effort: M-L · Review mode: pair**

## Evidence

The backend has a complete, well-designed declared-schema system for sources:

- `read_source` accepts overrides under `schema_overrides | dtypes | column_dtypes | schema`
  (`src/haute/_io.py:127-138`), validates declared-vs-actual with precise
  `SchemaMismatchError`s (`:286-318`), and **requires** declared dtypes for CSV in every
  bounded-memory profile — `_validate_csv_declared_schema_for_profile` raises
  `BoundedMemoryUnsupportedError: "CSV sources require declared dtypes for bounded-memory
  execution profiles…"` (`:268-281`).
- `DataSourceConfig` persists all four keys plus `categorical_levels` (`_types.py:110-122`);
  the parser round-trips them (`SOURCE_DTYPE_CONFIG_KEYS`, `_config_builder.py:49-55`).
- Deploy verifies static sources at `DEPLOY_BATCH` profile (`deploy/_bundler.py:157-208`), so
  the requirement is enforced at deploy time too.

And the frontend exposes **none of it**. `DataSourceEditor.tsx` (all 142 lines read) offers
source-type, file pick, Databricks fields, and post-load Polars code — no dtype surface. The
post-load `code` box cannot help: it runs *after* the read that raises.

## Failure scenario (the trap as a user experiences it)

1. Analyst picks `quotes.csv`, previews happily — `PREVIEW_EAGER` allows inference
   (`_io.py:40-45`), everything looks green.
2. They batch-run / deploy. The run dies with `BoundedMemoryUnsupportedError` telling them to
   add "schema_overrides, dtypes, column_dtypes, or schema" — config keys that exist nowhere
   in the product's UI. The only remedy is hand-writing
   `config/data_source/<node>.json`, in a product whose pitch is "you don't need to know those
   tools to use Haute".

Secondary cost, classic pricing variant: no dtype surface also means no way to pin
`postcode`/`account_id` as String — inference reads them as ints, leading zeros gone; today's
only recourse is post-load Polars code the user must know to write.

## Fix design

1. **A "Column types" section in the flat-file branch of `DataSourceEditor`** (shown for
   formats where it matters — csv always; optional elsewhere for validation-pinning):
   - Seed rows from the `/api/schema` response the picker already fetches
     (`files.py:139` returns name+dtype for every column) — a one-click "pin all inferred
     types" affordance turns the failure class off wholesale.
   - Each row: column name (from schema), dtype dropdown (the `_POLARS_DTYPE_ALIASES` names,
     `_io.py:47-65` — serve them, don't re-list them in TS).
   - Writes a single canonical key: `schema_overrides` (see 3).
2. **Actionable pre-flight in the editor:** when the file is `.csv` and no overrides are set,
   a passive hint ("Batch/deploy runs require declared column types for CSV — pin types"),
   NOT a preview blocker. The bounded error already fires loud at run time; the UI's job is to
   make the remedy reachable before that.
3. **Canonicalise the four alias keys.** Keep reading all four in `read_source` (files on disk
   exist), but the editor writes only `schema_overrides`, and
   `_prepare_config_for_sidecar` normalises the aliases to it at save so sidecars converge.
   Fold `_schema_overrides_from_config`'s alias-precedence quirk (`or`-chain means an empty
   `schema_overrides` falls through to `dtypes` — fine, but undocumented) into a documented
   rule while there.
4. Databricks branch: out of scope (its schema comes from the warehouse; no inference gap).

## TDD plan (failing tests first)

- Frontend (`DataSourceEditor.test.tsx`): editing a dtype row calls `onUpdate` with a
  `schema_overrides` object; "pin all inferred" writes the full map from a mocked schema
  response; the csv hint renders iff no overrides.
- Contract: a GUI-built config (schema_overrides present) reads under
  `ExecutionProfile.LAZY_SINK` — pin with the existing `read_source` bounded-profile tests'
  fixtures (`tests/test_io.py:64,104,118,506` name the current failure strings; add the
  passing-with-overrides sibling).
- Sidecar canonicalisation: saving a config carrying `dtypes` persists `schema_overrides`
  (and a load→save round-trip converges).

## Cross-refs

- IO01 (the same `/api/schema` fetch powers the seeded rows — its error surfacing must be
  fixed for the seeding UX to fail loud properly).
- IO12: `needs_declared_schema_when_bounded` is a per-format capability — the editor should
  show the "Column types" section based on the registry's flag, not an extension literal.
- fable-Review/eda-node E04/W-series established the pattern of stat-gated re-reads for
  schema fetches; reuse rather than re-fetch on every editor open.
