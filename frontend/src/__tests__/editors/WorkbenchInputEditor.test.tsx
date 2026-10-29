/**
 * The Workbench Input's panel (specs/workbench): the tables the workbench store fetched,
 * read-only, with each name's problem as a port and what previews run on.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import WorkbenchInputEditor from "../../panels/editors/WorkbenchInputEditor"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import type { WorkbenchColumn, WorkbenchTable } from "../../api/types"

const RESERVED = new Set(["class", "pl", "pipeline"])

const column = (name: string, type: WorkbenchColumn["type"]) => ({ name, type })
const tables: WorkbenchTable[] = [
  { name: "policy_details", rows: "one", columns: [column("state", "str"), column("inception_date", "date")] },
  { name: "equipment", rows: "many", columns: [column("item_id", "str"), column("value", "float")] },
  { name: "pl", rows: "one", columns: [column("x", "int")] },
  { name: "bare", rows: "one", columns: [] },
]
/** The newest fetch of the workbench's tables, with the sample quote it gave. */
const fetched = (sample: Record<string, unknown> = {}) =>
  useWorkbenchStore.setState({ tables: { tables, sample, responseTables: [], fetch: 1 } })
function renderEditor() {
  return render(<WorkbenchInputEditor accentColor="#E69F00" reservedFrameLabels={RESERVED} />)
}

describe("WorkbenchInputEditor", () => {
  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: true, tables: null })
  })
  afterEach(() => {
    cleanup()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline", tables: null })
  })

  it("shows the tables fetched read-only, with nothing to edit them", () => {
    fetched()
    renderEditor()

    const section = screen.getByTestId("workbench-input-tables")
    expect(within(section).getByText("Tables from the workbench")).toBeInTheDocument()
    const policy = within(section).getByTestId("workbench-table-policy_details")
    expect(within(policy).getByText("one per quote")).toBeInTheDocument()
    expect(within(policy).getByText("state")).toBeInTheDocument()
    expect(within(policy).getByText("date")).toBeInTheDocument()
    expect(within(within(section).getByTestId("workbench-table-equipment")).getByText("many per quote")).toBeInTheDocument()
    // A table without a column has no frame, so no port.
    expect(within(section).getByTestId("workbench-table-bare")).toHaveTextContent("No columns yet, so no port.")
    expect(screen.queryByText("Infer Tables")).not.toBeInTheDocument()
    expect(screen.queryAllByRole("textbox")).toEqual([])
    expect(screen.queryByRole("note")).not.toBeInTheDocument()

    // It reads no file: previews run on nulls, so there is no sample to choose.
    expect(screen.queryByText("Preview Data")).not.toBeInTheDocument()
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on one row of nulls per table until the workbench supplies values.",
    )
  })

  it("shows each fetch's tables as it lands, and none before the first", () => {
    renderEditor()
    expect(screen.getByTestId("workbench-input-tables")).toHaveTextContent("No tables yet.")

    act(() => fetched())
    expect(screen.getByTestId("workbench-table-policy_details")).toBeInTheDocument()
    expect(screen.getByTestId("workbench-input-tables")).not.toHaveTextContent("No tables yet.")
  })

  it("says previews run on the workbench's sample once it supplies values", () => {
    fetched({ policy_details: { state: "NY" } })
    renderEditor()
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on the workbench's sample values, read as a request is: a table with many rows and nothing typed has none.",
    )

    act(() => fetched({}))
    expect(screen.getByTestId("workbench-input-preview-note")).toHaveTextContent(
      "Previews run on one row of nulls per table until the workbench supplies values.",
    )
  })

  it("says what stops a table's name being a port", () => {
    fetched()
    renderEditor()

    const reserved = screen.getByTestId("workbench-table-pl")
    expect(within(reserved).getByRole("alert")).toHaveTextContent("cannot be a Python hard keyword or a name node code binds itself")
    expect(within(screen.getByTestId("workbench-table-policy_details")).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("offers to edit the tables in the workbench's view while the workbench is enabled", () => {
    renderEditor()

    fireEvent.click(screen.getByRole("button", { name: "Edit in Workbench" }))
    expect(useWorkbenchStore.getState().activeView).toBe("workbench")

    cleanup()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline" })
    renderEditor()
    expect(screen.queryByRole("button", { name: "Edit in Workbench" })).not.toBeInTheDocument()
  })
})
