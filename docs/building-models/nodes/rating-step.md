# Rating Step

You have a set of rating factors  - area, age band, NCD level  - and a table of relativities for each. The Rating Step looks up the right factor for each row and combines them into a single multiplier (or sum). This is how you build a traditional multiplicative or additive rating structure.

!!! info "When to use"
    - Building a traditional multiplicative or additive rating structure.
    - Recreating factor tables from another rating tool.
    - Looking up relativities based on one, two, or three dimensions.
    - Use [Banding](banding.md) first if your tables expect banded inputs rather than raw values.

A Rating Step takes a single input and outputs it with one new column per rating table, plus any combined columns. The panel has four tabs: **TABLES**, **COMBINED**, **TRANSFORM** and **COLUMNS**.

## The TABLES tab

The **INPUT** chip at the top names the node's connection; its × removes the connection. When a table rates on a column of the input, a line says where the column's levels come from: **Levels from all rows ·** and the row count once the node's input data is cached, **Levels from a sample ·** and the number of preview rows until then, or "Cached data is out of date".

If a Banding node's output has no labelled rule, a warning names it: "Banding outputs … have no valid levels. Add a labelled Banding rule before using them as rating factors."

The list shows one row per table, named by its output column (the column the looked-up value goes into, which errors and logs identify the table by), with a dot (green when healthy, amber when not), the number of factors and the number of entries, and a bin to remove it (when there is more than one). Hover a row for its problems, such as "Output column is required", "Output column name must be unique", "Add at least one factor" or "Add at least one rating entry"; the selected table's problems are also listed above its factors.

- The search box finds a table by its output column or factors; **All** and **Issues** filter the list.
- **Add table**, under the list, adds a table with no output column, on **ON MISS** **Leave empty**.
- Drag a row onto another, or use **Alt+Up** and **Alt+Down**, to reorder: the tables' output columns come out in list order.
- The selected table's name sits in a box: click in it, double-click any row, or press **F2** on a row, to name or rename its output column in the row: **Enter** or clicking away keeps the new name, **Escape** keeps the old one. A name another table has is taken, and both are marked.

Click a table in the list to edit it below:

| Field | What it does |
|---|---|
| **FACTORS (N/3)** | The columns the table looks up, up to three, side by side in order on one line, each a third of its width however many there are. With none yet, choose the first from **Select factor...**; after that **Add factor**, beside the factors, adds another, and the × beside a factor removes it. Each is a dropdown of the columns with known levels, each listed with its number of levels, that you can type in to search: the band names of every Banding node's outputs, and the text columns of the input (each leaves out the table's other factors). A factor shows its name alone, and hovering it gives its name and number of levels. A saved factor that is no longer among them keeps showing, its box marked. A column named `value` cannot be a factor, since each entry keeps its relativity under `value`: rename it upstream, in a Transform node for example. Adding or removing a factor rebuilds the entries for every combination of levels, keeping the values already entered. |

Under the factors, the table itself:

- **One factor**: one row per level, with its **RELATIVITY**.
- **Two factors**: a grid, with the first factor's levels as rows and the second's as columns. **Copy visible table as TSV**, at the right of the line under the grid, copies it; pasting tab-separated values into a cell fills the grid from that cell.
- **Three factors**: a list for the third factor chooses which level's grid to show; the grid is the first factor by the second, as for two factors.

Type a relativity into a cell; it is saved when you leave the cell, and an emptied cell takes the table's **DEFAULT** (`1` when **DEFAULT** is empty). A value that is not a number, such as `1,5` (write `1.5`), is not saved: the cell is marked in red and keeps what you typed until you correct it. Drag across cells to select several, and **Ctrl+C** copies their values. A line under the table gives the number of rates and their lowest, average and highest.

A factor with many levels can make a table too large to edit: past 20,000 cells the editor says how big it would be, for example "area (2000) × vehicle_model (400) would be 800,000 cells - too many to edit here. Band the column first, or rate it in a table of its own."

Under the table, **↻ Rebuild from factor levels** rebuilds its entries from the factors' current levels - after a Banding node gains a band, for example - keeping the values already entered. It is greyed out while the table is too large to edit.

