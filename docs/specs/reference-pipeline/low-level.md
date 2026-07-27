# Reference Pipeline — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `rating/__init__.py` | Marks the reference project directory as a Python package, matching the layout scaffolded by `haute init`. |
| `rating/main.haute.json` | Stores the main pipeline canvas positions, available source list, and active source selection. |
| `rating/main.py` | Generated `pipeline` graph object for the my-pipeline example: v2 JSON API input, output node, and four named port connections. |
| `rating/config/quote_input/quotes.json` | V2 API-input sidecar defining emitted quotes/drivers/vehicles/licenses tables, JSONPath columns, inferred statuses, and opaque contract. |
| `rating/config/quote_response/Quote_Response_9.json` | Output sidecar defining JSON output mappings for columns from each input port and an empty input/output contract declaration. |
| `rating/modules/model_stuff.py` | Generated `submodel` graph object for the model-stuff example, with Polars transforms, a scenario-expander transform, and its internal graph connections. |
| `rating/utility/__init__.py` | Marks the project utility package and supplies its module documentation. |
| `rating/utility/features.py` | Project-authored Polars date/interval, postcode, dotted-column cleanup, and predicate-based column selection helpers. |
| `rating/models/conversion.rsglm` | Checked-in project GLM model artifact. |
| `rating/models/Model_Training_11.rsglm` | Checked-in project GLM model artifact. |
| `rating/models/modelling_8.rsglm` | Checked-in project GLM model artifact. |

The map deliberately lists only tracked reference files. This is a
non-runnable layout/example snapshot, not an executable compatibility fixture:
rating/data/quotes/nest_example.json, the generated main pipeline's
project-relative quote-data input, and
rating/config/expander/premium.json, the generated submodel's sidecar, are
missing from the tracked tree. Local output directories, additional local model
files, and Python bytecode may also exist in a working directory but are not
part of this checked-in reference-pipeline specification.

The repository-root `haute.toml` points `[project].pipeline` at `rating/main.py`;
the canonical project resolver therefore treats this reference graph as the
repository's authoritative default pipeline.

## Key types and data structures

- **Main metadata** in `rating/main.haute.json` is a JSON object containing
  `positions` keyed by node ID, `sources`, and `active_source`. `active_source` must be one of
  `sources`. It is UI/project metadata, not executable graph wiring.
- **API-input v2 sidecar** in `rating/config/quote_input/quotes.json` has a
  top-level `path`, `contract`, and `tables` array. Each table declares a
  JSONPath `path`, `displayPath`, label, emit flag, optional row-id column, and columns with
  name/path/type/status/selection/levels fields.
- **Output sidecar** in `rating/config/quote_response/Quote_Response_9.json`
  has `outputMapping` entries (`source_port`, `source_column`, `output_path`,
  `enabled`), `outputFormat`, and a contract object.
- **Generated main pipeline** is a `haute.Pipeline` whose API-input decorator names
  the relative path "config/quote_input/quotes.json" and its opaque contract. The callable returns
  `pl.LazyFrame | dict[str, pl.LazyFrame]`; this four-table sidecar produces the mapping form.
  Its output callable accepts four lazy-frame ports and returns a lazy frame.
- **Generated submodel** is a `haute.Submodel` with `@submodel.polars` and
  `@submodel.scenario_expander` callables. Its generated contracts are either
  explicit input/output column lists or the string `"opaque"`.
- **Utility helper interfaces** include `to_date(col_name, fmt) -> pl.Expr`,
  `years_between(earlier, later) -> pl.Expr`, `months_between(earlier, later)
  -> pl.Expr`, `days_between(earlier, later) -> pl.Expr`,
  `postcode_area(col_name) -> pl.Expr`, `clean_columns(df) -> pl.LazyFrame`,
  and `cols_matching(all_cols, pattern_fn) -> list[str]`.

## Control flow

1. Parsing `rating/main.py` loads and validates both decorators' referenced sidecars. Importing
   the file constructs `haute.Pipeline("my_pipeline")`, registers the decorated `quotes` and
   `Quote_Response_9` functions, then declares the four source-port connections.
