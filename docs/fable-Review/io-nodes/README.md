# Fable Review — Input / Output nodes

**Read-only deep review of the Input and Output node implementation, performed 2026-07-06 at
HEAD `aca58177` (branch `code-fixes`, dirty working tree).**
Scope: DATA_SOURCE + API_INPUT (flat files, nested-JSON shred, Databricks), DATA_SINK,
OUTPUT (response-document assembly), their editors, the file-picker routes, codegen/parse
round-trip for these node types, and the format-generalisation question (xml/jsonl/xlsx/…).
Four parallel scoped reviewers (frontend UX, JSON-input backend, output/batch backend,
format-touchpoint analysis) plus a first-hand synthesis pass that re-verified every
load-bearing claim against the source; the sharpest claims were reproduced empirically
against the pinned Polars 1.39.2 (`.venv`) — those are marked *verified* in the packages.

**Nothing in the source tree was changed by this review.** This folder is the deliverable:
verdict, findings, and per-package implementation plans for the follow-up (Opus) agent.

---

## Verdict

The bones are genuinely good. `read_source` is a disciplined lazy boundary with principled
bounded-memory rules (CSV needs declared dtypes; plain JSON refused *before* eager parse);
the sink is streaming + atomic with typed bounded-memory errors; the nested-JSON shred is a
conservation-accounted, mutation-tested piece of real engineering; the Databricks fetch is
the strongest write path in the repo; and the OUTPUT assembler's schema-determined cut
planner is elegant. **CLEARED.md lists 38 behaviours that were adversarially checked and
found correct — do not "fix" anything on that list.**

But the subsystem is not yet as easy, honest, or extensible as the product story requires:

1. **The OUTPUT node silently drops fields.** `pl.LazyFrame(document)` infers its schema from
   the first 100 rows of a null-pruned document, so any field null for the first 100 records
   vanishes from `/quote` responses and previews — reproduced end-to-end. Silent wrongness,
   worst class. → IO03
2. **The documented apiInput workflow dead-ends on common data.** Inference widens mixed
   int/str columns to `str`; the build then refuses the very values inference approved, with
   advice that loops ("re-infer"). Plus one intruder shape that becomes an uncounted
   fabricated null — the exact record-loss class the shred exists to prevent. → IO04
3. **The first-contact UX is dishonest.** The picker advertises `.xml` (unreadable → a
   sanitized, non-actionable 400) and hides `.jsonl` (fully supported); every user-fixable
   read error is collapsed to a generic constant; a large CSV is fully parsed just to show a
   row count. → IO01
4. **"The file is the source of truth" has two exceptions, both on IO nodes.** The
   data-source config exists twice on disk (sidecar + baked body expression) and hand-edits
   to the body's path are *silently discarded* by the boilerplate stripper; and the generated
   OUTPUT body is a passthrough, so `pipeline.run()` on the saved file returns a different
   result than the canvas. → IO02, IO07
5. **CSV pipelines can't reach deploy from the GUI.** The engine requires declared dtypes for
   bounded CSV and the config schema supports them — but no UI writes them, so the failure
   fires at deploy time pointing at JSON keys the product never surfaces. → IO08
6. **Format knowledge is smeared across ~30 sites** (enum + four if-chains + picker string +
   sink ternaries + frontend literals + test pins), which is why the `.xml`/`.jsonl` drift
   exists. A small `FormatSpec` registry + `GET /api/formats` turns "add jsonl output / IPC /
   xlsx / csv.gz / globs" into one tuple entry each, with XML as a designed extension of the
   existing shred machinery (~60% reuse). Polars 1.39.2 already ships every scanner/sinker
   needed except Excel engines (dependency decision) and XML (bespoke reader). → IO12

Total: **5 HIGH, ~14 MEDIUM, ~15 LOW** verified findings across 12 packages, plus the
registry design. Sink/write robustness (silent format coercion to parquet, BOM-less CSV for
Excel users, colliding temp names, unobserved overwrites) clusters in IO05; editor UX
(invisible sink destination, remount-lost write state, v1 banner on new OUTPUT nodes,
swallowed apiInput errors) in IO06/IO09.

---

## Packages, in recommended execution order

