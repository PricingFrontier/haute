/** NodePanel supplies context to graph-aware editors without repeated graph scans. */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { render, screen, cleanup } from "@testing-library/react"
import NodePanel from "../NodePanel"
import { GraphProvider } from "../GraphContext"
import type { OnUpdateConfigResult, SimpleNode, SimpleEdge } from "../editors"
import useUIStore from "../../stores/useUIStore"

// ─── Editor mocks ─────────────────────────────────────────────────
// We only care that the right editor renders and that it does *not*
// receive `allNodes` / `edges` / `submodels` / `preamble` via props.
// The mocks capture the props they are called with so tests can assert
// against them.  `vi.hoisted` is required because `vi.mock` factories
// are hoisted above module-level `const`s — without it, the factory
// would hit a TDZ error when run.

const { dataOutputEditorProps, modellingConfigProps, optimiserConfigProps } = vi.hoisted(() => ({
  dataOutputEditorProps: [] as Record<string, unknown>[],
  modellingConfigProps: [] as Record<string, unknown>[],
  optimiserConfigProps: [] as Record<string, unknown>[],
}))

vi.mock("../LazyNodeEditors", () => ({
  LazyEditorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DataInputEditor: () => <div data-testid="DataInputEditor" />,
  TransformEditor: () => <div data-testid="TransformEditor" />,
  ModelScoreEditor: () => <div data-testid="ModelScoreEditor" />,
  BandingEditor: () => <div data-testid="BandingEditor" />,
  RatingStepEditor: () => <div data-testid="RatingStepEditor" />,
  OutputEditor: () => <div data-testid="OutputEditor" />,
  ExternalFileEditor: () => <div data-testid="ExternalFileEditor" />,
  ApiInputEditor: () => <div data-testid="ApiInputEditor" />,
  LiveSwitchEditor: () => <div data-testid="LiveSwitchEditor" />,
  DataOutputEditor: (props: Record<string, unknown>) => {
    dataOutputEditorProps.push(props)
    return <div data-testid="DataOutputEditor" />
  },
  ScenarioExpanderEditor: () => <div data-testid="ScenarioExpanderEditor" />,
  OptimiserApplyEditor: () => <div data-testid="OptimiserApplyEditor" />,
  ConstantEditor: () => <div data-testid="ConstantEditor" />,
  SubmodelEditor: () => <div data-testid="SubmodelEditor" />,
  ColumnsTab: () => <div data-testid="ColumnsTab" />,
  ModellingConfig: (props: Record<string, unknown>) => {
    modellingConfigProps.push(props)
    return <div data-testid="ModellingConfig" />
  },
  OptimiserConfig: (props: Record<string, unknown>) => {
    optimiserConfigProps.push(props)
    return <div data-testid="OptimiserConfig" />
  },
}))

// ─── Fixtures ─────────────────────────────────────────────────────

function makeNode(overrides: Partial<SimpleNode> = {}): SimpleNode {
  const candidate = {
    id: "node_1",
    data: {
      label: "My Node",
      description: "",
      nodeType: "polars",
      config: {},
    },
    ...overrides,
  }
  return {
    ...candidate,
    data: {
      ...candidate.data,
      _defaultInputName: candidate.data._defaultInputName
        ?? candidate.data.label.replaceAll(" ", "_"),
      _sourceHandleInputNames: candidate.data._sourceHandleInputNames ?? {},
    },
  }
}

/**
 * Render NodePanel wrapped in `GraphProvider` — graph data flows via
 * context, not via NodePanel props.  `node` is still a direct prop
 * because it identifies the *selected* node, not the graph itself.
 */
function renderWithGraph(opts: {
  node: SimpleNode | null
  allNodes?: SimpleNode[]
  edges?: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  onClose?: () => void
  onUpdateNode?: (id: string, data: Record<string, unknown>) => OnUpdateConfigResult
  onDeleteEdge?: (id: string) => void
}) {
  const {
    node,
    allNodes = [],
    edges = [],
    submodels,
    preamble,
    onClose = vi.fn(),
    onUpdateNode = vi.fn(() => ({ ok: true as const })),
    onDeleteEdge = vi.fn(),
  } = opts
  return render(
    <GraphProvider
      allNodes={allNodes}
      edges={edges}
      submodels={submodels}
      preamble={preamble}
    >
      <NodePanel
        node={node}
        onClose={onClose}
        onUpdateNode={onUpdateNode}
        onRenameNode={vi.fn(async () => ({ ok: true as const }))}
        onDeleteEdge={onDeleteEdge}
      />
    </GraphProvider>,
  )
}

