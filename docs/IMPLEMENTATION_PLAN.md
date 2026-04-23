# Implementation Plan: Production-Grade Refactor

## P0-A: Pydantic Models Everywhere

**Goal**: Replace `TypedDict` graph dicts with validated Pydantic models throughout the backend. Parse once at the API boundary, pass typed objects everywhere.

**Current state**: `schemas.py` has Pydantic models for API validation, but `.model_dump()` discards them immediately. The rest of the codebase passes `dict[str, Any]`. `graph_utils.py` has parallel `TypedDict` definitions that mypy can't enforce (`total=False`).

### Steps

1. **Promote `schemas.py` models to the canonical graph types**
   - Move `Graph`, `GraphNode`, `GraphNodeData`, `GraphEdge` from `schemas.py` → new `src/haute/types.py`
   - Add `model_config = ConfigDict(extra="allow")` so submodel metadata and React Flow extras survive round-tripping
   - Add convenience properties: `GraphNode.node_type`, `GraphNode.config`, `GraphNode.label` (thin wrappers over `self.data.x`)
   - Keep `schemas.py` for request/response models only — they reference `types.py`

2. **Update `graph_utils.py`**
   - Delete the `TypedDict` definitions (`GraphNode`, `GraphEdge`, `PipelineGraph`, `NodeData`)
   - Replace `_Frame = pl.LazyFrame` alias (keep as-is, it's fine)
   - Update `_prepare_graph`, `ancestors`, `topo_sort_ids`, `flatten_graph`, `_execute_lazy` signatures to accept `Graph` / `list[GraphNode]` / `list[GraphEdge]`
   - Internal dict operations (`node["data"]["config"]`) become `node.data.config`

3. **Update executor.py**
   - `_build_node_fn(node: GraphNode, ...)` — attribute access throughout
   - `execute_graph(graph: Graph, ...)` — no more `.get("nodes", [])`
   - `_eager_execute` — same treatment
   - `resolve_instance_node` — returns `GraphNode` not `dict`

4. **Update trace.py**
   - `execute_trace(graph: Graph, ...)` — attribute access
   - `_TraceCache` stores `dict[str, GraphNode]` for `node_map`

5. **Update codegen.py**
   - `_node_to_code(node: GraphNode, ...)` — attribute access
   - `graph_to_code(graph: Graph, ...)` — same
   - `_topo_sort` accepts typed list

6. **Update deploy/**
   - `_pruner.py`: `prune_for_deploy(graph: Graph, ...)` returns `Graph`
   - `_scorer.py`: `score_graph(graph: Graph, ...)`
   - `_schema.py`: attribute access on nodes
   - `_config.py`: `ResolvedDeploy.pruned_graph` becomes `Graph`
   - `_validators.py`: attribute access

7. **Update parser.py**
   - `parse_pipeline_file` returns `Graph` (constructed via `Graph(nodes=[...], edges=[...], ...)`)
   - Internal `raw_nodes` stay as dicts during parsing, converted to `GraphNode` at the boundary

8. **Update server.py**
   - Remove `.model_dump()` at API entry points — pass `Graph` objects directly
   - `_parse_pipeline_to_graph` returns `Graph`

9. **Update tests**
   - Test fixtures construct `GraphNode(...)` instead of `{"id": ..., "data": {...}}`
   - Existing dict-based test graphs need migration

10. **Verification**
    - `uv run pytest tests/ -x -q` — all pass
    - `uv run ruff check src/haute/` — clean
    - `uv run mypy src/haute/` — check for new type coverage

**Risk**: Large surface area. Mitigate by doing it module-by-module, running tests after each.

**Estimated scope**: ~400 lines changed across 12 files. No behavioral changes.

---

## P0-B: asyncio.to_thread() for Compute Endpoints

**Goal**: Stop blocking the event loop during heavy computation (Polars model scoring, pipeline execution).

**Current state**: `run_pipeline`, `trace_row`, `preview_node`, `execute_sink_node` all call sync functions directly inside `async def` handlers. The Databricks fetch endpoint already correctly uses `asyncio.to_thread()`.

### Steps

1. **Wrap compute calls in server.py**
   - `run_pipeline`: `results = await asyncio.to_thread(execute_graph, graph)`
   - `trace_row`: `result = await asyncio.to_thread(execute_trace, graph, ...)`
   - `preview_node`: `results = await asyncio.to_thread(execute_graph, graph, ...)`
   - `execute_sink_node`: `result = await asyncio.to_thread(execute_sink, graph, ...)`

2. **Verify thread safety of caches**
   - `_preview_cache` and `_TraceCache` are read/written from the thread pool
   - Add `threading.Lock` guards (ties into P2-C)

3. **Verification**
   - Start `haute serve`, open GUI
   - Click preview on a heavy node → WebSocket should stay responsive
   - File watcher should still broadcast during computation

**Estimated scope**: ~20 lines changed in `server.py`. Quick win.

---

## P1-A: Decouple Tests from main.py

**Goal**: Tests should not break when the user edits their pipeline.

**Current state**: `conftest.py` defines `full_graph` by parsing the real `main.py`. Every test that uses this fixture is coupled to the user's pipeline structure. We saw cascading failures when `main.py` changed.

### Steps

1. **Create `tests/fixtures/test_pipeline.py`**
   - A minimal but complete pipeline with:
     - 1 `apiInput` node (with `row_id_column`)
     - 1 `dataSource` node (parquet)
     - 1 `liveSwitch` node
     - 2 `transform` nodes
     - 1 `externalFile` node (catboost)
     - 1 `output` node
     - 1 `dataSink` node
   - Wire with `pipeline.connect()` calls
   - Include a small test data file in `tests/fixtures/data/`

2. **Create `tests/fixtures/test_submodel.py`**
   - A minimal submodel with 2 nodes for submodel-related tests

3. **Update `conftest.py`**
   - `full_graph` fixture parses `tests/fixtures/test_pipeline.py` instead of `main.py`
   - Add a separate `user_graph` fixture (optional) that parses `main.py` for the single integration test

4. **Migrate test assertions**
   - Update node IDs, node types, and expected counts to match the fixture pipeline
   - This is the bulk of the work — ~20 test functions reference specific node names

5. **Add a single integration test**
   - `test_user_pipeline_parses()` — just verifies `main.py` is valid Python and parses without error
   - No assertions on specific node names or structure

6. **Verification**
   - All tests pass with fixture pipeline
   - Edit `main.py` arbitrarily → tests still pass

**Estimated scope**: 1 new fixture file, ~100 lines of test updates.

---

## P1-B: Split graph_utils.py into Focused Modules

**Goal**: Single-responsibility modules with clear dependency graph.

**Current state**: `graph_utils.py` (463 lines) handles types, sorting, caching, flattening, execution, and I/O.

### Steps

1. **Create new modules** (all under `src/haute/`):
   - `types.py` — `GraphNode`, `GraphEdge`, `PipelineGraph` (or Pydantic models from P0-A), `_Frame`, `_sanitize_func_name`, `build_instance_mapping`, `resolve_orig_source_names`
   - `topo.py` — `topo_sort_ids`, `ancestors`
   - `cache.py` — `graph_fingerprint`
   - `io.py` — `read_source`, `load_external_object`, `_load_external_object_uncached`, `_object_cache`
   - `flatten.py` — `flatten_graph`

2. **Keep `graph_utils.py` as the canonical graph API facade**
   ```python
   # graph_utils.py — graph models, helpers, and runtime imports
   from haute.types import *
   from haute.topo import *
   from haute.cache import *
   from haute.io import *
   from haute.flatten import *
   from haute.execute_lazy import _execute_lazy, _prepare_graph
   ```
   This means zero import changes needed elsewhere initially.

3. **Gradually update imports** in other modules to import from the specific module

4. **Move `_execute_lazy` and `_prepare_graph`** to a dedicated `execute_lazy.py` or keep in `graph_utils.py` (these are the only functions with complex dependencies)

5. **Verification**
   - All tests pass
   - No circular imports (verify with `python -c "import haute.types"` etc.)

**Estimated scope**: ~500 lines moved (not changed), 1 facade file. Low risk.

**Note**: If P0-A is done first, `types.py` already exists and this becomes simpler.

---

## P2-A: Document exec()/pickle Trust Boundary

**Goal**: Make the security model explicit so future contributors don't accidentally widen it.

### Steps

1. **Create `docs/SECURITY.md`**
   - Document that `exec()` runs user code in the executor — this is by design for the single-user `haute serve` context
   - Document that `pickle.load()` deserializes files from the project directory
   - State the trust boundary: "The user who runs `haute serve` is trusted. The pipeline `.py` file and all referenced data/model files are trusted."
   - List what would need to change for multi-user: sandboxed execution, restricted file access, input validation

2. **Add path validation to `load_external_object`**
   - Resolve the path and verify it's within `Path.cwd()` (same check `server.py` does for file browsing)
   - Raise `ValueError` if the path escapes the project root

3. **Add a `# SECURITY:` comment** above `exec()` in `executor.py` pointing to the doc

**Estimated scope**: 1 new doc, ~10 lines of code.

---

## P2-B: Extract NodeRegistry Base Class

**Goal**: Eliminate the duplicated `node()`, `connect()`, property logic between `Pipeline` and `Submodel`.

### Steps

1. **Create `NodeRegistry` base class in `pipeline.py`**
   ```python
   class NodeRegistry:
       def __init__(self, name: str, description: str = ""):
           self.name = name
           self.description = description
           self._nodes: list[Node] = []
           self._node_map: dict[str, Node] = {}
           self._edges: list[tuple[str, str]] = []

       def node(self, fn=None, **config): ...
       def connect(self, source, target): ...

       @property
       def nodes(self) -> list[Node]: ...
       @property
       def edges(self) -> list[tuple[str, str]]: ...
   ```

2. **`Pipeline(NodeRegistry)`** — adds `run()`, `score()`, `to_graph()`, `submodel()`

3. **`Submodel(NodeRegistry)`** — empty body (inherits everything it needs)

4. **Verification**: existing tests pass unchanged

**Estimated scope**: ~40 lines removed, ~20 added. Very low risk.

---

## P2-C: Bounded Caches with Locks

**Goal**: Prevent unbounded memory growth and race conditions.

### Steps

1. **`_PreviewCache` in executor.py**
   - Add `threading.Lock`
   - Add `max_entries: int = 1` (single-entry is fine, just needs the lock)
   - Wrap `execute_graph` cache read/write in `with self._lock:`

2. **`_TraceCache` in trace.py**
   - Same treatment: `threading.Lock`, guarded read/write

3. **`_object_cache` in graph_utils.py (or io.py after split)**
   - Replace bare dict with `cachetools.LRUCache(maxsize=32)` or a simple bounded dict
   - Add `threading.Lock`
   - Existing mtime-based invalidation stays

4. **Verification**
   - Tests pass
   - Manual: open GUI, click preview rapidly, no crashes

**Estimated scope**: ~30 lines added across 3 files.

---

## P3-A: Structured Logging

**Goal**: JSON-structured logs with request context for production observability.

### Steps

1. **Add `structlog` dependency** to `pyproject.toml`
2. **Create `src/haute/logging.py`** — configure structlog with:
   - Dev: colored console output (current behavior)
   - Prod (`HAUTE_LOG_FORMAT=json`): JSON lines to stdout
3. **Add request-ID middleware** in `server.py` — generate UUID per request, bind to structlog context
4. **Replace `logger.info/warning/error`** calls with `structlog.get_logger()` bound calls
5. **Add execution timing** to structured log events (already computed, just needs emitting)

**Estimated scope**: 1 new file, ~50 lines of config, ~30 lines of call-site changes.

---

## P3-B: Frontend — Tailwind-Only + Error Boundaries

**Goal**: Maintainable styles, resilient UI.

### Steps

1. **Add `ErrorBoundary` component** in `frontend/src/components/ErrorBoundary.tsx`
   - Catches render errors, shows a recoverable error card
   - Wrap `NodePanel`, `NodePalette`, and the main canvas

2. **Migrate inline styles to Tailwind** (incremental, file-by-file):
   - `PipelineNode.tsx` — highest impact, rendered for every node
   - `NodePanel.tsx` — largest file, most inline styles
   - `NodePalette.tsx` — already mostly Tailwind
   - Use Tailwind's `arbitrary values` (`bg-[var(--bg-elevated)]`) for CSS custom properties

3. **Extract color constants** — move `accent`, `statusColors`, etc. to `tailwind.config.js` theme or a shared `constants.ts`

**Estimated scope**: Incremental. ~200 lines per component migrated.

---

## Recommended Execution Order

```
P0-B  (async threads)     — 30 min, instant win, no dependencies
P2-B  (NodeRegistry)      — 30 min, clean standalone refactor
P2-A  (security docs)     — 30 min, docs + 10 lines of code
P1-A  (decouple tests)    — 1-2 hrs, unblocks confident refactoring
P1-B  (split graph_utils) — 1 hr, sets up clean module structure
P0-A  (Pydantic models)   — 2-3 hrs, largest change, benefits from P1-B
P2-C  (bounded caches)    — 30 min, benefits from P0-B
P3-A  (structured logging) — 1 hr
P3-B  (frontend styles)   — ongoing, incremental
```

Start with P0-B (smallest, highest immediate impact), then P2-B and P2-A (quick wins that reduce tech debt), then P1-A (enables safe refactoring), then P1-B → P0-A (the big structural changes).
