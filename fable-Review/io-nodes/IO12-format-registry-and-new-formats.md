# IO12 — One format registry, then new formats become one tuple entry

**Kind: design/refactor + feature programme · Effort: M (registry) + S/M per format · Review mode: pair (registry), batch (per format after)**

This is the direct answer to "generalise to xml, jsonl and other formats". JSONL is already
supported by the engine (`scan_ndjson`) and merely hidden by IO01; XML is not supported at
all; everything else in between is gated by one structural problem: **format knowledge is
smeared across ~30 sites** instead of living in one registry. The full touchpoint table below
is the format-map reviewer's, verified against source with the pinned Polars 1.39.2.

## The cost today (measured, not vibes)

Adding one new input format currently touches, minimum: `SourceFormat` enum (`_io.py:24`),
`_source_format` extension chain (`:150`), the `read_source` if-chain (`:467-549`), the
chunking allow-list (`chunking.py:1512-1542`), the code-extraction scan-prefix allow-list
(`_code_extraction.py:473-483`), the picker default (`routes/files.py:31`), plus — for eager
formats — the bounded-profile frozensets (`_io.py:33-45`) and the deploy static-source verify
(`deploy/_bundler.py:157-208`), plus a flipped test (`tests/test_io.py:290` pins
"xlsx unsupported" verbatim). **6–10 edits across 6+ files, a frontend change, and a test
rewrite per format** — textbook shotgun surgery. The sink side mirrors it in five more places
(`_graph_utils.py:66`, `_polars_utils.py:160-183`, `executor.py:1531,1658-1668`,
`_codegen_builders.py:1023-1027`, `SinkEditor.tsx:65-68`).

The drift is already observable — the picker advertises `.xml` it can't read and hides
`.jsonl` it can (IO01); `nodeTypes.ts:60-61` tells users "parquet, CSV, or Databricks";
apiInput hard-codes `.json/.jsonl` in three separate literals (`ApiInputEditor.tsx:159,376,417`).

## Verified Polars 1.39.2 capabilities (what the engine gives us for free)

- `scan_ipc` — Arrow IPC/feather is **fully lazy**: the cheapest possible new format.
- `scan_csv('t.csv.gz')` — **gzip CSV is transparent**; only Haute's extension sniffing blocks it.
- Every scanner has `glob=True` by default — `data-*.parquet` already works in Polars; only
  `_source_format`/`_validate_source_path` and the chunking allow-list stand in the way.
- `sink_ndjson`, `sink_ipc` exist — JSONL/IPC **output** are one-writer adds.
- `pl.read_excel` exists but the default engine (`fastexcel`) is **NOT installed**;
  `engine="openpyxl"` works today (openpyxl is installed). `write_excel` needs `xlsxwriter`
  (NOT installed) — xlsx starts read-only unless the dep is added.
