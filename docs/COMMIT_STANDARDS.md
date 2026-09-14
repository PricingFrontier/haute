# Commit Standards

Every commit merged into `main` must satisfy the checks below. Review this list during code review and before approving a PR.

---

## Design Philosophy

These are the non-negotiable principles that shape every decision in haute. If a commit conflicts with any of these, it needs rethinking.

### Code is the source of truth

The `.py` file is always canonical. The GUI is a live, editable *view* of that code - never the other way around. Layout metadata lives in a sidecar `.haute.json`, not in the Python. A pipeline must always be runnable with `python main.py` - no GUI required.

### Same pipeline, every context

The same pipeline code runs for a 1-row live API quote and a 100M-row batch job. No separate "live mode" vs "batch mode" implementations. If a node works in preview, it works in production.

### Single execution engine

CLI, GUI, and programmatic usage all flow through the same parser → executor path. There is one topo sort, one graph executor, one code generator. Parallel implementations drift and break.

### Polars-native, lazy by default

All intermediate results stay as `LazyFrame` until the moment data is needed. This lets Polars push predicates and limits into scans. Never call `.collect()` earlier than necessary. Never convert to pandas.

### The GUI never crashes

Bad code in a `.py` file shows an error banner, not a white screen. Parse errors, execution errors, and malformed data are all caught and surfaced gracefully. The last known good state is always preserved.

### Permissive parsing, strict generation

The parser accepts messy, hand-edited Python - regex fallback, partial decorators, missing type hints. The code generator always emits clean, idiomatic, `ruff`-passing Python. Round-tripping through the GUI improves code quality.

### Thin orchestration, not a platform

Haute is a client library that leans on Databricks, MLflow, and Polars. It does not reimplement model training, data storage, or serving infrastructure. New integrations should wrap existing tools, not replace them.

### Performance is non-negotiable

The GUI must feel instant. Every change — adding nodes, dragging, connecting edges, undo/redo — must complete without perceptible lag, even with large pipelines. Backend calls (preview, run) are inherently async, but the UI thread must never block on them. Specific rules:

- **No unnecessary re-renders.** Memoize components, callbacks, and derived data (`memo`, `useMemo`, `useCallback`). Never create new objects/arrays in render that could be stable references.
- **No unnecessary network calls.** Don't fetch preview data on multi-select, drag, or any interaction where the user isn't asking for data. Batch where possible.
- **Keep the history stack cheap.** Undo/redo snapshots are plain JSON references, not deep clones. Position-only drags batch into a single snapshot, not one per pixel.
- **Lazy by default.** Polars `LazyFrame` stays lazy until the last moment. Preview limits rows. Large datasets never load fully into the frontend.
- **Profile before optimising.** Don't add complexity for hypothetical performance. But if a real interaction feels slow, fix it before shipping.

### Low floor, high ceiling

A pricing analyst with no engineering background can drag nodes and connect them in the GUI. A senior engineer can write raw Polars expressions, custom decorators, and deploy via CI/CD. Both use the same tool. Features should serve the analyst by default and get out of the engineer's way.

### Everything is diffable

Pipeline logic is `.py` files - reviewable in PRs, diffable in git, testable in CI. Node positions are JSON. There are no opaque binary formats. If it can't be diffed, it shouldn't be committed.

### Explain every price

Every output value must be traceable back through the graph to its inputs, showing the intermediate value at each node. This is a regulatory requirement (Solvency II, IFRS 17) and a core product differentiator. Features that break traceability are not acceptable.

---

## Engineering Standards

## 1. No Duplication (DRY)

- No copy-pasted logic. If two places do the same thing, extract a shared function/module.
- Shared utilities live in well-known locations (`graph_utils.py`, `frontend/src/utils/`).
- Shared constants (e.g. config key tuples used by both parser and codegen) live in `_types.py` alongside the TypedDict they describe, and are re-exported through `graph_utils.py`.
- Cross-runtime rules must come from one generated contract or one owning runtime; do not maintain handwritten semantic twins.

## 2. Simplicity (KISS)

- Prefer the simplest solution that works. No premature abstractions.
- If a function takes more than 5 parameters, consider a config object or breaking it up.
- Avoid clever one-liners that sacrifice readability.

## 3. Single Responsibility

- Each module, class, and function does one thing.
- API endpoints are thin - validation and response shaping only. Business logic lives in the library layer (`executor.py`, `pipeline.py`, etc.).
- React components: rendering only. Side effects in hooks, logic in utils.

## 4. Type Safety

