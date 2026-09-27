# Rating Step

You have a set of rating factors  - area, age band, NCD level  - and a table of relativities for each. The Rating Step looks up the right factor for each row and combines them into a single multiplier (or sum). This is how you build a traditional multiplicative or additive rating structure.

!!! tip "Spreadsheet equivalent"
    Like VLOOKUP or INDEX/MATCH in Excel, but it handles multi-dimensional lookups and combines the results automatically.

!!! info "When to use"
    - Building a traditional multiplicative or additive rating structure.
    - Recreating factor tables from a spreadsheet or another rating tool.
    - Looking up relativities based on one, two, or three dimensions.
    - Use [Banding](banding.md) first if your tables expect banded inputs rather than raw values.

This node accepts a single input.

| Config | Description |
|---|---|
| `tables` | **Required.** List of rating tables |
| `combinedOutputs` | Columns that combine every table's output. Each has an `outputColumn`, an `operation`  - `"multiply"`, `"add"`, `"min"`, or `"max"`  - and a numeric `baseValue` it starts from. If omitted, the individual factor columns are still created but no combined column is produced. |
| `steps` | Optional post-rating steps built on the node's **Polars** tab (see [In the editor](#in-the-editor)). |
| `code` | The Polars code the steps generate, or your own code after **Switch to code**. |

Each table has:

| Field | Description |
|---|---|
| `factors` | **Required.** Input columns to match on (up to 3 for multi-way lookups) |
| `outputColumn` | **Required.** Column name for this table's looked-up value. Errors and logs identify the table by it. |
| `defaultValue` | Value used when the input doesn't match any entry in the table (e.g. an area code you haven't mapped). A table added in the editor starts with `1.0`. |
| `onMissing` | What to do when a lookup misses and no usable `defaultValue` is set: `"error"` (default) fails the run with the table's output column, the missing keys and the affected row count; `"neutral"` leaves the table output null — combined outputs treat it as the operation's neutral element (×1.0 / +0.0) — and logs a warning with the miss count. The editor has no control for it: set it by hand in the sidecar JSON, and note that any edit to the node's tables in the editor removes it. |
| `entries` | **Required.** The factor table: one row per level, holding a value for each factor and the looked-up `value`. |

!!! warning "What happens on a miss"
    A row whose factor value has no entry in the table is an unpriceable
    row.  A table with a usable numeric `defaultValue` prices it at that
    value without an error. Tables built in the editor start with
    **Default** `1.0`, so a miss is priced at `1.0` unless you clear
    **Default**. Editing a table in the editor also gives it Default `1.0`
    when its sidecar JSON has no `defaultValue`.

    Without a usable `defaultValue`, the run fails with a
    `RatingTableMissError` naming the table's output column and the missing
    keys — a renamed band label cannot silently price at base rate.  Set
    `"onMissing": "neutral"` only if you explicitly want misses to
    contribute nothing to the combined output; they are still counted and
    logged.

A one-way table maps a single column. A two-way table maps two columns. In the sidecar JSON, a one-way area factor and a one-way age factor, multiplied together, look like this:

```json
{
  "tables": [
    {
      "factors": ["area"],
      "outputColumn": "area_factor",
      "defaultValue": 1.0,
      "entries": [
        { "area": "London", "value": 1.25 },
        { "area": "Manchester", "value": 1.10 },
        { "area": "Rural", "value": 0.85 }
      ]
    },
    {
      "factors": ["age_band"],
      "outputColumn": "age_factor",
      "defaultValue": 1.0,
      "entries": [
        { "age_band": "18-25", "value": 1.40 },
        { "age_band": "26-65", "value": 1.00 },
        { "age_band": "65+", "value": 1.15 }
      ]
    }
  ],
  "combinedOutputs": [
    { "outputColumn": "location_age_factor", "operation": "multiply", "baseValue": 1.0 }
  ]
}
```

A two- or three-way table has one row per combination, with a key for each factor. In the editor, a three-way table shows its third factor as the outer dropdown, the second as the column group, and the first as the row key.

**Before and after:**

```
BEFORE                                AFTER
| area       | age_band |            | area   | age_band | area_factor | age_factor | location_age_factor |
|------------|----------|            |--------|----------|-------------|------------|---------------------|
| London     | 18-25    |      →     | London | 18-25    | 1.25        | 1.40       | 1.75                |
| Rural      | 26-65    |            | Rural  | 26-65    | 0.85        | 1.00       | 0.85                |
| Manchester | 65+      |            | Man... | 65+      | 1.10        | 1.15       | 1.265               |
```

!!! warning "How entries match"
    Each entry's factor values are first converted to the type of the input
    column, then matched. On a numeric column the value counts, not how it
    was typed: on a float column, entries `25`, `"25"` and `"25.0"` are the
    same key and all match `25.0`. An entry that cannot be converted to the
    column's type fails the run rather than silently never matching, for
    example `"25.0"` on an integer column or a malformed date on a date column.

    On a text column, entries are matched exactly as written, and matching is
    case-sensitive: if your data has `"London"` but your table has `"london"`,
    it won't match. Use a [Polars](polars.md) node upstream to normalise casing if needed.

## In the editor

The editor has two sections:

- **Tables**: one tab per rating table. Pick up to three **Factors** from the columns with known levels (the outputs of an upstream [Banding](banding.md) node, or text columns of the input), set the **Output Column** and the **Default**, and fill in the values. The editor lays the entries out from the factors' levels; **Rebuild from factor levels** rebuilds them when the levels change.
- **Combined**: the combined outputs, each with a **Combined Output Column**, an **Operation** and a **Base Value**.

The node's **Polars** tab adds steps that run after the rating, on the rated frame `df`, with the same step builder as a [Polars](polars.md) node. Add a **Free code** step, or click **Switch to code** to replace the steps with their code and edit it directly (this is one-way). Code assigns its result back to `df`.
