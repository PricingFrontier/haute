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
- "Not cached · Refresh this node to read all rows" or "Cached data is out of date · Refresh this node to read all rows" until you refresh the node; "Caching the data…" and "Counting…" while that happens.
- Why there is nothing to count, when the node has no data to read yet, such as before its input is connected.
- "Couldn't read the data:" and the reason, when reading it failed, or "Cached data is out of date · Refreshing it failed:" and the reason, when a refresh failed. Refresh the node to try again.

In a workbench project the node bands the quote being priced, not a dataset, so there is no such line and none of the counts or the histogram below.

### The factor list

Each banded column is a **factor**. The list shows one row per factor: a dot (green when complete, amber when not), the factor's input column, an arrow, its output column - the name of the new banded column, or its input column until it has one - the number of rules, and a bin to remove it (when there is more than one). Hover a row for what an incomplete factor still needs: "No input column", "No output column" or "No rules yet", or that its input column is one the input no longer has ("proposer_agee is not in the input"). Click a row to edit that factor below the list; what is wrong with its output column ("No output column", or "Factor 2 also writes age_band") is also said above its settings.

- The input column is a dropdown of the input's columns: open it and type to search them, each listed with its type (hover the closed dropdown for its column's type). The input's columns are read as the node opens, so it says no columns are known only while they are being read or when the input cannot be read; a saved column the input no longer has keeps showing, its box marked. Choosing a column selects its row, sets **TYPE** to match it - **Numeric** for a number or date, otherwise **Categorical**, clearing the rules if the type changes - and names the factor's output column as the column name with `_band` added, unless you have named it yourself.
- The search box finds a factor by its output or input column; **All** and **Issues** (with the number of incomplete factors) filter the list.
- **Add banding**, under the list, adds a factor, starting as **Numeric**.
- Drag a row onto another, or use **Alt+Up** and **Alt+Down**, to reorder: the factors are applied in list order.
- The selected factor's name sits in a box: click in it, double-click any row, or press **F2** on a row, to name or rename its output column in the row: **Enter** or clicking away keeps the new name, **Escape** keeps the old one. Two factors cannot write the same column, so a name another factor already writes is refused, with the reason under the box.

### Editing a factor

| Field | What it does |
|---|---|
| **TYPE** | **Numeric** bands numbers and dates by breakpoints; **Categorical** maps exact values to groups. **Numeric** is unavailable for a column that is neither a number nor a date (its tooltip names the column's type). Switching type puts the current rules aside, and switching back restores them until the pipeline is reloaded. |
| **DEFAULT** (unmatched rows) | The band given to rows that match no rule, with the number of such rows beside it once the data is counted ("12 of 5000 rows"). Leave it empty to give them a missing value. |

With the data counted, a **Numeric** factor shows a histogram of the whole dataset with the band boundaries drawn over it.

### Numeric breakpoints

A **Numeric** factor with no breakpoints offers two ways to start:

- **Generate even bands** asks for a **Start**, **End** and **Step** and fills in evenly spaced breakpoints, each labelled by its range (such as `18–25`); the last band ends at **End**, so it may be shorter. On a date column, **Start** and **End** are dates and **Step** is a number of days, weeks, months or years. The fields start from the data's lowest and highest values when they are known, with a **Step** of about ten bands at the range's own scale (0.1 for values from 0.2 to 0.9, 1000 for 4000 to 13600). Mistakes are reported in the box, such as "End must be greater than start".
- **Add manually** adds one empty breakpoint.

After that, **BREAKPOINTS (N)** lists them, with **Generate** to replace them with generated ones (its fields start from the settings the current breakpoints imply). Each row has:

| Column | What it does |
|---|---|
| **Up to** (incl.) | The band's upper boundary. The band includes the boundary itself, so `25` covers everything up to and including 25. Leave the last row's boundary empty for an open-ended final band. |
| **Band name** | The label rows in this band get. Spaces at either end are removed as you type it, so it matches the level a Rating Step lists. |
| **Matches** | How many rows fall in the band, once the data is counted. |

- **Add** adds a breakpoint, and the bin deletes one. A breakpoint without a **Band name** is marked in red until it has one; a save is refused while one remains.
- A boundary out of order is shaded with a warning, such as "Breakpoint 3 is out of order; enter a value greater than 45."
- The copy button at the foot of the table (**Copy banding as TSV**) copies the breakpoints as tab-separated text with the headers `Up to` and `Band name`. Pasting tab-separated rows into a cell fills the rows from that cell down.

**Dates work the same way.** On a Date or Datetime column, write each boundary as a date (`YYYY-MM-DD`) or a date and time (`YYYY-MM-DD HH:MM`); every boundary in one factor must be the same kind, and the table warns about a boundary that is not a date or is of the other kind. A date covers its whole day, even on a Datetime column, so `2024-03-31` includes everything up to midnight at the end of 31 March. Times are read in the column's own time zone. This suits rules that change on a date, such as a rate change for policies starting from 1 April.

### Categorical rules

A **Categorical** factor shows the column's **Available values** as chips - every value in the data, with its row count, once the data is counted, or the values in the preview until then ("Connect data to see values" when there are none). With more than ten values, **Filter values...** narrows the chips. Click a chip to add a rule for that value; values already used are greyed.

A **Categorical** factor with no rules offers two ways to start:

- **Import from params** reads a list of values from a CSV file in the pipeline's `files/params/` folder: choose the file, then its column. Each value in that column (trimmed, blanks left out, in the file's order) becomes a rule that maps it to itself, as clicking its chip would; edit **Maps To** afterwards to group them. The values are copied in: changing the file later does not change the rules. A file or column that cannot give values says why under the choices, such as "rating/files/params/regions.csv has no column region".
- **Add manually** adds one empty rule.