- **Python**: All function signatures have type annotations. No `Any` unless truly unavoidable.
- **Python API**: Every endpoint uses Pydantic request/response models. No `body: dict`.
- **TypeScript**: No `any`. Use proper interfaces/types for all props, state, and API responses.
- **PEP standards**: The codebase uses PEP 563 (`from __future__ import annotations` in every `.py` file), PEP 585 (`list`, `dict`, `tuple` — not `List`, `Dict`, `Tuple`), and PEP 604 (`str | None` — not `Optional[str]`).
- **Pydantic field conventions**: Optional primitives use `str | None = None`. Mutable defaults use `Field(default_factory=dict)` or `Field(default_factory=list)`. Numeric defaults are inline: `float = 0.0`, `int = 0`.

## 5. Linter Clean

- `ruff check src/haute/` must pass with zero errors before merge.
- Frontend must have no TypeScript errors (`tsc --noEmit`).
- New ruff rules are not silenced without a comment explaining why.

## 6. No Dead Code

- No unused imports, variables, or functions.
- No commented-out code blocks. Use version control to retrieve old code.
- No empty files. If a file has no content, delete it.

## 7. No Stale Documentation

- If you change behaviour, update the relevant doc in `docs/`.
- If you add, remove, or rename a deploy target, CLI command, or user-facing feature, update both `README.md` and `docs/index.md` to match.
- `README.md` is the first thing people read. Deploy target tables, architecture tables, and the "what runs where" summary must reflect the current code.
- Status tables in design docs must reflect reality (no "❌ Missing" for things that exist).
- README examples must actually run.

## 8. Dependency Discipline

- No heavy optional dependencies in core `[project.dependencies]`. Use `[project.optional-dependencies]`.
- Pin minimum versions, not exact versions.
- Every new dependency must be justified in the PR description.

## 9. No Resource Leaks

- File handles: use `with` statements or `subprocess.DEVNULL`. Never `open()` without close.
- Async tasks: always cancellable. Use lifespan context managers, not `on_event`.
- WebSocket connections: always cleaned up in `finally` blocks.

## 10. Single Execution Path

- CLI and GUI must use the same execution engine (`parse → execute_graph`).
- No parallel implementations of the same algorithm (e.g. two topo sorts).
- If a code path is only reachable from one caller, consider inlining it.

## 11. Correct Data Structures

- Use `deque` for FIFO, not `list.pop(0)`.
- Use `set` for membership checks, not list scans.
- Use lazy evaluation (Polars `LazyFrame`) until the moment you need concrete data.

## 12. Error Handling

- Never swallow exceptions silently (`except: pass`).
- The same applies to frontend code: `.catch(() => {})` is the TypeScript equivalent of `except: pass`. Always log with `console.warn` at minimum.
- API errors return structured JSON with status codes, not bare strings.
- `HTTPException` and other intentional error responses must be raised **outside** generic `try/except Exception` blocks. Otherwise the framework exception is caught and re-wrapped as a 500.
- The GUI never crashes due to bad data. Worst case: show last good state + error banner.

## 13. Consistent Naming

- Python: `snake_case` for functions/variables, `PascalCase` for classes.
- TypeScript: `camelCase` for functions/variables, `PascalCase` for components/types.
- API fields that cross the frontend/backend boundary use `snake_case` (Python convention). Since we own both frontend and backend, one convention everywhere avoids alias machinery and translation layers.

## 14. Idiomatic React

- No module-level mutable state. Use `useRef` for instance-scoped counters.
- Side effects only in `useEffect` or event handlers.
- Memoize expensive computations with `useMemo`/`useCallback`.
- Prefer controlled components.

## 15. Security Basics

- No hardcoded secrets or API keys. Use environment variables.
- File access endpoints must validate paths stay within the project root. Use `_get_project_root()` from `haute._sandbox`, resolve the path, and check with `.startswith()`:

```python
from haute._sandbox import _get_project_root

base = _get_project_root()
target = (base / user_path).resolve()
if not str(target).startswith(str(base)):
    raise HTTPException(status_code=403, detail="Cannot save outside project root")
```

- Path validation must include a test that verifies traversal paths like `../../etc/passwd` are rejected with 403.
- No `eval()` on untrusted input. User code execution is sandboxed to pipeline context.
- Sensitive internal docs (competitive analysis, credentials) must be in `.gitignore`.

## 16. Test Coverage

- New business logic must include at least one test.
- Bug fixes must include a regression test.
- Tests must not depend on external services or network access.
- Never delete or weaken an existing test without explicit justification in the PR.

## 17. Commit Hygiene

