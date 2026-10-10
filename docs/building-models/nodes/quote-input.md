# Quote Input

Your pipeline will receive live API requests in production. During development, you need realistic data to build and test against. The Quote Input node handles both  - it's the entry point for live pricing, and it reads a preview file so you can work with sample data on your machine.

!!! info "When to use"
    Use this as the entry point for live pricing. During development, it reads a preview file so you can build and test your pipeline. In production, it receives live API requests instead. Use [Data Input](data-input.md) for loading historical data or reference tables.

A Quote Input has no inputs. It maps each request into one or more tables, and each table you emit is a separate output, named by the table's label; connect each output to the node that uses it. A pipeline has only one Quote Input or [Workbench Input](workbench-input.md): once it has either, the palette entry is greyed out with "Only one Quote Input or Workbench Input allowed per pipeline". While the project's workbench is enabled, the palette offers the Workbench Input in its place.

The panel has no tabs: you choose the columns inside the tables, so there is no **COLUMNS** tab. A banner at the top reads "This node receives live API requests at deploy time".

## PREVIEW DATA

| Field | What it does |
|---|---|
| **PREVIEW DATA** | The sample request file the node reads while you build: a `.json`, `.jsonl`, `.ndjson` (JSON Lines - one JSON object, one quote, per line) or `.xml` file in your project folder, chosen in the file browser. Once a file is chosen, **change** opens the browser again. The file should match the shape of the requests your deployed pipeline will receive. |

Until the node has tables, the panel shows the file's top-level schema under the tables, with **Show preview** for its first rows. If the schema cannot be read, the panel says "Could not fetch schema:" with the reason, and **Retry schema** tries again.

## TABLES

The **TABLES** section maps the request into tables. Its header holds three controls:

