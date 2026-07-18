# P11 — Low-priority perf nits (batch, single reviewer OK)

**Severity:** LOW · **Effort:** S each. None is urgent; batch them opportunistically. Where a "fix"
is speculative, measure first — do not add complexity for unmeasured wins.

## FR-41 — `execute_sink` re-parses the entire CSV it just wrote to count rows
**`executor.py:1663-1674`** — parquet path is fine (`scan_parquet(...).select(pl.len())` reads
metadata); the CSV path is a full re-parse, doubling I/O for large CSV sinks. Options: count during
the write (not supported by `sink_csv`), or scan with minimal options; simplest honest improvement:
keep parquet as-is and, for CSV, count lines via a buffered byte scan minus header (respecting quoted
newlines is why polars is used — if exactness under embedded newlines matters, leave as-is and just
document the cost). Decide with a benchmark; documenting is an acceptable outcome.

## FR-42 — chunking loop nits (`chunking.py`)
- `:1687-1697` — `checkpoint_dir.mkdir(parents=True, exist_ok=True)` per chunk → hoist before the
  chunk loop (or a created-flag).
- `:898` — `node_id in pre_chunk_node_ids` tuple membership inside the node loop → build a set once.
- `:1700-1716` — per-write failure path calls `checkpoint_dir.rmdir()` which is expected to fail (and
  warn) once earlier chunks wrote files; the outer `_cleanup_written_checkpoints` (:1207-1212) already
  owns cleanup → drop the inner rmdir.

## FR-43 — `pipeline.py` Node arity
**`:56-61`** — `n_inputs` recomputes `inspect.signature` per access (used per `__call__`); compute
once at registration (`functools.cached_property` or store on the Node). Also count only
POSITIONAL_ONLY/POSITIONAL_OR_KEYWORD params and reject `*args` node signatures loudly if
unsupported (today they miscount).

## FR-44 — model scorer minor paths (`_model_scorer.py`)
- `:962-982` — the live + categorical-levels path collects the FULL unprojected frame for validation
  and applies `write_projection` only afterwards; push `_score_input_projection_columns` into the
  validation collect, mirroring `_score_eager_unified` (:631-638).
- `:1116` — the CatBoost numeric fast-path `to_numpy()` yields a column-major (non-C-contiguous)
  array; CatBoost re-copies it into its Pool. Only touch if profiling shows the copy matters
  (`order="c"`); otherwise add a one-line comment.

## FR-45 — `_node_apply.py:136-138` — numpy → Python list → `pl.lit` round-trip
`pl.lit(vals.astype("float32").tolist())` → `pl.lit(pl.Series(vals.astype("float32")))` (or
`pl.linear_space`). Grids are small; trivial.

## FR-46 — cache-internals notes
- `_lru_cache.py:288-310` — byte-driven multi-eviction re-runs the O(n) pinned-count sum in the while
  guard and re-materialises `list(self._data.keys())` per single eviction → O(n·k). Harmless at n≤128;
  fix only if cache sizes grow (single pass from the LRU end).
- `_stat_gated_cache.py:79` — `clear()` racing an in-flight load can transiently double-load (both
  loads gate-checked, so never stale — a redundant read, not a bug), and `_entries`/`_load_locks`
  prune only on `clear()`. Bounded for config/schema usage; add a docstring note that keys must be
  bounded.

## FR-47 — per-request whole-graph fingerprint serialisation (measure before acting)
`graph_fingerprint`'s structural base is cached per `PipelineGraph` INSTANCE
(`_cache.py:495 graph._haute_base_fingerprint`), but routes build a fresh graph per HTTP request, so
every preview click canonical-JSONs the entire graph — including multi-thousand-row rating-table
configs — in pure Python. For big pipelines this could be a per-click cost in the tens of ms.
**Action:** benchmark first (a synthetic 100-node graph with 20×2k-row rating tables through
`execute_graph` twice); if material, add a content-addressed cross-request memo (keyed by the raw
request body hash) or persist the base fingerprint on the parsed-graph cache the routes already use.
Do NOT build this without the measurement. (Partially superseded by P05: lineage scoping shrinks the
hashed payload for upstream clicks.)
