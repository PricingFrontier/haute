/**
 * The Workbench Output's panel (specs/workbench): the response's tables the workbench
 * store fetched, each with the node connected to its port and each of its columns
 * bordered by how the connected frame fills it.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import WorkbenchOutputEditor from "../../panels/editors/WorkbenchOutputEditor"
import type { InputSource } from "../../panels/editors/_shared"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import type { WorkbenchColumn, WorkbenchTable } from "../../api/types"

const column = (name: string, type: WorkbenchColumn["type"]) => ({ name, type })
const responseTables: WorkbenchTable[] = [
  { name: "pricing_output", rows: "one", columns: [column("premium", "float"), column("referral", "str")] },
  { name: "layers", rows: "many", columns: [column("layer", "int"), column("premium", "float")] },
  { name: "two words", rows: "one", columns: [column("x", "int")] },
  { name: "bare", rows: "one", columns: [] },
]
/** The newest fetch of the workbench's tables, whose response tables the panel shows. */
const fetched = (tables: WorkbenchTable[] = responseTables) =>
  useWorkbenchStore.setState({ tables: { tables: [], sample: {}, responseTables: tables, fetch: 1 } })
const priced: InputSource = {
  sourceNodeId: "priced",
  name: "priced",
  sourceLabel: "priced",
  edgeId: "e_priced",
  targetHandle: "pricing_output",
  columns: [{ name: "premium", dtype: "Float64" }, { name: "base_premium", dtype: "Float64" }],
}

function renderEditor(props: { inputSources?: InputSource[] } = {}) {
  render(
    <WorkbenchOutputEditor
      inputSources={props.inputSources ?? [priced]}
      reservedFrameLabels={new Set(["class"])}
    />,
  )
}
const border = (table: string, name: string) =>
  screen.getByTestId(`workbench-column-${table}-${name}`).style.borderColor

describe("WorkbenchOutputEditor", () => {
  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: true })
    fetched()
  })
  afterEach(() => {
    cleanup()
    useWorkbenchStore.setState({ enabled: false, tables: null })
  })

  it("shows each table read-only with the node connected to its port", () => {
    renderEditor()

    expect(within(screen.getByTestId("workbench-output-tables")).getByText("Tables from the workbench")).toBeInTheDocument()
    const pricing = screen.getByTestId("workbench-table-pricing_output")
    expect(pricing).toHaveTextContent("one per quote")
    expect(within(pricing).getByText("referral")).toBeInTheDocument()
    expect(screen.getByTestId("workbench-table-connection-pricing_output")).toHaveTextContent("From priced")
    expect(screen.getByTestId("workbench-table-layers")).toHaveTextContent("many per quote")
    expect(screen.getByTestId("workbench-table-connection-layers")).toHaveTextContent("Not connected")
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    // A table without a column has no port, so nothing to connect.
    expect(screen.getByTestId("workbench-table-bare")).toHaveTextContent("No columns yet, so no port.")
    expect(screen.queryByTestId("workbench-table-connection-bare")).not.toBeInTheDocument()
  })

  it("borders a column red while the frame has no column of exactly its name, or nothing is connected", () => {
    renderEditor({
      inputSources: [{ ...priced, columns: [{ name: "premium", dtype: "Float64" }, { name: "Referral", dtype: "String" }] }],
    })

    expect(border("pricing_output", "premium")).toBe("var(--border)")
    expect(border("pricing_output", "referral")).toBe("var(--danger)")
    expect(screen.getByTestId("workbench-column-pricing_output-referral")).toHaveAttribute("title", "Missing from the frame")
    // Every column of a table nothing is connected to is missing.
    expect(border("layers", "layer")).toBe("var(--danger)")
    expect(border("layers", "premium")).toBe("var(--danger)")
    // Nothing to pick: the panel edits nothing.
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  })

  it("borders a column orange while the frame's column of its name is of a type that does not fit", () => {
    const layered: InputSource = {
      ...priced,
      sourceNodeId: "layered",
      sourceLabel: "layered",
      edgeId: "e_layered",
      targetHandle: "layers",
      // A Decimal takes an integer, an Integer no float.
      columns: [{ name: "layer", dtype: "Float64" }, { name: "premium", dtype: "Int64" }],
    }
    renderEditor({
      inputSources: [{ ...priced, columns: [{ name: "premium", dtype: "String" }, { name: "referral", dtype: "Categorical" }] }, layered],
    })

    expect(border("pricing_output", "premium")).toBe("var(--caution)")
    expect(screen.getByTestId("workbench-column-pricing_output-premium")).toHaveAttribute("title", "String in the frame")
    expect(border("pricing_output", "referral")).toBe("var(--border)")
    expect(border("layers", "layer")).toBe("var(--caution)")
    expect(border("layers", "premium")).toBe("var(--border)")
  })

  it("never borders the quote's reference and option, which the response gives, connected or not", () => {
    const identity = [column("quote_reference", "str"), column("quote_option", "int")]
    fetched([
      { name: "pricing_output", rows: "one", columns: [...identity, column("premium", "float")] },
      { name: "layers", rows: "many", columns: [...identity, column("layer", "int")] },
    ])
    render(
      <WorkbenchOutputEditor
        // The frame holds a quote_reference of another type, and no quote_option.
        inputSources={[{ ...priced, columns: [{ name: "premium", dtype: "Float64" }, { name: "quote_reference", dtype: "Int64" }] }]}
        reservedFrameLabels={new Set()}
      />,
    )

    for (const table of ["pricing_output", "layers"]) {
      for (const name of ["quote_reference", "quote_option"]) {
        expect(border(table, name)).toBe("var(--border)")
        expect(screen.getByTestId(`workbench-column-${table}-${name}`)).not.toHaveAttribute("title")
      }
    }
    // Its own columns are still judged: nothing is connected to layers.
    expect(border("layers", "layer")).toBe("var(--danger)")
  })

  it("judges no column of a connected frame not previewed yet", () => {
    renderEditor({ inputSources: [{ ...priced, columns: undefined }] })

    expect(border("pricing_output", "premium")).toBe("var(--border)")
    expect(border("pricing_output", "referral")).toBe("var(--border)")
    expect(screen.getByTestId("workbench-column-pricing_output-referral")).not.toHaveAttribute("title")
  })

  it("says what stops a table's name being a port", () => {
    renderEditor()

    expect(within(screen.getByTestId("workbench-table-two words")).getByRole("alert")).toBeInTheDocument()
    expect(within(screen.getByTestId("workbench-table-layers")).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("shows each fetch's tables as it lands, and none before the first", () => {
    useWorkbenchStore.setState({ tables: null })
    renderEditor()
    expect(screen.getByTestId("workbench-output-tables")).toHaveTextContent("No tables yet.")
    expect(screen.queryByRole("note")).not.toBeInTheDocument()

    act(() => fetched(responseTables.slice(1, 2)))
    expect(screen.getByTestId("workbench-table-layers")).toBeInTheDocument()
    expect(screen.queryByTestId("workbench-table-pricing_output")).not.toBeInTheDocument()
  })

  it("offers to edit the tables in the workbench's view while the workbench is enabled", () => {
    renderEditor()

    fireEvent.click(screen.getByRole("button", { name: "Edit in Workbench" }))
    expect(useWorkbenchStore.getState().activeView).toBe("workbench")
    useWorkbenchStore.setState({ activeView: "pipeline" })
  })
})