- Each commit is atomic: one logical change per commit.
- Commit messages follow conventional format: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`.
  *Note: this rule currently stands — follow it. Whether it is eventually struck or
  actively enforced is an open decision tracked separately from this document.*
- No generated files (`node_modules/`, `__pycache__/`, build output) in commits.

## 18. Design Before Code

- Changes that touch more than 3 files, change a public API, or introduce a new subsystem require a short design doc in `docs/` *before* implementation begins.
- The doc does not need to be long. A few paragraphs covering **problem**, **approach**, **alternatives considered**, and **open questions** is sufficient.
- The purpose is to catch bad designs before they become 2,000 lines of code. A 20-minute doc saves a week of rework.
- Trivial changes (rename, bug fix, test addition) do not need a doc.

## 19. 

- Solutions are as elegant as possible and not overly engineered

## 20. Canonical Data Types

- Structured data that flows between modules must use **Pydantic `BaseModel`**, not plain dicts. Dicts provide no validation, no autocomplete, and no documentation. Attribute access (`node.data.nodeType`) is explicit; dict access (`node["data"]["nodeType"]`) is opaque.
- The canonical graph types live in `_types.py`: `NodeData`, `GraphNode`, `GraphEdge`, `PipelineGraph`. All internal code passes these models. Use `.model_dump()` only at serialization boundaries (JSON files, API responses).
- Use `.model_validate(d)` to convert incoming dicts (e.g. from JSON) to models at the earliest opportunity. Do not pass raw dicts through multiple function calls.
- If you find code accessing a Pydantic model with `["key"]` syntax or `.get()`, that is a bug.

## 21. Minimal API Surface

- Every public function, class, and constant is a maintenance promise. Prefix internal helpers with `_` (e.g. `_build_edges`, `_sanitize_func_name`).
- `__init__.py` re-exports should be deliberate. Only export what external consumers need. Do not re-export everything from submodules.
- If a function is only called from one module, consider whether it belongs in that module rather than in a shared utility file.
- Before adding a new public function, check whether an existing function can be extended to cover the use case.

## 22. Test Quality

- **Deterministic.** Tests must not depend on wall-clock time, random seeds, filesystem ordering, or network. Flaky tests erode trust and get ignored.
- **Fast.** Unit tests should stay sub-second where practical, and the full preflight suite should stay comfortably under 10 minutes on a normal developer machine. If a test needs heavy I/O, use `tmp_path` with minimal data and mark genuinely expensive cases as `slow` or `perf`. Never load production-scale data in tests.
- **Focused.** Each test verifies one behaviour. If a test name needs "and" in it, split it into two tests.
- **Independent.** Tests must not depend on execution order. No shared mutable state between tests. Use fixtures, not module-level setup.
- **Readable.** A test is documentation. The arrange/act/assert structure should be obvious. Use helper functions (e.g. `_make_graph`, `_n`, `_g`) to keep test bodies short and intention-clear.
- Prefer **real objects** over mocks. Mock external services and I/O; do not mock your own code unless there is no alternative.

## 23. Module Boundaries

- Imports flow **downward**: high-level modules import low-level modules, never the reverse. The dependency direction is: `cli` / `server` → `executor` / `codegen` / `deploy` → `parser` → `_types` / `_topo` / `graph_utils`.
- Circular imports are a design smell. If module A imports module B and module B imports module A, one of them has the wrong responsibility. Fix the design, do not paper over it with deferred imports (unless the circular dependency is genuinely at the type-checking level only).
- Each module should have a clear one-sentence purpose. If you cannot describe what a module does without "and", it may need splitting.
- Cross-module communication happens through well-defined types (`PipelineGraph`, `ResolvedDeploy`, `TraceResult`), not raw dicts or tuples.

## 24. Logging & Observability

Haute pipelines run in production serving environments (Databricks model serving, containerised endpoints). When something fails at 2am, logs are the only diagnostic tool. Good logging is not optional.

- **Use structlog, not `print`.** All runtime output goes through `structlog` via `from haute._logging import get_logger`. `print()` is for CLI user-facing output only (via `click.echo`). Never use `print` for diagnostic or debug information.
- **One logger per module.** Declare `logger = get_logger(component="module_name")` at module level. Do not pass logger objects between functions. The `component` kwarg identifies the source in JSON logs.

```python
from haute._logging import get_logger

logger = get_logger(component="parser")
```

- **Structured context via keyword arguments.** structlog logs are key-value pairs, not format strings. Include the identifiers a human needs to diagnose the problem.

```python
# BAD
logger.error("Node failed")
logger.error(f"Node {node_id} failed: {exc}")

