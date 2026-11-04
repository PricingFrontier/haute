# Rating Step

You have a set of rating factors  - area, age band, NCD level  - and a table of relativities for each. The Rating Step looks up the right factor for each row and combines them into a single multiplier (or sum). This is how you build a traditional multiplicative or additive rating structure.

!!! info "When to use"
    - Building a traditional multiplicative or additive rating structure.
    - Recreating factor tables from another rating tool.
    - Looking up relativities based on one, two, or three dimensions.
    - Use [Banding](banding.md) first if your tables expect banded inputs rather than raw values.

A Rating Step takes a single input and outputs it with one new column per rating table, plus any combined columns. The panel has three tabs: **CONFIG**, **TRANSFORM** and **COLUMNS**.

## The CONFIG tab

The **INPUT** chip at the top names the node's connection; its × removes the connection. When a table rates on a column of the input, a line says where the column's levels come from: **Levels from all rows ·** and the row count once the node's input data is cached, **Levels from a sample ·** and the number of preview rows until then, or "Cached data is out of date".

If a Banding node's output has no labelled rule, a warning names it: "Banding outputs … have no valid levels. Add a labelled Banding rule before using them as rating factors."

**RATING SECTION** switches the tab between **Tables**, the rating tables, and **Combined**, the columns that combine them (it reads **Combined set** once a combined output is named). The first time you open a node that already has combined outputs, the tab opens on **Combined**; after that it remembers your choice.

### Tables

A banner counts the tables ("Rating Tables · 2 tables"). The list shows one row per table, named by its output column, with a dot (green when healthy, amber when not), the number of factors (`2f`) and the number of entries, and a bin to remove it (when there is more than one). Hover a row for its problems, such as "Output column is required", "Add at least one factor" or "Add at least one rating entry".

- The search box finds a table by its output column or factors; **All** and **Issues** filter the list.
- The + button beside the filter adds a table, with an empty **DEFAULT**, so a level it does not list stops the run.

Click a table in the list to edit it below:

