# IO11 — I/O hygiene batch: small, real, mechanical

**Severity: LOW (each) · Effort: S each · Review mode: batch — one reviewer for the lot**

Verified items from all reviewers that are individually small. Fix as one batch commit series;
none changes behaviour a healthy pipeline depends on.

## JSON / apiInput

- **a. Bracket path with an embedded dot silently mis-resolves** *(verified)*:
  `_BRACKET_NAME` (`_jsonpath.py:63`) accepts `['a.b']` as one segment, but
  `parse_column_path_full` re-joins with `.` (`_api_input_schema.py:345`) and `_resolve_leaf`
  re-splits on `.` (`_json_shred.py:581`) — so `$[:]['a.b']` reads nested `a→b`, not the
  literal key. Only reachable by hand-editing a non-canonical path. Fix: reject a dot inside a
  bracket name at parse time (the escape and the dotted-leaf walker are incompatible).
- **b. Build/status `columns` field is placeholder junk** *(verified)*:
  `_aggregate_v2_tables` (`routes/json_cache.py:298-299`) emits `{f"{label}.col{ci}": "v2"}` —
  fake names, constant "v2" — in fields typed as column→dtype maps
  (`schemas.py:715,746`). No consumer today (grep clean), which is exactly why it's a trap.
  Populate from the real per-frame schema (parquet footer / meta `tables[]`) or drop the field
  + schema entry.
- **c. Duplicate keys: config rejects, data last-wins silently** *(read)*: `_read_v2_config`
  uses `reject_duplicate_keys_hook` (`routes/json_cache.py:234-237`); data records go through
  `orjson.loads` (`:382,391`) which keeps the last duplicate silently. orjson has no pairs
  hook, so rejecting costs a slower parser on the hot path — a genuine tradeoff. Decision for
  Ralph: accept + document the asymmetry in the module docstring (cheap), or spot-check
  duplicates in the sampled inference pass only (middle ground). Do not silently leave it
  undocumented.
- **d. Build cancel is a permanent stub**: `cancel_json_cache_build` (`routes/json_cache.py:450`)
  always returns `cancelled=False` while `_BUILD_TIMEOUT` allows 1800 s builds. Either wire
  real cancellation (the job-lifecycle pattern exists in `routes/_job_lifecycle.py`) or state
  the limitation in the UI copy. Roadmap-note severity.

## Databricks source

- **e. `clear_cache` TOCTOU**: `_databricks_io.py:180-186` — `cached_path()` then `unlink()`
  with no `missing_ok`; two concurrent clears (or clear racing a fetch's atomic replace) raise
  `FileNotFoundError`. Fix: `unlink(missing_ok=True)`.
- **f. Backtick-sensitive cache identity**: `_cache_path_for` (`:163`) keeps backticks, and
  `_TABLE_NAME_RE` admits backtick-quoted parts — `` `cat`.`sch`.`tbl` `` and `cat.sch.tbl`
  cache to different files (redundant refetch, split identity). Fix: strip backticks in
  `safe_name` so identity is canonical.

## File routes

- **g. `browse_files` runs on the event loop and follows symlinks** *(read)*:
  `routes/files.py:42-56` stats every entry inline (no `run_in_threadpool`, unlike
  `get_schema` `:153`); a broken symlink makes `entry.stat()` raise → 500; a symlink pointing
  outside cwd is listed with its real size. Fix: threadpool the listing, `lstat`/skip broken
  links, and don't follow out-of-root symlinks (containment is `validate_safe_path`'s intent).

## Extension sniffing (read-side counterpart of IO05-a; registry-adjacent but shippable now)

- **h. No `.ndjson` alias**: `_source_format` (`_io.py:156`) accepts only `.jsonl` for NDJSON;
  `.ndjson` is a common spelling of the same thing → "Unsupported file type: .ndjson".
  One-line registry-entry/extension-tuple addition once IO12 lands; acceptable as an
  `endswith` addition before that.
- **i. Compressed-CSV error names the wrong thing**: `x.csv.gz` → `suffix = rsplit(".", 1)` →
  `"Unsupported file type: .gz"` (`_io.py:161-163`) — the message hides that the file *is*
  CSV. Polars scans `.csv.gz` transparently (verified, format-map report §2), so either
  support it via the registry's multi-suffix extensions (`(".csv", ".csv.gz")` — IO12) or at
  minimum report the full compound suffix in the error. Note: `.csv.gz` must NOT become
  chunkable (`chunking.py:1536` allow-list stays `{.parquet, .csv}` — not range-seekable).

## TDD note

Every item is a one-assert failing test first: (a) parse rejects `['a.b']`; (b) build response
`columns` carries real names/dtypes or the field is gone from the schema + guards; (e) double
clear_cache is a no-op; (f) both table spellings hit one cache file; (g) broken symlink →
listed-as-skipped not 500; (h) `.ndjson` scans; (i) error message contains `.csv.gz`.
