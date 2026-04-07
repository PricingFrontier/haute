# Source Switch

You want your pipeline to use live API data in production but a batch file during development. The Source Switch lets you wire up both paths and toggle between them.

!!! tip "Spreadsheet equivalent"
    Like having two versions of a data tab and a toggle to switch between them.

!!! info "When to use"
    Use this when your pipeline needs to work with different data sources depending on the context  - typically batch data for development and live API data in production. You won't need this for your first pipeline.

## How it works

A **source scenario** is a named mode (e.g. "live" or "batch"). Each input to the Source Switch node is mapped to a scenario. The node passes through whichever input matches the active scenario.

In the UI, you can toggle the active scenario with a dropdown. At deployment, the live scenario is activated automatically.

The Source Switch accepts multiple inputs  - one per scenario you want to support.

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
    If the active scenario doesn't match any input mapping, the node produces no output and downstream nodes will show an error.
