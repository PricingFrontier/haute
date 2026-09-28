# Banding

You have a continuous value like driver age or sum insured, but your rating structure needs age bands or value brackets. The Banding node turns a column of raw values into a column of bands, using rules you define. It also works with categorical values  - grouping many fuel types into "Standard" vs "Green", for example.

!!! info "When to use"
    - Converting continuous values (age, mileage, sum insured) into discrete bands for your rating tables.
    - Grouping categorical values into broader categories.
    - Preparing inputs for a [Rating Step](rating-step.md) that expects banded values.

A Banding node takes a single input and outputs it with one new banded column per factor. One node can band several columns. The panel has two tabs: **CONFIG** and **COLUMNS**.

## The CONFIG tab

The **INPUT** chip at the top names the node's connection; its × removes the connection. Next to it, a line says which rows the counts and charts describe:

- **All rows ·** and the row count, when the node's input data is cached: every count below is for the whole dataset.
- "Not cached · Refresh this node to count all rows" or "Cached data is out of date · Refresh this node to count all rows" until you refresh the node; "Caching the data…" and "Counting…" while that happens.

### The factor list

Each banded column is a **factor**. The list shows one row per factor, named by its output column (or its input column until it has one), with a dot (green when complete, amber when not), the number of rules, and a bin to remove it (when there is more than one). Hover a row for what an incomplete factor still needs: "No input column", "No output column" or "No rules yet". Click a row to edit that factor below the list.

- The search box finds a factor by its output or input column; **All** and **Issues** (with the number of incomplete factors) filter the list.
- The + button beside the filter adds a factor, starting as **Numeric**.
- Drag a row onto another, or use **Alt+Up** and **Alt+Down**, to reorder: the factors are applied in list order.

### Editing a factor

