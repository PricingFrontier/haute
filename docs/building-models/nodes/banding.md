# Banding

You have a continuous value like driver age or sum insured, but your rating structure needs age bands or value brackets. The Banding node turns a column of raw values into a column of bands, using rules you define. It also works with categorical values  - grouping many fuel types into "Standard" vs "Green", for example.

!!! tip "Spreadsheet equivalent"
    This replaces nested IF statements in Excel (e.g. `=IF(age<=25, "18-25", IF(age<=65, "26-65", "65+"))`) or banding definitions in tools like Earnix or Radar.

!!! info "When to use"
    - Converting continuous values (age, mileage, sum insured) into discrete bands for your rating tables.
    - Grouping categorical values into broader categories.
    - Preparing inputs for a [Rating Step](rating-step.md) that expects banded values.

This node accepts a single input.

| Config | Description |
|---|---|
| `factors` | **Required.** List of banding factors |

Each factor has:

| Field | Description |
|---|---|
| `column` | **Required.** Input column to band |
| `outputColumn` | **Required.** Name of the new banded column |
| `banding` | **Required.** `"continuous"`, `"categorical"`, or `"breakpoints"` |
| `rules` | **Required.** Rules defining each band. Continuous rules are a list; categorical and breakpoint schema mappings use key/value maps |
| `default` | Value assigned to rows that don't match any rule |

Rules are evaluated top to bottom. The first match wins.

**Continuous rules** define ranges using operator/value pairs. Each rule can use one or both conditions. Operators: `<`, `<=`, `>`, `>=`, `=`.

This example bands driver age into three groups:

```json
{
  "factors": [{
    "banding": "continuous",
    "column": "driver_age",
    "outputColumn": "age_band",
    "rules": [
      { "op1": ">=", "val1": "18", "op2": "<=", "val2": "25", "assignment": "18-25" },
      { "op1": ">",  "val1": "25", "op2": "<=", "val2": "65", "assignment": "26-65" },
      { "op1": ">",  "val1": "65", "op2": "",   "val2": "",   "assignment": "65+" }
    ],
    "default": "Unknown"
  }]
}
```

**Categorical rules** map exact values to groups. In the schema-mapping JSON, the
source value is the key and the assigned group is the value:

```json
{
  "factors": [{
    "banding": "categorical",
    "column": "fuel_type",
    "outputColumn": "fuel_band",
    "rules": {
      "Petrol": "Standard",
      "Diesel": "Standard",
      "Electric": "Green"
    },
    "default": "Other"
  }]
}
```

**Breakpoint rules** are a concise way to define ordered numeric bands. The
boundary is the key and the assigned band is the value. Use an empty-string key
for the open-ended final band:

```json
{
  "factors": [{
    "banding": "breakpoints",
    "column": "driver_age",
    "outputColumn": "age_band",
    "rules": {
      "25": "18-25",
      "65": "26-65",
      "": "65+"
    },
    "default": "Unknown"
  }]
}
```

**Before and after:**

```
BEFORE                              AFTER
| driver_age | fuel_type |          | driver_age | fuel_type | age_band | fuel_band |
|------------|-----------|          |------------|-----------|----------|-----------|
| 22         | Petrol    |    →     | 22         | Petrol    | 18-25    | Standard  |
| 45         | Electric  |          | 45         | Electric  | 26-65    | Green     |
| 71         | Diesel    |          | 71         | Diesel    | 65+      | Standard  |
```

!!! warning "Watch for gaps"
    Rows that don't match any rule get the `default` value. Make sure your ranges don't have gaps unless you intentionally want unmatched rows to fall through to the default.
