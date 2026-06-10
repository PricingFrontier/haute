# Code ↔ GUI Sync - Design Document

## 1. Core Principle

**Python is the source of truth.** The `.py` file on disk is the canonical representation of a pipeline. The GUI is a live, editable view of that file. Edit either one - the other stays in sync.

---

## 2. Current State

```
GUI edits → JSON graph (source of truth) → executor.py runs it
                                          → codegen.py can export .py
```

The JSON graph is currently the source of truth. The `.py` file is a secondary export. This needs to flip.

### What exists today

| Component | Status | Notes |
|---|---|---|
| `codegen.py` | ✅ Works | Generates `.py` from graph JSON (GUI → Code) |
| `executor.py` | ✅ Works | Runs pipeline from graph JSON |
| `pipeline.py` | ✅ Works | Separate execution path via type-specific `@pipeline.<type>` decorators |
| `parser.py` (`.py` → graph JSON) | ✅ Works | Parses `.py` back into graph JSON (ast + regex fallback) |
| File watcher | ✅ Works | watchfiles-based live sync from file changes to GUI |
| WebSocket | ✅ Works | `/ws/sync` push channel from backend to frontend |

### Two execution engines (need to converge)

1. **`executor.py`** - runs from JSON graph (used by GUI preview/run)
2. **`pipeline.py` `Pipeline.run()`** - runs from decorated Python (used by CLI `haute run`)

These must converge so the same pipeline code drives both the GUI and the CLI.

---

## 3. Target Architecture

```
.py file (source of truth)
    │
    ├──→ parser.py (libcst) ──→ graph JSON ──→ GUI renders it
    │                                              │
    │                                              ▼
    └──────────────────────────── codegen.py ◀── GUI edits
                                     │
                                     ▼
                              .py file (written back)
                                     │
                                     ▼
                              file watcher detects change
                                     │
                                     ▼
                              parser re-parses → graph JSON
                                     │
                                     ▼
                              WebSocket push → GUI updates
```

### Data flow: Code → GUI

1. User edits `.py` file in VS Code (or any editor)
2. File watcher (watchfiles) detects the change
3. Parser (libcst) reads the `.py` file → produces graph JSON
4. Backend pushes graph JSON to frontend via WebSocket
5. GUI re-renders the graph

### Data flow: GUI → Code

