# Data Output

You want to save results  - scoring a full dataset and writing the output to parquet, CSV or a database table for downstream analysis.

!!! info "When to use"
    Use this for batch scoring  - processing a full dataset and saving the results. For live API responses, use [Quote Response](output.md) instead.

This node takes a single input and passes it on unchanged: running or previewing the pipeline passes data through a Data Output without writing anything. It writes only when you click **Write**.

## The CONFIG tab

| Field | What it does |
|---|---|
| **PROVIDER** | Where the data goes: **File** (the default), **Database** or **Lakehouse**. Changing it keeps only the settings the new provider uses. |
| **FORMAT** | The format to write, from the formats the provider supports (see the table below). A format whose writer needs a package that is not installed is listed with **needs one of:** and the package, and a format Polars marks as unstable is listed with **(unstable)**. |
| **MODE** | **sink** (streaming) or **write** (in memory). Shown when the format supports more than one mode, or once a mode is set (a new node starts on **sink**). Each format offers only the modes Polars supports for it. |
| **FILENAME OR PATH** | File provider: the file to write. Type a name or pick one from the file browser. Paths are resolved from your project folder, not from the pipeline file's folder. A bare file name goes in the project's `outputs/` folder, and the format's extension is added when the name has none: `scored_policies` with Parquet selected writes `outputs/scored_policies.parquet`. A relative path such as `exports/scored.parquet` is taken from the project folder, and an absolute path must stay inside the project. Missing folders are created. |
| **TABLE LOCATOR** | Lakehouse provider: the table to write. |
| **CONNECTION ENVIRONMENT REFERENCE** | Database provider: the name of an environment variable that holds the database URI. |
| **CREDENTIAL-FREE URI** | Database provider: a database URI without credentials, used instead of the connection reference. |
| **TABLE** | Database provider: the table to write. |
| **ARGUMENTS** | Extra keyword arguments for the Polars writer. **Add argument** adds a row with a **name** (suggested from the arguments the writer accepts) and a JSON value (for example `","` or `true`). Only arguments the writer accepts are allowed; an unknown name or a value that is not valid JSON is flagged. |

The formats each provider offers:

| **PROVIDER** | **FORMAT** |
|---|---|
| **File** | **Parquet**, **CSV**, **NDJSON**, **JSON**, **Arrow IPC / Feather**, **Arrow IPC stream**, **Avro**, **Excel** |
| **Lakehouse** | **Delta Lake**, **Iceberg** |
| **Database** | **Database (URI)** |

Under the fields, the panel says how the format is written (a streaming sink or an eager writer) and how it is published.

## Writing

When the settings are complete, the panel shows the resolved **Destination:** before you write, and warns if the destination's extension does not match the selected format.

- **Write** runs the pipeline up to the node and writes the full dataset to the destination. It is disabled until the settings are complete, and reads **Writing...** while it runs. When it finishes, the panel reports the result with the number of rows and the path.
- If the destination file already exists, the panel asks you to confirm with **Replace existing file** before overwriting it. For a lakehouse or database destination, what happens when the table already exists is the Polars writer's own behaviour, which you can set with **ARGUMENTS** (for example `if_table_exists` for a database table).

## The COLUMNS tab

By default every input column is written; to write only some of them, untick the others on the **COLUMNS** tab, with no upstream node needed (see [Working with any node](index.md#working-with-any-node)).

## Example

Writing a scored dataset to parquet:

1. Connect the scored data to a Data Output node.
2. Leave **PROVIDER** on **File**, **FORMAT** on **Parquet** and **MODE** on **sink**.
3. Type `scored_policies` in **FILENAME OR PATH**. The panel shows the destination, `outputs/scored_policies.parquet`.
4. Click **Write**.

!!! note "Not part of a deployed API"
    A deployed pricing API never writes: its Data Output nodes pass data through without invoking the writer.

!!! note "Multiple outputs"
    You can have multiple Data Output nodes in a pipeline  - for example, to write both a parquet file and a CSV, or to save results at different stages.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/data_output/<node name>.json`, which the pipeline's `.py` file names in the node's decorator: `@pipeline.data_output(config="config/data_output/<node name>.json")`. Every configuration chooses exactly one destination with `outputType`.

    | Setting in the editor | Stored as |
    |---|---|
    | **PROVIDER** | `outputType`: `"file"`, `"lakehouse"` or `"database"` |
    | **FORMAT** | `format`: `"parquet"`, `"csv"`, `"ndjson"`, `"json"`, `"ipc"`, `"ipc_stream"`, `"avro"` or `"excel"` for a file; `"delta"` or `"iceberg"` for a lakehouse; `"database"` for a database |
    | **MODE** | `mode`: `"sink"` or `"write"`; absent uses the format's usual mode |
    | **FILENAME OR PATH**, **TABLE LOCATOR** | `path` |
    | **CONNECTION ENVIRONMENT REFERENCE** | `connection` |
    | **CREDENTIAL-FREE URI** | `uri` |
    | **TABLE** | `table` |
    | **ARGUMENTS** | `arguments`, a map of argument name to value |
    | **COLUMNS** tab | `selected_columns` |

    The column metadata any node may carry, `column_renames` and `categorical_levels`, has no
    editor control on this node.

    The example above is stored as:

    ```json
    {
      "outputType": "file",
      "format": "parquet",
      "mode": "sink",
      "path": "scored_policies"
    }
    ```

**See also:**

- [Quote Response](output.md)  - define the API response for live pricing
