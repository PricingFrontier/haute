# Output

You've calculated a price. Now you choose which columns to send back in the API response  - the final premium, any breakdown fields, a reference ID. Everything not listed here is still calculated but stays internal.

!!! tip "Spreadsheet equivalent"
    Like choosing which columns to include in a final report tab  - everything is still calculated behind the scenes, but only selected fields are shown.

!!! info "When to use"
    Use this to define the API response for live pricing. For saving results to a file (batch scoring), use [Data Sink](data-sink.md) instead. You can use both in the same pipeline.

This node accepts a single input.

| Config | Description |
|---|---|
| `fields` | **Required.** List of column names to include in the response |

These are the column names from the data flowing into this node. In the UI, available columns are shown in a dropdown.

**Example:**

```json
{
  "fields": ["quote_id", "final_premium", "area_factor", "age_factor"]
}
```

This returns only the quote ID, the final premium, and two factor breakdowns in the API response. All other columns (raw inputs, intermediate calculations) are still computed but not exposed.

!!! warning "One per pipeline"
    You can only have one Output node in a pipeline.

!!! note "Required for live pricing"
    The Output node is required for live pricing deployments. If your pipeline is batch-only (using Data Sink), you don't need one.

**See also:**

- [Data Sink](data-sink.md)  - save results to a file for batch scoring
- [Deployment guide](../../deployment/index.md)  - how the Output node maps to your live API response
