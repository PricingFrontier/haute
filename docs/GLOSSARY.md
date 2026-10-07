# Glossary

The vocabulary this project uses for its own concepts, and the register to use each word in. Where two words are interchangeable, that is said; where a word has more than one sense, all senses are listed, because the ambiguity is real and picking one silently is how documentation drifts.

## 1. Data model

- **frame** — a polars DataFrame or LazyFrame: the runtime payload carried on a port or edge. Use "frame" for runtime and internals discussion. In user-facing text the display language is column- and table-level, not "frame".

- **table** — a rectangle of data. Subsumes frame; in context the two are interchangeable — a frame *is* a table once you are talking about its contents rather than its runtime type. "Table" is also the unit of a schema mapping and the unit of the output assembler. When discussing the assembler, stick to **join constraints, tables, and fields**.

- **column / field** — interchangeable: a named column of a table. One carve-out — **field** can also mean a text-entry element in the UI. If there is any risk of that reading, say "column" for data and reserve "field" for the widget.

- **node** — a vertex of the pipeline graph.

- **port / data-port** — the unit of attention on a node. Each emitting table on a Quote Input node becomes a data-port emitting one frame. The identifying pair is (source node label, port name); single-port nodes have no port name.

- **edge** — the graph primitive connecting a specific data-port to a node input. This is both the canvas term and the code term.

- **connector** — the hoverable handle on a node that you drag an edge from or to. Not the edge itself.

- **link** — an informal synonym for edge in conversation. Write "edge" in documentation.

- **rail** — a sequence of directed edges whose neighbours share a common node, where none of those nodes emits or accepts multiple frames. Formally each stage is a different frame; in effect the rail cumulatively defines the transformation of *that* frame. Rails are made of edges — do **not** use "rail" to mean a single edge.

## 2. Node types and their names

Most node types carry up to four names: the **wire value** used in config and API payloads, the **config folder** on disk, the **canvas badge**, and the **UI label**.

**Register rule: use the UI label when discussing user-facing behaviour, and the backticked wire value when discussing config, code, or API payloads.** Do not mix registers in one sentence without reason, and do not use the config-folder name as a prose name at all.

| Wire value | Config folder | Badge | UI label |
|---|---|---|---|
| `apiInput` | `quote_input/` | QUOTE IN | Quote Input |
| `dataSource` | `data_source/` | SOURCE | Data Source |
| `polars` | — | POLARS | Polars |
| `modelScore` | `model_scoring/` | SCORING | Model Scoring |
| `banding` | `banding/` | BANDING | Banding |
| `ratingStep` | `rating_step/` | RATING | Rating Step |
| `output` | `quote_response/` | QUOTE OUT | Quote Response |
| `dataSink` | `data_sink/` | SINK | Data Sink |
| `explore` | — | EXPLORE | Explore |
| `externalFile` | `load_file/` | LOAD FILE | Load File |
| `liveSwitch` | `source_switch/` | SWITCH | Source Switch |
| `modelling` | `model_training/` | TRAINING | Model Training |
| `optimiser` | `optimisation/` | OPTIMISATION | Optimisation |
| `scenarioExpander` | `expander/` | EXPANDER | Expander |
| `optimiserApply` | `apply_optimisation/` | APPLY OPT | Apply Optimisation |
| `constant` | `constant/` | CONSTANT | Constant |
| `submodel` | — | SUBMODEL | Submodel |
| `submodelPort` | — | PORT | Port |

Two naming notes:

- **`tables` key collision.** The Quote Input config's `tables` are schema-mapping tables; the Rating Step config's `tables` are rating factor tables. Same JSON key, different domains, both user-visible in config files — qualify it in prose ("the Quote Input's tables", "the rating tables").
- **polars / transform.** The `polars` node is named for what it is today, a pure code node. A notebook-style variant is planned, at which point "transform" becomes the name. Some code and fixtures already use "transform"; treat it as trending, not settled, and do not rename anything unilaterally.

## 3. Caching and state

- **`.haute_cache/`** — the cache root at the project root. It holds more than one cache system — shredded parquet for structured inputs, remote-table parquet cache, output schema artefacts — so do not describe it as any single one of them.

- **working / committed cache layers** — the two layers of the structured-input parquet cache. The **working** layer is volatile and in-session, written by the explicit cache action; the **committed** layer is durable, promoted at save.
