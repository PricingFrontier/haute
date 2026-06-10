# Quote Input

Your pipeline will receive live API requests in production. During development, you need realistic data to build and test against. The Quote Input node handles both  - it's the entry point for live pricing, and it reads a preview file so you can work with sample data on your machine.

!!! tip "Spreadsheet equivalent"
    Like the input tab in an Excel workbook  - the place where raw data enters your calculation chain.

!!! info "When to use"
    Use this as the entry point for live pricing. During development, it reads a preview file so you can build and test your pipeline. In production, it receives live API requests instead. Use [Data Source](data-source.md) for loading historical data or reference tables.

| Config | Description |
|---|---|
| `path` | **Required.** Path to a `.json` or `.jsonl` (JSON Lines  - one JSON object per line, where each line is a quote) preview file, relative to your project folder (e.g. `data/quotes.json`) |
| `row_id_column` | Column that uniquely identifies each quote or record, e.g. `quote_id` or `policy_number`. Optional. If set, this column is used to identify individual records in execution traces and debugging. If not set, Haute uses the row index. |

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

The preview file should match the shape of the requests your deployed pipeline will receive. When your JSON is nested, the node's editor maps it into one or more tables (click **Infer Tables** to populate the mapping): nested object fields like `proposer.date_of_birth` can be pulled out as columns, and nested arrays  - whether arrays of objects or scalar arrays such as `["TPFT", "comprehensive"]`  - become their own child tables that you can emit as additional output ports. See [Preparing Your Data](../preparing-your-data.md) for how to shape these.

!!! note "Production behaviour"
    When deployed, the preview file is ignored  - the node receives live JSON requests from your API instead. The schema should match your preview file so your pipeline works identically in both modes.

!!! warning "One per pipeline"
    You can only have one Quote Input node in a pipeline.
