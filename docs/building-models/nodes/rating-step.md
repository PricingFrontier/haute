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
| `operation` | **Required.** How to combine factors across tables: `"multiply"`, `"add"`, `"min"`, or `"max"` |
| `combinedColumn` | Name of the column containing the combined result. If omitted, individual factor columns are still created but no combined column is produced. |

Each table has:

| Field | Description |
|---|---|
| `name` | **Required.** Table name |
| `factors` | **Required.** Input columns to match on (up to 3 for multi-way lookups) |
| `outputColumn` | **Required.** Column name for this table's looked-up value |
| `defaultValue` | Value used when the input doesn't match any entry in the table (e.g. an area code you haven't mapped) |
| `entries` | **Required.** The factor table. In JSON sidecars, factor values are nested keys and the leaf is the looked-up value. |

A one-way table maps a single column. A two-way table maps two columns. In the sidecar JSON, a one-way area factor and a one-way age factor look like this:

```json
{
  "tables": [
    {
      "name": "Area Factor",
      "factors": ["area"],
      "outputColumn": "area_factor",
      "defaultValue": "1.0",
      "entries": {
        "London": 1.25,
        "Manchester": 1.10,
        "Rural": 0.85
      }
    },
    {
      "name": "Age Factor",
      "factors": ["age_band"],
      "outputColumn": "age_factor",
      "defaultValue": "1.0",
      "entries": {
        "18-25": 1.40,
        "26-65": 1.00,
        "65+": 1.15
      }
    }
  ],
  "operation": "multiply",
  "combinedColumn": "location_age_factor"
}
```

For two-way tables, each factor adds one nesting level in the order listed in `factors`. For three-way tables, the sidecar matches the editor: the third factor is the outer dropdown, the second factor is the column group, and the first factor is the row key. With `factors` set to `["vehicle_age_band", "cover_type", "channel"]`, entries nest as `channel -> cover_type -> vehicle_age_band -> value`. When Haute loads the sidecar, it expands these maps back into row entries with one key per factor plus `value` for the editor, execution, and trace.

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
