# Rating Step Design

## Problem

Pricing teams need to maintain rating tables that are easy to inspect, easy to edit in bulk, and still saved as plain Python-backed configuration. A rating step may apply one table, dozens of tables, optional combined outputs, and optional custom Polars post-processing. Analysts also need to use raw categorical/string columns directly, not only columns produced by a banding step.

The rating step therefore has to support:

- one-, two-, and three-factor lookup tables;
- factors from upstream banding outputs and raw upstream string/categorical columns;
- Excel-style copy and paste for table values;
- a navigable UI when many tables exist;
- optional combined outputs with explicit base values;
- optional Polars code that runs after table and combined outputs exist.

## Approach

The rating step is split into three UI sections: **Tables**, **Combined**, and **Code**. The section selector is local UI state only; it is not persisted in node config.

Execution order is fixed:

1. Apply each configured rating table and create its output column.
2. Apply each configured combined output, if any.
3. Run custom Polars code, if present.

This means code in `rating/main.py` can reference columns produced by both the table stage and the combined stage.

## Config Schema

```json
{
  "tables": [
    {
      "name": "age_factor",
      "factors": ["age_band"],
      "outputColumn": "age_factor",
      "defaultValue": "1.0",
      "entries": [
        { "age_band": "young", "value": 1.2 },
        { "age_band": "older", "value": 0.9 }
      ]
    }
  ],
  "combinedOutputs": [
    {
      "outputColumn": "technical_premium",
      "operation": "multiply",
      "baseValue": 100
    }
  ],
  "code": "df = df.with_columns(...)"
}
```

Key points:

- `tables` is the canonical table list.
- `outputColumn` is also the display name for a table; a separate editable table name is not needed.
- `factors` contains one to three column names.
- `entries` stays flat: one key per factor plus `value`.
- `defaultValue` is used when a table lookup misses.
- `combinedOutputs` is optional. An empty or missing list means no combined output.
- `code` is optional and runs last.

Legacy `combinedColumn` and `operation` configs are still read by execution/codegen paths where needed, but the current UI writes `combinedOutputs` for new combined definitions.

## Factor Levels

The UI builds factor-level options from three sources:

1. Banding nodes: `config.factors[].outputColumn` and each rule assignment.
2. Preview rows: upstream columns with string/categorical-like dtypes and finite string values.
3. Saved rating table entries: existing factor values already persisted in the node config.

Banding levels take precedence. Raw string levels are used for unbanded fields such as `channel` or `cover_type`; they come from preview rows and saved table entries. Values not listed in a table use the table default.

Raw level editing controls are intentionally not shown for unbanded fields. The source of truth is the preview data plus the saved table entries.

## Tables UI

The **Tables** section uses a selector suited to large numbers of tables:

- a search box filters by output column or factor name;
- an issues filter shows only tables with validation problems;
- each row has a green/yellow status marker, factor count, and entry count;
- selecting a table opens only that table's editor;
- adding a table clears search/filter state and selects the new table.

Validation is visible in the editor. Blank output columns are flagged, duplicate output columns are flagged, and incomplete table setup is summarised for the selected table.

The table editor variants are:

- one factor: two-column table of level and value;
- two factors: grid with row and column labels;
- three factors: selector for the third factor's slice, then a two-factor grid.

Editable cells use neutral Excel-style borders rather than value-based heatmap colouring. Labels are visually distinct from editable cells. Users can paste numeric matrices from Excel, paste labelled tables where row/column order differs, drag-select multiple cells, copy selected values as TSV, and copy the visible two-way table via the icon-only copy action below the grid.

Clipboard failures are logged with context rather than being swallowed.

## Combined UI

The **Combined** section is optional. A rating step can have no combined outputs.

When combined outputs are configured, the UI uses a selector similar to banding nodes:

- each combined output has an output column, operation, and base value;
- multiple combined outputs can be configured;
- each output gets a green/yellow status marker;
- output columns must be unique across tables and combined outputs;
- base value is required for non-legacy combined outputs.

Supported operations are:

- `multiply`: base value multiplied by all table outputs;
- `add`: base value plus all table outputs;
- `min`: minimum of base value and table outputs;
- `max`: maximum of base value and table outputs.

The backend validates new `combinedOutputs` strictly. Unsupported operations, blank output columns, duplicate output columns, missing base values, and non-finite base values raise errors instead of silently falling back.

## Code UI

The **Code** section exposes an optional Polars code editor. The code editor receives available columns from:

- upstream columns;
- rating table output columns;
- combined output columns.

The code should use `df` for the rated data. Since code runs after table and combined outputs, it can reference any column created earlier in the same rating step.

## Backend

For each table:

1. Build a Polars lookup frame from `entries`.
2. Cast `value` to `Float64`.
3. Reject NaN/Inf values.
4. Deduplicate lookup rows on the factor columns, keeping the last entry.
5. Left join the lookup frame onto the input frame.
6. Fill misses with `defaultValue` when a numeric default is configured.
7. Emit the table's `outputColumn`.

Combined outputs are then applied over the table output columns. Custom code is extracted/generated after `apply_rating_step_from_config(...)` so it observes the table and combined columns.

## Tests

The rating step test coverage is intentionally layered:

- backend tests cover table lookup behaviour, combined output validation, codegen, parsing, config loading, and code-after-combined execution;
- frontend utility tests cover table normalisation, factor-level extraction, cartesian table rebuilding, status calculation, and malformed config shapes;
- editor tests cover section navigation, output column validation, unbanded raw string factors, large table selectors, combined output selection, and code editor columns;
- grid tests cover neutral editable-cell styling, paste semantics, drag selection, selected-range copy, visible-table copy, and clipboard rejection logging;
- the frontend critical coverage gate requires full statement/function/line coverage on `ratingTableUtils.ts` and high branch coverage.

## Alternatives Considered

### Single Long Editor

Rejected because it becomes difficult to navigate once a rating step has many tables. The section selector keeps table editing, combined output setup, and code editing distinct.

### Only Allow Banded Factors

Rejected because analysts need to rate directly on raw string columns such as `channel`. The UI now merges banded levels with preview-derived and saved-entry levels.

### Persist The Active UI Section

Rejected because the selected UI section is not execution configuration. Persisting it creates stale config and transition fields.

### Heatmap Formatting In Editable Cells

Rejected for the current table editor because the user's workflow is closer to spreadsheet editing. Neutral editable cells reduce visual noise and make labels vs editable values clearer.

### Separate Node Per Rating Table

Rejected because a production rating structure can have many factors. Keeping related tables inside one rating step keeps the graph manageable.

### Store 2D/3D Arrays

Rejected because flat entries are easier to diff, parse, generate, and execute uniformly.

## Open Questions

- Very large two-way grids may eventually need virtualised rendering.
- A visible toast for failed copy actions may be useful in addition to the current logged warning.
- Malformed persisted table entries are currently normalised so the editor remains usable; a dedicated malformed-config banner would make that recovery path more explicit.