- **No XML support in Polars at all** (and no lxml/xmltodict in the venv) — XML needs a
  bespoke tree→columns reader. Critically, the apiInput shred core is already
  format-agnostic: `_emit_at`/`_walk_array`/`_resolve_leaf` walk generic dict/list trees, and
  the `_jsonpath` grammar has a designed "transport shape" seam. An XML ingestion path reuses
  ~60% of that machinery (JSON-input reviewer's estimate); what's format-specific is record
  iteration, lexical type inference (XML is all-text), and an `@attr` selector. Fix IO04-a/c
  first or the XML path duplicates both defects.

## Target design (the registry)

One frozen dataclass + one tuple in `_io.py`; everything else derives:

```python
@dataclass(frozen=True, slots=True)
class FormatSpec:
    name: str                                  # "parquet" — replaces SourceFormat members
    extensions: tuple[str, ...]                # (".csv", ".csv.gz") — longest-match sniffing
    lazy_scan: bool                            # False → eager read + .lazy()
    reader: Callable[[str, ReadContext], pl.LazyFrame]
    needs_declared_schema_when_bounded: bool   # csv/xlsx/json/xml = True
    bounded_profile_allowed: bool              # False for eager-only → reject BEFORE parse
    chunkable: bool                            # parquet/csv/ipc; NOT xlsx/xml/.gz
    writer: Callable[..., None] | None = None  # None → read-only format
    write_extension: str | None = None
    write_options: ...                         # BOM, float_precision (IO05-b hook)
```

- `read_source` collapses to ~10 lines: `spec = format_for_path(path)`; enforce
  `bounded_profile_allowed` / `needs_declared_schema_when_bounded` **generically** against the
  profile; call `spec.reader`. The per-format bespoke branches disappear.
- The satellite sniffers (`_ram_estimate.py:248-334`, `chunking.py:1536`,
  `deploy/_schema.py:28-35`, `_code_extraction.py:473-483`) query `spec.chunkable` /
  `spec.lazy_scan` instead of `.endswith` literals.
- Sink side: `_resolve_sink_path`, the writers, the row-count scan, and the codegen template
  choice all key off `spec.writer` / `spec.write_extension`; the IO05-a allowlist IS the
  registry keys — fail-loud for free.
- **Frontend never hard-codes formats again**: a tiny `GET /api/formats` returns
  `{source_extensions, sink_formats, per_format_capabilities}` from the registry;
  `browse_files`'s default, `DataSourceEditor`/`ApiInputEditor` filters, `SinkEditor` options,
  `nodeTypes.ts` copy, and IO06-a's resolved-path preview all consume it. The UI can then
  *disable with a reason* (e.g. "xlsx: preview-only, not deployable") instead of today's mix
  of supported-but-invisible and visible-but-broken.

## Migration order (each step green before the next)

1. **Registry lands as a pure refactor** — parquet/csv/ndjson/json entries reproducing today's
   behaviour and **exact error strings** (`"Unsupported file type: .{suffix}"` is pinned by
   `tests/test_io.py:290,294,298`; keep it verbatim for unknown extensions). Suite unchanged.
2. Repoint satellite sniffers at registry predicates. Still no behaviour change.
3. `GET /api/formats` + frontend consumption (closes IO01 structurally).
4. **IPC** — one tuple entry + `_read_ipc`/`_write_ipc` + tests. This is the proof the seam works.
5. **JSONL output** (`sink_ndjson`) + `.ndjson` read alias — trivial after 1–3.
6. **xlsx read** — entry with `bounded_profile_allowed=False`, reader pinned to
   `engine="openpyxl"` (or add `fastexcel` to deps — a decision for Ralph);
   `tests/test_io.py:290` is REWRITTEN (not deleted) into a bounded-reject assertion.
   xlsx write only if `xlsxwriter` is added; otherwise `writer=None`.
7. **Compressed CSV + globs** — extend csv's `extensions`; globs need `_validate_source_path`
   to validate each resolved file and chunking to keep rejecting non-seekable variants.
8. **XML last** — bespoke reader (lxml or stdlib iterparse) → dict/list tree → reuse the shred
   walk; eager-only rules like plain JSON; grammar gains the attr selector via the transport
   seam. Prerequisites: IO04-a/c fixed, dependency decision made.

## Hard constraints the implementer must respect (from the risk sweep)

- **R2 — bounded-memory is enforced at deploy too**: `_verify_static_source_schema` reads
  static sources at `DEPLOY_BATCH`, so an eager-only format as a static deploy source is
  rejected at deploy time. Decide deliberately (allow-with-cache-to-parquet vs reject) and
  test it; don't discover it in production.
- **R3 — chunkability is opt-in**: never let a new format become chunkable by omission.
- **R5 — security posture is format-independent**: every reader (and every glob expansion)
  stays behind `_validate_source_path`; URL-shaped sources (delta/iceberg on s3://) trip the
  URL guard **by design** — remote sources are a separate policy decision, not a format entry.
- **R6 — deploy bundling is already format-agnostic** (`collect_artifacts` copies by path,
  no allow-list). Keep it that way; add nothing there.
- **R1 — error-string contracts**: unknown-extension message stays byte-identical.

## What NOT to build (scope guard)

- No plugin/entry-point system for third-party formats — a frozen in-repo tuple is enough;
  the extension story is "edit one tuple", not "ship a plugin API".
- No auto-detection by content sniffing — extension dispatch is predictable and testable;
  content sniffing reintroduces silent wrongness.
- No remote-URI sources smuggled in as formats (see R5).
