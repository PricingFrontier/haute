# IO05 — Sink write correctness: silent format coercion, Excel-hostile CSV, colliding temp files, unobserved overwrites

**Severity: MEDIUM (cluster; the format coercion is silent wrongness) · Effort: M · Review mode: pair for a/c, batch for the rest**

The DATA_SINK write path (`execute_sink` → `bounded_sink` → `streaming_sink` → `atomic_write`)
is streaming and mostly atomic — see CLEARED.md for what is right. These are the gaps. Items
a–e were verified by the output-side reviewer (Polars 1.39.2); f–g by reading.

---

## IO05-a — Unknown or mis-cased `format` silently writes Parquet (MEDIUM, silent wrongness)

Every format decision is a case-sensitive `fmt == "csv"` with `else → parquet`, and nothing
validates `format` anywhere:

- `src/haute/executor.py:1531` — `fmt = config.get("format", "parquet")`
- `src/haute/_graph_utils.py:72` — `ext = ".csv" if fmt == "csv" else ".parquet"`
- `src/haute/_polars_utils.py:167` — `if fmt == "csv": sink_csv … else: sink_parquet`
- `src/haute/_config_builder.py:202` — parser copies `format` verbatim
- `src/haute/_codegen_builders.py:1027` — `template = _SINK_CSV if fmt == "csv" else _SINK_PARQUET`

So `format="json"`, `format="xlsx"`, or the typo `"CSV"` all write a **Parquet** file to a
`.parquet` path while `SinkResponse.format` echoes the bogus value and the success message
reads "Wrote N rows to outputs/x.parquet". The GUI's two-option toggle can't produce this, but
a hand-edited sidecar/decorator (the round-trip story) can. `tests/test_sink.py` covers only
`parquet`/`csv`/default — the coercion is untested.

**Fix.** One lower-cased allowlist (`{"parquet", "csv"}` today) raising `ConfigError` naming
the supported formats — enforced at save/validation and defensively in `execute_sink`. This
allowlist becomes the sink half of the format registry (IO12); do NOT add formats here first.

**TDD.** Failing tests: `execute_sink` with `format="json"` and `"CSV"` → loud error naming
supported formats, no file written.

## IO05-b — CSV sink writes no UTF-8 BOM → Excel mojibake for £/€/accents (MEDIUM, verified)

`_polars_utils.py:168` `lf.sink_csv(target)` and `:181` `df.write_csv(target)` pass no
`include_bom` (supported by both in 1.39.2; file verified BOM-less). Excel on Windows opens a
BOM-less UTF-8 CSV in the legacy code page: `£`, `€`, accented policyholder names render as
mojibake. CSV is the one Excel-facing output format Haute has, and its stated users live in
Excel.

**Fix.** `include_bom=True` for CSV writes (thread a small per-format options object through
`_streaming_sink_to_path` / `_eager_write_to_path` — the registry's options hook, IO12).
Consider `float_precision` at the same time so premiums don't serialise with float noise.

**TDD.** Write a CSV sink containing `£`; assert the file starts `b"\xef\xbb\xbf"` and
round-trips under `encoding="utf-8-sig"`.

## IO05-c — `atomic_write` temp name is not unique → cross-format collision and concurrent-write corruption (MEDIUM, verified)

`_polars_utils.py:287` — `tmp = dest.with_suffix(".parquet.tmp")`:

- `Path("foo.csv")` and `Path("foo.parquet")` **both** map to `foo.parquet.tmp` (verified) —
  a CSV sink and a Parquet sink sharing a stem clobber each other's temp.
- Two writes of the *same* sink (double-click Write across a panel remount — see IO06-c — or a
  retry racing the first) share one tmp: interleaved writes, then a race on
  `tmp.replace(dest)` → truncated output or `FileNotFoundError`.
- The Databricks fetch already solved this with `tempfile.mkstemp` (`_databricks_io.py:316`);
  the generic helper regressed to a fixed name.

**Fix.** Mint the temp via `mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".tmp")`
(unique per writer; same directory keeps `replace` atomic). Drop the misleading `.parquet`
literal.

**TDD.** Two threads `atomic_write` to one dest → both complete, dest is one *complete* file;
csv+parquet stem pair get distinct temps (monkeypatched `mkstemp` recorder).

## IO05-d — Overwrites are unconditional and unobserved (LOW→MEDIUM)

`tmp.replace(dest)` overwrites with no exists-check, no `overwrite` flag, no distinct log.
Re-pricing onto yesterday's rated book replaces it with zero friction — for a pricing artefact
that deserves at least observability. **Fix:** log `sink_overwrote_existing` (prior
size/mtime) and plumb an optional `overwrite: bool` on `SinkRequest` (default allow-with-log)
that the UI can surface (IO06-b). **TDD:** pre-existing target → log record emitted;
`overwrite=False` → typed error, file untouched.

## IO05-e — Rename without fsync: "atomic" is not crash-durable (LOW)

`atomic_write` renames without fsync of file or directory; power loss can leave the rename
visible with unflushed data. Polars 1.39.2 sinks expose `sync_on_close` (verified). **Fix:**
`sync_on_close="all"` (or explicit `os.fsync`) before the rename for sink outputs. Cheap
insurance for a consumed artefact.

## IO05-f — Row-count re-scan fully re-parses a just-written CSV (LOW)

`executor.py:1663-1674`: parquet count is metadata (cheap); CSV count is
`pl.scan_csv(out).select(pl.len())` — a full re-parse of the file just streamed out, doubling
I/O on big outputs. **Fix:** count newline bytes in a buffered pass (minus header), or carry
the count from the plan when cheaply available. Keep the response contract identical.

## IO05-g — Generated standalone sink never creates `outputs/`, and the atomic path silently degrades exactly then (MEDIUM)

The codegen sink templates (`_codegen_builders.py:401-419`) call
`bounded_sink(df, Path(__file__).parent / "outputs/x.parquet")` with **no mkdir** — and
`_write_atomically_if_possible` (`_polars_utils.py:186-191`) checks `if path.parent.exists()`:
when the parent is missing it skips `atomic_write` and hands the raw path to Polars, which
fails with a raw OS error on the missing directory. So a fresh clone running `python main.py`
fails uglier than it should — and any future caller with a missing parent silently loses
atomicity instead of gaining it via `mkdir`. (`execute_sink` pre-mkdirs at `executor.py:1560`,
so the GUI path is atomic — CLEARED.)

**Fix.** Make `_write_atomically_if_possible` create the parent and always take the atomic
path (delete the non-atomic branch — it exists only to avoid a mkdir); drop the now-redundant
mkdir in `execute_sink`. **TDD:** failing test — `bounded_sink` to a path whose parent doesn't
exist succeeds and leaves no `.tmp` residue; generated-file e2e (`python` the saved pipeline in
a temp project) writes `outputs/…` on first run.

---

## Sequencing

c and g touch the same helper — land together. a is independent and must precede any sink
format addition (IO12). b/d/e/f are mechanical. Cross-ref: IO06 (SinkEditor UX surfaces d and
the resolved path), IO12 (registry owns the format→writer/extension mapping this package
hardens).
