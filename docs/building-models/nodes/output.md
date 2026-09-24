# Output

You've calculated a price. Now you choose which columns to send back in the API response  - the final premium, any breakdown fields, a reference ID. Everything not listed here is still calculated but stays internal.

!!! tip "Spreadsheet equivalent"
    Like choosing which columns to include in a final report tab  - everything is still calculated behind the scenes, but only selected fields are shown.

!!! info "When to use"
    Use this to define the API response for live pricing. For saving results to a file (batch scoring), use [Data Output](data-output.md) instead. You can use both in the same pipeline.

Each incoming connection is one frame. A single frame is the usual case; several frames let the response nest child arrays, such as each quote's drivers.

| Config | Description |
|---|---|
| `outputMapping` | **Required.** One row per response field: the incoming frame (`source_port`), the column (`source_column`), where it goes in the response (`output_path`) and whether the row is `enabled` |
| `outputFormat` | The response format: `"json"` |

An `output_path` uses the same path notation as the Quote Input tables: `$[:].final_premium` is a field of each top-level response object, and `$[:].drivers[:].age_factor` is a field of each object in a nested `drivers` array. A child frame nests under its parent by the parent-level fields it also maps, for example `$[:].quote_id`. In the UI, pick each frame's columns from a dropdown and edit their paths; **Infer** adds one row per column of a frame.

**Example:**

```json
{
  "outputMapping": [
    { "source_port": "priced", "source_column": "quote_id", "output_path": "$[:].quote_id", "enabled": true },
    { "source_port": "priced", "source_column": "final_premium", "output_path": "$[:].final_premium", "enabled": true },
    { "source_port": "priced", "source_column": "area_factor", "output_path": "$[:].area_factor", "enabled": true }
  ],
  "outputFormat": "json"
}
```

This returns only the quote ID, the final premium, and the area factor in the API response. All other columns (raw inputs, intermediate calculations) are still computed but not exposed. Fields whose value is null, and empty arrays or objects, are left out of the response.

!!! warning "One per pipeline"
    You can only have one Output node in a pipeline.

!!! note "Required for live pricing"
    The Output node is required for live pricing deployments. If your pipeline is batch-only (using Data Output), you don't need one.

**See also:**

- [Data Output](data-output.md)  - save results to a file for batch scoring
- [Deployment guide](../../deployment/index.md)  - how the Output node maps to your live API response