# GOOD — structured, searchable, machine-parseable
logger.error("node_execution_failed", node_id=node_id, error=str(exc))
logger.info("pipeline_parsed", file=str(path), node_count=len(graph.nodes))
```

- **Correct log levels.**
  - `DEBUG` — internal state useful only when actively debugging (intermediate row counts, cache hits, topo sort order).
  - `INFO` — significant lifecycle events (pipeline parsed, deploy started, model logged to MLflow, graph executed).
  - `WARNING` — something unexpected that the system recovered from (fallback parser used, missing optional config, cache invalidated).
  - `ERROR` — something failed that the user needs to know about (node execution error, file not found, deploy rejected).
- **Every module that does work must have a logger.** Pure data-structure modules (`_types.py`) are exempt. Anything that parses, executes, deploys, validates, or does I/O needs logging at `INFO` level for lifecycle events and `ERROR` level for failures.
- **No logging in hot paths.** Do not log per-row or per-node-per-execution inside the executor loop. Log once at the start and once at the end with summary counts.
- **No sensitive data in logs.** Do not log file contents, DataFrame values, API keys, or user code. Log metadata (file paths, column names, row counts) only.
- **Errors carry context up.** When catching and re-raising, use `raise ... from exc` to preserve the chain. When logging an error, include the exception: `logger.exception("...")` or `logger.error("...", exc_info=True)`.

## 25. Background Job Pattern

Long-running operations use a consistent lifecycle across route modules:

- **Job store**: `_jobs: dict[str, dict[str, Any]] = {}` at module level, keyed by a hex job ID from `os.urandom`.
- **TTL eviction**: `_evict_stale_jobs()` called at the start of the submit endpoint. Jobs older than `_JOB_TTL_SECONDS` (24h) are removed.
- **Killable compute dispatch**: Heavy data execution must run in a supervised isolated
  worker with native memory enforcement. A route may use a thin thread only for job-state
  orchestration or compatibility I/O that does not retain the heavy computation; a timeout
  or cancellation must terminate and join the compute worker before the route publishes a
  terminal state.
- **Status polling**: A GET endpoint returns the job's current status, progress, and elapsed time. The result is included when status is `"completed"`.
- **Thin dispatchers**: Thread or process entrypoints remain thin. Extract the actual work
  into module-level functions (e.g. `_solve_online`, `_solve_ratebook`) with explicit typed,
  serialisable parameters so they are testable and compatible with spawn-based workers.

New route modules that add background jobs must follow this lifecycle and must classify
whether their work is heavy enough to require isolated execution. See the execution-engine,
background-jobs, and server-api specs for the isolation and publication contracts.

## 26. Fix It If You See It

- Any linting error, CI failure, or test failure must be fixed in the same commit that touches the affected area, regardless of when the issue was introduced.
- Do not leave pre-existing broken windows for the next person. If `ruff`, `mypy`, `eslint`, `tsc`, or `pytest` fails on code you are working near, fix it.
- This applies to all checks in the preflight script: Python lint, Python types, TypeScript types, ESLint, and tests.
- If a fix is non-trivial and unrelated to your change, it may be split into a separate commit, but it must still ship in the same PR.

## 27. Formatted Lookup Tables

When a file contains an aligned lookup table (e.g. `NODE_TYPE_META` in `nodeTypes.ts`), new entries must match the column alignment of existing entries. Misaligned entries make diffs noisy and the table harder to scan.

## 28. Name-to-Artefact Mapping

Haute maps user-controlled names onto artefacts: node labels onto config files and generated Python functions, submodel names onto module files, table labels onto cache files, data paths onto cache keys, table references onto remote tables.

**Every such mapping must respect the artefact side's own identity relation, not string equality on the name side.** For a map from names to artefacts:

1. **No silent collision.** If two names in play denote the same artefact under the artefact's identity relation — the same file on a case-insensitive filesystem, the same remote table under case-insensitive identifier resolution, the same file under Unicode normalisation — then code that writes, dedupes, or diffs by the raw name string will let one artefact silently clobber or shadow another. Collision guards, stale-cleanup diffs, and cache keys must compare in the artefact's relation.

2. **No false collision.** Comparing names in a relation *coarser* than the artefact's rejects valid input or splits one artefact across two keys. A guard that buckets identifiers across separate modules rejects a legal graph; a cache key that fails to canonicalise path spelling loads one file twice.

3. **Right scope.** Apply the relation over the namespace the artefact actually lives in — per emitted module for Python identifiers, per directory for files, per node for input table labels, and *across saves* for anything diffed against a previous state.

4. **One derivation.** Where the same mapping is needed in two places (backend and frontend sanitizer, dependency-map build and file-watcher lookup, editor preview and server write), it comes from one generated contract or one owning runtime, per standard 1 — never from two handwritten implementations. A legacy twin that still exists must be mechanically pinned to its counterpart until it is retired; drift between them is a violation even when each side is internally consistent.

**The relation follows the lookup stack, not the operating system.** "macOS and Windows are case-insensitive, Linux is case-sensitive" is itself a simplification: haute inside WSL is a Linux process on a Linux filesystem even though the machine runs Windows; a Windows process reading through the WSL share gets the Linux side's byte-exact resolution; network mounts resolve by the server's rules; NTFS supports per-directory case sensitivity. The consequence for guards is that **platform-independent rejection is right** — not merely for checkout portability, but because the platform cannot be inferred from the process's own view in the first place.

**Why this keeps biting.** The relation is invisible on a development machine where it happens to match. Every instance found so far passed all tests on the leg where the relation does not hold. The counter-measures are therefore structural: characterisation tests run on the `platform-smoke` lane (Windows and macOS), and guards reject on *every* platform so a pipeline saved on one OS stays loadable on another.

### Standing checklist for new code

When adding any surface that derives an artefact name or key from user input:

1. Route names through the shared sanitizer — `_sanitize_func_name` in `src/haute/_graph_utils.py`, with its frontend counterpart pinned to it by the parity fixture — never a local regex.
2. Compare artefact paths case-folded; canonicalise cache keys through normalised resolution.
3. Ask which namespace the artefact lives in, and guard exactly that scope — including across saves if anything diffs against previous state.
4. If the mapping is needed on both sides of the wire, derive it from one generated contract or one owning runtime (standard 1). Do not add a new handwritten twin; an existing one stays in the parity fixture — the checked-in vectors both sides are pinned to, regenerated together so that divergence fails both suites — until it is retired.
5. Put the platform-relation test in a file the `platform-smoke` lane runs.

The user-facing consequences of this invariant are documented in [Filesystem Portability](building-models/filesystem-portability.md); the wider failure family is [Defect Classes](DEFECT_CLASSES.md).

---

## LLM-Generated Code: Watch For These

AI coding assistants produce plausible-looking code that often hides real problems. Every reviewer (human or AI) must check for these patterns specifically.

### Dangerous fallbacks that mask errors

```python
# BAD - silently returns empty data instead of crashing
def get_data(path):
    try:
        return pl.read_parquet(path)
    except Exception:
        return pl.DataFrame()  # caller has no idea it failed
