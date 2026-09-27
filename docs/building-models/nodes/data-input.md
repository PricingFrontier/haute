# Data Input

You have tabular data you want to bring into your pipeline  - historical policies, external enrichment data, lookup tables. The Data Input node reads it from a file, a lakehouse table, a database, a Databricks table, or records typed straight into the node.

!!! info "When to use"
    - Loading historical data for analysis or model training.
    - Bringing in reference data to join with your quotes (e.g. postcode lookups, external scores).
    - Use [Quote Input](quote-input.md) instead when building the live API entry point.

A Data Input has no inputs and one output: the data it reads. Its panel has three tabs: **CONFIG**, where you choose the source, **POLARS** and **COLUMNS**.

## The CONFIG tab

**PROVIDER**, at the top, chooses where the data comes from: **File**, **Database**, **Lakehouse**, **Databricks** or **Inline**. A new node starts on **File** with the **Parquet** format. Choosing another provider starts that provider's settings afresh; the node's steps or code on the **POLARS** tab and its **COLUMNS** selection are kept.

The fields below **PROVIDER** depend on the provider. A field marked **\*** is required.

### File

| Field | What it does |
|---|---|
| **FORMAT** | The file format: **CSV**, **JSON**, **NDJSON** (`.jsonl`/`.ndjson`), **Parquet**, **Arrow IPC / Feather**, **Arrow IPC stream**, **Avro**, **Excel**, **OpenDocument spreadsheet** or **Text lines** (one text line per row). A format Polars marks as unstable says **(unstable)**, and a format whose reader needs a package that is not installed says "needs one of:" and the packages. Changing the format keeps the path and clears **ARGUMENTS**. |
| **PATH \*** | The file, chosen in the file browser, which lists the folders of your project and the files with the format's extensions. Once a file is chosen, **change** opens the browser again. The path is stored relative to your project folder. |

For a **CSV** file, the tab also shows the columns and types Haute detects in the file. **Use detected schema** copies them into **ARGUMENTS** as the reader's `schema`, which fixes each column's type; until you do, the tab shows "A schema mapping is required for this bounded input." Without a declared schema, Haute infers each column's type from the whole file when it copies the file into its snapshot (see [Snapshots](#in-the-data-preview)). If the file cannot be read, the tab says "Could not detect schema:" with the reason, and **Retry schema** tries again.

### Database

| Field | What it does |
|---|---|
| **FORMAT** | **Database (URI)**. This release reads SQLite databases. |
| **CONNECTION ENVIRONMENT REFERENCE** | The name of an environment variable that holds the database URI, so credentials stay out of the pipeline. |
| **CREDENTIAL-FREE URI** | A database URI with no user name, password or secret parameter, used instead of a connection reference. Filling in one of the two clears the other. |
| **QUERY \*** | The read-only SQL query that selects the data. |

### Lakehouse

| Field | What it does |
|---|---|
| **FORMAT** | **Delta Lake** or **Iceberg (unstable)**. |
| **TABLE LOCATOR \*** | The table's location in your project. |

### Databricks

| Field | What it does |
|---|---|
| **SQL WAREHOUSE** | The SQL warehouse's HTTP path, such as `/sql/1.0/warehouses/abc123`; your Databricks administrator can provide it. Type it, or click **Browse** to list the workspace's warehouses (each with its state and size) and pick one. |
| **TABLE** | The table to read, chosen in three lists: **Select catalog...**, then **Select schema...**, then **Select table...**. The full name, `catalog.schema.table`, shows underneath. |
| **SELECT CLAUSE** | Optional. A `SELECT` clause without `FROM`, such as `SELECT policy_id, premium`; Haute appends `FROM` and the chosen table ("Optional projection/filter clause. Haute supplies the validated table."). It must start with `SELECT` and must not contain `FROM`, semicolons, SQL comments or write keywords. Leave it empty to read every column. |

A Databricks input has no format.

### Inline

| Field | What it does |
|---|---|
| **FORMAT** | **Inline records**. |
| **RECORDS \*** | The rows typed straight into the node, as a JSON list of row objects, such as `[{"area": "London", "factor": 1.25}]`. Invalid text is not saved: the box says "Invalid JSON - changes have not been saved." or "Records must be a JSON array." |

### ARGUMENTS

Every provider's fields end with **ARGUMENTS**: extra keyword arguments for the Polars reader, such as `separator` for a CSV. **Add argument** adds a row with a name (the box suggests the arguments the reader accepts) and a value written as JSON, for example `";"` for a text value or `true`; the bin removes a row. A database or Databricks input accepts only `batch_size`.

- A name the reader does not accept is kept, with "*name* is not a supported … argument. It is kept so the backend can reject it explicitly."
- A value that is not valid JSON keeps the previous value: "Invalid JSON. The previous value is kept until this parses."

