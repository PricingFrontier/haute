# IO10 — apiInput hot paths: full-file SHA-256 on every load, row-store→column transpose in the shred

**Severity: MEDIUM (perf on the interactive path) · Effort: M · Review mode: pair (a — cache identity), batch (b–d)**

`_json_shred.py` is the hottest file in the repo by edit telemetry; these are the two real
costs plus two hygiene items. From the JSON-input reviewer (read + targeted verification).

---

## IO10-a — Every `load_v2_api_source` re-hashes the entire source file (MEDIUM)

`is_per_port_cache_valid` (`_json_shred.py:1210`) → `_data_file_matches` (`:279`) →
`_hash_file` (`:271`) reads and SHA-256s the **whole** data file on every load — i.e. on every
preview/execution of a pipeline containing a JSON apiInput — with only a size pre-check
(`:303`) and a *deliberately removed* mtime short-circuit (`:288-296`). A multi-hundred-MB
source is fully re-read per preview even when unchanged.

**Assessment (present to Ralph as a tradeoff to ratify, per the reviewer):** the always-hash
is a documented correctness stance — a same-size, same-mtime, byte-changed rewrite must not
serve stale rating rows. The safe optimisation is an **in-process memo keyed by
`(resolved_path, size, mtime_ns)`**: repeated loads of an unchanged file skip the re-read; any
size/mtime change re-hashes. This narrows (does not reopen) the pathological window to
within a single process lifetime — an explicit decision, not an accident. This is the same
stat-gated pattern the repo already established for preview/trace
(`_stat_gated_cache.py`; cross-ref `fable-Review/polars-backend/P06` and `eda-node/E04`, which
fixed this exact class elsewhere) — reuse it rather than inventing a new memo.

**TDD.** With the memo: two loads, no edit → one hash call (spy/counter); touch mtime or
change size → re-hash; content-change-with-same-stat behaviour documented in the test name.

## IO10-b — Shred builds per-row dicts, then transposes per column (MEDIUM)

`shred_to_buffers` accumulates `dict[label, list[dict]]` (`:713`); `_emit_row` allocates a
fresh dict per emitted row (`:729-736`); `_buffer_to_frame` then transposes with
`[row.get(col) for row in rows]` per column (`:833`) — one dict + N inserts per row, then
N lookups × columns, a row-store→column-store conversion on the hottest path.

**Fix.** Accumulate columnar from the start: per table, `dict[col_name, list]`, appending the
resolved value in `_emit_row`; `_buffer_to_frame` consumes the lists directly. Ancestor
distribution and skip accounting are untouched. Behaviour-preserving — the property suites
pin it; add an output-equality test against a broad fixture and a timeit note in the PR.

## IO10-c — JSON-array builds hold the full parsed file + all row buffers (LOW, comment rot)

`_iter_records` (`:388-399`) materialises a JSON array wholesale (`orjson.loads(read_bytes())`)
and keeps it alive while the generator drains; the W1 comment at `:966-971` ("not materialised
into a list … no full extra copy") is true only for JSONL. Correct the comment; document
NDJSON as the scalable transport (already hinted at `_io.py:484-487`). No behaviour change.

## IO10-d — `_BUILD_LOCKS` grows unbounded (LOW)

`_build_lock_for` (`:322`) `setdefault`s one `Lock` per resolved cache dir forever. Bound it
(careful: an in-use lock must not be dropped mid-build — a `WeakValueDictionary` holding
strong refs during the build, or an LRU with in-use pinning).

---

Cross-refs: IO04 must land first (same file; a/b rebase trivially but the property suites
IO04 adds are the safety net IO10-b relies on). `polars-backend/P06` for the hashing class.
