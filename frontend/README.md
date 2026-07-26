# Haute Frontend

Visual node-based pipeline editor for [Haute](../README.md). Users build
data-processing pipelines by dragging nodes onto a canvas, wiring them
together, and inspecting live data previews -- all backed by a Python
FastAPI server that executes the pipeline with Polars.

## Tech Stack

- **React 19** with **TypeScript** (strict)
- **Vite** -- dev server with HMR, proxies `/api` and `/ws` to the backend
- **ReactFlow** (`@xyflow/react`) -- canvas, nodes, edges, layout (ELK)
- **Zustand** -- lightweight stores for UI state, settings, node results, toasts
- **Tailwind CSS v4** -- utility-first styling via the Vite plugin
- **CodeMirror 6** -- embedded Python editor for node expressions
- **Vitest** + **React Testing Library** -- unit / component tests

## Getting Started

```bash
# From the frontend/ directory:
npm install
npm run dev      # starts Vite dev server (default http://localhost:5173)
npm run build    # type-checks then builds into ../src/haute/static/
npm run test     # runs Vitest in single-run mode
npm run lint     # ESLint
```

The dev server proxies `/api` requests to `http://127.0.0.1:8000` and
`/ws` WebSocket connections to the same host, so start the Haute backend
first.

## Project Structure

```
src/
  App.tsx            Main FlowEditor component (ReactFlow canvas + panels)
  nodes/             Custom ReactFlow node components (PipelineNode, SubmodelNode, ...)
  panels/            Side panels: NodePanel, NodePalette, DataPreview, TracePanel, ...
  components/        Shared UI: Toolbar, ContextMenu, BreadcrumbBar, Toast, ...
  stores/            Zustand stores
    useUIStore       Chrome layout (palette open, dialogs, dirty flag)
    useSettingsStore MLflow config, persistent settings
    useNodeResultsStore  Cached previews, background job results
    useToastStore    Notification queue
  hooks/             React hooks
    usePipelineAPI   Load/save pipeline, fetch node previews
    useWebSocketSync Real-time file-change sync from backend
    useUndoRedo      Undo/redo over nodes + edges
    useTracing       Cell-level data lineage tracing
    useSubmodelNavigation  Drill-in/out of submodel groups
    useKeyboardShortcuts   Global hotkeys (save, undo, copy/paste, ...)
    useEdgeHandlers  Connect, delete, drag-drop edge logic
    useNodeHandlers  Delete, duplicate, rename, auto-layout
  api/               Typed fetch helpers for backend endpoints
  types/             Shared TypeScript types (node data, configs)
  utils/             Constants (node type registry), helpers
  trace/             Lineage tracing logic
```

## Architecture Notes

- **Stores over props.** Global UI state lives in Zustand stores so deeply
  nested components can subscribe to individual slices without prop drilling.
- **Hooks extract logic.** The main `FlowEditor` delegates to focused hooks
  (`usePipelineAPI`, `useUndoRedo`, `useTracing`, etc.) to keep the
  orchestrator readable.
- **Build output** lands in `../src/haute/static/` so the Python package can
  serve the frontend as static files in production.
- **Development listener.** `vite.config.ts` owns the fixed
  `127.0.0.1:5173` Vite address and the API/WebSocket proxies. Package
  versioning remains Python metadata and is not injected into the browser.