A **CONFIGURATION ERRORS** box at the top of the fields lists anything in the node's settings that the tab cannot use, such as "Select a valid format for this provider." or "Databricks batch_size must be a positive integer."

## The POLARS tab

The **POLARS** tab adds optional steps that run on the loaded data, which the steps see as `df` (the tab's hint reads "df = the opened input snapshot"). They are built with the step builder described in [Building the node from steps](polars.md#building-the-node-from-steps).

!!! note "When to use the Polars tab vs a Polars node"
    Steps and code are optional. You can also add a [Polars](polars.md) node downstream for the same effect. Use the **POLARS** tab here to filter or reshape the data as it loads: add steps, a **Free code** step, or click **Switch to code** to write it all as code.

## The COLUMNS tab

The **COLUMNS** tab chooses which of the loaded columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## In the data preview

A parquet file is read directly. Every other source - another file format, a database, a lakehouse or Databricks table, inline records - is first copied into a cached parquet snapshot, and the pipeline reads the snapshot. Haute refreshes a snapshot when it detects that the source has changed.

For a snapshot source, **Import** sits beside **Refresh** in the node's data preview. It re-reads the source and caches it as a new snapshot, and its tooltip shows when the source was last imported. Haute cannot detect changes to a database or Databricks table, so for those sources **Import** is the only way to pick up new data. To delete cached snapshots, use **Cached data** in Pipeline settings.

## Example

A parquet file in your project:

1. Leave **PROVIDER** on **File** and **FORMAT** on **Parquet**.
2. Under **PATH**, open the `data` folder and click `policies.parquet`.

The preview shows the file's first rows.

A CSV file that uses `;` as its separator:

1. Choose **CSV** as the **FORMAT**, and `data/claims.csv` as the **PATH**.
2. Under **ARGUMENTS**, click **Add argument**, type `separator` as the name and `";"` as the value.
3. Click **Use detected schema** to fix the column types.

A Databricks table:

1. Choose **Databricks** as the **PROVIDER**.
2. Under **SQL WAREHOUSE**, click **Browse** and pick the warehouse, or type its HTTP path, such as `/sql/1.0/warehouses/abc123`.
3. Under **TABLE**, choose the catalog `pricing`, the schema `motor` and the table `policies`.
4. Preview the node. When the table changes later, click **Import** in the data preview to read it again.

!!! warning "Credentials never go in the pipeline"
    A **CREDENTIAL-FREE URI** must not contain a user name, password or secret query parameter. Put a URI with credentials in an environment variable and give that variable's name as the **CONNECTION ENVIRONMENT REFERENCE**.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/data_input/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.data_input(config="config/data_input/<node name>.json")`. Steps on the **POLARS** tab are stored in the sidecar as `steps`, and the code they generate is the body of the node's function, which takes the loaded data as `df`; after **Switch to code**, the body is your code.

    | Setting in the editor | Stored as |
    |---|---|
    | **PROVIDER** | `inputType`: `"file"`, `"database"`, `"lakehouse"`, `"databricks"` or `"inline"` |
    | **FORMAT** | `format` (none for Databricks): see the table below |
    | **PATH** / **TABLE LOCATOR** | `path`, relative to your project folder |
    | **CONNECTION ENVIRONMENT REFERENCE** | `connection` |
    | **CREDENTIAL-FREE URI** | `uri` |
    | **QUERY** (Database) | `query` |
    | **SQL WAREHOUSE** | `http_path` |
    | **TABLE** (Databricks) | `table` (`catalog.schema.table`) |
    | **SELECT CLAUSE** | `query` (absent when empty) |
    | **RECORDS** | `records`: a list of row objects |
    | **ARGUMENTS** | `arguments`: an object of argument names and values |
    | **POLARS** tab | `steps`, or the function body after **Switch to code** |
    | **COLUMNS** tab | `selected_columns` |

    The formats each provider accepts:

    | `inputType` | `format` |
    |---|---|
    | `"file"` | `"parquet"`, `"csv"`, `"ndjson"` (`.jsonl`/`.ndjson`), `"json"`, `"ipc"` (Arrow/Feather), `"ipc_stream"`, `"avro"`, `"excel"`, `"ods"`, `"lines"` (one text line per row) |
    | `"lakehouse"` | `"delta"`, `"iceberg"` |
    | `"database"` | `"database"` (SQLite in this release) |
    | `"inline"` | `"records"` |

    With no editor control: `mode`, for file, lakehouse and inline sources, is `"scan"` (lazy) or `"read"` (eager). Choosing a format sets it to the format's usual mode, and a format offers only the modes Polars supports for it. A parquet file with `mode` `"read"` is copied into a snapshot like the other sources. Database and Databricks inputs have no `mode`.

**See also:** [Polars](polars.md) for steps and code. Sharing pipelines across operating systems, WSL, or network mounts? See [Filesystem Portability](../filesystem-portability.md).
