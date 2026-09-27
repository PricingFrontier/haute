# Quote Input

Your pipeline will receive live API requests in production. During development, you need realistic data to build and test against. The Quote Input node handles both  - it's the entry point for live pricing, and it reads a preview file so you can work with sample data on your machine.

!!! tip "Spreadsheet equivalent"
    Like the input tab in an Excel workbook  - the place where raw data enters your calculation chain.

!!! info "When to use"
    Use this as the entry point for live pricing. During development, it reads a preview file so you can build and test your pipeline. In production, it receives live API requests instead. Use [Data Input](data-input.md) for loading historical data or reference tables.

| Config | Description |
|---|---|
| `path` | **Required.** Path to a `.json`, `.jsonl` or `.ndjson` (JSON Lines  - one JSON object per line, where each line is a quote) or `.xml` preview file, relative to your project folder (e.g. `data/quotes.json`) |
| `tables` | **Required.** How the request document maps into tables. Each table has a `label` (its frame name downstream), a `path` naming the array it iterates (`$[:]` for the top-level records, `$[:].drivers[:]` for a nested array), an `emit` flag, its `columns` (each with a name, a source path, a declared type and whether it is selected) and an optional `row_id_column`, the column that identifies each record in execution traces. Click **Infer Tables** in the editor to build this from the preview file. |

## Example

A preview file might look like this:

```json
[
  { "quote_id": "Q001", "driver_age": 22, "area": "London", "vehicle_value": 15000 },
  { "quote_id": "Q002", "driver_age": 45, "area": "Rural", "vehicle_value": 8000 }
]
```

The node reads this file and produces a table:

```
| quote_id | driver_age | area   | vehicle_value |
|----------|------------|--------|---------------|
| Q001     | 22         | London | 15000         |
| Q002     | 45         | Rural  | 8000          |
```

The preview file should match the shape of the requests your deployed pipeline will receive.

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

When the preview file changes, click **Import** in the node's data preview to read it again.

!!! note "Production behaviour"
    When deployed, the preview file is ignored  - the node receives live JSON requests from your API instead. The schema should match your preview file so your pipeline works identically in both modes.

!!! warning "One per pipeline"
    You can only have one Quote Input node in a pipeline.