```

- Never return a default value from a `catch` unless the caller explicitly expects it.
- Prefer letting exceptions propagate. If you must catch, log the error and re-raise or return a result type that signals failure.
- Watch for `or {}`, `or []`, `or ""` fallbacks that turn a bug into silent wrong data.

### Broad exception swallowing

- `except Exception: pass` is almost never correct. Catch the specific exception you expect.
- `except Exception as e: return {"error": str(e)}` in API endpoints is fine - but only at the outermost layer. Inner code should not catch broadly.

### Hallucinated APIs and parameters

- LLMs invent function signatures, config keys, and library methods that don't exist. Every API call, import, and parameter must be verified against the actual codebase or library docs.
- Watch for plausible-but-wrong Polars methods (e.g. `df.groupby()` instead of `df.group_by()`).

### Stale patterns from older library versions

- LLMs train on old code. Watch for deprecated patterns:
  - `@app.on_event("startup")` → use `lifespan` context manager
  - `from typing import List, Dict` → use `list`, `dict` (Python 3.11+)
  - `pd.DataFrame` when we use `pl.DataFrame`

### Defensive code that hides bugs

```python
# BAD - if node_map is missing a key, this silently skips it
result = node_map.get(nid, {}).get("data", {}).get("config", {})
```

- Chained `.get()` with default dicts makes `KeyError` impossible to diagnose. If the key should exist, access it directly and let it fail loudly.
- Only use `.get()` with defaults when the key is genuinely optional.

### Duplicated logic disguised as "safety"

- LLMs often generate a "just in case" check that duplicates logic already handled upstream. This creates two code paths that must be kept in sync.
- If a function already validates its input, don't re-validate it in the caller.

### Over-abstraction and premature generalisation

- LLMs love creating `BaseNode`, `NodeFactory`, `AbstractPipelineExecutor` hierarchies for code that has exactly one concrete implementation.
- If there's only one subclass, you don't need the base class.
- Prefer plain functions over class hierarchies until you have three concrete use cases.

### Comments that restate the code

```python
# BAD - the comment adds zero information
x = x + 1  # increment x by 1
```

- LLMs pad output with obvious comments. Comments should explain *why*, not *what*.
- Delete any comment that a competent reader could infer from the code itself.

### Untested edge cases presented as handled

- LLMs generate `if` branches for edge cases but don't test them. An untested branch is worse than no branch - it gives false confidence.
- If you add an edge case handler, add a test for it. If you can't test it, add a `# TODO: untested` comment.

