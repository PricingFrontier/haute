# IO01 — The file picker lies about formats, and its errors are opaque

**Severity: HIGH (UX / product hygiene) · Effort: S · Review mode: batch**

The first thing a new user does is point a Data Source node at a file. That boundary currently
advertises a format the engine cannot read, hides one it can, silently sanitises every
user-fixable error into a generic message, and full-scans large CSVs before the user has even
confirmed the pick.

All citations at `aca58177`; locate by symbol, not line number.

---

## Findings

### IO01-a — `.xml` advertised, unreadable; `.jsonl` readable, not advertised (HIGH)

`src/haute/routes/files.py:31`:

```python
extensions: str = ".parquet,.csv,.json,.xml",
```

is the **default filter for the file-picker UI** (`browse_files`). But the reader dispatch
(`src/haute/_io.py:150`, `_source_format`) accepts exactly `.csv`, `.json`, `.jsonl`, `.parquet`
— `.xml` raises `ValueError("Unsupported file type: .xml")`.

- A user who picks an `.xml` file the browser showed them gets a dead end (made worse by IO01-b:
  the error reaching them is a sanitized constant, not "XML is not supported").
- `.jsonl` files — a format the backend reads *lazily*, i.e. the **best** format for large JSON —
  are invisible in the picker. The same file (`files.py:110-125`) even contains a
  JSONL-specific row-count estimator that is unreachable through the default filter.
- `DataSourceEditor.tsx` passes no `extensions` override (`frontend/src/panels/editors/DataSourceEditor.tsx:70-77`
  renders `<FileBrowser currentPath={undefined} onSelect=... />`), so every data-source pick uses
  this wrong default.

**Fix design.** The extension list must come from the same source of truth as the reader —
this is the seed of the format registry (IO09). Minimal fix now: change the default to
`".parquet,.csv,.json,.jsonl"` and have the frontend request the list from a
`/api/formats` endpoint (or at minimum a shared constant exported next to `SourceFormat`)
so picker and reader cannot drift again. Do not keep `.xml` until XML ingestion exists (IO09).

**TDD.** `tests/test_files_routes.py`: (1) failing test — every extension in the
`browse_files` default is accepted by `_source_format`, and every `SourceFormat` member's
canonical extension appears in the default (this is the drift-proof contract test); (2) a
`.jsonl` fixture listed by default browse. Frontend: extend `DataSourceEditor.test.tsx` to pin
that the browser shows `.jsonl` files once the shared list is wired.

### IO01-b — `/api/schema` sanitises user-fixable errors into `_INTERNAL_ERROR_DETAIL` (HIGH)

`src/haute/routes/files.py:156-170`: **every** `ValueError` from the schema read — including
`SchemaMismatchError` subtypes and the `"Unsupported file type"` dispatch error — is logged
server-side and returned as `HTTPException(400, detail=_INTERNAL_ERROR_DETAIL)`. The generic
`Exception` branch (`:171-187`) does the same at 500.

The sanitisation motive is sound (raw messages can embed absolute paths / git output), but the
result is that the picker's schema panel can never tell the user *what they can do about it*:
unsupported type, malformed CSV header (duplicate columns — `_io.py:207-218` produces an
excellent message that is then thrown away), empty file, mis-declared dtype. This is the exact
anti-pattern CLAUDE.md's fail-loud rule exists to prevent, applied at the one boundary a
brand-new user is guaranteed to hit.

**Fix design.** Typed allowlist, not blanket sanitisation: `SchemaMismatchError` and the
unsupported-file-type error already carry structured, path-safe context (`missing`,
`duplicates`, `dtype`, extension). Surface those as a structured 400/422
(`{"type": "SchemaMismatchError", ...}` — the shape `OutputMappingSchemaError` routes already
use, `_output_assembler.py:58-67`). Keep the sanitized constant for everything else.
Make the dispatch error a typed `UnsupportedSourceFormatError` (listing supported extensions)
instead of a bare `ValueError` so the allowlist keys on type, not on string matching.

**TDD.** Failing tests first: `.xml` path → 400 whose detail names XML and lists supported
formats; duplicate-header CSV → 400 whose detail includes the duplicate column names; a path
outside the project → still the sanitized constant (the security posture must not regress).

### IO01-c — Schema preview full-scans CSV/JSON for a row count (MEDIUM)

`src/haute/routes/files.py:127`: for non-parquet, non-jsonl files the row count is
`lf.select(pl.len())` — for CSV a **full parse of the file on every picker click**, before the
user has even attached it to a node. Parquet gets metadata (`:85-100`), JSONL gets a size-based
estimate (`:110-125`), CSV/JSON get the full scan. A 10 GB CSV on a network share makes the
picker appear to hang — the exact "silent stall" class the README's "knows your machine's
limits" promise is about.

**Fix design.** Reuse the JSONL trick for CSV: newline count on a bounded byte sample →
estimated count with the existing `row_count_estimated` flag (the schema response already
carries it, `schemas.py` `SchemaResponse`). Cap exact counting at a size threshold (e.g.
< 32 MB exact, else estimate). Plain `.json` is eagerly parsed anyway for schema, so its count
is free once parsed — leave it.

**TDD.** Failing test: monkeypatch a byte-size threshold, assert a large CSV fixture returns
`row_count_estimated=True` without a full `pl.len()` collect (structural assert via
monkeypatched `streaming_collect` call count).

### IO01-d — `browse_files` stats every file synchronously on the event loop (LOW)

`src/haute/routes/files.py:42-56`: the directory listing (including `entry.stat()` per file)
runs inline in the async handler; `get_schema` correctly offloads to `run_in_threadpool`
(`:153`) but `browse_files` does not. Large or slow (network) directories stall every other
request on the single event loop.

**Fix.** Wrap the listing body in `run_in_threadpool`, same as `get_schema`.
**TDD.** Structural test asserting `browse_files` delegates through `run_in_threadpool`
(monkeypatch it, assert called).

---

## Out of scope here

- Serving the format list dynamically and per-format capability flags — IO09 (registry).
- The DataSourceEditor's missing dtype/format options UI — IO03.