| Field | What it does |
|---|---|
| **FACTORS (N/3)** | The columns the table looks up, up to three. **+ Add factor...** adds one, and the × beside a factor removes it. The lists offer the columns with known levels, each with its number of levels: the band names of every Banding node's outputs, and the text columns of the input. A factor that is not among them shows as "(not a banding factor)". A column named `value` cannot be a factor, since each entry keeps its relativity under `value`: rename it upstream, in a Transform node for example. Adding or removing a factor rebuilds the entries for every combination of levels, keeping the values already entered. |
| **OUTPUT COLUMN** | The name of the column the looked-up value goes into. Errors and logs identify the table by it. It is required and must be unique: the box turns red with "Output column is required" or "Output column name must be unique". |
| **DEFAULT** | The value given to a row whose factor values have no entry in the table (e.g. an area code you haven't mapped). A new table starts empty, so such a row is handled by **ON MISS** (see [What happens on a miss](#what-happens-on-a-miss)). Type a value such as `1.0` to price every miss at it. |
| **ON MISS** | What a row with no entry does while **DEFAULT** is empty: **Stop the run** (the default) or **Leave empty**, which leaves the table's output empty and logs a warning with the number of misses. It is greyed out while **DEFAULT** holds a value, which fills every miss. |
| **↻ Rebuild from factor levels** | Rebuilds the entries from the factors' current levels - after a Banding node gains a band, for example - keeping the values already entered. |

Under the fields, the table itself:

- **One factor**: one row per level, with its **RELATIVITY** and a bar showing its size.
- **Two factors**: a grid, with the first factor's levels as rows and the second's as columns. **Copy visible table as TSV** copies it; pasting tab-separated values into a cell fills the grid from that cell.
- **Three factors**: a list for the third factor chooses which level's grid to show; the grid is the first factor by the second, as for two factors.

Type a relativity into a cell; it is saved when you leave the cell, and an emptied cell takes the table's **DEFAULT** (`1` when **DEFAULT** is empty). A value that is not a number, such as `1,5` (write `1.5`), is not saved: the cell is marked in red and keeps what you typed until you correct it. Drag across cells to select several, and **Ctrl+C** copies their values. A line under the table gives the number of entries and the range of values.

A factor with many levels can make a table too large to edit: past 20,000 cells the editor says how big it would be, for example "area (2000) × vehicle_model (400) would be 800,000 cells - too many to edit here. Band the column first, or rate it in a table of its own."

A band's **DEFAULT** (unmatched rows) on the Banding node is not a level of the table, so rows that fall to it are rated with the table's **DEFAULT**.

### Combined

Each combined output is a tab, named by its column (with a dot, green when complete); the + button after the tabs adds one, named `combined_1`, `combined_2` and so on, and the × on a tab removes it. With none, the section says "No combined output".

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

1. Connect your data to a Rating Step. In the first table, choose `area` under **+ Add factor...**, type `area_factor` as the **OUTPUT COLUMN** and leave **DEFAULT** empty, so an area the table does not list stops the run.
2. Fill in the relativities: London `1.25`, Manchester `1.10`, Rural `0.85`.
3. Click the + button beside the table list to add a second table. Choose `age_band` as its factor, type `age_factor` as the **OUTPUT COLUMN**, and fill in `18-25` → `1.40`, `26-65` → `1.00`, `65+` → `1.15`.
4. Switch **RATING SECTION** to **Combined**, click the + button, and change the **COMBINED OUTPUT COLUMN** to `location_age_factor`. Leave **OPERATION** on **× Multiply** and **BASE VALUE** at `1.0`.

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

A row whose factor value has no entry in the table is an unpriceable row. A table with a numeric **DEFAULT** prices it at that value without an error. Tables start with an empty **DEFAULT**, so a miss follows **ON MISS**.

With **ON MISS** on **Stop the run**, the run fails with a `RatingTableMissError` naming the table's output column, the missing keys and the number of rows affected - a renamed band label cannot silently price at base rate.

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
    | **OUTPUT COLUMN** | `tables[].output_column` |
    | **DEFAULT** | `tables[].default_value`, a number such as `1.0` (absent when empty) |
    | **ON MISS** | `tables[].on_missing`: `"error"` (**Stop the run**, also what an absent key means) or `"neutral"` (**Leave empty**) |
    | The relativities | a grid: with one factor, `tables[].rows` holds a line per level, the level and its relativity; with two or three, `tables[].columns` lists the last factor's levels and each line of `tables[].rows` holds the other factors' levels, then a relativity per column, `null` where there is no entry |
    | The combined outputs | `combined_outputs`: a list with one object per combined output |
    | **COMBINED OUTPUT COLUMN** | `combined_outputs[].output_column` |
    | **OPERATION** | `combined_outputs[].operation`: `"multiply"`, `"add"`, `"min"` or `"max"` |
    | **BASE VALUE** | `combined_outputs[].base_value`, as text such as `"1.0"` |
    | **TRANSFORM** tab | `steps`, or the function body when the node's code is not steps |
    | **COLUMNS** tab | `selected_columns` |

    With no editor control:

    - `tables[].factor_dtypes`: the data types of the factor columns, which Haute records.

    With `"neutral"`, a combined output treats the table's empty output as the operation's neutral element (×1.0 / +0.0).

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
    | `steps` | list of map of text to any JSON value | The low-code steps the node's code is rendered from, in order. |
    | `tables` | list of object | The rating tables. |
    | `tables[].factors` | list of text | The factor columns the table is keyed by, at most three. |
    | `tables[].factor_dtypes` | map of text to map of text to any JSON value | For each factor, the type its levels are read as. |
    | `tables[].output_column` | text | The column the looked-up value is written to. |
    | `tables[].default_value` | text or number or `null` | The value of a factor combination the table does not hold; `null` for none. |
    | `tables[].on_missing` | `"error"` or `"neutral"` | With no default, whether a missing combination fails the run (`error`) or takes the neutral value (`neutral`). |
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