2. Running `quotes()` passes the relative path "config/quote_input/quotes.json" and the resolved generated-script
   directory to `resolve_api_input_from_config()`. The shared helper validates the sidecar,
   resolves its data path within the project boundary, and calls `load_v2_api_source()` to yield
   the multi-table lazy input. The missing data path fails at the loader's filesystem metadata
   read before any lazy frames are returned.
3. `Quote_Response_9()` receives the four graph ports but returns the first
   (`quotes`) unchanged. The `Quote_Response_9` sidecar separately tells the
   output mechanism how port columns map to nested JSON paths.
4. Importing `rating/modules/model_stuff.py` constructs its submodel, registers
   `sale_flag`, `competitor_features`, and `premium`, and connects `sale_flag`
   to the latter two nodes. At execution, `sale_flag` derives `sale_flag` and
   `burn_cost`; `competitor_features` derives a premium ratio; `premium` applies
   the scenario multiplier.
5. Project-authored code may import `rating/utility/features.py` helpers to
   construct Polars expressions. The model artifacts have no executable import
   path in this reference; a model workflow must select/load them separately.

## Edge cases and invariants

- The tracked quote-input sidecar has four emitted table labels that match the
  four `pipeline.connect()` source ports and the output function's parameter
  order: `quotes`, `drivers`, `vehicles`, and `licenses`.
- `rating/main.py` requires a v2 `tables` list even though its decorator contract
  is `"opaque"`; it rejects an absent/non-list table map before calling the
  loader.
- The generated `Quote_Response_9` function accepts `quotes_2`, `quotes_3`, and
  `quotes_4` but does not read them directly. Their contribution is represented
  in the output sidecar mappings, not by Python-frame transformations in the
  function body.
- `clean_columns()` only calls `LazyFrame.rename()` when a schema column contains
  `.`; otherwise it returns the input lazy frame. `cols_matching()` preserves
  incoming column order because it filters the supplied list directly.
- The reference is non-runnable from the checked-in tree: it has no tracked
  rating/data/quotes/nest_example.json at the generated main pipeline's
  expected project-relative location and no tracked
  rating/config/expander/premium.json sidecar named by the submodel decorator.
  This is a property of the snapshot, not a claim that a maintainer's local
  project cannot supply either file.
- `.rsglm` contents are opaque to this component. Their filenames do not imply
  compatibility with every installed Haute/model-library version.

## Error handling

- Sidecar/data filesystem operations propagate errors such as `FileNotFoundError`; the current
  missing quote data is detected by `load_v2_api_source()` when it stats the resolved data path.
  No placeholder frame or default JSON is supplied.
- `resolve_api_input_from_config()` propagates shared sidecar shape and v2 schema errors.
- `features.to_date()` and other Polars helpers defer expression errors until
  Polars evaluates the enclosing plan; `clean_columns()` can propagate schema
  collection/rename errors. They intentionally do not swallow invalid columns,
  types, formats, or predicate failures.
- Generated decorators/connections and output mappings rely on Haute's generic
  parser/runtime validation for graph, port, contract, and mapping failures;
  this reference adds no error translation around those operations.

## Testing

- `tests/test_output_nest_example_contract.py` snapshots both reference sidecars.
- `tests/test_docs_accuracy.py` requires every tracked `rating/` Python, JSON, and model artifact
  to appear in this module map.
- The generic mechanisms it exercises are covered by active tests such as
  `tests/test_pipeline.py`, `tests/test_v2_codec_and_shred.py`,
  `tests/test_apiinput_multi_port_runtime.py`, `tests/test_output_assembler.py`,
  and `tests/test_scenario_expander.py`.
- This indirect coverage does not certify that the reference's missing local
  data, model artifacts, generated code, or sidecars form a runnable complete
  scenario. A maintainer changing it should add focused coverage if they intend
  to promote it from example material to an executable contract.