Below that, what a row whose factor values have no entry in it (e.g. an area code you haven't mapped) gets:

| Field | What it does |
|---|---|
| **ON MISS** | **Leave empty** (a new table's choice) leaves the table's output empty for that row and logs a warning with the number of misses; a combined output treats the empty value as having no effect. **Use default** gives the row the table's **DEFAULT**. A miss never stops the run (see [What happens on a miss](#what-happens-on-a-miss)). |
| **DEFAULT** | The value a missed row takes on **Use default**: choosing **Use default** sets it to `1.0`, and you can change it. It is greyed out on **Leave empty**, and clearing it switches **ON MISS** back to **Leave empty**. |

A band's **DEFAULT** (unmatched rows) on the Banding node is not a level of the table, so rows that fall to it follow the table's **ON MISS**.

## The COMBINED tab

Each combined output is a tab, named by its column (with a dot, green when complete); the + button after the tabs adds one, named `combined_1`, `combined_2` and so on, and the × on a tab removes it. With none, the tab says "No combined output".

| Field | What it does |
|---|---|
| **COMBINED OUTPUT COLUMN** | The name of the combined column. It is required and must differ from every table's output column and every other combined column. |
| **OPERATION** | How the tables' values are combined: **× Multiply**, **+ Add**, **Min** or **Max**. |
| **BASE VALUE** | The value the combination starts from. Choosing an operation sets it to `1.0` (or `0.0` for **+ Add**); change it if you need to. |

A line under the fields shows the formula, such as `location_age_factor = 1.0 × area_factor × age_factor`. Every table takes part. With no combined output, the individual factor columns are still created but no combined column is produced.

## The TRANSFORM tab

See [Transform](transform.md#building-the-node-from-steps).

## The COLUMNS tab

The **COLUMNS** tab chooses which columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Example

An area factor and an age factor, multiplied together, where `age_band` comes from an upstream Banding node:

1. Connect your data to a Rating Step. Double-click the first table in the list and name it `area_factor`, then choose `area` under **Select factor...**. Leave **ON MISS** on **Leave empty**, so an area the table does not list leaves `area_factor` empty and is logged.
2. Fill in the relativities: London `1.25`, Manchester `1.10`, Rural `0.85`.
3. Click **Add table** to add a second table, name it `age_factor` in the list, choose `age_band` as its factor, and fill in `18-25` → `1.40`, `26-65` → `1.00`, `65+` → `1.15`.
4. Open the **COMBINED** tab, click the + button, and change the **COMBINED OUTPUT COLUMN** to `location_age_factor`. Leave **OPERATION** on **× Multiply** and **BASE VALUE** at `1.0`.

**Before and after:**

```
BEFORE                                AFTER
| area       | age_band |            | area   | age_band | area_factor | age_factor | location_age_factor |
|------------|----------|            |--------|----------|-------------|------------|---------------------|
| London     | 18-25    |      →     | London | 18-25    | 1.25        | 1.40       | 1.75                |
| Rural      | 26-65    |            | Rural  | 26-65    | 0.85        | 1.00       | 0.85                |
| Manchester | 65+      |            | Man... | 65+      | 1.10        | 1.15       | 1.265               |
```

## What happens on a miss

A row whose factor value has no entry in the table is a miss. A miss never stops the run - a live quote is still priced - and it is never silent:

- On **Leave empty** (where a new table starts), the table's output is empty for that row, and the run logs a warning naming the table's output column, the number of rows missed and the missing keys - so a renamed band label shows up rather than pricing at base rate unnoticed. A combined output gives the empty value no effect (×1 for **× Multiply**, +0 for **+ Add**; **Min** and **Max** skip it).
- On **Use default**, the row takes the table's **DEFAULT**, with no warning.

A **DEFAULT** that is not a number, such as one typed into the pipeline file by hand, is refused when the pipeline is loaded or saved, naming the table, rather than being ignored.

!!! warning "How entries match"
    Each entry's factor values are first converted to the type of the input
    column, then matched. On a numeric column the value counts, not how it
    was typed: on a float column, entries `25`, `"25"` and `"25.0"` are the
    same key and all match `25.0`. An entry that cannot be converted to the
    column's type fails the run rather than silently never matching, for
    example `"25.0"` on an integer column or a malformed date on a date column.
    Two entries that become the same key are refused, naming the table and the
    levels, rather than one of them silently winning.

    On a text column, entries are matched exactly as written, and matching is
    case-sensitive: if your data has `"London"` but your table has `"london"`,
    it won't match. Use a [Transform](transform.md) node upstream to normalise casing if needed.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/rating_step/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.rating_step(config="config/rating_step/<node name>.json")`. Steps on the **TRANSFORM** tab are stored in the sidecar as `steps`, and the code they generate is the body of the node's function, which takes the rated data as `df`; code the other steps cannot hold goes in a **Free code** step.

    | Setting in the editor | Stored as |
    |---|---|
    | The tables | `tables`: a list with one object per table |
    | **FACTORS** | `tables[].factors`: the input columns to match on, at most 3 |
    | A table's name in the list | `tables[].output_column` |
    | **ON MISS** and **DEFAULT** | `tables[].default_value`: a number such as `1.0` on **Use default**, absent on **Leave empty** |
    | The relativities | a grid: with one factor, `tables[].rows` holds a line per level, the level and its relativity; with two or three, `tables[].columns` lists the last factor's levels and each line of `tables[].rows` holds the other factors' levels, then a relativity per column, `null` where there is no entry |
    | The combined outputs | `combined_outputs`: a list with one object per combined output |
    | **COMBINED OUTPUT COLUMN** | `combined_outputs[].output_column` |
    | **OPERATION** | `combined_outputs[].operation`: `"multiply"`, `"add"`, `"min"` or `"max"` |
    | **BASE VALUE** | `combined_outputs[].base_value`, as text such as `"1.0"` |
    | **TRANSFORM** tab | the function body, which Haute reads the steps back from |
    | **COLUMNS** tab | `selected_columns` |

    With no editor control:

    - `tables[].factor_dtypes`: the data types of the factor columns, which Haute records.

    Without a `default_value`, a combined output treats the table's empty output as the operation's neutral element (×1.0 / +0.0).

    A one-way area factor, as stored:

    ```json
    {
      "factors": ["area"],
      "output_column": "area_factor",
      "default_value": "1.0",
      "rows": [
        ["London", 1.25],
        ["Manchester", 1.1],
        ["Rural", 0.85]
      ]
    }
    ```

    <!-- config-keys:start -->

    Every key the config may hold:

    | Key | Type | Description |
    |---|---|---|
    | `code` | text | The Polars code the node's function runs; when `steps` are present, their rendering. |
    | `steps` | list of map of text to any JSON value | The low-code steps, in order, rebuilt from the node's code each time it loads; the config file does not hold them. |
    | `tables` | list of object | The rating tables. |
    | `tables[].factors` | list of text | The factor columns the table is keyed by, at most three. |
    | `tables[].factor_dtypes` | map of text to map of text to any JSON value | For each factor, the type its levels are read as. |
    | `tables[].output_column` | text | The column the looked-up value is written to. |
    | `tables[].default_value` | text or number or `null` | The value every factor combination the table does not hold takes; absent or `null` leaves such a combination empty. |
    | `tables[].entries` | list of map of text to any JSON value | The table's rows, each a level of every factor and its `value`; the config file writes them as a grid of `columns` and `rows`. |
    | `combined_outputs` | list of object | Columns combined from the tables' values. |
    | `combined_outputs[].output_column` | text | The combined column. |
    | `combined_outputs[].operation` | `"multiply"` or `"add"` or `"min"` or `"max"` | How the tables' values are combined. |
    | `combined_outputs[].base_value` | text or number | The value the combination starts from: a number, or the editor's text of one. |
    | `input_mapping` | map of text to text | The input names the node's code uses, each mapped to the connected input it reads. |
    | `contract` | `"opaque"` or object | The node's column contract as its decorator declares it: `"opaque"`, or the columns it reads and produces. |
    | `selected_columns` | list of text | The columns the node passes downstream, chosen on its **Columns** tab; absent passes all. |
    | `column_renames` | map of text to text | New names for columns passed downstream, from each column's name to its new one. |

    <!-- config-keys:end -->

**See also:**

- [Banding](banding.md)  - create the bands the tables look up
- [Polars (Getting Started)](../../getting-started/polars.md#how-rating-tables-and-banding-work)  - how rating tables work under the hood