| # | Package | Kind | Severity | Effort | Review mode |
|---|---------|------|----------|--------|-------------|
| IO03 | [OUTPUT drops null-headed fields](IO03-output-document-schema-drop.md) | silent wrongness | HIGH | S | pair |
| IO04 | [apiInput infer⇒build contract](IO04-apiinput-infer-build-contract.md) | workflow dead-end + silent loss | HIGH | M | pair |
| IO01 | [Picker format honesty](IO01-picker-format-honesty.md) | UX / product hygiene | HIGH | S | batch |
| IO02 | [Two-copies config, discarded hand-edits](IO02-two-copies-config-hand-edits.md) | architecture / UX | HIGH | M | pair |
| IO07 | [OUTPUT standalone parity](IO07-output-standalone-parity.md) | consistency | MED-HIGH | S-M | pair |
| IO05 | [Sink write correctness](IO05-sink-write-correctness.md) | robustness | MEDIUM | M | pair (a,c) / batch |
| IO08 | [Schema/dtype declaration surface](IO08-schema-declaration-surface.md) | feature gap (blocks deploy) | HIGH | M-L | pair |
| IO06 | [Sink & Output editor UX](IO06-sink-output-editor-ux.md) | UX | MEDIUM | M | batch (c: pair) |
| IO09 | [Input editor feedback](IO09-input-editor-feedback.md) | UX | MEDIUM | M | batch |
| IO10 | [Shred & load performance](IO10-shred-and-load-performance.md) | perf | MEDIUM | M | pair (a) / batch |
| IO11 | [I/O hygiene batch](IO11-io-hygiene-batch.md) | hygiene | LOW | S | batch |
| IO12 | [Format registry + new formats](IO12-format-registry-and-new-formats.md) | design + features | — | M + S/M each | pair (registry) |

Rationale for the order: IO03/IO04 first — silent wrongness and a broken primary workflow,
small and fully specified. IO01 is the highest-leverage small UX fix. IO02/IO07 repair the
product's core "file is truth" story and establish the shared-helper pattern IO12 builds on.
IO05 must precede any sink format work (its allowlist is the registry's write half). IO08 is
the feature gap blocking GUI→deploy for CSV. IO06/IO09/IO10/IO11 are bounded improvements.
IO12 last: steps 1–3 (registry + `/api/formats`) are the investment; formats 4–8 (IPC, jsonl
output, xlsx, csv.gz/globs, XML) then land one tuple entry at a time.

Dependencies: IO07 depends on IO03 (shared helper must build the frame safely). IO06-a/b
consume IO05-d's flag and IO12's resolved-path source. IO08's editor rows build on IO09-b's
schema fetch. IO12 depends on IO05-a and benefits from IO04-a/c landing first (XML reuses the
shred; don't duplicate its defects). IO01's minimal fix can land immediately and be absorbed
by IO12 step 3.

---

## Implementation protocol (binding, per project CLAUDE.md)

1. **Failing test first, always.** Every package lists its TDD plan. Backend entry points:
   `tests/test_io.py`, `tests/test_sink.py`, `tests/test_json_shred_properties.py`,
   `tests/test_output_assembler*`, `tests/test_files_routes*`, round-trip/e2e suites named in
   the packages. Frontend: the existing vitest files named per package.
2. **Review split:** full dev/reviewer pairs where the table says "pair" (silent-wrongness,
   cache identity, state machines, round-trip semantics); single batch reviewer for the
   mechanical rest.
3. **Fail loud, no fallbacks.** IO05-a replaces a silent default with an allowlist error;
   IO04-a stringifies *scalars only* and keeps container leaves loud; IO01-b surfaces typed,
   path-safe errors instead of a constant — never widen sanitisation into swallowing.
4. **Error-string contracts:** `tests/test_io.py:290,294,298` pin messages verbatim; keep
   unknown-extension text byte-identical (IO12-R1). Rewrite (never delete) the xlsx pin when
   xlsx lands.
5. **Line numbers will drift.** All citations are valid at `aca58177`; locate by symbol.
6. **Gates before every commit:** `ruff format --check`, `ruff check`, `mypy`, the focused
   test files for the package, full suite before a package's final commit. Accumulate on the
   existing PR; **do not merge** — Ralph reviews independently.
7. **Cross-review boundaries:** the O(rows²) assembler cost and redundant source hashing were
   resolved by the [v0.6.0 Polars remediation](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md);
   IO03/IO10 retain only their I/O-specific interactions. Nothing in this folder touches the
   optimiser/modelling/EDA/git review scopes.

## Finding ID scheme

Packages IO01–IO12; findings are lettered within packages (e.g. IO05-c). Severity:
HIGH = silently wrong output, a dead-ended primary workflow, or a broken product promise;
MEDIUM = real but bounded cost, or misleading-but-recoverable UX; LOW = hygiene.
Provenance: every finding carries file:line at `aca58177`; claims marked *verified* were
reproduced empirically (Polars 1.39.2 `.venv`) by a reviewer; the rest are verified by
reading with quoted evidence.

## Deliberately out of scope (rejected, with reasons — do not scope-creep these in)

- **Content-sniffing format detection** — extension dispatch is predictable and testable;
  sniffing reintroduces silent wrongness (IO12).
- **A plugin API for third-party formats** — the registry tuple is the extension point;
  Haute is open source, "edit the tuple" is the story.
- **Remote/URI sources (s3://, delta-on-cloud)** — blocked by the URL guard *by design*;
  a separate policy decision with security review, not a format entry (IO12-R5).
- **Upload/drag-drop into the picker** — real feature idea, but orthogonal to correctness;
  note only.
- **Weakening the always-hash cache validity check** without ratifying the tradeoff —
  IO10-a presents the stat-gated memo as a decision for Ralph, not a default.
