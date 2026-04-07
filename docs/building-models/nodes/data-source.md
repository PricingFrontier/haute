# Data Source

You have tabular data you want to bring into your pipeline  - historical policies, external enrichment data, lookup tables. The Data Source node reads flat files (parquet or CSV) or Databricks tables.

!!! tip "Spreadsheet equivalent"
    Like opening a CSV or connecting to an external data source in Excel  - it brings data into your workbook.

!!! info "When to use"
    - Loading historical data for analysis or model training.
    - Bringing in reference data to join with your quotes (e.g. postcode lookups, external scores).
    - Use [Quote Input](quote-input.md) instead when building the live API entry point.

| Config | Description |
|---|---|
| `sourceType` | **Required.** `"flat_file"` or `"databricks"` |
| `path` | File path (parquet or CSV). Required when sourceType is `"flat_file"`. Relative to your project folder. |
| `table` | Databricks table name (`catalog.schema.table`). Required when sourceType is `"databricks"`. |
| `http_path` | Databricks SQL warehouse HTTP path (e.g. `/sql/1.0/warehouses/abc123`). Your Databricks administrator can provide this. |
| `query` | SQL query to filter or transform the data before it enters the pipeline. Only applies to Databricks sources. For flat files, use the `code` field to filter data after loading. |
| `code` | Polars code applied after loading  - the loaded data is available as `df`. See [Polars](polars.md) for code syntax. |

!!! note "When to use `code` vs a Polars node"
    The `code` field is optional. You can also add a [Polars](polars.md) node downstream for the same effect. Use `code` here when you want to filter a large dataset before it fully loads into memory, which can improve performance.

## Example

A minimal flat file configuration:

```json
{
  "sourceType": "flat_file",
  "path": "data/policies.parquet"
}
```

!!! note "Supported file formats"
    Flat file sources support parquet and CSV. Excel files (`.xlsx`) are not supported directly  - export to CSV first.

**See also:** [Polars](polars.md) for code syntax and [Preparing Your Data](../preparing-your-data.md) for a walkthrough.
