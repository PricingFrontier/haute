# P08 — RAM estimator: under-measured strings, self-disabling guard, resolver blow-up

**Severity:** MEDIUM (silent-wrongness: the OOM guard can green-light data that OOMs) · **Effort:** M · **Dev/reviewer pair: REQUIRED**

File: `src/haute/_ram_estimate.py` (context: `_polars_utils.py:301-344` `read_parquet_metadata`)

This module is the README's "Knows your machine's limits" feature — the failure mode it exists to
prevent is a silent OOM crash, so its own silent failure modes are the priority.

---

## FR-24 [MEDIUM, silent wrongness] — dictionary-encoded string columns under-measured 2.4×–19×
**`_ram_estimate.py:802-807` (`_source_column_base_widths`)**

### Evidence
Variable-width byte estimates are derived from Parquet `column_uncompressed_size_bytes`, which
`read_parquet_metadata` fills from `column.total_uncompressed_size` (`_polars_utils.py:323`). For
RLE_DICTIONARY-encoded columns — Polars' default for low/medium-cardinality strings —
`total_uncompressed_size` measures **dictionary + indices**, not the expanded in-memory form.
Verified by the reviewing agent: a 200k-row low-cardinality string column is ~1 byte/row in Parquet
but **19 bytes/row** in memory as `pl.String`; the estimator's 8-byte floor still under-measures
~2.4×, worse for wider strings. This feeds `peak_bytes = rows × bytes/row × 3.0` (:447-453), so
`estimate_safe_training_rows` can return "no downsampling needed" for a dataset that OOMs during
training — the exact crash class the module exists to prevent.

### Fix design
Estimate string width from an expansion-aware signal, in preference order:
1. Row-group statistics where present: average value length (or min/max lengths) per column ×
   row count, plus the String memory overhead (offsets: 8B/row on the offset buffer + data bytes).
2. A bounded sample read: `estimated_size() / height` over the first N rows of just that column
   (`chunking.py:767` already uses this pattern — reuse its helper or mirror it).
3. Only credit dictionary-size-based numbers when the column will actually be READ as
   Categorical/Enum (i.e., the scan schema says so).
Keep the existing 8-byte floor as a floor, not a substitute.

### TDD plan (failing first)
1. Build a parquet fixture with a low-cardinality 20-char string column (200k rows); assert the
   estimated bytes/row for that column is within 2× of `pl.read_parquet(...).estimated_size()/height`
   (today: off by >2×, test fails).
2. Wide-string fixture (200-char values) — same bound.
3. Categorical read-path fixture: when the column is read as Categorical, the smaller estimate is
   allowed (pin that the exemption requires the read dtype, not the parquet encoding).

---

## FR-25 [MEDIUM, silent wrongness] — broad `except Exception → return None` disables the guard
**`_ram_estimate.py:280-282` (`_count_source_rows_for_node`), `:322-324` (`_detailed_source_metadata_for_node`)**

### Evidence
Any exception — a corrupt/locked parquet (`ArrowInvalid`, `OSError`) but ALSO a programmer error
(`KeyError`/`AttributeError`/`TypeError` from a refactor) — is swallowed and returns `None`, which
propagates to `estimate_safe_training_rows` (:861-877) → `safe_row_limit=None` → "no limit". The OOM
guard silently switches itself off, indistinguishable from "size genuinely unknown". Direct violation
of the fail-loud mandate.

### Fix design
Narrow the catch to the genuinely-environmental set: `OSError` and the Arrow/Polars read errors
(`pyarrow.lib.ArrowInvalid`, `pl.exceptions.ComputeError` for unreadable files — enumerate what the
metadata reader can actually raise; check `read_parquet_metadata` and the csv/jsonl counters). Log at
WARNING with the path when returning `None` for those. Everything else propagates.

**Failing test first:** monkeypatch the metadata reader to raise `KeyError("bug")`; assert it
propagates out of `estimate_safe_training_rows` (today: silently returns unlimited). Second test:
raise `OSError`; assert `None`-flow (guard degrades with a warning, documented behaviour).

---

## FR-26 [MEDIUM, perf] — `_resolve_target_columns_detail` rebuilds graph indices per call and recurses without memoisation
**`_ram_estimate.py:671-712`, `_resolve_edge_join_columns` `:630-631`**

### Evidence
Every call rebuilds `node_map` / `all_ids` / `_prune_live_switch_edges` / `build_parents_of`
(:681-685), then recurses (:712); the edge-join branch (:703 → :630-631) resolves BOTH join inputs,
each repeating the rebuild + recursion → worst-case exponential on chained/diamond joins. Additionally
`estimate_safe_training_rows` calls three resolver helpers that each re-derive the same indices.
Training-setup latency, not per-row cost — but avoidable and unbounded in graph shape.

### Fix design
Build the prepared indices once per public entry call and thread them through (a small
`_ResolveContext` dataclass: node_map, parents_of, pruned edges); memoise resolution on
`(node_id, source)` within the call. Pure refactor — behaviour identical.

**Test:** counter on the prune/build helpers via monkeypatch; diamond-join fixture; assert the
helpers run once per estimate call (today: ≥2 and growing with depth). Golden-value test that the
estimate output is unchanged on existing fixtures.

---

## FR-24b/25b [LOW, optional, document-or-fix]
- `:389` — "max rows across ancestor sources" under-estimates one-to-many join fan-out. Document the
  assumption in the estimate payload (`assumptions: ["no join fan-out"]`) or scale when key stats show
  fan-out. Documenting is acceptable.
- `:114` — final fallback hard-codes 4 GiB available RAM when every probe fails. In a fail-loud
  codebase this should probably raise with "set HAUTE_AVAILABLE_RAM_BYTES" instead; real probes cover
  win32/linux/darwin so this is exotic-platform only. Decide with Ralph; cheap either way.
- `:329` — `_csv_row_count`/`_jsonl_row_count` read the whole file (docstring sells "instant footer
  metadata") and CSV over-counts multi-line quoted fields (conservative direction, safe). Document.
