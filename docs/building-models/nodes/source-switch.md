# Source Switch

You want your pipeline to use live API data in production but a batch file during development. The Source Switch lets you wire up both paths and toggle between them.

!!! info "When to use"
    Use this when your pipeline needs to work with different data sources depending on the context  - typically batch data for development and live API data in production. You won't need this for your first pipeline.

## How it works

A **source scenario** (a **source** in the editor) is a named mode (e.g. "live" or "batch"). Each input to the Source Switch node is mapped to a scenario. The node passes through whichever input matches the active scenario.

In the editor, the toolbar's **Source:** dropdown chooses the active source. Only `live` exists at first: create others, such as `batch`, with **Add source** in that dropdown, and remove the active one there too. The node's editor shows the **Active Source** and, under **Input → Source Mapping**, the source each input is mapped to. At deployment, the live scenario is activated automatically, so one input must be mapped to the source named exactly `live`.

The Source Switch accepts multiple inputs  - one per scenario you want to support. A pipeline can have only one Source Switch.

| Config | Description |
|---|---|
| `input_scenario_map` | **Required.** Maps each input name to a source scenario |

**Example:**

```json
{
  "input_scenario_map": {
    "quotes": "live",
    "batch_data": "batch"
  }
}
```

When the active scenario is `"live"`, the node outputs data from the `quotes` input. When switched to `"batch"`, it outputs data from the `batch_data` input. Everything downstream sees the same columns regardless of which source is active.

!!! warning "Unmatched scenario"
    If the active scenario doesn't match any input mapping, the Source Switch node itself fails with an error naming the missing source, such as `Live switch 'switch' has no input for scenario 'batch'`. If no input is mapped at all, the node passes through its first input.
