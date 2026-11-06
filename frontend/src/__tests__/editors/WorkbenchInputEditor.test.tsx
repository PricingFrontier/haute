/**
 * The Workbench Input's panel (specs/workbench): the tables the workbench store fetched,
 * each table's inputs by its port, `<table>_input`, read-only, with each port's problem and
 * what previews run on.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import WorkbenchInputEditor from "../../panels/editors/WorkbenchInputEditor"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import type { WorkbenchColumn, WorkbenchTable } from "../../api/types"

const RESERVED = new Set(["class", "pl", "pipeline"])

const column = (name: string, type: WorkbenchColumn["type"]) => ({ name, type })
/** A table's input side, as a fetch gives it. */
const inputSide = (name: string, rows: WorkbenchTable["rows"], columns: WorkbenchColumn[], key: string | null = null): WorkbenchTable => ({
  name,
  side: "input",
  port: `${name}_input`,
  rows,
  columns,
  key,
  null_key: key === null ? null : 1, index: null,
})
const tables: WorkbenchTable[] = [
  inputSide("policy_details", "one", [column("state", "str"), column("inception_date", "date")]),
  inputSide("equipment", "many", [column("row_number", "int"), column("item_id", "str"), column("value", "float")], "row_number"),
  inputSide("pl", "one", [column("x", "int")]),
  inputSide("two words", "one", [column("x", "int")]),
  inputSide("bare", "one", []),
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
    const policy = within(section).getByTestId("workbench-table-policy_details_input")
    expect(within(policy).getByText("policy_details_input")).toBeInTheDocument()
    expect(within(policy).getByText("one per quote")).toBeInTheDocument()
    expect(within(policy).getByText("state")).toBeInTheDocument()
    expect(within(policy).getByText("date")).toBeInTheDocument()
    expect(within(within(section).getByTestId("workbench-table-equipment_input")).getByText("many per quote")).toBeInTheDocument()
    // A table without a column has no frame, so no port.
    expect(within(section).getByTestId("workbench-table-bare_input")).toHaveTextContent("No columns yet, so no port.")
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
    expect(screen.getByTestId("workbench-table-policy_details_input")).toBeInTheDocument()
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

  it("says what stops a table's port being a port, judging the port's name rather than the table's", () => {
    fetched()
    renderEditor()

    expect(within(screen.getByTestId("workbench-table-two words_input")).getByRole("alert")).toHaveTextContent("must be an ASCII identifier")
    // pl is a reserved label, but its table's port, pl_input, is not.
    expect(within(screen.getByTestId("workbench-table-pl_input")).queryByRole("alert")).not.toBeInTheDocument()
    expect(within(screen.getByTestId("workbench-table-policy_details_input")).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("says a port is reserved when its name is one of the document's reserved labels", () => {
    fetched()
    render(<WorkbenchInputEditor accentColor="#E69F00" reservedFrameLabels={new Set(["pl_input"])} />)

    expect(within(screen.getByTestId("workbench-table-pl_input")).getByRole("alert")).toHaveTextContent(
      "cannot be a Python hard keyword or a name node code binds itself",
    )
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