### HTTPException swallowed by generic except

```python
# BAD - the 403 is caught and becomes a 500
try:
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Outside root")
    target.write_text(data)
except Exception as exc:
    raise HTTPException(status_code=500, detail=str(exc))
```

- LLMs naturally place validation inside the same `try` block as the I/O. `HTTPException` inherits from `Exception`, so it gets caught and re-wrapped as a 500.
- Fix: move validation (and its `raise HTTPException`) **before** the `try` block.

### Frontend silent catches

```typescript
// BAD - TypeScript equivalent of except: pass
fetch(url).catch(() => {})

// GOOD - at minimum, log the failure
fetch(url).catch((e) => console.warn("request failed", e))
```

- LLMs generate `.catch(() => {})` to suppress Promise rejections. This hides network errors, auth failures, and API bugs.
- Always log with `console.warn` at minimum. If the catch updates state (e.g. clearing a list), still log the error.

### Import bloat

- LLMs import modules speculatively. If a function isn't used, the import shouldn't be there.
- Watch for `from typing import ...` lines that grow with every edit but never shrink.

---

## No Transition Layers

This is a brand new application. Prefer one clean API shape over transition layers, version checks, or migration code.

- **No old API paths** - if an API is poorly designed, change it. Do not keep the old version alongside the new one.
- **No feature flags** - if a feature is ready, ship it. Do not add `ENABLE_NEW_X` environment variables.
- **No versioned endpoints** - `/api/v1/` is unnecessary. Use `/api/` and evolve it as needed.
- **No migration scripts** - if the data model changes, update the code. There is no production data to migrate yet.
- **No warning-based migration periods** - if something is wrong, remove it. Do not add `warnings.warn` with a future removal date.

The only exception is the public PyPI package interface (`haute` CLI and core APIs). Prioritize clean, simple code over transitional indirection.

---

## Security Principles for Development

These principles are compulsory for code written for haute.

**1. Minimise data egress.** Data does not leave the host except via deliberate, user-specified destinations. No telemetry, no analytics, no phone-home, no auto-update checks, no error-reporting beacons. If a feature or dependency would introduce egress, surface it explicitly and discuss before adding.

**2. Minimise tooling dependencies.** Default to *not* taking a new dependency. The bar for adding one is low — real code simplification or substantial functionality is enough — but the bar for *keeping* one with no clear value is zero. Smaller dependency trees mean less supply-chain surface and less to keep updated. When you find an unused or marginal dependency, remove it.

**3. Resolve through the lock file.** All Python dependency resolution produces a committed lock file that is the source of truth for what gets installed in CI and every environment. Do not bypass it with raw installs or unmanaged virtualenvs.

**4. Pin dependencies precisely, and invoke them via the lock file.** Two halves of one supply-chain rule.

*Pinning — three layers, because the right posture differs by who resolves the manifest.*

- **Lock files** are the actual supply-chain and reproducibility control. They pin every package in the graph to an exact version *and* record its hash, so a tampered or re-uploaded artifact at a given version fails at install time. A bare version pin is **not** this control: a version string is a label, and the bytes behind it can change on the index — only the recorded hash detects that. This layer defends against both a malicious re-upload and a merely-buggy release that changes behaviour silently.
- **Development and tooling dependencies** are exact-pinned in the manifest as well. They never reach an end user; they are the toolchain the repository runs on, so manifest exactness reinforces the lock file and stops a stray install from quietly relaxing one.
- **Published runtime dependencies** use a floor with a compatibility cap — for packages at 1.0 or above, cap below the next major; for 0.x packages, cap below the next minor — and **not** an exact pin. Exact-pinning them is counterproductive: it gives end users no integrity protection (a version number is not a hash, dependency specifiers cannot carry hashes, and the wheel does not ship our lock file), and it can render the package uninstallable when a transitive dependency is yanked. The floor keeps the published artifact installable and composable downstream; the cap bounds an unattended fresh install to non-breaking releases.

*Invocation.* Prefer invocation paths that resolve through the project's lock file rather than a global tool of the same name — the project runner and local binary directory over a resolver that falls back to the registry. The motivating incident: a globally installed test runner shadowed the locally pinned version and broke DOM environment setup silently. The lock file pin was correct; the invocation walked past it.

**5. Lazy-load anything that expands the trust boundary.** Imports, code execution, external service connections — load them when their function is invoked, not at module import. A server at idle should expose as little as possible. Lazy loading also makes dependency vulnerabilities harder to reach even when present.

