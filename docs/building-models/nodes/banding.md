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
| `banding` | **Required.** `"breakpoints"` (numeric bands) or `"categorical"` |
| `rules` | **Required.** Rules defining each band. In the JSON sidecar they are a key/value map |
| `default` | Value assigned to rows that don't match any rule |
| `rightClosed` | Breakpoints only. `true` (the default) makes each band include its upper boundary; `false` makes it include its lower one |

**Breakpoint rules** define ordered numeric bands. Each key is the upper
boundary of a band and its value is the band's name; use an empty-string key for
the open-ended final band. Bands are closed on the right by default, so `25`
covers everything up to and including 25:

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

**Dates work the same way.** On a Date or Datetime column, write each boundary
as a date (`YYYY-MM-DD`) or a date and time (`YYYY-MM-DD HH:MM`); every boundary
in one factor must be the same kind. A date covers its whole day, even on a
Datetime column, so `"2024-03-31"` includes everything up to midnight at the end
of 31 March. Times are read in the column's own time zone. This suits rules that
change on a date, such as a rate change for policies starting from 1 April:

```json
{
  "factors": [{
    "banding": "breakpoints",
    "column": "start_date",
    "outputColumn": "rate_period",
    "rules": {
      "2024-03-31": "Before April 2024",
      "": "From April 2024"
    },
    "default": "Unknown"
  }]
}
```

**Categorical rules** map exact values to groups. In the JSON sidecar, the
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

**Before and after:**

```
BEFORE                              AFTER
| driver_age | fuel_type |          | driver_age | fuel_type | age_band | fuel_band |
|------------|-----------|          |------------|-----------|----------|-----------|
| 22         | Petrol    |    →     | 22         | Petrol    | 18-25    | Standard  |
| 45         | Electric  |          | 45         | Electric  | 26-65    | Green     |
| 71         | Diesel    |          | 71         | Diesel    | 65+      | Standard  |
```

!!! warning "What falls to the default"
    Rows with a missing value get the `default`, as do categorical values you haven't listed. Without an open-ended final breakpoint, values above the last boundary get it too.