- **salt names** - how a key copied into another table is named (see [Nested requests](#nested-requests)). Ticked by default.
- **Infer Tables** (shown once a preview file is chosen) - reads the whole preview file and proposes the tables and their columns. The first time, it fills the section directly; after that it asks before replacing anything.
- **Add Table** - adds an empty table to fill in by hand. The first one is the root table, labelled `quote_info`; a later one gets the placeholder path `$[:].table_1[:]` and the label `table_1`. Set its path, rename it if you like, then add its columns.

With no tables yet, the section says "No tables yet. Click Infer Tables to auto-populate from the data file, or Add Table to start from scratch."

Each table is a box with these controls:

| Control | What it does |
|---|---|
| **emit** | Makes the table an output of the node. Only emitted tables reach the canvas. |
| Label (first box) | The table's name, and the input name downstream nodes see. It must be an ASCII identifier (letters, digits and underscores), not a Python keyword, and unique in the node, ignoring case; the box refuses anything else with a message such as "Duplicate label: "claims" is already used by another table." |
| Path (second box) | Which array of the request the table's rows come from: `$[:]` for the top-level records, `$[:].drivers[:]` for a nested array. A table path must end at an array. |
| × | Removes the table. |
| **Confirm all** | Confirms every column not yet confirmed (shown while there are some). |
| Table actions | Icon buttons that copy the columns as tab-separated text, copy or download the table's mapping as JSON, download the columns as CSV or TSV, and paste tab-separated `name`, `path`, `type`, `selected` rows in to replace the columns (**Apply paste**). |
| **Add Column** | Adds a blank column row. Type its path and the name fills in from the path. |

Each column row has, from left to right:

| Control | What it does |
|---|---|
| Tick box | Whether the column is kept. Untick the columns you don't need. |
| Name | The column's name in the table. It is required and must be unique in its table. A name used in another table for a different field is shaded, with a warning that a name should mean one field everywhere. |
| Path | Where the value sits in the request, such as `$[:].proposer.date_of_birth`. It must name a field at the table's own level or a shallower one; a path deeper than the table, or on another branch, is flagged under the row. |
| Type | `int`, `float`, `str`, `bool` or `date`. A column typed `date` arrives as a date, so no parsing code is needed downstream. |
| Key icon | Marks the column as a key: it is confirmed and moves into the keys at the top of the table. Click again to unmark it. |
| Origin chip | Where the column came from: **INFERRED**, **INHERITED** (a key copied from a shallower table) or **MANUAL** (typed in by hand), with a tick once confirmed. |
| Confirm tick | Confirms the column (shown until it is confirmed). Editing a column's name, path or type also confirms it. |
| × | Removes the column. |

## Frames

Once the node has tables, a **Frames (N)** box sits above them, with one row per table. Click its title to show the rows. It is where keys are carried between tables:

- **Cascade keys** pushes keys into every deeper table on their branch.
- **Inherit**, on a table's row, pulls a key from a shallower table onto that table. It appears only when a shallower table has keys to offer.
- **Add keys**, on a table's row, adds keys from the fields Haute knows about, or a field you type in by hand.

Each opens a picker listing the candidate fields by level, each with its name, path and type; fields already present are ticked and greyed. In **Add keys**, **Enter a field by hand** takes a path such as `$[:].orders[:].currency` and a type, with **Add** and **Add & close**. A row whose table has an invalid path is greyed, names the problem, and cannot take keys.

## Nested requests

Real quote requests are nested: a proposer object, a vehicle object, a list of additional drivers. The node's editor maps the request into tables, so downstream nodes work with ordinary columns and never with nested JSON.

Click **Infer Tables** and the editor reads the whole preview file and proposes the tables:

- **One table per level.** The top-level records become one table, labelled `quote_info`. Every array in the request  - an array of objects such as `additional_drivers`, or a scalar array such as `["TPFT", "comprehensive"]`  - starts its own child table, labelled from its key (`$[:].proposer.claims[:]` becomes `claims`).
- **Columns from nested objects.** A field inside a nested object becomes a column of its table, named by its own key: `proposer.date_of_birth` becomes `date_of_birth`. When two fields in one table share a key, both are named by their path with underscores instead, such as `proposer_date_of_birth` and `vehicle_date_of_birth`.
- **Types from every record.** Each column gets the type that fits all the records in the file: `int`, `float`, `str`, `bool` or `date`.

Then shape the tables in the editor:

- **Rename and retype columns.** Edit a column's name, or pick another type from its list. A column typed `date` arrives as a date, so no parsing code is needed downstream. A name should mean one field everywhere: the editor flags a name that another table uses for a different field.
- **Choose columns.** Untick the columns you don't need.
- **Emit the tables you use.** Only an emitted table becomes an output of the node, named by its label, which is the input name downstream nodes see. Each extra emitted table is an extra output.
- **Carry keys into child tables.** A child table needs its parent's key, such as `quote_id`, to join back. Mark a field as a key with its key icon, then use **Cascade keys** to push a key into every deeper table on its branch, or a table's **Inherit** to pull a key from a shallower table onto it. A table's **Add keys** picks keys from the file's fields or takes one you type. The **salt names** option controls how a copied key is named: ticked (the default), `customer.id` becomes `customer_id`; unticked, it keeps the bare `id`. A remaining clash gets a numeric suffix (`_2`).
- **Confirm what you've checked.** Inferred columns stay marked as inferred until you confirm them, one at a time or with **Confirm all**. Running **Infer Tables** again asks before it replaces anything (**Replace tables**): tables at the same path keep your labels and emit choices and your confirmed columns, and pick up newly found columns.
- **Build a table by hand** with **Add Table**.

## In the data preview

**Refresh** in the node's data preview reads the preview file again and caches its emitted tables again before showing them, whether or not the file has changed, so press it after you edit the file.

## Example

A preview file might look like this:

```json
[
  { "quote_id": "Q001", "driver_age": 22, "area": "London", "vehicle_value": 15000 },
  { "quote_id": "Q002", "driver_age": 45, "area": "Rural", "vehicle_value": 8000 }
]
```

1. Add a Quote Input node and choose `data/quotes.json` in **PREVIEW DATA**.
2. Click **Infer Tables**. The editor proposes one table, `quote_info`, at path `$[:]`, with the columns `quote_id` (`str`), `driver_age` (`int`), `area` (`str`) and `vehicle_value` (`int`).
3. Check the columns and click **Confirm all**, and make sure the table is ticked **emit**.
4. Connect the node's `quote_info` output to the next node. The preview shows:

```
| quote_id | driver_age | area   | vehicle_value |
|----------|------------|--------|---------------|
| Q001     | 22         | London | 15000         |
| Q002     | 45         | Rural  | 8000          |
```

!!! note "Production behaviour"
    When deployed, the preview file is ignored  - the node receives live JSON requests from your API instead. The schema should match your preview file so your pipeline works identically in both modes.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/quote_input/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.api_input(config="config/quote_input/<node name>.json")`.

    | Setting in the editor | Stored as |
    |---|---|
    | **PREVIEW DATA** | `path`: the preview file, relative to your project folder (e.g. `data/quotes.json`) |
    | The tables | `tables`: a list with one object per table |
    | A table's label | `tables[].label` |
    | A table's path | `tables[].path`, a JSONPath naming the array the table iterates (`$[:]`, `$[:].drivers[:]`) |
    | **emit** | `tables[].emit` (`true` or `false`) |
    | A table's columns | `tables[].columns`: one object per column row |
    | A column's tick box | `tables[].columns[].selected` (`true` or `false`) |
    | A column's name | `tables[].columns[].name` |
    | A column's path | `tables[].columns[].path` (its source JSONPath) |
    | A column's type | `tables[].columns[].type`: `"int"`, `"float"`, `"str"`, `"bool"` or `"date"` |
    | Confirmed or not | `tables[].columns[].status`: `"Confirmed"` or `"Inferred"` |
    | The origin chip | `tables[].columns[].origin`: `"inferred"`, `"inherited"` or `"manual"` |
    | The key icon | `tables[].columns[].key` (`true` or `false`) |

    With no editor control:

    - `tables[].row_id_column` names the column that identifies each record in execution traces; renaming that column in the editor carries the setting along.
    - `tables[].displayPath` and `tables[].columns[].levels` are kept as they are.

**See also:**

- [Data Input](data-input.md)  - for historical data and reference tables
- [Source Switch](source-switch.md)  - for switching between live and batch data
- [Quote Response](output.md)  - for the columns the API returns
