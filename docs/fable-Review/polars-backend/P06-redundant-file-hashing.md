# P06 — Redundant full-file hashing on request paths

**Severity:** MEDIUM (O(filesize) per request; multi-GB sources pay full extra reads) · **Effort:** S–M

Files: `src/haute/execution.py`, `src/haute/_json_shred.py`

---

## FR-16 [MEDIUM] — batch input fingerprints bypass the stat-gated memo in the same module
**`execution.py:297-321` (`_runtime_path_fingerprint`), `:369-380` (`dataframe_paths_input_fingerprint`), `:454-470` (`_runtime_input_fingerprint_entry`), `:473-509` (`dataframe_graph_input_fingerprint`)**

### Evidence
- `_runtime_path_fingerprint` content-hashes the whole file (`content_hash(resolved)`, :320).
- The **preview/trace** key path wraps it in `_stat_gated_runtime_path_fingerprint` (:331-366): a
  process-wide `(mtime_ns, size)`-gated memo with a double-stat torn-read guard, so unchanged inputs
  cost one `stat`, never a re-hash.
- The **batch** key paths do NOT: `_runtime_input_fingerprint_entry` (used by
  `dataframe_graph_input_fingerprint`) and `dataframe_paths_input_fingerprint` call
  `_runtime_path_fingerprint` directly.
- Callers that pay the full hash per request: `executor.py:1604` (`execute_sink`),
  `deploy/_scorer.py:828`, `routes/_explore_service.py:659`, `routes/_optimiser_service.py:4251`,
  `routes/_train_service.py:856`. An explore/train/optimise request over an unchanged 5 GB CSV reads
  the whole file once just to build the cache key, then again to execute.

### Fix design
Route both batch paths through `_stat_gated_runtime_path_fingerprint` (it already returns the
identical payload shape and handles missing paths/directories by delegating to the raw function).
That is a 2-line change per site. Keep the documented trade-off note: a same-size same-mtime rewrite
is below the gate's resolution — this is the SAME accepted trade the preview path already makes
(docstring at :340-348); mention in the docstrings of the two batch helpers that they now share it.

### TDD plan
1. Failing test: spy on `content_hash` (monkeypatch in `haute.execution`); call
   `dataframe_graph_input_fingerprint` twice on an unchanged temp file; assert `content_hash` runs
   once (today: twice).
2. Invalidation test: touch/rewrite the file (change size or mtime_ns); assert re-hash occurs and the
   fingerprint changes.
3. Payload-shape parity: fingerprints for missing path / directory / file identical to before.

---

## FR-17 [MEDIUM] — JSON-shred load path SHA-256s the whole data file every load, twice on fallback
**`_json_shred.py:1190-1195` (`load_v2_api_source`), `:279-305` (`_data_file_matches` → `_hash_file`), build path `:926` + `:949`**

### Evidence
- `load_v2_api_source` calls `is_per_port_cache_valid` for the working layer, then again for the
  committed layer (:1191-1194); each reaches `_data_file_matches` → `_hash_file` (:305), streaming the
  entire file. Stale-working fallback → the same `data_path` fully hashed twice back-to-back.
- `build_per_port_cache` no-op trapdoor (:926) hashes via the validity probe, then the build hashes
  again via `_data_file_signature(dp)` (:949) — twice per data-only rebuild.
- Note: the module deliberately always verifies content (correctness-over-speed stance, documented) —
  do NOT weaken that; only deduplicate within one call.

### Fix design
- Compute the data-file content hash **once per `load_v2_api_source` call** and pass the signature
  into both layer checks (one path cannot hash differently between the two checks microseconds apart —
  and if it could, the existing stat-gate double-check pattern covers it).
- Same for the build path: compute `_data_file_signature` first and feed it to the validity probe.
- Optional (opt-in only, to preserve the strict stance): a process-local memo keyed on
  `(os.path.normcase(path), size, mtime_ns)` to skip re-hashing across loads, mirroring
  `_stat_gated_runtime_path_fingerprint` including its documented caveat. Gate behind an env flag or
  leave out — the per-call dedup is the uncontroversial win.

### TDD plan
1. Failing test: spy on `_hash_file`; stale-working fixture (working exists, invalid; committed valid);
   one `load_v2_api_source` call → assert `_hash_file` called once for the data file (today: twice).
2. Data-only rebuild: spy count == 1 through `build_per_port_cache` (today: 2).
3. Correctness: byte-changed file with preserved size+mtime still detected (the always-hash stance
   must survive — this is the test that forbids replacing the fix with a stat-only gate).

---

## FR-18 [LOW-MEDIUM] — in-memory frame fingerprint round-trips through Python lists
**`execution.py:383-398` (`dataframe_frame_input_fingerprint`)**

```python
"row_hash": content_hash_bytes(
    ",".join(str(value) for value in input_df.hash_rows(seed=0).to_list()).encode()
),
```
`hash_rows` already returns a UInt64 Series; `.to_list()` + per-value `str()` + join allocates O(n)
Python objects for large frames. **Fix:** hash the buffer directly —
`content_hash_bytes(input_df.hash_rows(seed=0).to_numpy().tobytes())` (document that this changes the
digest — it's a cache key, so the one-time invalidation is acceptable; note it in the commit message).
Also note in the docstring that `hash_rows` is not stable across Polars versions (upgrade ⇒ one-time
cache miss) — already implicitly accepted, make it explicit.

**Test:** fingerprint of a 1M-row frame completes without materialising a Python list (spy on
`Series.to_list`), and two identical frames produce equal fingerprints / a one-cell change produces a
different one.