**6. Don't break auditability.** It must remain possible to answer: what data entered the system, what left it, what code executed, what external services were contacted. Do not introduce code paths that make these questions unanswerable. Prefer designs that preserve or improve audit-trail clarity.

---

## Local Verification (Preflight)

CI catches issues after you push, but failed CI wastes time. Run checks **locally** before pushing to catch problems in seconds rather than minutes.

### Preflight script

The repo includes one-command verification scripts that mirror CI exactly. Windows is the
priority local development environment, so use PowerShell there:

```powershell
# Windows full check: lint + types + tests (~6 min)
powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1

# Windows quick check: lint + types only (~30s)
powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1 --quick
```

Use Bash on Linux and macOS:

```bash
# Linux/macOS full check
./scripts/preflight.sh

# Linux/macOS quick check
./scripts/preflight.sh --quick
```

The script runs these checks in order and reports every selected failure:

| Check | What it catches | Approximate time |
|-------|----------------|-----------------|
| `ruff check .` | Python lint errors | 1s |
| `ruff format --check .` | Python formatting | 1s |
| `mypy src/haute/` | Python type errors | 5s |
| `pytest tests/ --collect-only -q` | Broken test imports/collection | 10s |
| `pytest tests/ -n 4 --cov=src/haute --cov-branch --cov-fail-under=90` + `scripts/check_critical_coverage.py` | Python test failures, global line+branch coverage < 90%, per-file critical coverage (60s per-test timeout) | 4 min |
| `HAUTE_BUILD_FRONTEND=1 uv build` | Package build failures | 1 min |
| `npm run typecheck` (frontend, `tsc -b --noEmit`) | TypeScript type errors | 5s |
| `npm run lint` (frontend, `eslint .`) | JS/TS lint errors (no-explicit-any, unused vars, React hooks rules) | 3s |
| `npm run build` (frontend) | Production build failures | 30s |
| `npm run check:bundle` (frontend) | Bundle budget and UI dependency violations | 1s |
| `npm run test:benchmark:pr` (frontend) | Render/hash performance regressions | 30s |
| `npm run test:coverage` (frontend) | Frontend test failures, coverage thresholds, per-file critical coverage | 1 min |

`--quick` runs only the lint, format, and type rows — it skips every test, build, and
budget gate, and composes with the `-only` flags. `--backend-only` / `--frontend-only`
run the corresponding half of the table: a full frontend-only run includes the build,
bundle-budget, benchmark, and coverage gates — *not* just typecheck + lint + tests.
Opt-in extras: `--perf` adds the backend perf suite, and `--init-smoke` runs the
fresh-install wheel smoke on its own.

Use `--quick` during active development (catches ~80% of CI failures in 15 seconds). Run the full version before opening a PR. Set `PYTEST_WORKERS=<n>` to tune the backend test worker count for unusually small or large machines.

### Pre-commit hooks

Pre-commit hooks run automatically on `git commit`. Install them once:

```bash
uv run pre-commit install
```

The hooks run:
- **Ruff lint + format** on changed `.py` files
- **Mypy** on changed `.py` files in `src/haute/`
- **TypeScript type check** on changed `.ts`/`.tsx` files
- **ESLint** on changed `.ts`/`.tsx` files

Hooks do **not** run tests (too slow for commit-time). Run tests via the preflight script or manually.

### Manual checks for specific changes

For opt-in performance, benchmark, bundle, and memory smoke lanes, see
[Local Performance Checks](PERFORMANCE_CHECKS.md).

If you only touched backend Python, run the backend half of preflight:
```bash
./scripts/preflight.sh --backend-only
```
For a faster inner loop while iterating (not a substitute for the full backend gate):
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/haute/ && uv run pytest tests/ -q
```

If you only touched frontend TypeScript, run the frontend half of preflight — it is the
same gate CI applies (typecheck, lint, build, bundle budget, PR benchmark gate, and
coverage-gated tests):
```bash
./scripts/preflight.sh --frontend-only
```
For a faster inner loop while iterating (not a substitute for the full frontend gate):
```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

If you added a new test file:
```bash
# Verify it compiles, passes lint, and actually runs
uv run pytest tests/test_your_new_file.py -v   # backend
cd frontend && npm test -- src/__tests__/your_new_file.test.ts  # frontend
```

### Common CI failures and how to catch them locally

