/**
 * Contract tests for Bundle 3b — banner UX + cache-button repositioning.
 *
 * 1. **Stale-columns banner: dismissible**. Adds a close (×) button to
 *    the banner at `NodePanel.tsx:716-742`. Today the banner is sticky
 *    — once `_schemaWarnings` is present it occupies real estate at the
 *    top of the node panel until the warnings clear, with no way for
 *    the user to clear it short of resolving every warning. Adds a ×
 *    button that hides the banner for the current warning content.
 *
 * 2. **"Refresh and check" button**. At the top of the banner, styled
 *    like the existing Refresh button (`NodePanel.tsx:651-661` —
 *    `var(--accent)` background, `var(--text-on-accent)` foreground,
 *    `RefreshCw` icon, `text-[11px] font-medium`). On click: calls
 *    `onRefreshPreview` (which re-runs the executor and re-populates
 *    `_schemaWarnings`) and optimistically dismisses the banner — same
 *    effect as user clicking × then Refresh, in one gesture.
 *
 * 3. **Clear-triggers (signature-based dismissal)**. Dismissal is tied
 *    to the specific warning content the user dismissed (sig =
 *    `column|status` pairs joined). If `_schemaWarnings` changes content
 *    (a new column appears, a status changes, the length changes), the
 *    banner re-appears even if the user previously dismissed it. The
 *    user is always informed about NEW issues; only previously-seen
 *    ones stay hidden. Dismissal also resets when the panel switches to
 *    a different node (per-node-id scoping via `useEffect` on `node.id`).
 *
 * 4. **Cache button above schema editor**. In `ApiInputEditor.tsx` the
 *    cache button currently sits inside the Tables editor section,
 *    below the table list (lines 388-411). Move it OUT of the Tables
 *    editor and above it, between the Preview Data file picker and the
 *    Tables editor. Rationale: the cache action is a data-loading
 *    affordance contextually tied to the data file, not the schema; it
 *    also gives the schema (table list) primary visual weight as the
 *    main authoring surface, with the cache as a supporting affordance
 *    near the data source it operates on.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"

import NodePanel from "../../panels/NodePanel"
import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode, SimpleEdge } from "../../panels/editors"
import useUIStore from "../../stores/useUIStore"

// ---------------------------------------------------------------------------
// Mocks for NodePanel-based tests (banner & dismissal behaviour)
// ---------------------------------------------------------------------------

vi.mock("../../panels/LazyNodeEditors", () => ({
  LazyEditorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DataInputEditor: () => <div data-testid="DataInputEditor" />,
  TransformEditor: () => <div data-testid="TransformEditor" />,
  ExploreCodeEditor: () => <div data-testid="ExploreCodeEditor" />,
  ModelScoreEditor: () => <div data-testid="ModelScoreEditor" />,
  BandingEditor: () => <div data-testid="BandingEditor" />,
  RatingStepEditor: () => <div data-testid="RatingStepEditor" />,
  OutputEditor: () => <div data-testid="OutputEditor" />,
  ExternalFileEditor: () => <div data-testid="ExternalFileEditor" />,
  ApiInputEditor: () => <div data-testid="ApiInputEditor" />,
  LiveSwitchEditor: () => <div data-testid="LiveSwitchEditor" />,
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
    id: "n1",
    data: {
      label: "My Node",
      description: "",
      nodeType: "polars",
      config: {},
    },
    ...overrides,
  }
}

function renderPanel(node: SimpleNode, onRefreshPreview = vi.fn()) {
  return render(
    <GraphProvider allNodes={[node]} edges={[] as SimpleEdge[]}>
      <NodePanel
        node={node}
        onClose={vi.fn()}
        onUpdateNode={vi.fn()}
        onDeleteEdge={vi.fn()}
        onRefreshPreview={onRefreshPreview}
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
})

afterEach(cleanup)

// ---------------------------------------------------------------------------
// 1. Banner — close × button
// ---------------------------------------------------------------------------

describe("Bundle 3b — Stale columns banner is dismissible", () => {
  it("renders a close (×) button on the banner when warnings present", () => {
    const node = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "ghost_col", status: "missing" }],
      },
    })
    renderPanel(node)

    expect(screen.getByText(/Stale columns/)).toBeInTheDocument()
    // The close button is identified by its accessible role + name.
    // Using `name: /dismiss/i` so the implementation can use either
    // `title="Dismiss"` or `aria-label="Dismiss"`.
    expect(screen.getByRole("button", { name: /dismiss/i })).toBeInTheDocument()
  })

  it("hides the banner after the close button is clicked", () => {
    const node = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "ghost_col", status: "missing" }],
      },
    })
    renderPanel(node)

    expect(screen.getByText(/Stale columns/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }))
    expect(screen.queryByText(/Stale columns/)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 2. Banner — "Refresh and check" button
// ---------------------------------------------------------------------------

describe("Bundle 3b — Stale columns banner has a Refresh-and-check button", () => {
  it("renders a Refresh and check button at the top of the banner", () => {
    const node = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "x", status: "missing" }],
      },
    })
    renderPanel(node)
    expect(screen.getByRole("button", { name: /refresh and check/i })).toBeInTheDocument()
  })

  it("calls onRefreshPreview when Refresh and check is clicked", () => {
    const onRefreshPreview = vi.fn()
    const node = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "x", status: "missing" }],
      },
    })
    renderPanel(node, onRefreshPreview)

    fireEvent.click(screen.getByRole("button", { name: /refresh and check/i }))
    expect(onRefreshPreview).toHaveBeenCalledTimes(1)
  })

  it("also dismisses the banner when Refresh and check is clicked (optimistic dismiss)", () => {
    // Rationale: clicking Refresh-and-check implies "acknowledge the
    // current warning set and re-evaluate". Banner reappears only if
    // the post-refresh warnings differ from what was dismissed.
    const node = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "x", status: "missing" }],
      },
    })
    renderPanel(node)
    fireEvent.click(screen.getByRole("button", { name: /refresh and check/i }))
    expect(screen.queryByText(/Stale columns/)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 3. Clear-triggers — signature-based dismissal
// ---------------------------------------------------------------------------

describe("Bundle 3b — Banner re-appears when warning content changes", () => {
  it("re-renders banner if a new column appears in warnings after dismissal", () => {
    const node1 = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "x", status: "missing" }],
      },
    })
    const { rerender } = renderPanel(node1)

    // Dismiss the initial banner
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }))
    expect(screen.queryByText(/Stale columns/)).not.toBeInTheDocument()

    // New warning content appears (different signature) — banner should
    // re-appear because the dismissal was specific to the prior content.
    const node2 = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [
          { column: "x", status: "missing" },
          { column: "y", status: "missing" },
        ],
      },
    })
    rerender(
      <GraphProvider allNodes={[node2]} edges={[] as SimpleEdge[]}>
        <NodePanel
          node={node2}
          onClose={vi.fn()}
          onUpdateNode={vi.fn()}
          onDeleteEdge={vi.fn()}
          onRefreshPreview={vi.fn()}
        />
      </GraphProvider>,
    )
    expect(screen.getByText(/Stale columns/)).toBeInTheDocument()
  })

  it("keeps banner dismissed when warning content is unchanged across re-render", () => {
    // Same sig before and after → dismissal sticks.
    const warnings = [{ column: "x", status: "missing" }]
    const node = makeNode({
      data: {
        label: "Test",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: warnings,
      },
    })
    const { rerender } = renderPanel(node)
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }))
    expect(screen.queryByText(/Stale columns/)).not.toBeInTheDocument()

    // Same node, identical warnings — banner stays gone.
    rerender(
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
    expect(screen.queryByText(/Stale columns/)).not.toBeInTheDocument()
  })

  it("resets dismissal when switching to a different node id", () => {
    const node1 = makeNode({
      id: "node_a",
      data: {
        label: "A",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "x", status: "missing" }],
      },
    })
    const { rerender } = renderPanel(node1)
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }))
    expect(screen.queryByText(/Stale columns/)).not.toBeInTheDocument()

    // Different node id, same warning content — dismissal should NOT
    // carry across nodes.
    const node2 = makeNode({
      id: "node_b",
      data: {
        label: "B",
        description: "",
        nodeType: "polars",
        config: {},
        _schemaWarnings: [{ column: "x", status: "missing" }],
      },
    })
    rerender(
      <GraphProvider allNodes={[node2]} edges={[] as SimpleEdge[]}>
        <NodePanel
          node={node2}
          onClose={vi.fn()}
          onUpdateNode={vi.fn()}
          onDeleteEdge={vi.fn()}
          onRefreshPreview={vi.fn()}
        />
      </GraphProvider>,
    )
    expect(screen.getByText(/Stale columns/)).toBeInTheDocument()
  })
})
