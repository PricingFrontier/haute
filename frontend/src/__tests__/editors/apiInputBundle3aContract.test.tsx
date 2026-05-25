/**
 * Contract tests for Bundle 3a — three small apiInput-specific UI fixes.
 *
 * 1. **Hide the Columns tab from apiInput nodes**. Per the option-2
 *    decision in the v2 consolidation discussion: the v2-native
 *    column-filter surface is the per-column `selected: bool` inside
 *    `tables[].columns[]` in the Schema panel. The legacy `Columns`
 *    tab (which writes the universal-but-apiInput-illegitimate
 *    `selected_columns` / `column_renames` keys via
 *    `GroupedColumnsTab`) overlapped with the v2 surface and let the
 *    user double-author the same intent. Add `NODE_TYPES.API_INPUT`
 *    to `NO_COLUMNS_TAB` in NodePanel.tsx so the tab is no longer
 *    rendered for apiInput nodes.
 *
 * 2. **configPath must use the sanitised label, not node.id**. The
 *    backend's canonical filename scheme writes config sidecars at
 *    `config/<type_folder>/<_sanitize_func_name(label)>.json`
 *    (`_config_io.py:320-321`). The frontend at `NodePanel.tsx:458`
 *    was passing `config/quote_input/${node.id}.json` to the
 *    `ApiInputEditor`, which the cache-status GET then routed to a
 *    path the backend never wrote to → silent `cached=false`
 *    response → cache button appears unresponsive. Fix: use
 *    `sanitizeName(node.data.label)` so frontend and backend agree.
 *
 * 3. **Radius fix** (no test here — pure CSS, covered by existing
 *    PipelineNode.test.tsx regression; the prior fix was committed
 *    to the worktree but never staged, this commit picks it up).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

import NodePanel from "../../panels/NodePanel"
import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode, SimpleEdge } from "../../panels/editors"
import useUIStore from "../../stores/useUIStore"
import { sanitizeName } from "../../utils/sanitizeName"

// Capture props passed to the (mocked) ApiInputEditor so we can pin the
// configPath contract from outside the component.
const apiInputEditorProps: Record<string, unknown>[] = []

vi.mock("../../panels/LazyNodeEditors", () => ({
  LazyEditorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DataSourceEditor: () => <div data-testid="DataSourceEditor" />,
  TransformEditor: () => <div data-testid="TransformEditor" />,
  ExploreCodeEditor: () => <div data-testid="ExploreCodeEditor" />,
  ModelScoreEditor: () => <div data-testid="ModelScoreEditor" />,
  BandingEditor: () => <div data-testid="BandingEditor" />,
  RatingStepEditor: () => <div data-testid="RatingStepEditor" />,
  OutputEditor: () => <div data-testid="OutputEditor" />,
  ExternalFileEditor: () => <div data-testid="ExternalFileEditor" />,
  ApiInputEditor: (props: Record<string, unknown>) => {
    apiInputEditorProps.push(props)
    return <div data-testid="ApiInputEditor" />
  },
  LiveSwitchEditor: () => <div data-testid="LiveSwitchEditor" />,
  SinkEditor: () => <div data-testid="SinkEditor" />,
  ScenarioExpanderEditor: () => <div data-testid="ScenarioExpanderEditor" />,
  OptimiserApplyEditor: () => <div data-testid="OptimiserApplyEditor" />,
  ConstantEditor: () => <div data-testid="ConstantEditor" />,
  SubmodelEditor: () => <div data-testid="SubmodelEditor" />,
  ColumnsTab: () => <div data-testid="ColumnsTab" />,
  GroupedColumnsTab: () => <div data-testid="GroupedColumnsTab" />,
  ModellingConfig: () => <div data-testid="ModellingConfig" />,
  OptimiserConfig: () => <div data-testid="OptimiserConfig" />,
}))

function makeNode(overrides: Partial<SimpleNode> = {}): SimpleNode {
  return {
    id: "node_xyz123",
    data: {
      label: "My Node",
      description: "",
      nodeType: "polars",
      config: {},
    },
    ...overrides,
  }
}

function renderPanel(node: SimpleNode) {
  return render(
    <GraphProvider allNodes={[node]} edges={[] as SimpleEdge[]}>
      <NodePanel
        node={node}
        onClose={vi.fn()}
        onUpdateNode={vi.fn()}
        onDeleteEdge={vi.fn()}
        onRefreshPreview={vi.fn()}
      />
    </GraphProvider>,
  )
}

beforeEach(() => {
  Object.defineProperty(window, "innerWidth", {
    value: 1920,
    writable: true,
    configurable: true,
  })
  useUIStore.setState({
    nodePanelWidth: 600,
    paletteOpen: true,
    explorePanes: {},
    explorePreviewPanes: {},
  })
  apiInputEditorProps.length = 0
})

afterEach(cleanup)

// ---------------------------------------------------------------------------
// 1. Columns tab hidden from apiInput nodes
// ---------------------------------------------------------------------------

describe("Bundle 3a — Columns tab hidden from apiInput", () => {
  it("does NOT render the Columns tab button for an apiInput node", () => {
    const node = makeNode({
      id: "n1",
      data: {
        label: "Quote Input",
        description: "",
        nodeType: "apiInput",
        config: { path: "data.json", tables: [] },
      },
    })
    renderPanel(node)

    // The tab bar renders two buttons ("config" / "columns") only when
    // showColumnsTab is true. apiInput must be in NO_COLUMNS_TAB so the
    // bar is suppressed entirely — neither button appears.
    const columnsBtn = screen.queryByRole("button", { name: /^columns$/i })
    expect(columnsBtn).toBeNull()
  })

  it("STILL renders the Columns tab button for a polars node (regression check)", () => {
    const node = makeNode({
      id: "n2",
      data: {
        label: "transform_one",
        description: "",
        nodeType: "polars",
        config: { code: "return df" },
      },
    })
    renderPanel(node)

    const columnsBtn = screen.queryByRole("button", { name: /^columns$/i })
    expect(columnsBtn).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 2. configPath uses sanitised label, not node.id
// ---------------------------------------------------------------------------

describe("Bundle 3a — configPath canonical (sanitised label, not node.id)", () => {
  it("passes configPath built from sanitizeName(node.data.label)", () => {
    const node = makeNode({
      id: "internal_uuid_xyz123",
      data: {
        label: "Quote Input",
        description: "",
        nodeType: "apiInput",
        config: { path: "data.json", tables: [] },
      },
    })
    renderPanel(node)

    expect(apiInputEditorProps.length).toBeGreaterThan(0)
    const props = apiInputEditorProps[apiInputEditorProps.length - 1]
    const configPath = props.configPath as string
    // Backend canonical scheme: config/quote_input/<sanitised_label>.json.
    // sanitizeName("Quote Input") → typically "Quote_Input" (mirroring
    // the Python `_sanitize_func_name`).
    const expectedSuffix = `${sanitizeName(node.data.label)}.json`
    expect(configPath).toBe(`config/quote_input/${expectedSuffix}`)
    // Crucially, NOT using node.id:
    expect(configPath).not.toContain(node.id)
  })

  it("uses a different configPath when the label changes (rename round-trips)", () => {
    const nodeA = makeNode({
      id: "same_id",
      data: {
        label: "Original Label",
        description: "",
        nodeType: "apiInput",
        config: { path: "x.json", tables: [] },
      },
    })
    renderPanel(nodeA)
    const pathA = (
      apiInputEditorProps[apiInputEditorProps.length - 1].configPath as string
    )

    cleanup()
    apiInputEditorProps.length = 0

    const nodeB = makeNode({
      id: "same_id", // same node.id
      data: {
        label: "Renamed Label",
        description: "",
        nodeType: "apiInput",
        config: { path: "x.json", tables: [] },
      },
    })
    renderPanel(nodeB)
    const pathB = (
      apiInputEditorProps[apiInputEditorProps.length - 1].configPath as string
    )

    // node.id is identical but label differs → paths must differ.
    // (Previous buggy behaviour: both paths would be identical because
    // they were keyed on node.id.)
    expect(pathA).not.toBe(pathB)
  })
})
