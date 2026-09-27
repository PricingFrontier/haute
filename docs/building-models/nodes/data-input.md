# Data Input

You have tabular data you want to bring into your pipeline  - historical policies, external enrichment data, lookup tables. The Data Input node reads it from a file, a lakehouse table, a database, a Databricks table, or records typed straight into the node.

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
| `query` | For `"database"`: the read-only SQL query. For `"databricks"`: an optional **SELECT clause** without `FROM`, such as `SELECT policy_id, premium`; Haute appends `FROM` and the chosen table. It must start with `SELECT` and must not contain `FROM`, semicolons, SQL comments or write keywords |
| `table` | For `"databricks"`: the table name (`catalog.schema.table`) |
| `http_path` | For `"databricks"`: the SQL warehouse HTTP path (e.g. `/sql/1.0/warehouses/abc123`). Your Databricks administrator can provide this. |
| `records` | For `"inline"`: a list of row objects typed into the node |
| `mode` | For file, lakehouse and inline sources: `"scan"` (lazy) or `"read"` (eager). Defaults to the format's usual mode; a format offers only the modes Polars supports for it. Database and Databricks inputs have no `mode`. |
| `arguments` | For file, lakehouse and inline sources: extra keyword arguments for the Polars reader, such as `separator` for a CSV; only arguments the reader accepts are allowed. A database or Databricks input accepts only `batch_size`. |
| `steps` | Steps applied after loading, built in the node's **Polars** tab  - they start from the loaded data, `df`. See [Polars](polars.md) for the step builder. |
| `code` | Polars code applied after loading. While the node has steps, this is the code they generate; after **Switch to code**, it is the code you edit, and the loaded data is available as `df`. |

The formats each source type accepts:

| `inputType` | `format` |
|---|---|
| `"file"` | `"parquet"`, `"csv"`, `"ndjson"` (`.jsonl`/`.ndjson`), `"json"`, `"ipc"` (Arrow/Feather), `"ipc_stream"`, `"avro"`, `"excel"`, `"ods"`, `"lines"` (one text line per row) |
| `"lakehouse"` | `"delta"`, `"iceberg"` |
| `"database"` | `"database"` (SQLite in this release) |
| `"inline"` | `"records"` |

A Databricks input needs no `format`.

!!! note "When to use the Polars tab vs a Polars node"
    Steps and code are optional. You can also add a [Polars](polars.md) node downstream for the same effect. Use the **Polars** tab here to filter or reshape the data as it loads: add steps, a **Free code** step, or click **Switch to code** to write it all as code.

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
    A parquet file read with `mode` `"scan"` (the default) is read directly. Every other configuration  - a parquet file read eagerly, another file format, a database, a lakehouse or Databricks table, inline records  - is first copied into a cached parquet snapshot, and the pipeline reads the snapshot. Haute refreshes a snapshot when it detects that the source has changed. To re-read the source now, click **Import** beside **Refresh** in the node's preview: it re-reads the source and caches it as a new snapshot, and its tooltip shows when the source was last imported. Haute cannot detect changes to a database or Databricks table, so for those sources **Import** is the only way to pick up new data. To delete cached snapshots, use **Cached data** in Pipeline settings.

!!! warning "Credentials never go in the config"
    A database `uri` must not contain a user name, password or secret query parameter. Put a URI with credentials in an environment variable and name that variable in `connection`.

**See also:** [Polars](polars.md) for code syntax. Sharing pipelines across operating systems, WSL, or network mounts? See [Filesystem Portability](../filesystem-portability.md).
