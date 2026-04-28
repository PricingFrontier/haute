# Banding Node — Design Doc

## Problem

Actuarial pricing pipelines need to discretise continuous variables (e.g. driver age → age band) and group categorical variables (e.g. property type → category) before applying rating factors. This is called **banding**. A single pipeline step often bands multiple columns at once.

## Approach

A new `banding` node type that supports **multiple factors per node**, each independently configured as either continuous (range-based) or categorical (value grouping).

### Config schema

```json
{
  "factors": [
    {
      "banding": "continuous",
      "column": "driver_age",
      "outputColumn": "age_band",
      "rules": [
        { "op1": ">", "val1": 0, "op2": "<=", "val2": 25, "assignment": "0-25" }
      ],
      "default": null
    },
    {
      "banding": "categorical",
      "column": "property_type",
      "outputColumn": "prop_band",
      "rules": {
        "Semi-detached House": "House",
        "Detached House": "House"
      },
      "default": null
    }
  ]
}
```

Categorical and breakpoint sidecars use compact key/value maps. The parser
expands those maps back to the internal row-array shape before execution,
tracing, or frontend editing. Continuous rules stay as explicit objects because
they need operator and threshold fields.

### Backend

- **Banding config helpers** (`_banding_config.py`): Expands compact sidecar rule maps to the canonical row-array shape and compacts categorical/breakpoint rules when writing JSON.
- **Executor** (`executor.py`): `_normalise_banding_factors` reads the `factors` array. The handler loops over factors, calling `_apply_banding` for each. Continuous rules build a Polars `when/then/otherwise` chain. Categorical rules use `replace_strict`.
- **Parser** (`_parser_helpers.py`): Detects `banding=` or `factors=` in the decorator → infers `"banding"` node type. Always normalises to `factors: [...]` in config (permissive parsing).
- **Codegen** (`codegen.py`): Single factor → clean decorator `@pipeline.banding(banding=..., column=..., ...)`. Multiple factors → `@pipeline.banding(factors=[...])`.

### Frontend

- **NodePanel.tsx**: `BandingConfig` component with tabbed UI — one tab per factor. Each tab has type toggle (continuous/categorical), input column (dropdown when upstream schema available, with auto-detection of type from dtype), output column, rules grid, and default value.
- **NodePalette.tsx**: Default config uses `factors: [...]`.
- **Auto-detection**: When upstream columns are cached from preview, input column becomes a dropdown. Selecting a numeric column auto-sets continuous; string column auto-sets categorical.

### Decorator syntax (public API)

Single factor (clean):
```python
@pipeline.banding(banding="continuous", column="age",
                  output_column="age_band", rules=[...])
```

Multiple factors:
```python
@pipeline.banding(factors=[{"banding": "continuous", "column": "age", ...}, ...])
```

The parser accepts both; the codegen emits whichever is appropriate.

## Alternatives considered

1. **One node per factor** — simpler per-node config but clutters the graph when banding 5+ columns. Rejected.
2. **CSV-based rules** — read banding tables from files. Rejected: inline editing in the GUI is faster and keeps rules version-controlled in the `.py` file.
3. **Separate continuous/categorical node types** — rejected for same reason as (1).

## Files touched

- `src/haute/executor.py` — `_apply_banding`, `_banding_condition`, `_normalise_banding_factors`, banding handler
- `src/haute/_parser_helpers.py` — type inference, config building
- `src/haute/codegen.py` — templates + banding code generation
- `frontend/src/panels/NodePanel.tsx` — `BandingConfig`, `BandingRulesGrid`
- `frontend/src/panels/NodePalette.tsx` — palette entry
- `frontend/src/App.tsx` — node type registration
- `frontend/src/utils/nodeTypes.ts` — icon, colour, label
- `tests/test_banding.py` — 21 tests

---

# Banding Editor And Trace Improvements

## Problem

Banding rules are often copied in from spreadsheets, adjusted in-place, and then audited through preview traces. Before this improvement, the banding grids had wider spreadsheet-like spacing, could paste only whole rule rows reliably, and had no one-click copy for the whole factor. Trace output for banding-created fields also looked like a generic computed value, which hid the source value and made the lineage harder to follow.

## Approach

The banding editor keeps the existing boxed-cell visual style, but reduces cell padding and removes row divider lines so dense rule tables remain easy to scan. Continuous, categorical, and breakpoint grids support range paste starting from any editable cell; pasted header rows are ignored when they match the supported copy headers. Each factor has a copy action that exports the visible rules as TSV for spreadsheet use.

Breakpoint rows validate ordering in the editor and surface out-of-order ranges as inline warnings rather than changing runtime semantics. The warnings are local UI feedback only; execution still uses the Python config as the source of truth.

Trace enrichment now emits structured banding details for the traced output column, including the input column/value, selected band, matched rule, default status, and range bounds where applicable. The calculation payload uses compact text such as `driver_age -> age_band` and `22 -> "young"`, and recursively attaches upstream input sources so a banding output continues the same lineage chain as other calculated fields.

The frontend renders banding traces as one concise line such as `driver_age=22 -> young`. Range/default metadata appears as small secondary chips, and markdown export collapses real backend payloads into a single `Banding:` detail instead of repeating the expression, substituted value, and raw metadata separately.

## Alternatives Considered

### Reuse The Rating-Step Table Renderer

Rejected. Rating-step tables are lookup tables, while banding rules are editable range/category definitions. Sharing clipboard helpers is useful, but forcing both editors through one renderer would make the banding UI less direct.

### Keep Banding Trace As Opaque Computed Output

Rejected because it breaks the traceability requirement. Users need to see the input value that selected a band and then continue following that input's upstream source.

### Show Full Rule Conditions In The Trace Headline

Rejected because the trace panel is narrow and long condition text truncates quickly. The headline stays `input=value -> band`; bounds/default metadata is secondary.

## Open Questions

- Whether multi-factor banding traces should expose a compact factor switcher when the user traces the node rather than a specific output column.
- Whether breakpoint warning styling should eventually block saving invalid configurations or remain advisory while Python remains canonical.

## Verification

The regression coverage includes clipboard parsing/copy tests for banding grids, breakpoint ordering warnings, direct backend enrichment tests, execute-trace integration tests for banding-created fields and chained lineage, frontend calculation-panel tests, markdown export tests, and response parser contract tests for nested input sources.
