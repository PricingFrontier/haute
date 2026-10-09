# Workbench Input

When your quotes are keyed in through the project's workbench, its schema already defines the quote's tables. The Workbench Input is the pipeline's entry point for those quotes: its tables are the workbench's input tables, copied into the pipeline and kept up to date as you change the schema.

!!! info "When to use"
    While the project's workbench is enabled in `haute.toml` (`[workbench] enabled = true`, which `haute init --workbench` writes), the palette offers the Workbench Input in place of the [Quote Input](quote-input.md). Use it as the entry point for live pricing of keyed-in quotes. Without a workbench, use the Quote Input and build its tables from a sample file.

A Workbench Input has no inputs. Each table of the workbench's schema is a separate output, named by the table's name; connect each output to the node that uses it. A pipeline has only one Quote Input or Workbench Input: once it has either, the palette entry is greyed out with "Only one Quote Input or Workbench Input allowed per pipeline". Enabling or disabling the workbench changes only what the palette offers; to swap a Quote Input for a Workbench Input, delete it and drag in the other.

The panel has no tabs. A banner at the top reads "This node receives live API requests at deploy time", and a note under it says what previews run on: "Previews run on the workbench's sample values; a table with none is one row of nulls." once the workbench supplies a sample, and "Previews run on one row of nulls per table until the workbench supplies values." until then.

The node reads no file. While you build, it runs on the workbench's sample quote: the values typed into the workbench's Tables and Collections while building, as saved in its form, `forms/form.json`, when the editor fetches the tables (each time it opens or reloads the pipeline). Each table is the sample's rows for it, with the types the workbench declares; a table you typed nothing into is one row whose values are all null, many-row tables included. Nodes downstream run on those rows, so a preview shows every node's values as the sample gives them. A node that can't take a null fails on a null as it would on any: a [Rating Step](rating-step.md) table whose factor is null finds no entry, so with an empty **DEFAULT** and **ON MISS** on **Stop the run** it stops with a `RatingTableMissError`. A value that doesn't fit its column, such as text in a number column, stops the previews with "The workbench's sample does not fit this Workbench Input's tables: ...": correct it in the workbench and save it. A live request is read through the tables as a Quote Input reads one, and never uses the sample.

## TABLES FROM THE WORKBENCH

The section lists the workbench's input tables. You can't edit them here:

| Shown | What it means |
|---|---|
| The table's name | The output's name, and the input name downstream nodes see. |
| **one per quote** or **many per quote** | Whether a quote has one row of the table or a list of them. |
| Its columns | Each column's name and type. |
| A warning under the name | The name can't be an output: it isn't an identifier, it's a Python keyword, or another table has it. Rename the table in the workbench. |

The tables are the workbench's schema, edited in its form, `forms/form.json` (the Workbench view that edits it inside Haute is on the roadmap). When the form is saved and the editor next fetches the tables, the node's tables and outputs follow, and the pipeline has changes to save, as after any edit. Connections follow table names: a connection to a table that keeps its name stays, and one to a table that was renamed or removed is removed, with a message. Changes not yet saved in the form don't reach the pipeline.

While the workbench is not enabled, the section says "The workbench is not enabled in haute.toml, so these tables are the last copy and nothing updates them." The pipeline still runs, tests and deploys from that copy. Inside a submodel the section says "The editor updates these tables only at the pipeline's top level."

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/workbench_input/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.workbench_input(config="config/workbench_input/<node name>.json")`.

    | Setting in the editor | Stored as |
    |---|---|
    | The tables | `tables`: the workbench's tables, in the same format as a Quote Input's |
    | The sample | `sample`: the workbench's sample quote, one quote as a request holds it (`{}` for none) |

    The pipeline reads a request through `tables` exactly as a Quote Input does, and its previews and `run()` read `sample`, so it runs, tests and deploys without the workbench enabled. Deploying takes the request's shape from the tables too, so it needs no sample file, and never reads `sample`: it checks the pipeline on one request whose values are all null, so a node that can't take a null stops the deploy as it stops a preview of a table with no values.

**See also:**

- [Quote Input](quote-input.md)  - the entry point whose tables you build from a sample file
- [Quote Response](output.md)  - for the columns the API returns
- [Workbench Output](workbench-output.md)  - for the output tables the workbench defines