| Field | What it does |
|---|---|
| **TYPE** | **Numeric** bands numbers and dates by breakpoints; **Categorical** maps exact values to groups. **Numeric** is unavailable for a column that is neither a number nor a date (its tooltip names the column's type). Switching type puts the current rules aside, and switching back restores them until the pipeline is reloaded. |
| **INPUT COLUMN** | The column to band, chosen from the input's columns (each with its type). Choosing a column sets **TYPE** to match it - **Numeric** for a number or date, otherwise **Categorical**, clearing the rules if the type changes - and fills in **OUTPUT COLUMN** as the column name with `_band` added, unless you have typed your own. |
| **OUTPUT COLUMN** | The name of the new banded column. |
| **DEFAULT** (unmatched rows) | The band given to rows that match no rule, with the number of such rows beside it once the data is counted ("12 of 5000 rows"). Leave it empty to give them a missing value. |

With the data counted, a **Numeric** factor shows a histogram of the whole dataset with the band boundaries drawn over it.

### Numeric breakpoints

A **Numeric** factor with no breakpoints offers two ways to start:

- **Generate even bands** asks for a **Start**, **End** and **Step** and fills in evenly spaced breakpoints, each labelled by its range (such as `18–25`); the last band ends at **End**, so it may be shorter. On a date column, **Start** and **End** are dates and **Step** is a number of days, weeks, months or years. The fields start from the data's lowest and highest values when they are known. Mistakes are reported in the box, such as "End must be greater than start".
- **Add manually** adds one empty breakpoint.

After that, **BREAKPOINTS (N)** lists them, with **Generate** to replace them with generated ones (its fields start from the settings the current breakpoints imply). Each row has:

| Column | What it does |
|---|---|
| **Up to** (incl.) | The band's upper boundary. The band includes the boundary itself, so `25` covers everything up to and including 25. Leave the last row's boundary empty for an open-ended final band. |
| **Band name** | The label rows in this band get. |
| **Matches** | How many rows fall in the band, once the data is counted. |

- **Add** adds a breakpoint, and the bin deletes one.
- A boundary out of order is shaded with a warning, such as "Breakpoint 3 is out of order; enter a value greater than 45."
- The copy button at the foot of the table (**Copy banding as TSV**) copies the breakpoints as tab-separated text with the headers `Up to` and `Band name`. Pasting tab-separated rows into a cell fills the rows from that cell down.

**Dates work the same way.** On a Date or Datetime column, write each boundary as a date (`YYYY-MM-DD`) or a date and time (`YYYY-MM-DD HH:MM`); every boundary in one factor must be the same kind, and the table warns about a boundary that is not a date or is of the other kind. A date covers its whole day, even on a Datetime column, so `2024-03-31` includes everything up to midnight at the end of 31 March. Times are read in the column's own time zone. This suits rules that change on a date, such as a rate change for policies starting from 1 April.

### Categorical rules

A **Categorical** factor shows the column's **Available values** as chips - every value in the data, with its row count, once the data is counted, or the values in the preview until then ("Connect data to see values" when there are none). With more than ten values, **Filter values...** narrows the chips. Click a chip to add a rule for that value; values already used are greyed.

**RULES (N)** lists the rules, with **Add** to add an empty one:

| Column | What it does |
|---|---|
| **Value** | A value of the input column, matched exactly. |
| **Maps To** | The group that value is given. Pressing **Enter** in the last row's **Maps To** adds a new rule. |
| **Matches** | How many rows have the value, once the data is counted. |

- The bin deletes a rule.
- A value listed twice is flagged, such as "Duplicate value "Petrol" in rules 1, 3".
- **Copy banding as TSV** copies the rules with the headers `Value` and `Maps To`; pasting tab-separated rows adds them as rules.

## The COLUMNS tab

The **COLUMNS** tab chooses which columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Example

A node that bands `driver_age` and groups `fuel_type`:

1. Connect your data to a Banding node and choose `driver_age` as the **INPUT COLUMN**. **TYPE** switches to **Numeric** and **OUTPUT COLUMN** becomes `driver_age_band`; change it to `age_band`.
2. Click **Add manually**, then **Add** twice, and fill in the breakpoints: **Up to** `25` → `18-25`, `65` → `26-65`, and an empty **Up to** → `65+`. Type `Unknown` as the **DEFAULT**.
3. Click the + button to add a second factor, choose `fuel_type` as the **INPUT COLUMN** (**TYPE** becomes **Categorical**) and change the **OUTPUT COLUMN** to `fuel_band`.
4. Click the chips `Petrol`, `Diesel` and `Electric` under **Available values**, and set their **Maps To** to `Standard`, `Standard` and `Green`. Type `Other` as the **DEFAULT**.

**Before and after:**

```
BEFORE                              AFTER
| driver_age | fuel_type |          | driver_age | fuel_type | age_band | fuel_band |
|------------|-----------|          |------------|-----------|----------|-----------|
| 22         | Petrol    |    →     | 22         | Petrol    | 18-25    | Standard  |
| 45         | Electric  |          | 45         | Electric  | 26-65    | Green     |
| 71         | Diesel    |          | 71         | Diesel    | 65+      | Standard  |
```

For a rate change from 1 April 2024, band a `start_date` column with **Up to** `2024-03-31` → `Before April 2024` and an empty **Up to** → `From April 2024`.

!!! warning "What falls to the default"
    Rows with a missing value get the **DEFAULT**, as do categorical values you haven't listed. Without an open-ended final breakpoint, values above the last boundary get it too.

!!! warning "Every rule needs a name"
    Saving refuses a breakpoint without a **Band name**, a categorical rule without a **Value** or **Maps To**, and a boundary or value used twice in one factor, with a message naming the rule.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/banding/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.banding(config="config/banding/<node name>.json")`. The sidecar holds `factors`, a list with one object per factor.

    | Setting in the editor | Stored as |
    |---|---|
    | **TYPE** | `factors[].banding`: `"breakpoints"` (**Numeric**) or `"categorical"` (**Categorical**) |
    | **INPUT COLUMN** | `factors[].column` |
    | **OUTPUT COLUMN** | `factors[].outputColumn` |
    | **DEFAULT** | `factors[].default` (`null` when empty) |
    | **BREAKPOINTS** | `factors[].rules`: a map from each **Up to** boundary to its **Band name**, in order, with `""` as the key of an open-ended final band |
    | **RULES** | `factors[].rules`: a map from each **Value** to its **Maps To** group |
    | **COLUMNS** tab | `selected_columns` |

    With no editor control: `factors[].rightClosed`, for breakpoints. `true` (the default) makes each band include its upper boundary; `false` makes it include its lower one. The table header shows it as **(incl.)** or **(excl.)**, but only the sidecar can change it.

    ```json
    {
      "factors": [{
        "banding": "breakpoints",
        "column": "driver_age",
        "outputColumn": "age_band",
        "rules": { "25": "18-25", "65": "26-65", "": "65+" },
        "default": "Unknown"
      }, {
        "banding": "categorical",
        "column": "fuel_type",
        "outputColumn": "fuel_band",
        "rules": { "Petrol": "Standard", "Diesel": "Standard", "Electric": "Green" },
        "default": "Other"
      }]
    }
    ```

**See also:**

- [Rating Step](rating-step.md)  - look up factors by band
- [Polars (Getting Started)](../../getting-started/polars.md#how-rating-tables-and-banding-work)  - how banding works under the hood