| CI failure | Local command to catch it | Root cause |
|-----------|-------------------------|-----------|
| `error TS2591: Cannot find name 'require'` | `npm run typecheck` | Used `require()` instead of `import` in a `.ts` file |
| `error TS6133: declared but never read` | `npm run typecheck` | Unused import or variable |
| `@typescript-eslint/no-explicit-any` | `npm run lint` | Used `any` type — use a specific type or `unknown` |
| `react-hooks/refs` | `npm run lint` | Passed a ref to JSX via `createElement` — use JSX syntax or callback refs |
| `prefer-const` | `npm run lint` | Used `let` for a variable that's never reassigned |
| `FAILED ... DID NOT RAISE` | `uv run pytest tests/test_file.py -v` | Test expects an exception on one platform but not another |
| `xfail(strict=True) XPASS` | `uv run pytest tests/test_file.py -v` | Race condition test passed unexpectedly — use `strict=False` |
| `coverage fail_under=90` | `uv run pytest --cov=src/haute --cov-fail-under=90` | New code without tests, or test file not discovered |

---

## Quick Checklist

Copy into PR descriptions:

```
Design Philosophy
- [ ] Code (.py) is the source of truth - GUI is a view, not the canonical form
- [ ] Same code path works for 1-row and N-row execution
- [ ] Uses the single execution engine (parse → execute_graph), no parallel paths
- [ ] Polars LazyFrame throughout - no premature .collect() or pandas conversion
- [ ] GUI handles errors gracefully (error banner, not crash)
- [ ] Generated code is clean, idiomatic, ruff-passing Python
- [ ] No unnecessary abstraction layers or platform reimplementation
- [ ] All outputs are traceable back through the graph
- [ ] GUI interactions feel instant - no unnecessary re-renders, network calls, or blocking

Engineering Standards
- [ ] No duplicated logic; shared constants in `_types.py`, re-exported through `graph_utils.py`
- [ ] All functions have type annotations
- [ ] Uses PEP 563/585/604: `from __future__ import annotations`, `list`/`dict`/`tuple`, `X | None`
- [ ] Pydantic fields: `str | None = None` for optionals, `Field(default_factory=...)` for mutables
- [ ] API endpoints use Pydantic request/response models
- [ ] `ruff check src/haute/` passes with zero errors
- [ ] No unused imports, variables, or dead code
- [ ] Docs updated if behaviour changed (including `README.md` and `docs/index.md`)
- [ ] No new heavy dependencies in core
- [ ] No resource leaks (file handles, async tasks, sockets)
- [ ] Error cases return structured responses, not bare strings
- [ ] `HTTPException` raised outside `try/except Exception` blocks
- [ ] Consistent naming (snake_case Python and API fields, camelCase TypeScript functions/variables, PascalCase classes/components)
- [ ] React state in hooks/refs, not module-level variables
- [ ] File access endpoints validate paths with `_get_project_root()` + resolve + startswith
- [ ] Path traversal rejection tested (e.g. `../../etc/passwd` → 403)
- [ ] Tests added for new logic; bug fixes include regression tests
- [ ] Design doc in `docs/` if change touches >3 files or changes a public API
- [ ] Structured data uses Pydantic models, not raw dicts
- [ ] Internal helpers prefixed with `_`; only intentional public API is exported
- [ ] Tests are deterministic, fast, focused, and independent
- [ ] No circular imports; import direction flows downward
- [ ] Logging uses structlog, not `print`; correct levels; structured context
- [ ] Background jobs follow the standard pattern (§25): job store, TTL eviction, killable compute dispatch where heavy, status polling
- [ ] Pre-existing lint/type/test failures in touched areas are fixed (§26)
- [ ] New entries in aligned lookup tables match existing column alignment

LLM Code Review
- [ ] No silent fallbacks that mask errors (return empty data, `or {}`, `or []`)
- [ ] No broad exception swallowing (`except Exception: pass` in Python, `.catch(() => {})` in TypeScript)
- [ ] No `HTTPException` inside generic `try/except Exception` blocks
- [ ] All API calls and imports verified against actual codebase/library docs
- [ ] No deprecated patterns (old typing imports, on_event, pandas)
- [ ] No chained .get() on keys that should exist - fail loudly on missing data
- [ ] No redundant validation that duplicates upstream checks
- [ ] No premature abstraction (base classes with one subclass)
- [ ] Comments explain why, not what - no restating the code
- [ ] Edge case branches have tests, or are marked # TODO: untested

API Shape
- [ ] No transition shims, version flags, or migration code
- [ ] Bad APIs are replaced, not versioned alongside

Preflight
- [ ] Windows: `powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1 --quick` passes (lint + types)
- [ ] Windows: `powershell -ExecutionPolicy Bypass -File .\scripts\preflight.ps1` passes (full check with tests) - required before merge
- [ ] Linux/macOS: `./scripts/preflight.sh --quick` passes if the change touches Unix-only tooling
```