After that, **RULES (N)** lists the rules, with **Import** to add a rule for each value of a params column that no rule has yet, and **Add** to add an empty one:

| Column | What it does |
|---|---|
| **Value** | A value of the input column. It is read as the column's type, as a Rating Step entry is: on a true/false column `true` and `1` are the same value, on a number column `25` and `25.0` are. A value the column cannot hold, such as `maybe` on a true/false column, is refused when the node runs, naming the factor and the value; so are two rules for the same value. |
| **Maps To** | The group that value is given. Pressing **Enter** in the last row's **Maps To** adds a new rule. |
| **Matches** | How many rows have the value, once the data is counted. |

- The bin deletes a rule.
- A rule the pipeline cannot save is marked in red, with the reason on its warning icon: a rule without a **Value** or **Maps To**, and a value an earlier rule already lists (such as "Rule 3 repeats the value "Petrol" of rule 1."). Fix or delete it before saving; a save is refused while one remains.
- **Copy banding as TSV** copies the rules with the headers `Value` and `Maps To`; pasting tab-separated rows adds them as rules.

## The COLUMNS tab

The **COLUMNS** tab chooses which columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Example

A node that bands `driver_age` and groups `fuel_type`:

1. Connect your data to a Banding node and choose `driver_age` as the first factor's input column, at the left of its row. **TYPE** switches to **Numeric** and the factor is named `driver_age_band`; double-click the name and rename it `age_band`.
2. Click **Add manually**, then **Add** twice, and fill in the breakpoints: **Up to** `25` → `18-25`, `65` → `26-65`, and an empty **Up to** → `65+`. Type `Unknown` as the **DEFAULT**.
3. Click **Add banding** to add a second factor, choose `fuel_type` as its input column (**TYPE** becomes **Categorical**) and rename it `fuel_band`.
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

    A band name is text. In a settings file written by hand, write a band named `0` as `"0"`: a number or `true`/`false` as a **Band name**, **Maps To** or **DEFAULT**, or a name with a space at either end, is refused when the pipeline is loaded, linted or run, naming the factor and the band.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/banding/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.banding(config="config/banding/<node name>.json")`. The sidecar holds `factors`, a list with one object per factor.

    | Setting in the editor | Stored as |
    |---|---|
    | **TYPE** | `factors[].banding`: `"breakpoints"` (**Numeric**) or `"categorical"` (**Categorical**) |
    | A factor's input column in the list | `factors[].column` |
    | A factor's name in the list | `factors[].output_column` |
    | **DEFAULT** | `factors[].default` (`null` when empty) |
    | **BREAKPOINTS** | `factors[].rules`: one `{"upper": ..., "label": ...}` a band, its **Up to** boundary and its **Band name**, in order, an open-ended final band with no `upper` |
    | **RULES** | `factors[].rules`: a map from each **Value** to its **Maps To** group |
    | **COLUMNS** tab | `selected_columns` |

    With no editor control: `factors[].right_closed`, for breakpoints. `true` (the default) makes each band include its upper boundary; `false` makes it include its lower one. The table header shows it as **(incl.)** or **(excl.)**, but only the sidecar can change it.

    ```json
    {
      "factors": [{
        "banding": "breakpoints",
        "column": "driver_age",
        "output_column": "age_band",
        "rules": [
          {"upper": 25, "label": "18-25"},
          {"upper": 65, "label": "26-65"},
          {"label": "65+"}
        ],
        "default": "Unknown"
      }, {
        "banding": "categorical",
        "column": "fuel_type",
        "output_column": "fuel_band",
        "rules": { "Petrol": "Standard", "Diesel": "Standard", "Electric": "Green" },
        "default": "Other"
      }]
    }
    ```

    <!-- config-keys:start -->

    Every key the config may hold:

    | Key | Type | Description |
    |---|---|---|
    | `factors` | list of object | The factors, each an input column, an output column and its bands. |
    | `factors[].banding` | `"breakpoints"` or `"categorical"` | Whether the column is banded by numeric or date boundaries or by category. |
    | `factors[].column` | text | The input column banded. |
    | `factors[].output_column` | text | The column the band is written to. |
    | `factors[].rules` | list of map of text to any JSON value or map of text to any JSON value | The bands, as rule rows; the config file writes breakpoints as a list of `upper` and `label`, and categories as a map from each value to its group. |
    | `factors[].default` | text or `null` | The band of a value no rule matches; `null` leaves it empty. |
    | `factors[].right_closed` | `true` or `false` | For breakpoints, whether each band includes its upper boundary (`true`, the default) or its lower one. |
    | `input_mapping` | map of text to text | The input names the node's code uses, each mapped to the connected input it reads. |
    | `contract` | `"opaque"` or object | The node's column contract as its decorator declares it: `"opaque"`, or the columns it reads and produces. |
    | `selected_columns` | list of text | The columns the node passes downstream, chosen on its **Columns** tab; absent passes all. |
    | `column_renames` | map of text to text | New names for columns passed downstream, from each column's name to its new one. |

    <!-- config-keys:end -->

**See also:**

- [Rating Step](rating-step.md)  - look up factors by band
- [Polars (Getting Started)](../../getting-started/polars.md#how-rating-tables-and-banding-work)  - how banding works under the hood
