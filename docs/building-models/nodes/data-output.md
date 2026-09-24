# Data Output

You want to save results  - scoring a full dataset and writing the output to parquet, CSV or a database table for downstream analysis.

!!! tip "Spreadsheet equivalent"
    Like "Save As CSV" in Excel: the destination is part of your pipeline, and you write it when you're ready.

!!! info "When to use"
    Use this for batch scoring  - processing a full dataset and saving the results. For live API responses, use [Output](output.md) instead.

This node accepts a single input. Every configuration chooses exactly one destination with `outputType`:

| Config | Description |
|---|---|
| `outputType` | **Required.** Where the data goes: `"file"`, `"lakehouse"` or `"database"` |
| `format` | **Required.** The registered format to write (see the table below) |
| `path` | The destination for `"file"` and `"lakehouse"` outputs (e.g. `outputs/scored_policies`) |
| `connection` | For `"database"`: the name of an environment variable that holds the database URI |
| `uri` | For `"database"`: a credential-free database URI, used instead of `connection` |
| `table` | For `"database"`: the table to write |
| `mode` | `"sink"` (streaming) or `"write"` (in memory). Defaults to the format's usual mode; a format offers only the modes Polars supports for it. |
| `arguments` | Extra keyword arguments for the Polars writer. Only arguments the writer accepts are allowed. |

The formats each destination type accepts:

| `outputType` | `format` |
|---|---|
| `"file"` | `"parquet"`, `"csv"`, `"ndjson"`, `"json"`, `"ipc"`, `"ipc_stream"`, `"avro"`, `"excel"` |
| `"lakehouse"` | `"delta"`, `"iceberg"` |
| `"database"` | `"database"` |

File paths are resolved from your project folder, not from the pipeline file's folder. A bare file name goes in the project's `outputs/` folder, and the format's extension is added when the name has none: `scored_policies` with parquet selected writes `outputs/scored_policies.parquet`. A relative path such as `exports/scored.parquet` is taken from the project folder, and an absolute path must stay inside the project. Missing folders are created.

**Example:**

```json
{
  "outputType": "file",
  "format": "parquet",
  "mode": "sink",
  "path": "outputs/scored_policies"
}
```

Clicking **Write** writes the full scored dataset to `outputs/scored_policies.parquet`.

## Writing

Running or previewing the pipeline passes data through a Data Output without writing anything. To write, click **Write** in the node's editor: Haute runs the pipeline up to the node and writes the destination. If the destination file already exists, the editor asks you to confirm with **Replace existing file** before overwriting it. For a lakehouse or database destination, what happens when the table already exists is the Polars writer's own behaviour, which you can set through `arguments` (for example `if_table_exists` for a database table).

!!! note "All columns are written"
    All columns from the input are written. To control which columns are saved, add a [Polars](polars.md) node upstream with [`selected_columns`](polars.md#selected_columns).

!!! note "Not part of a deployed API"
    A deployed pricing API never writes: its Data Output nodes pass data through without invoking the writer.

!!! note "Multiple outputs"
    You can have multiple Data Output nodes in a pipeline  - for example, to write both a parquet file and a CSV, or to save results at different stages.

**See also:**

- [Output](output.md)  - define the API response for live pricing
