/**
 * Render tests for OutputEditor.
 *
 * Tests: response fields label, empty state, column table rendering,
 * checkbox interactions, and JSON preview.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render as rtlRender, screen, fireEvent, cleanup } from "@testing-library/react"
import OutputEditor from "../../panels/editors/OutputEditor"
import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode, SimpleEdge } from "../../panels/editors"

afterEach(cleanup)

const UPSTREAM_COLUMNS = [
  { name: "premium", dtype: "Float64" },
  { name: "area", dtype: "String" },
  { name: "power", dtype: "Int64" },
]

const allNodes: SimpleNode[] = [
  {
    id: "upstream",
    data: {
      label: "Upstream Node",
      description: "",
      nodeType: "polars",
      _columns: UPSTREAM_COLUMNS,
    },
  },
]

const edges: SimpleEdge[] = [{ id: "e1", source: "upstream", target: "output_1" }]

const DEFAULT_PROPS = {
  config: {},
  onUpdate: vi.fn(),
  nodeId: "output_1",
}

/**
 * Renders OutputEditor wrapped in a GraphProvider.  Accepts `allNodes` and
 * `edges` overrides that flow through the provider rather than as component
 * props (post Phase 2 Package 3C — graph data lives in context).
 */
function render(
  element: React.ReactElement,
  opts: { allNodes?: SimpleNode[]; edges?: SimpleEdge[] } = {},
) {
  return rtlRender(
    <GraphProvider allNodes={opts.allNodes ?? []} edges={opts.edges ?? []}>
      {element}
    </GraphProvider>,
  )
}

describe("OutputEditor", () => {
  it("renders Response Fields label", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Response Fields")).toBeTruthy()
  })

  it("shows empty state message when no upstream columns", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Preview or run the upstream node to see columns")).toBeTruthy()
  })

  it("shows empty state when there are no edges", () => {
    render(<OutputEditor {...DEFAULT_PROPS}  />, { allNodes: allNodes, edges: [] })
    expect(screen.getByText("Preview or run the upstream node to see columns")).toBeTruthy()
  })

  it("shows empty state when upstream node has no _columns", () => {
    const noColumnsNodes = [
      { id: "upstream", data: { label: "Empty", description: "", nodeType: "polars" } },
    ]
    render(<OutputEditor {...DEFAULT_PROPS}  />, { allNodes: noColumnsNodes, edges: edges })
    expect(screen.getByText("Preview or run the upstream node to see columns")).toBeTruthy()
  })

  it("renders column table when upstream has _columns data", () => {
    render(<OutputEditor {...DEFAULT_PROPS}  />, { allNodes: allNodes, edges: edges })
    expect(screen.getByText("Column")).toBeTruthy()
    expect(screen.getByText("Type")).toBeTruthy()
    expect(screen.queryByText("Preview or run the upstream node to see columns")).toBeNull()
  })

  it("shows all upstream column names in the table", () => {
    render(<OutputEditor {...DEFAULT_PROPS}  />, { allNodes: allNodes, edges: edges })
    expect(screen.getByText("premium")).toBeTruthy()
    expect(screen.getByText("area")).toBeTruthy()
    expect(screen.getByText("power")).toBeTruthy()
  })

  it("shows dtype for each upstream column", () => {
    render(<OutputEditor {...DEFAULT_PROPS}  />, { allNodes: allNodes, edges: edges })
    expect(screen.getByText("Float64")).toBeTruthy()
    expect(screen.getByText("String")).toBeTruthy()
    expect(screen.getByText("Int64")).toBeTruthy()
  })

  it("renders a checkbox for each upstream column", () => {
    render(<OutputEditor {...DEFAULT_PROPS}  />, { allNodes: allNodes, edges: edges })
    const checkboxes = screen.getAllByRole("checkbox")
    expect(checkboxes.length).toBe(3)
  })

  it("checkboxes are unchecked by default when no fields in config", () => {
    render(<OutputEditor {...DEFAULT_PROPS}  />, { allNodes: allNodes, edges: edges })
    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[]
    checkboxes.forEach((cb) => {
      expect(cb.checked).toBe(false)
    })
  })

  it("checkboxes reflect fields from config", () => {
    render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{ fields: ["premium", "power"] }}
      />,
      { allNodes, edges },
    )
    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[]
    // Order: premium, area, power
    expect(checkboxes[0].checked).toBe(true)  // premium
    expect(checkboxes[1].checked).toBe(false)  // area
    expect(checkboxes[2].checked).toBe(true)  // power
  })

  it("checking a column calls onUpdate with the column name added", () => {
    const onUpdate = vi.fn()
    render(
      <OutputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
      />,
      { allNodes, edges },
    )
    const checkboxes = screen.getAllByRole("checkbox")
    fireEvent.click(checkboxes[0]) // premium
    expect(onUpdate).toHaveBeenCalledWith("fields", ["premium"])
  })

  it("unchecking a column calls onUpdate with the column removed", () => {
    const onUpdate = vi.fn()
    render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{ fields: ["premium", "area"] }}
        onUpdate={onUpdate}
      />,
      { allNodes, edges },
    )
    const checkboxes = screen.getAllByRole("checkbox")
    fireEvent.click(checkboxes[0]) // uncheck premium
    expect(onUpdate).toHaveBeenCalledWith("fields", ["area"])
  })

  it("checking multiple columns accumulates them in order", () => {
    const onUpdate = vi.fn()
    render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{ fields: ["premium"] }}
        onUpdate={onUpdate}
      />,
      { allNodes, edges },
    )
    const checkboxes = screen.getAllByRole("checkbox")
    fireEvent.click(checkboxes[2]) // add power
    expect(onUpdate).toHaveBeenCalledWith("fields", ["premium", "power"])
  })

  it("does not show JSON Preview when no fields are selected", () => {
    render(<OutputEditor {...DEFAULT_PROPS} />, { allNodes, edges })
    expect(screen.queryByText("JSON Preview")).toBeNull()
  })

  it("shows JSON Preview when fields are selected", () => {
    render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{ fields: ["premium"] }}
      />,
      { allNodes, edges },
    )
    expect(screen.getByText("JSON Preview")).toBeTruthy()
  })

  it("JSON Preview includes selected field names", () => {
    const { container } = render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{ fields: ["premium", "area"] }}
      />,
      { allNodes, edges },
    )
    expect(screen.getByText("JSON Preview")).toBeTruthy()
    const pre = container.querySelector("pre")!
    expect(pre.textContent).toContain('"premium"')
    expect(pre.textContent).toContain('"area"')
  })

  it("JSON Preview does not include unselected field names", () => {
    const { container } = render(
      <OutputEditor
        {...DEFAULT_PROPS}
        config={{ fields: ["premium"] }}
      />,
      { allNodes, edges },
    )
    const pre = container.querySelector("pre")!
    expect(pre.textContent).toContain('"premium"')
    expect(pre.textContent).not.toContain('"area"')
    expect(pre.textContent).not.toContain('"power"')
  })
})
