# Source Switch

You want your pipeline to use live API data in production but a batch file during development. The Source Switch lets you wire up both paths and toggle between them.

!!! info "When to use"
    Use this when your pipeline needs to work with different data sources depending on the context  - typically batch data for development and live API data in production. You won't need this for your first pipeline.

A **source** is a named mode of the pipeline, such as `live` or `batch`. The Source Switch takes one input per source you want to support, and outputs whichever input is mapped to the active source. Everything downstream sees the same columns regardless of which source is active.

A pipeline can have several Source Switches, for example one for policies and one for claims. Every Source Switch follows the same active source, so switching the source in the toolbar switches them all. The panel has two tabs: **CONFIG** and **COLUMNS**.

## Choosing the active source

The active source is chosen for the whole pipeline in the toolbar's **Source:** dropdown, not in the node. Only `live` exists at first:

- **Add source**, at the foot of the dropdown, asks for a name (such as `batch`); press **Enter** to create it and make it active. A name that clashes with an existing source is refused with "Matches existing source" and that source's name.
- **Remove "batch"** (named after the active source) deletes the active source. The `live` source cannot be removed.

## The CONFIG tab

A banner reads "Routes inputs based on the active source". Below it:

| Field | What it does |
|---|---|
| **ACTIVE SOURCE** | Shows the active source, read-only ("Change the active source in the toolbar dropdown"). |
| **INPUT → SOURCE MAPPING (N)** | One row per connected input, with a list of the pipeline's sources. Choose the source each input serves; `-` means the input serves no source. The row mapped to the active source is highlighted and marked **active**. |

## The COLUMNS tab

The **COLUMNS** tab chooses which columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Example

1. Connect your Quote Input's `quotes` table and a Data Input called `batch_data` to a Source Switch.
2. In the toolbar's **Source:** dropdown, click **Add source** and create `batch`.
3. In the Source Switch's **INPUT → SOURCE MAPPING**, set `quotes` to `live` and `batch_data` to `batch`.

When the active source is `live`, the node outputs data from the `quotes` input. When you switch the toolbar to `batch`, it outputs data from the `batch_data` input.

!!! warning "Unmatched source"
    If the active source doesn't match any input mapping, the Source Switch node itself fails with an error naming the missing source, such as `Live switch 'switch' has no input for scenario 'batch'`. A Source Switch with no input mapped, whether new or with every input set back to `-`, passes through its first input.

!!! note "At deployment"
    The deployed pipeline runs with the `live` source active, so every Source Switch must map one input to the source named exactly `live`.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/source_switch/<node name>.json`, which the node's decorator in the pipeline's `.py` file names: `@pipeline.live_switch(config="config/source_switch/<node name>.json")`. The pipeline's sources themselves are not part of the node.

    | Setting in the editor | Stored as |
    |---|---|
    | **INPUT → SOURCE MAPPING** | `input_scenario_map`: each input's name and the source it serves, e.g. `{"quotes": "live", "batch_data": "batch"}`; an input set to `-` is left out |
    | **COLUMNS** tab | `selected_columns` |

    With no editor control: `inputs`, the names of the connected inputs, which Haute records from the canvas.

**See also:**

- [Quote Input](quote-input.md)  - the live entry point
- [Data Input](data-input.md)  - for batch data
