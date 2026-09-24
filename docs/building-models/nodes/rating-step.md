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

Each table has:

| Field | Description |
|---|---|
| `name` | **Required.** Table name |
| `factors` | **Required.** Input columns to match on (up to 3 for multi-way lookups) |
| `outputColumn` | **Required.** Column name for this table's looked-up value |
| `defaultValue` | Value used when the input doesn't match any entry in the table (e.g. an area code you haven't mapped) |
| `onMissing` | What to do when a lookup misses and no usable `defaultValue` is set: `"error"` (default) fails the run with the table name, the missing keys and the affected row count; `"neutral"` leaves the table output null — combined outputs treat it as the operation's neutral element (×1.0 / +0.0) — and logs a warning with the miss count. |
| `entries` | **Required.** The factor table: one row per level, holding a value for each factor and the looked-up `value`. |

!!! warning "Misses fail loudly by default"
    A row whose factor value has no entry in the table is an unpriceable
    row.  Unless the table has a numeric `defaultValue`, the run fails with
    a `RatingTableMissError` naming the table and the missing keys — a
    renamed band label cannot silently price at base rate.  Set
    `"onMissing": "neutral"` only if you explicitly want misses to
    contribute nothing to the combined output; they are still counted and
    logged.

A one-way table maps a single column. A two-way table maps two columns. In the sidecar JSON, a one-way area factor and a one-way age factor, multiplied together, look like this:

```json
{
  "tables": [
    {
      "name": "Area Factor",
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
      "name": "Age Factor",
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

!!! warning "String matching"
    Factor values are matched as strings. If your data has `"London"` but your table has `"london"`, it won't match. Use a [Polars](polars.md) node upstream to normalise casing if needed.

    Numeric values are canonicalised before matching: an int-like float
    column value `25.0` matches the table key `"25"` (and a numeric entry
    key `25.0` matches an integer column value `25`).  String keys are
    taken verbatim — a key typed as `"25.0"` is a label and only matches
    the literal string `"25.0"`.
