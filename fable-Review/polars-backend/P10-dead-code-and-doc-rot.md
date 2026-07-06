# P10 — Dead code and doc-rot (batch, single reviewer OK)

**Severity:** LOW · **Effort:** S · Deletion-heavy; regression risk near zero. Three items close
deferred entries in `review/REMEDIATION-PROGRAM.md` — say so in the commit message.

## FR-34 — `COLLECT_LAZY` checkpoint strategy is defined, documented, and never chosen
**`_execute_lazy.py:361-407`** — the `_CheckpointAction` enum defines `COLLECT_LAZY` ("Only used when
the estimated intermediate fits comfortably in available memory") and `_checkpoint_decision`'s
docstring says it "chooses the cheapest safe strategy" — but the function only ever returns `SKIP` or
`PARQUET`. Every join/fan-out checkpoint pays a disk round-trip even for tiny frames.

**Decide, don't drift:** either (a) implement it — return `COLLECT_LAZY` when a cheap size signal
(e.g. `_ram_estimate` row estimate × width, or a row-count cap) says the intermediate is small, and
wire the `collect().lazy()` branch in the checkpoint executor; or (b) delete the enum member and fix
both docstrings to describe the real two-way decision. (a) is a genuine batch-latency win for small
data but needs a size signal and tests; (b) is honest and free. Recommend (b) now and file (a) as a
follow-up optimisation unless benchmarks justify it immediately.

## FR-35 — `safe_sink` and `best_effort_sink` have zero production callers
**`_polars_utils.py:391-457`** — grep confirms no callers under `src/` (tests may reference them).
They are the only remaining broad-collect fallback surface in the sink family; the bounded paths
(`bounded_sink`/`streaming_sink`) serve every production call site. **Fix:** delete both (and the
`_is_streaming_sink_error` alias if it becomes unused), migrate/delete any tests that exercised them.
If some external consumer needs a fallback-capable sink, that consumer should opt in explicitly at its
own call site per the module's own design notes.

## FR-36 — no-op guard `_raise_if_unbounded_user_code_is_terminal`
**`projection.py:1403-1410`** (call site `:2279-2283`) — body is `_ = ...; return`. The name promises a
guard that does not exist (flagged in the audit as a deletion-only simplification; still present).
**Fix:** delete the function and its call site. If a terminal-unbounded-user-code guard is actually
wanted under strict profiles, that's a feature decision — do not leave a name pretending it exists.

## FR-37 — hand-rolled `json.dumps` where `canonical_json` is mandated
**`execution.py:509`** (`dataframe_graph_input_fingerprint`) — uses
`json.dumps(payload, sort_keys=True, separators=(",", ":"))` while `_cache.py:84-92` declares
`canonical_json` "THE canonical-JSON encoding for digest material — the only one … do not add a
second one; import this." The payload is scalar-only today so behaviour matches, but it is the exact
drift seam the mandate exists to close (audit sim #50). **Fix:** switch to `canonical_json(payload)`.
Note: this CHANGES the digest → one-time invalidation of dfexec cache keys built from it; acceptable
(it's a cache), flag in the commit message.

## FR-38 — comments describing behaviour the code no longer has
- `executor.py:467-472` — preview-cache header: "The pipeline doesn't change between node clicks —
  only the target node changes. Cache the materialized DataFrames so clicking different nodes is
  instant" — false under `target_preview_only` keys (see P05; fix the comment WITH P05, or now with a
  pointer).
- `executor.py:843-846` — `execute_graph` docstring: "single-entry cache" — it's an 8-entry LRU.
- `trace.py:368` — same "single-entry" claim (also listed in P03/FR-11).
- `_execute_lazy.py:377-393` — `_checkpoint_decision` docstring (covered by FR-34's choice).

## FR-39 — duplicated plain-model-score predicate
**`_execute_lazy.py:1716-1727` vs `:1962-1967`** — the condition "MODEL_SCORE and no code and no
column_renames and no selected_columns" is written twice (inside `_full_model_score_schema` and at its
call site). The audit's suggested fold: have `_full_model_score_schema` return
`(full_columns, is_plain_model_score)` and branch on that. Mechanical.

## FR-40 — `_fingerprint_cache` sugar-layer nits
**`_fingerprint_cache.py:105`** — `try_get`'s first-slot `_MISSING` check is dead (`store` always
fills every slot); delete or comment why it guards hand-inserted entries.
**`:147`** — non-size-sensitive `update_slot` bypasses `put`'s byte bookkeeping; correct only because
the preview `size_of` (`executor.py:742`) reads solely `eager_outputs` and
`size_sensitive_slots=("eager_outputs",)` matches — an invariant enforced in another module. Add an
assertion or a contract comment binding "`size_of` must read only size-sensitive slots".
