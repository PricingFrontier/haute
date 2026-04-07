# Data Sink

You want to save results to a file  - scoring a full dataset and writing the output to parquet or CSV for downstream analysis.

!!! tip "Spreadsheet equivalent"
    Like "Save As CSV" in Excel, but built into your pipeline so it runs automatically.

!!! info "When to use"
    Use this for batch scoring  - processing a full dataset and saving the results. For live API responses, use [Output](output.md) instead.

This node accepts a single input.

| Config | Description |
|---|---|
| `path` | **Required.** Output file path (e.g. `outputs/scored_policies`) |
| `format` | **Required.** `"parquet"` or `"csv"` |

If you provide a filename without a directory, it's written to `outputs/`. The format extension is added automatically if missing.

**Example:**

```json
{
  "path": "outputs/scored_policies",
  "format": "parquet"
}
```

This writes the full scored dataset to `outputs/scored_policies.parquet`.

!!! note "All columns are written"
    All columns from the input are written to the file. To control which columns are saved, add a [Polars](polars.md) node upstream with [`selected_columns`](polars.md#selected_columns).

!!! note "Overwrites existing files"
    If the file already exists, it is overwritten.

!!! note "Multiple sinks"
    You can have multiple Data Sink nodes in a pipeline  - for example, to write both a parquet file and a CSV, or to save results at different stages.

**See also:**

- [Output](output.md)  - define the API response for live pricing
