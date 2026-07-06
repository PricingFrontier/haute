# G13 — `/show` + comparison: whole-tree archives, Windows temp-dir 500s, silent empty graphs, 3.11 floor

**Severity: MEDIUM · Confidence: CONFIRMED (archive costs measured) · Class: latency + robustness on the history-view path**
**Files: `src/haute/_git.py` (`archive_commit`), `src/haute/routes/_helpers.py` (`commit_pipeline_graph`), `pyproject.toml`**
**Origin: P-6 (perf reviewer, measured), R-5 (routes reviewer), engine reviewer's tarfile-floor confirmation (primary-read candidate #5).**

## P-6 [MEDIUM] Every version view archives the ENTIRE committed tree — including data files

`archive_commit` runs `git archive --format=tar <sha>` over the whole tree and extracts it
(`_git.py:1839-1851`); `commit_pipeline_graph` (`_helpers.py:866-888`) then parses only pipeline
`.py` + config/sidecar files. Measured: **518 ms @ 30 MB** incompressible committed data
(~linear: 100 MB → ~1.7 s), vs **81 ms** with a pathspec — and pricing repos that predate G02
routinely carry committed datasets. Fires per `/show/{sha}` (every read-only version view /
comparison open).

**Fix.** Limit the archive to what the parser reads:
`git archive --format=tar <sha> -- '*.py' '*.json' '*.toml'` (align the pathspec list with what
`discover_pipelines` + the sidecar/config loaders actually consume — derive it from one constant,
don't guess; submodel files are `.py`+`.json` so they ride along). Measured 81 ms regardless of
data size. Optional second step (only if still hot): cache the parsed `PipelineGraph` per sha —
commits are immutable — with a small LRU; **not** required for acceptance.

## R-5a [MEDIUM] Temp-dir cleanup can 500 a successful parse on Windows

`with tempfile.TemporaryDirectory(prefix="haute-show-") as tmp:` (`_helpers.py:874`) lacks
`ignore_cleanup_errors=True`. On Windows, an AV scanner or indexer holding a just-extracted file
makes `__exit__` → `rmtree` raise `PermissionError` → unhandled → **500** (`routes/git.py:445-448`)
*after* the graph parsed fine.

**Fix.** `TemporaryDirectory(prefix="haute-show-", ignore_cleanup_errors=True)` — leaked temp dirs
under `%TEMP%` are the OS's problem; failing the request is ours. (Grep for other
`TemporaryDirectory` uses on request paths while there — apply the same flag where a parse
already succeeded.)

## R-5b [MEDIUM] Total parse failure returns 200 + empty graph — indistinguishable from an empty pipeline

When every discovered pipeline fails to parse, `commit_pipeline_graph` returns `PipelineGraph()`
(`_helpers.py:885-888`) → `/show` responds **200 with an empty graph** → the comparison canvas
renders blank with no signal. A user viewing an old commit whose format predates a parser change
sees "this version was empty" — silent wrongness about history.

**Fix.** Fail loud per project rules: when at least one pipeline file was discovered and **all**
failed to parse, raise a `GitDomainError`-equivalent for this layer → 400/422 with
*"This version's pipeline couldn't be read (it may predate a format change). The files are intact
— view them in git directly."* Keep the current behaviour when the commit genuinely contains no
pipeline files (empty graph is then true). The existing `commit_parse_failed` warning log stays.

## Tarfile floor [LOW → one-line fix]

`tar.extractall(dest, filter="data")` (`_git.py:1851`) requires Python ≥3.11.4;
`requires-python = ">=3.11"` (pyproject:11) admits 3.11.0-3.11.3 where it TypeErrors → 500 on
every history view. **Fix:** `requires-python = ">=3.11.4"`. (Keep `filter="data"` — it is the
correct traversal guard, cleared by the security pass.)

## TDD plan

1. Pathspec: fixture commit containing `big.bin` (a few MB) + pipeline files → assert the
   extracted temp tree contains the pipeline files but **not** `big.bin` (structural: list the
   extracted paths via a hook or by asserting on the archive pathspec argv through a counting
   wrapper); graph still parses with nodes.
2. Cleanup: monkeypatch `shutil.rmtree`/holder to raise on cleanup → `/show` still 200 with the
   parsed graph.
3. Parse failure: commit with a deliberately unparseable pipeline `.py` → `/show` returns the
   hand-written 4xx, NOT 200-empty; commit with no pipeline files → 200 empty graph (unchanged).
4. `pyproject` floor: trivial assert in the packaging test if one exists; else rely on the diff.

## Notes

All four legs are small and local; batch review acceptable except R-5b (silent-wrongness
classification → have the reviewer check the "no pipeline files" vs "all failed" split carefully).
