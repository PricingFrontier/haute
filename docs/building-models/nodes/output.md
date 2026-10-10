# Quote Response

You've calculated a price. Now you choose which columns to send back in the API response  - the final premium, any breakdown fields, a reference ID. Everything not listed here is still calculated but stays internal.

!!! info "When to use"
    Use this to define the API response for live pricing. For saving results to a file (batch scoring), use [Data Output](data-output.md) instead. You can use both in the same pipeline.

Each incoming connection is one frame. A single frame is the usual case; several frames let the response nest child arrays, such as each quote's drivers. Each array level of the response takes one frame: two frames that would both fill the top-level objects (or both fill one nested array) are rejected. Join them upstream with an [Edge Join](edge-join.md) or [Transform](transform.md) node, or map one of them into a child array. The node assembles the response document from the mapping described below.

## The panel

The node's panel has no tabs: its settings sit on one page, and it has no **COLUMNS** tab because the mapping below chooses the fields.

**RESPONSE CONFIGURATION**

**Output format** is the response format. **JSON** is the only format; a new node shows **-- select output format --** until you pick it, and the response is JSON either way.

**RESPONSE MAPPING**

With nothing connected, the panel says **Connect input frames to map them to the response.** Once frames are connected it shows, from top to bottom:

- **Frames (N)**: click to show each connected frame's columns and types. The icons beside it copy or download the list of frames (as tab-separated text, CSV or TSV) or the whole mapping as JSON.
- **Output preview**: expand it (or refresh it) to assemble the whole response document from the current mapping, before you save. It shows **The assembled document is empty (no enabled rows mapped, or no source rows).** when nothing would be returned.
- One block per frame, named after the frame and showing its field count, its path prefix and an **enabled** box.

A frame block has:

| Control | What it does |
|---|---|
| Path prefix | The frame's path in the response, for example `$[:]`. Double-click it (or click the pencil) to change it; the new prefix is applied across all of this frame's rows. |
| **enabled** | Includes or leaves out every field of the frame at once. |
| **Add row** | Adds an empty mapping row. |
| **Infer** | Adds one row per column of the frame, each with a suggested path and marked **Inferred** until you edit its column or path. Disabled until the frame's columns are known. |
| **Clear** | Removes all of this frame's mapping rows. |
| Table icons | Copy the frame's rows as tab-separated text or its mapping as JSON, download them as JSON, CSV or TSV, or paste tab-separated rows in (`column`, `output_path`, and optionally `enabled`). |
| **Input data** | Expand it to preview the frame's incoming rows as JSON. |

Each mapping row has a tick box (include this field in the response), the source column (**- column -** until you choose one), its output path (placeholder `$[:].field`) and a **×** to remove it. An empty path shows **An output path is required.**, and a path that clashes with another field's path in the same frame is flagged.

An output path uses the same path notation as the Quote Input tables: `$[:].final_premium` is a field of each top-level response object, and `$[:].drivers[:].age_factor` is a field of each object in a nested `drivers` array. Every path starts at the root array `$[:]`. A child frame nests under its parent by the parent-level fields it also maps, for example `$[:].quote_id`.

The panel warns when two connected frames resolve to the same name (give the sources distinct names before mapping them), and when two frames emit at the same array level (join them upstream or map one of them to a different level).

## Example

Returning the quote ID, the final premium and the area factor:

1. Connect your priced frame (here `priced`) to the Quote Response node.
2. Choose **JSON** as the **Output format**.
3. Expand the `priced` block and click **Add row** three times. Map `quote_id` to `$[:].quote_id`, `final_premium` to `$[:].final_premium` and `area_factor` to `$[:].area_factor`. (Or click **Infer** and untick or remove the rows you don't want.)
4. Expand **Output preview** to check the document.

The API response then holds only the quote ID, the final premium, and the area factor. All other columns (raw inputs, intermediate calculations) are still computed but not exposed. Fields whose value is null, and empty arrays or objects, are left out of the response.

!!! warning "One per pipeline"
    You can only have one Quote Response or [Workbench Output](workbench-output.md) node in a pipeline.

!!! note "Required for live pricing"
    A Quote Response or Workbench Output node is required for live pricing deployments. If your pipeline is batch-only (using Data Output), you don't need one.

??? note "In the pipeline file"
    The node's settings are stored in a JSON sidecar, `config/quote_response/<node name>.json`, which the pipeline's `.py` file names in the node's decorator: `@pipeline.output(config="config/quote_response/<node name>.json")`.

    | Setting in the editor | Stored as |
    |---|---|
    | **Output format** | `outputFormat`: `"json"` (a new node stores `""`) |
    | Mapping rows | `outputMapping`: one entry per row |
    | Row's frame | `outputMapping[].source_port`: the incoming frame's name |
    | Row's column | `outputMapping[].source_column` |
    | Row's output path | `outputMapping[].output_path` |
    | Row's tick box, frame's **enabled** box | `outputMapping[].enabled` (`true` or `false`) |

    The example above is stored as:

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

**See also:**

- [Data Output](data-output.md)  - save results to a file for batch scoring
- [Workbench Output](workbench-output.md)  - the response whose tables a workbench defines
- [Deployment guide](../../deployment/index.md)  - how the Quote Response node maps to your live API response
