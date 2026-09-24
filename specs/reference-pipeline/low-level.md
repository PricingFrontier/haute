# Reference Pipeline — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `examples/reference/main.py` | Generated `pipeline` graph object named `reference`: the `quotes` Data Input, the `features` Polars transform and the `priced` output, wired in a line. |
| `examples/reference/config/data_input/quotes.json` | Data Input sidecar: a scanned CSV at the project-root-relative path `examples/reference/data/quotes.csv`. |
| `examples/reference/config/quote_response/priced.json` | Output sidecar mapping `quote_id`, `vehicle_age`, `driver_band` and `sum_insured` from the `features` port to `$[:].<column>`. |
| `examples/reference/data/quotes.csv` | Six synthetic quotes: `quote_id`, `driver_age`, `vehicle_year`, `region`, `sum_insured`. |

The repository-root `haute.toml` points `[project].pipeline` at
`examples/reference/main.py`; the canonical project resolver therefore treats this
graph as the repository's default pipeline.

## Key types and data structures

- **Data Input sidecar**: `inputType` `file`, `format` `csv`, `mode` `scan`, a
  project-root-relative `path`, and empty `arguments`.
- **Output sidecar**: `outputMapping` entries (`source_port`, `source_column`,
  `output_path`, `enabled`) and `outputFormat` `json`.
- **Generated pipeline**: the decorators carry their sidecar paths and generated
  contracts; each body resolves its sidecar against the script directory
  (`_HAUTE_CONFIG_BASE`) and the project root.

## Control flow

1. `haute run` (or the editor) resolves `examples/reference/main.py` from the root
   `haute.toml` and parses it into the three-node graph.
2. Execution prepares the `quotes` Data Input's snapshot from the CSV, runs
   `features`, and assembles `priced` from its mapping.
3. `haute run` prints each node's row and column counts and the last node's rows.

## Edge cases and invariants

- `driver_band` is text (`(-inf, 25]`, `(25, 40]`, `(40, 65]`, `(65, inf]`), so
  the output mapping carries it as a JSON string.
- The six quotes produce six output rows; the example neither filters nor joins.
- `main.py` is the code generator's output for its graph; editing the graph in
  the editor rewrites it the same way it would any project pipeline.

## Error handling

- Input preparation, transform and output-assembly errors propagate through the
  generic execution contract; `haute run` reports the failing node and exits 1.
- The example supplies no fallback data and no error translation of its own.

## Testing

- `tests/test_reference_pipeline.py` copies the tracked root `haute.toml` and
  `examples/` into an empty directory and runs `haute run` there: every node
  succeeds and `priced` returns six rows of four columns.
- `tests/test_docs_accuracy.py` requires every tracked `examples/` Python, JSON and
  CSV file to appear in this module map.
- `tests/test_repository_hygiene.py` requires pipeline sidecars to live under
  `examples/reference/config/`, never at the repository root.
