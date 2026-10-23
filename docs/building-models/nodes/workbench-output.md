# Workbench Output

When your quotes are keyed in through the project's workbench, its schema also defines what a priced quote fills in: its output tables. The Workbench Output is the pipeline's response for those quotes. Its tables are the workbench's output tables, copied into the pipeline and kept up to date as you change the schema, and each one is an input you connect a frame to. When the pipeline runs, each table is filled from its frame, and the filled tables are the response.

!!! info "When to use"
    While the project's workbench is enabled, the palette offers the Workbench Output in place of the [Quote Response](output.md). Use it as the response for live pricing of keyed-in quotes. Without a workbench, use the Quote Response and map its columns yourself.

A Workbench Output has no outputs. Each table of the workbench's schema that has a column is a separate input, named by the table's name: drag a connection from the node whose frame fills the table onto that input. A table takes one connection, and a node fills one table; to fill two tables from the same data, give each its own node. A pipeline has only one Quote Response or Workbench Output: once it has either, the palette entry is greyed out with "Only one Quote Response or Workbench Output allowed per pipeline". Enabling or disabling the workbench changes only what the palette offers; to swap a Quote Response for a Workbench Output, delete it and drag in the other.

## Filling the tables

Each table is filled from the frame connected to it. Each of the table's columns is filled from the frame's column of exactly the same name, capitals included; the frame's other columns are left out. Each column takes the type the workbench declares: an integer column can fill a Decimal one, and a categorical column a Text one.

A column the frame doesn't have, or has with a type that doesn't fit (a Text column for a Decimal one, say), is left empty: its values are null, and the panel flags it (see below). A table nothing is connected to is left empty too: a table with one row per quote gets one row of nulls, and a table with many rows gets none. None of these stops the run, so the pipeline previews and prices while you are still building it. To fill a column, give the frame a column of its name and type before the Workbench Output, for example in a [Transform](transform.md) node.

A table's **reference** and **option** are left empty in the node's own tables, whatever its frame holds: the response gives every row the quote's (see below), so a pipeline need not carry them through.

A table with one row per quote takes exactly one row from its frame; a table with many rows per quote takes all of its frame's rows, in the order the frame delivers them. A step before it that leaves row order to Polars, such as an [Edge Join](edge-join.md) with **Order** set to **None**, can list them in a different order from one run to the next.

Click the node to preview its tables, filled for the workbench's sample quote: each table is a dataframe, with the table picker at the top of the preview choosing which one shows when there are several. The preview keeps the table you picked when you refresh it, when the pipeline recalculates and when you come back to the node. Click a value to trace it back through the pipeline: it is traced from the node connected to its table, through the frame's column of the same name. A null the Workbench Output filled in, for a column the frame doesn't fill or a table nothing is connected to, has nothing to trace.

In Preview, the workbench prices the sample live and shows the answer in its output columns (see [Preview](../workbench.md#preview)); the node's own preview runs on the sample as last saved.

## The response

When the pipeline answers a request, through a deployment's `/quote` or `pipeline.run()` and `pipeline.score()` in code, the tables become the response for one quote: each one-row table is an object under its name and each many-row table a list of objects under its name, every row holding the quote's **reference** and **option**, for example:

```json
[
  {
    "pricing_output": { "reference": "Q-000123", "option": 2, "model_premium": 1250.0, "charged_premium": 1300.0 },
    "layers": [
      { "reference": "Q-000123", "option": 2, "layer": 1, "premium": 800.0 },
      { "reference": "Q-000123", "option": 2, "layer": 2, "premium": 500.0 }
    ]
  }
]
```

The reference and option are the request's, read from its `quote_details`; in code, `run()` answers as quote `PREVIEW`, option 1, and `score()` with the `quote_details` frame it is given, leaving them out without one.

Values that are null, and tables with no rows, are left out of the response, as in a Quote Response's.

A Workbench Output answers one quote per request. A deployed pipeline refuses a request of several quotes at its Workbench Input ("A Workbench Input reads one quote per request, and this request holds 2."); a frame that reaches a table with one row per quote with other than one row, as `pipeline.score()` can bring, stops the run with "The Workbench Output's '...' table has one row per quote, but the frame connected to it has 2 rows."

## TABLES FROM THE WORKBENCH

The panel lists the workbench's output tables and shows how the frames connected to them fill them. Nothing in it can be changed here:

| Shown | What it means |
|---|---|
| The table's name | The input's name. |
| **one per quote** or **many per quote** | Whether a quote has one row of the table or a list of them. |
| **From** ... or **Not connected** | The node whose frame fills the table, or that nothing does yet. |
| Each column's name and type, in a box | The box's border is **red** when the column is missing: the connected frame has no column of exactly that name, or nothing is connected to the table. It is **orange** when the frame has the column but of a type that doesn't fit; hold the pointer over the box to see the frame's type. Either way the column is left empty when the pipeline runs. The borders follow the frame as the node it comes from was last previewed, so preview that node to bring them up to date. A table's **reference** and **option**, which the response fills, are never red or orange. |
| A warning under the name | The name can't be an input: it isn't an identifier, or another table has it. Rename the table in the workbench. |
| **No columns yet, so no port.** | The table has no columns, so there is nothing to fill. Add its columns in the workbench. |

The tables are the workbench's schema, edited in the Workbench view: **Edit in Workbench** in this section opens it, and the switcher at the bottom of the node palette brings you back to **Pricing**. When you save (**Save**, or Ctrl+S, from either view), the editor fetches the tables again, the node's tables and inputs follow, and the pipeline is saved with them in the same save. Connections follow table names: a connection to a table that keeps its name stays, and one to a table that was renamed or removed is removed, with a message. Changes not yet saved in the Workbench are priced on the Workbench's own sheets but don't reach the pipeline until saved.

While the workbench is not enabled, the section says "The workbench is not enabled in haute.toml, so these tables are the last copy and nothing updates them." The pipeline still runs, tests and deploys from that copy. Inside a submodel the section says "The editor updates these tables only at the pipeline's top level."

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/workbench_output/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.workbench_output(config="config/workbench_output/<node name>.json")`. Each connection names the table it fills: `pipeline.connect("priced", "response", target_port="pricing_output")`.

    | Setting in the editor | Stored as |
    |---|---|
    | The tables | `tables`: the workbench's output tables, in the Workbench Input's format: each its `name`, its `rows` per quote and its typed `columns` |

    The pipeline fills the tables from `tables` alone, so it runs, tests and deploys without the workbench enabled.

**See also:**

- [Workbench Input](workbench-input.md)  - the entry point whose tables the workbench defines
- [Quote Response](output.md)  - the response whose columns you map yourself
