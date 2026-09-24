# Data Input

You have tabular data you want to bring into your pipeline  - historical policies, external enrichment data, lookup tables. The Data Input node reads it from a file, a lakehouse table, a database, a Databricks table, or records typed straight into the node.

!!! tip "Spreadsheet equivalent"
    Like opening a CSV or connecting to an external data source in Excel  - it brings data into your workbook.

!!! info "When to use"
    - Loading historical data for analysis or model training.
    - Bringing in reference data to join with your quotes (e.g. postcode lookups, external scores).
    - Use [Quote Input](quote-input.md) instead when building the live API entry point.

This node has no inputs. Every configuration chooses exactly one source with `inputType`:

| Config | Description |
|---|---|
| `inputType` | **Required.** Where the data comes from: `"file"`, `"lakehouse"`, `"database"`, `"databricks"` or `"inline"` |
| `format` | **Required** except for Databricks. The registered format to read (see the table below) |
| `path` | The file or table location, for `"file"` and `"lakehouse"` sources. Relative paths are resolved from your project folder. |
| `connection` | For `"database"`: the name of an environment variable that holds the database URI, so credentials stay out of the pipeline |
| `uri` | For `"database"`: a credential-free database URI, used instead of `connection` |
| `query` | For `"database"`: the read-only SQL query. For `"databricks"`: an optional query that replaces reading the whole table |
| `table` | For `"databricks"`: the table name (`catalog.schema.table`) |
| `http_path` | For `"databricks"`: the SQL warehouse HTTP path (e.g. `/sql/1.0/warehouses/abc123`). Your Databricks administrator can provide this. |
| `records` | For `"inline"`: a list of row objects typed into the node |
| `mode` | For file, lakehouse and inline sources: `"scan"` (lazy) or `"read"` (eager). Defaults to the format's usual mode; a format offers only the modes Polars supports for it. Database and Databricks inputs have no `mode`. |
| `arguments` | For file, lakehouse and inline sources: extra keyword arguments for the Polars reader, such as `separator` for a CSV; only arguments the reader accepts are allowed. A database or Databricks input accepts only `batch_size`. |
| `code` | Polars code applied after loading  - the loaded data is available as `df`. See [Polars](polars.md) for code syntax. |

The formats each source type accepts:

| `inputType` | `format` |
|---|---|
| `"file"` | `"parquet"`, `"csv"`, `"ndjson"` (`.jsonl`/`.ndjson`), `"json"`, `"ipc"` (Arrow/Feather), `"ipc_stream"`, `"avro"`, `"excel"`, `"ods"`, `"lines"` (one text line per row) |
| `"lakehouse"` | `"delta"`, `"iceberg"` |
| `"database"` | `"database"` (SQLite in this release) |
| `"inline"` | `"records"` |

A Databricks input needs no `format`.

!!! note "When to use `code` vs a Polars node"
    The `code` field is optional. You can also add a [Polars](polars.md) node downstream for the same effect. Use `code` here to filter or reshape the data as it loads.

## Example

A parquet file in your project, read lazily:

```json
{
  "inputType": "file",
  "format": "parquet",
  "mode": "scan",
  "path": "data/policies.parquet"
}
```

A CSV with a non-default separator:

```json
{
  "inputType": "file",
  "format": "csv",
  "path": "data/claims.csv",
  "arguments": { "separator": ";" }
}
```

A Databricks table:

```json
{
  "inputType": "databricks",
  "table": "pricing.motor.policies",
  "http_path": "/sql/1.0/warehouses/abc123"
}
```

!!! note "Snapshots of non-parquet sources"
    A parquet file read with `mode` `"scan"` (the default) is read directly. Every other configuration  - a parquet file read eagerly, another file format, a database, a lakehouse or Databricks table, inline records  - is first copied into a cached parquet snapshot, and the pipeline reads the snapshot. Haute refreshes a snapshot when it detects that the source has changed; to force a fresh read, clear the snapshot in the project's cache inventory.

!!! warning "Credentials never go in the config"
    A database `uri` must not contain a user name, password or secret query parameter. Put a URI with credentials in an environment variable and name that variable in `connection`.

**See also:** [Polars](polars.md) for code syntax and [Preparing Your Data](../preparing-your-data.md) for a walkthrough. Sharing pipelines across operating systems, WSL, or network mounts? See [Filesystem Portability](../filesystem-portability.md).