1. User edits a node in the GUI (drag, rename, edit code, connect)
2. Frontend sends the edit to the backend
3. `codegen.py` performs a **surgical edit** on the `.py` file (libcst - replace just the changed function, don't regenerate the whole file)
4. File is written to disk
5. File watcher detects the change → but backend debounces to avoid a feedback loop (ignore writes it made itself within the last 500ms)

---

## 4. Parser Design

### What the parser understands (structured)

The parser extracts **graph structure** from the `.py` file:

| Python construct | Maps to |
|---|---|
| `@pipeline.<type>` decorated function | A node in the graph |
| Function name | Node ID / label |
| Function signature (`def transform(df)`) | Node inputs (0 params = source node) |
| Decorator kwargs (`@pipeline.data_source(path="...")`) | Node config |
| `pipeline.connect("a", "b")` calls | Edges |
| Parameter names matching other node names | Implicit edges |

### What the parser treats as opaque

Everything else is **preserved but not interpreted**:

- Code inside function bodies (just a text string in the GUI code editor)
- Imports
- Helper functions (not decorated with `@pipeline.<type>`)
- Comments, blank lines, formatting
- Module-level code that isn't `pipeline.connect()`

### Edge inference

Edges can be expressed two ways:

**Explicit** (complex DAGs):
```python
pipeline.connect("read_data", "polars_node")
pipeline.connect("read_data", "enrich")
pipeline.connect("polars_node", "output")
pipeline.connect("enrich", "output")
```

**Implicit** (parameter names match node names):
```python
@pipeline.output()
def output(polars_node: pl.DataFrame, enrich: pl.DataFrame) -> pl.DataFrame:
    # parameter names "polars_node" and "enrich" match node names → implicit edges
    return polars_node.join(enrich, on="id")
```

The parser supports both. Explicit `connect()` calls take precedence. If no `connect()` calls exist and parameter names match node names, infer edges from signatures.

### Error handling

The parser is **permissive, not strict**:

| Scenario | Behaviour |
|---|---|
| Valid Python, has `@pipeline.<type>` functions | Parse normally |
| Valid Python, no decorated functions | Show "No pipeline nodes found" warning banner |
| Syntax error in `.py` file | Show "Syntax error on line N" banner, keep last good graph |
| Decorated function with unparseable signature | Show that node with a warning icon, skip edge inference |
| Unknown decorator kwargs | Preserve them in config, don't crash |

**The GUI never crashes due to bad code.** Worst case: show the last known good state + an error banner.

### Implementation: libcst

[libcst](https://github.com/Instagram/LibCST) is a concrete syntax tree parser for Python. Unlike `ast`, it preserves:
- Comments
- Whitespace and formatting
- String quote style
- Trailing commas

This is critical for **round-trip fidelity**: parse a file, modify one function, write it back - everything else stays exactly as the user wrote it.

---

## 5. Surgical Code Edits (GUI → Code)

When the user edits a node in the GUI, we do NOT regenerate the entire `.py` file. Instead:

1. Parse the existing `.py` file with libcst
2. Find the decorated function matching the edited node
3. Replace just that function's body (or signature, or decorator args)
4. Write the modified tree back to disk

This preserves:
- All other functions exactly as written
- Comments and formatting throughout the file
- Import statements
- Helper functions
- Module-level code

### Adding a new node via GUI

1. Parse the existing file with libcst
2. Generate a new decorated function (using codegen templates)
3. Append it after the last `@pipeline.<type>` function
4. If edges were created, add corresponding `pipeline.connect()` calls

### Deleting a node via GUI

1. Parse the existing file
2. Remove the decorated function
3. Remove any `pipeline.connect()` calls referencing it
4. Write back

---

## 6. File Watcher + WebSocket

### Backend (Python)

```
watchfiles → detects .py change → parser → graph JSON → WebSocket push
```

- Use `watchfiles` (already in the tech stack plan) to watch the project root (and `modules/` if present)
- On change: debounce 300ms, then parse
- Compare new graph JSON to previous - if different, push via WebSocket
- **Self-write detection**: track file writes made by codegen. Ignore watcher events within 500ms of a codegen write to prevent feedback loops.

### Frontend (React)

```
WebSocket message → update React Flow state → re-render graph
```

- Maintain a WebSocket connection to `/ws/sync`
- On receiving a new graph: merge with current state (preserve positions if available)
- If the GUI has unsaved in-progress edits (e.g. user is typing in the code editor), show a "file changed externally - reload?" prompt instead of clobbering

---

## 7. Node Layout

### Layout engine: ELK

Replace dagre with [ELK](https://eclipse.dev/elk/) (Eclipse Layout Kernel) via the `elkjs` npm package. ELK is best-in-class for DAG layout:

- Proper layered layout (Sugiyama-style)
- Minimises edge crossings
- Handles fan-out/fan-in cleanly
- Supports port-based edge routing
- Handles 50+ node graphs without visual spaghetti

### Position persistence: pipeline schema-mapping file

Node positions are stored in a schema-mapping file alongside the pipeline:

```
motor.py              ← source of truth (Python code)
motor.haute.json       ← UI metadata (positions, layout preferences)
```

**`motor.haute.json`:**
```json
{
  "positions": {
    "read_data": { "x": 0, "y": 100 },
    "polars_node": { "x": 300, "y": 100 },
    "output": { "x": 600, "y": 100 }
  },
  "viewport": { "x": 0, "y": 0, "zoom": 1 }
}
```

**Rules:**
- If the schema-mapping file doesn't exist → auto-layout with ELK on first load
- If a new node appears in the `.py` file that isn't in the schema mapping → auto-position just that node
- If a node is deleted from the `.py` file → remove it from the schema mapping on next save
- Dragging a node in the GUI → update the schema mapping
- The schema-mapping file can be `.gitignore`d (layout is personal preference) or committed (team wants consistent layout)

---

## 8. Convergence Plan for Execution Engines

Currently there are two ways to run a pipeline:
1. `executor.py` - from JSON graph (GUI)
2. `pipeline.py` `Pipeline.run()` - from decorated Python (CLI)

### Target: single execution path

```
.py file → parser → graph JSON → executor.py → results
```

Both GUI and CLI use the same path:
- **GUI**: already has graph JSON, sends to executor
- **CLI** (`haute run motor.py`): parser reads `.py` → graph JSON → executor

`Pipeline.run()` and `Pipeline.score()` still work for the programmatic API, but under the hood they use the same executor logic.

---

## 9. Validation Layers

| Layer | When | What |
|---|---|---|
| **Syntax** | On file save / watcher trigger | `ast.parse()` - is it valid Python? |
| **Structure** | After successful parse | Does it have `@pipeline.<type>` functions? Are edges valid? |
| **Schema** | After structure validation | Do output columns of node A match input expectations of node B? (Polars lazy schema) |
| **Runtime** | On preview / run | Does the code actually execute without error? |

Each layer produces specific error messages shown in the GUI:
- Syntax errors → red banner with line number
- Structure errors → warning icons on affected nodes
- Schema errors → red edges between mismatched nodes
- Runtime errors → red node status with error message (already implemented)

---

## 10. What Needs to Be Built

| Component | File | Priority | Effort |
|---|---|---|---|
| **Parser** | `src/haute/parser.py` | P0 - critical path | Medium |
| **Surgical codegen** | modify `src/haute/codegen.py` | P0 - critical path | Medium |
| **WebSocket endpoint** | modify `src/haute/server.py` | P1 | Small |
| **File watcher** | modify `src/haute/server.py` | P1 | Small |
| **Frontend WebSocket handler** | modify `frontend/src/App.tsx` | P1 | Small |
| **ELK layout** | modify `frontend/src/App.tsx` | P1 | Small (drop-in replacement) |
| **Schema-mapping metadata** | modify `src/haute/server.py` | P2 | Small |
| **Error banners in GUI** | new frontend component | P2 | Small |
| **Execution engine convergence** | modify `executor.py`, `pipeline.py` | P2 | Medium |

### Build order

1. **Parser** - without this, nothing works. `.py` → graph JSON.
2. **ELK layout** - swap dagre for ELK so auto-layout is good enough out of the box.
3. **WebSocket + file watcher** - live sync from file changes to GUI.
4. **Surgical codegen** - GUI edits write back to `.py` without mangling.
5. **Schema-mapping metadata** - persist node positions.
6. **Error handling + banners** - graceful degradation for bad code.
7. **Execution convergence** - single path for CLI and GUI.

---

## 11. Risk & Mitigation

| Risk | Mitigation |
|---|---|
| libcst can't handle some Python syntax | Fall back to treating the whole file as unparseable; show error banner, don't crash |
| Feedback loop (watcher → parse → codegen → watcher → ...) | Self-write detection: ignore watcher events within 500ms of a codegen write |
| User writes code that doesn't map to any node type | Permissive parser - unknown code is preserved but invisible to GUI |
| Large pipelines (50+ nodes) look messy | ELK layout handles complex DAGs well; schema-mapping file preserves manual arrangement |
| Merge conflicts in the `.haute.json` schema mapping | Positions are non-critical; auto-layout resolves any conflict. File is optional. |
| Two users editing same file via GUI | Out of scope for now. Same as two people editing any file - git handles it. |
