# Workbench Output

When your quotes are keyed in through the project's workbench, its schema also defines what a priced quote fills in: its output tables. The Workbench Output is the pipeline's response for those quotes. Its tables are the workbench's output tables, copied into the pipeline and kept up to date as you change the schema, and each one is an input you connect a frame to. When the pipeline runs, each table is filled from its frame, and the filled tables are the response.

!!! info "When to use"
    While the project's workbench is enabled, the palette offers the Workbench Output in place of the [Quote Response](output.md). Use it as the response for live pricing of keyed-in quotes. Without a workbench, use the Quote Response and map its columns yourself.

A Workbench Output has no outputs. Each table of the workbench's schema that has a column is a separate input, named by the table's name: drag a connection from the node whose frame fills the table onto that input. A table takes one connection, and a node fills one table; to fill two tables from the same data, give each its own node. A pipeline has only one Quote Response or Workbench Output: once it has either, the palette entry is greyed out with "Only one Quote Response or Workbench Output allowed per pipeline". Enabling or disabling the workbench changes only what the palette offers; to swap a Quote Response for a Workbench Output, delete it and drag in the other.

## Filling the tables

Each table is filled from the frame connected to it. Each of the table's columns is filled from the frame's column of the same name, unless you pick another in the panel (see below); the frame's other columns are left out. A column that nothing fills, because the frame has no column of its name and you picked none, is left empty: its values are null, and the panel flags it. Each column takes the type the workbench declares. An integer column can fill a Decimal one, and a categorical column a Text one; a column of another type stops the run with a message naming the table and the column, so convert it upstream or pick another. A pick of a column the frame doesn't have stops the run too.

A table with one row per quote takes exactly one row from its frame; a table with many rows per quote takes all of its frame's rows.

Click the node to preview its tables, filled for the workbench's sample quote: each table is a dataframe, with the table picker at the top of the preview choosing which one shows when there are several. To trace a value back through the pipeline, click it in the preview of the node connected to its table.

While you build, the workbench prices the sample live and shows the answer in its output columns (see [The sample](../workbench.md#the-sample)); the node's own preview runs on the sample as last saved.

## The response

When the pipeline answers a request, through a deployment's `/quote` or `pipeline.run()` and `pipeline.score()` in code, the tables become the response for one quote: each one-row table is an object under its name and each many-row table a list of objects under its name, for example:

```json
[
  {
    "pricing_output": { "model_premium": 1250.0, "charged_premium": 1300.0 },
    "layers": [
      { "layer": 1, "premium": 800.0 },
      { "layer": 2, "premium": 500.0 }
    ]
  }
]
```

Values that are null, and tables with no rows, are left out of the response, as in a Quote Response's.

A Workbench Output answers one quote per request. A deployed pipeline refuses a request of several quotes at its Workbench Input ("A Workbench Input reads one quote per request, and this request holds 2."); a frame that reaches a table with one row per quote with other than one row, as `pipeline.score()` can bring, stops the run with "The Workbench Output's '...' table has one row per quote, but the frame connected to it has 2 rows."

## TABLES FROM THE WORKBENCH

The panel lists the workbench's output tables. You can't change the tables here, but you choose what fills each column:

| Shown | What it means |
|---|---|
| The table's name | The input's name. |
| **one per quote** or **many per quote** | Whether a quote has one row of the table or a list of them. |
| **From** ... or **Not connected** | The node whose frame fills the table, or that nothing does yet. |
| Each column's name and type, and a column picker | The frame column that fills it. A same-named column fills it until you pick another, shown as **(by name)**. **— none —** leaves it empty, and the picker is highlighted when nothing fills the column or the column you picked is no longer in the frame (**(missing)**). The picker lists the frame's columns once the node it comes from has been previewed, and is greyed out until a frame is connected. |
| A warning under the name | The name can't be an input: it isn't an identifier, or another table has it. Rename the table in the workbench. |
| **No columns yet, so no port.** | The table has no columns, so there is nothing to fill. Add its columns in the workbench. |

Changing a pick is an edit to the pipeline, saved and undone like any other.

The tables are the workbench's schema, edited in the Workbench view: **Edit in Workbench** in this section opens it, and the switcher at the bottom of the node palette brings you back to **Pricing**. When you save there (**Save**, or Ctrl+S), the editor fetches the tables again and the node's tables and inputs follow, and the pipeline has changes to save, as after any edit. Connections follow table names: a connection to a table that keeps its name stays, and one to a table that was renamed or removed is removed, with a message. Changes not yet saved in the Workbench are priced on the Workbench's own sheets but don't reach the pipeline until saved.

While the workbench is not enabled, the section says "The workbench is not enabled in haute.toml, so these tables are the last copy and nothing updates them." The pipeline still runs, tests and deploys from that copy. Inside a submodel the section says "The editor updates these tables only at the pipeline's top level."

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/workbench_output/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.workbench_output(config="config/workbench_output/<node name>.json")`. Each connection names the table it fills: `pipeline.connect("priced", "response", target_port="pricing_output")`.

    | Setting in the editor | Stored as |
    |---|---|
    | The tables | `tables`: the workbench's output tables, in the Workbench Input's format: each its `name`, its `rows` per quote and its typed `columns` |
    | Your picks | `mapping`: by table name, then column name, the frame column that fills it, or `null` for none. A column without an entry is filled by name, so a new node has no `mapping` |

    For example, `"mapping": {"pricing_output": {"model_premium": "premium"}}` fills `pricing_output`'s `model_premium` from the frame's `premium` column. When the workbench drops a table or column, the editor drops its entries from `mapping` as it updates the tables. The pipeline fills the tables from `tables` and `mapping` alone, so it runs, tests and deploys without the workbench enabled.

**See also:**

- [Workbench Input](workbench-input.md)  - the entry point whose tables the workbench defines
- [Quote Response](output.md)  - the response whose columns you map yourself