// ─── Suite ────────────────────────────────────────────────────────

describe("NodePanel - Phase 2 Package 3C (graph context)", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true })
    useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true })
    dataOutputEditorProps.length = 0
    modellingConfigProps.length = 0
    optimiserConfigProps.length = 0
  })

  afterEach(cleanup)

  // ─── Regression: rendered DOM is unchanged by the refactor ──────

  describe("regression - DOM unchanged when graph data comes from context", () => {
    it("renders node label in the header, driven by context-supplied graph", () => {
      const node = makeNode()
      renderWithGraph({ node, allNodes: [node] })
      expect(screen.getByDisplayValue("My Node")).toBeInTheDocument()
    })

    it("dispatches to the correct editor based on node type when graph is in context", () => {
      const node = makeNode({
        id: "ds_1",
        data: { label: "DS", description: "", nodeType: "dataInput", config: {} },
      })
      renderWithGraph({ node, allNodes: [node] })
      expect(screen.getByTestId("DataInputEditor")).toBeInTheDocument()
    })

  })

  // ─── Nested editors read graph data from context, not props ─────

  describe("nested editors consume graph via context, not props", () => {
    it("DataOutputEditor receives no `allNodes` / `edges` / `submodels` / `preamble` props", () => {
      const node = makeNode({
        id: "sink_1",
        data: { label: "Sink", description: "", nodeType: "dataOutput", config: {} },
      })
      renderWithGraph({
        node,
        allNodes: [node],
        edges: [],
        submodels: { sm1: { kind: "scorecard" } },
        preamble: "import numpy as np",
      })

      expect(dataOutputEditorProps).toHaveLength(1)
      const props = dataOutputEditorProps[0]!
      // Graph data must NOT be threaded through props any more.
      expect(props).not.toHaveProperty("allNodes")
      expect(props).not.toHaveProperty("edges")
      expect(props).not.toHaveProperty("submodels")
      expect(props).not.toHaveProperty("preamble")
    })

    it("ModellingConfig receives no graph-related props (reads from context)", () => {
      const node = makeNode({
        id: "mod_1",
        data: { label: "Model", description: "", nodeType: "modelling", config: {} },
      })
      renderWithGraph({
        node,
        allNodes: [node],
        edges: [],
        submodels: {},
        preamble: "",
      })

      expect(modellingConfigProps).toHaveLength(1)
      const props = modellingConfigProps[0]!
      expect(props).not.toHaveProperty("allNodes")
      expect(props).not.toHaveProperty("edges")
      expect(props).not.toHaveProperty("submodels")
      expect(props).not.toHaveProperty("preamble")
    })

    it("OptimiserConfig receives no graph-related props (reads from context)", () => {
      const node = makeNode({
        id: "opt_1",
        data: { label: "Opt", description: "", nodeType: "optimiser", config: {} },
      })
      renderWithGraph({
        node,
        allNodes: [node],
        edges: [],
        submodels: {},
      })

      expect(optimiserConfigProps).toHaveLength(1)
      const props = optimiserConfigProps[0]!
      expect(props).not.toHaveProperty("allNodes")
      expect(props).not.toHaveProperty("edges")
      expect(props).not.toHaveProperty("submodels")
    })

    it("structural: DataOutputEditor's props type no longer declares allNodes/edges/submodels/preamble", () => {
      // Guards against a half-done refactor where NodePanel stops passing
      // props but the editor still accepts them.  We inspect the source
      // text directly because it is the cheapest way to pin the contract
      // without spinning up a full TypeScript program.
      const src = readFileSync(
        path.resolve(__dirname, "..", "editors", "DataOutputEditor.tsx"),
        "utf8",
      )
      // Props are a type-literal argument.  Match the first `{ ... }`
      // that follows the component's default-export signature.
      const match = src.match(/export default function DataOutputEditor\s*\(\s*\{[^}]*\}\s*:\s*\{([\s\S]*?)\n\}\)/)
      expect(match, "Failed to locate DataOutputEditor props type literal").not.toBeNull()
      const propsBlock = match![1]!
      // The props type must no longer declare any of these keys.
      expect(propsBlock).not.toMatch(/\ballNodes\b\s*:/)
      expect(propsBlock).not.toMatch(/\bedges\b\s*:/)
      expect(propsBlock).not.toMatch(/\bsubmodels\b\s*:/)
      expect(propsBlock).not.toMatch(/\bpreamble\b\s*:/)
    })
  })

})
